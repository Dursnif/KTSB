"""KTSB's own file about a person — «hvem er <bruker>» (run-queue J26, 2026-11-12: «et 2-filsystem»).

Two files per person:
  - `profile.yaml` — only what the person said or confirmed (the live extract from their own reply, answers they gave);
  - `ktsb_file.enc` (this module) — what KTSB guesses: the night meeting's findings, each a labelled guess with its source
    (meeting / rid), when it was written and status «guess». Never shown as «står i profilen».
Same encryption as the profile: the dict is encrypted with the person's key (live session, or the system key for the
night's confirmed background callers, P87). With no key at hand the guess is written as a SealedBox vault entry
(«ktsb_guess», the person's public key) and lands here at their next login. P149: each guess is also one piece of
evidence in the weight store — subject («person_fact», «<section>.<field>», user), source class «self», a pointer to the
guess, never the value (no free text in weights.db).
A guess moves into the profile only on the person's own yes (not built here). Nothing is ever deleted from this file.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from kaare_core import session_keys, weights
from kaare_core.config import now_local
from kaare_core.crypto import decrypt_dict, encrypt_dict

log = logging.getLogger(__name__)

USERS_DIR = Path("/kaare/state/users")
FILE_NAME = "ktsb_file.enc"
STATUS_GUESS = "guess"
STATUS_REJECTED = "rejected"  # the person said it is wrong — kept, never shown as a fact
PENDING_MOVE = "ktsb_move_pending.json"  # an approved move list (fields only, no values), run by the night sequence


def path(user_id: str) -> Path:
    return USERS_DIR / user_id / FILE_NAME


def _key(user_id: str) -> bytes | None:
    try:
        return session_keys.resolve_key_sync(user_id, allow_system_key=True)
    except Exception:
        return None


def load(user_id: str, key: bytes | None = None) -> dict | None:
    """{"guesses": [...]}; None when the file exists but cannot be read now (no key / decrypt failed)."""
    p = path(user_id)
    if not p.exists():
        return {"guesses": []}
    key = key or _key(user_id)
    if not key:
        return None
    try:
        data = decrypt_dict(p.read_text(encoding="utf-8"), key)
    except Exception as e:
        log.warning("[KTSB_FILE] decrypt failed for %s: %s", user_id, type(e).__name__)
        return None
    data.setdefault("guesses", [])
    return data


def _save(user_id: str, data: dict, key: bytes) -> None:
    p = path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(encrypt_dict(data, key), encoding="utf-8")
    tmp.replace(p)


def _entry(section: str, field: str, value: str, source: str, rid: str, ts: str | None, note: str) -> dict:
    return {"id": uuid.uuid4().hex[:12], "section": section, "field": field, "value": value, "source": source,
            "rid": rid, "ts": ts or now_local().isoformat(timespec="seconds"), "status": STATUS_GUESS, "note": note}


def store_entry(user_id: str, entry: dict, key: bytes | None = None) -> bool:
    """Append one prepared guess (also the vault path at login). False when the file cannot be read/written now."""
    key = key or _key(user_id)
    if not key:
        return False
    data = load(user_id, key)
    if data is None:
        return False  # never overwrite a file we could not read
    data["guesses"].append(entry)
    _save(user_id, data, key)
    weights.record(("person_fact", f"{entry['section']}.{entry['field']}", user_id), source_class="self",
                   source=entry.get("source") or "", context=entry.get("rid") or "",
                   pointer=f"ktsb_file:{entry['id']}")
    return True


def add_guess(user_id: str, section: str, field: str, value: str, *, source: str, rid: str = "",
              ts: str | None = None, note: str = "") -> str:
    """Store one guess about the person. Returns "stored" | "vaulted" | "refused"."""
    if not user_id or not section or not field or not str(value or "").strip():
        return "refused"
    entry = _entry(section, field, str(value).strip(), source, rid, ts, note)
    if store_entry(user_id, entry):
        return "stored"
    # PLC0415: profile_manager applies «ktsb_guess» vault entries through this module — a real import cycle
    from kaare_core.users.profile_manager import write_vault_entry  # noqa: PLC0415
    if write_vault_entry(user_id, {"type": "ktsb_guess", "entry": entry}):
        return "vaulted"
    return "refused"


def guesses(user_id: str) -> list[dict]:
    """Every guess, oldest first; [] when nothing or not readable now."""
    data = load(user_id)
    return list(data["guesses"]) if data else []


def move_meeting_fields(user_id: str, fields: list) -> list[str]:
    """J26: move profile values that only the night meeting wrote into this file as labelled guesses («meeting», the date
    it was written), and clear them in the profile (a change_log line, never the value). Only for an approved list —
    the owner approves it (main session 2026-11-12). Returns the «section.field» moved. Nothing is deleted: the value lives
    on here."""
    # PLC0415: profile_manager imports this module for the vault path — a real import cycle
    from kaare_core.users import profile_manager  # noqa: PLC0415
    key = _key(user_id)
    if not key:
        return []
    profile = profile_manager.load_profile(user_id, allow_system_key=True)
    moved = []
    for row in fields:
        section, field, written = (row["section"], row["field"], row.get("written")) if isinstance(row, dict) else row[:3]
        rejected_by = row.get("rejected_by") if isinstance(row, dict) else (row[3] if len(row) > 3 else None)
        sec = profile.get(section)
        value = sec.get(field) if isinstance(sec, dict) else None
        if value in (None, "", []):
            continue
        text = "; ".join(map(str, value)) if isinstance(value, list) else str(value)
        entry = _entry(section, field, text, "meeting", "", written, "moved from the profile (J26)")
        if rejected_by:
            entry["status"], entry["rejected_by"] = STATUS_REJECTED, rejected_by
        if not store_entry(user_id, entry, key):
            break  # cannot write the guess → leave the profile untouched
        if rejected_by:
            weights.answer(("person_fact", f"{section}.{field}", user_id), by_user=rejected_by, verdict="no",
                           verbatim=True, pointer=f"ktsb_file:{entry['id']}")
        sec[field] = [] if isinstance(value, list) else None
        profile.setdefault("change_log", []).append({"date": now_local().strftime("%Y-%m-%d"), "field": f"{section}.{field}",
                                                     "reason": "J26: moved to KTSB's own file as a guess"})
        moved.append(f"{section}.{field}")
    if moved:
        profile["change_log"] = profile["change_log"][-50:]
        profile_manager.save_profile(user_id, profile, allow_system_key=True)
    return moved



def run_pending_moves(user_id: str) -> list[str]:
    """Run an approved move list once (the night sequence, before the person's meeting): `<user>/ktsb_move_pending.json`
    = [{section, field, written, rejected_by?}]. The list file is renamed `*.done-<ts>.json` afterwards — kept, never
    deleted. [] when there is no list or no key (the list then waits for the next night)."""
    p = USERS_DIR / user_id / PENDING_MOVE
    if not p.exists() or not _key(user_id):
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("[KTSB_FILE] pending move list unreadable for %s: %s", user_id, type(e).__name__)
        return []
    moved = move_meeting_fields(user_id, rows if isinstance(rows, list) else [])
    p.rename(p.with_name(f"ktsb_move_pending.done-{now_local().strftime('%Y%m%d-%H%M%S')}.json"))
    log.info("[KTSB_FILE] %s: moved %d profile field(s) into KTSB's file", user_id, len(moved))
    return moved


def shown_guesses(user_id: str) -> list[dict]:
    """Guesses the meeting may read — never one the person rejected."""
    return [g for g in guesses(user_id) if g.get("status") != STATUS_REJECTED]
