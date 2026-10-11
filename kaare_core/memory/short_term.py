# kaare_core/memory/short_term.py
"""
Korttidsminne v1 (RAM) – GLOBALT, ingen persistens.

Mål:
- Holde siste dialoglinjer (kort)
- Holde tolket tilstand (state_cache)
- Holde siste handlinger og utfall (actions)
- Kun levere en KORT prompt-kontekstblokk slik at LLM ikke drukner i tokens

Bevisste avgrensninger:
- Ingen embeddings
- Ingen SQL
- Ingen bakgrunnsjobber
- Ingen magisk tolkning (vi lagrer bare det vi vet)
"""

from __future__ import annotations

import base64
import json as _json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from kaare_core.config import now_local
from kaare_core.crypto import seal, unseal
from kaare_core.session_keys import resolve_key_sync
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users.store import get_public_key_b64


def _utc_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_PENDING_SYSTEM_NOTES_MAXLEN = 50
_SESSION_GAP_MINUTES = 30  # gap after which a new request counts as a fresh "session"


def _clip(s: str, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


@dataclass
class DialogTurn:
    ts: str
    role: str  # "user" | "assistant" | "system"
    text: str
    user_id: str = "global"


@dataclass
class ActionEvent:
    ts: str
    action: str
    entity_id: str
    ok: bool
    result: str = ""
    user_id: str = "global"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateEntry:
    ts: str
    key: str
    value: Any
    source: str = "unknown"
    meta: dict[str, Any] = field(default_factory=dict)


class ShortTermMemory:
    """
    Globalt korttidsminne.

    Kapasitetsstyring:
    - dialog_max_turns: maks antall dialog-turns (ikke "meldinger", men turns)
    - actions_max: maks antall action events
    - state_max_keys: maks antall state keys (eldste keys droppes ved overskridelse)

    Tokenkontroll:
    - build_prompt_context() lager en kompakt tekstblokk under context_max_chars.
    """

    def __init__(
        self,
        dialog_max_turns: int = 40,
        dialog_max_text: int = 600,
        actions_max: int = 80,
        actions_max_text: int = 300,
        state_max_keys: int = 2000,
        context_max_chars: int = 3000,
        context_last_dialog_turns: int = 8,
        context_last_actions: int = 4,
        context_last_state_keys: int = 24,
        user_id: str = "global",
    ) -> None:
        self._user_id = user_id
        self.dialog_max_turns = int(dialog_max_turns)
        self.dialog_max_text = int(dialog_max_text)

        self.actions_max = int(actions_max)
        self.actions_max_text = int(actions_max_text)

        self.state_max_keys = int(state_max_keys)

        self.context_max_chars = int(context_max_chars)
        self.context_last_dialog_turns = int(context_last_dialog_turns)
        self.context_last_actions = int(context_last_actions)
        self.context_last_state_keys = int(context_last_state_keys)

        self._lock = RLock()

        self._dialog: deque[DialogTurn] = deque(maxlen=self.dialog_max_turns)
        self._actions: deque[ActionEvent] = deque(maxlen=self.actions_max)
        self._daily_summary: str = (
            ""  # Loaded at startup from SQLite (yesterday's compressed context)
        )

        # One-shot system notices (fallback state change, timer fired, tool summary) —
        # delivered once via get_pending_system_notes()/ack_pending_system_notes(), never
        # replayed as dialog history. Separate from _dialog's role="system" turns, which
        # get_dialog_pairs() never surfaces to the model — see P105.
        self._pending_system_notes: deque[tuple[str, str]] = deque(
            maxlen=_PENDING_SYSTEM_NOTES_MAXLEN
        )

        # Bookkeeping only for mark_activity()'s session-gap detection — never shown to
        # the model directly (unlike the "day_started"/"session_started" state keys it sets).
        self._last_activity_ts: datetime | None = None

        # state: key -> StateEntry
        self._state: dict[str, StateEntry] = {}
        # key insertion order for eviction
        self._state_order: deque[str] = deque()

        # True once load_snapshot() has successfully decrypted an .enc file this session.
        # save_snapshot() will not overwrite an existing .enc until this is set — prevents
        # a post-restart thin session (1–2 turns before login) from destroying rich history.
        self._enc_snapshot_loaded: bool = False

        # autosave
        self._autosave_path: str | None = None
        self._autosave_min_interval: float = 5.0
        self._autosave_last: float = 0.0

    # -------------------------
    # Autosave
    # -------------------------

    def configure_autosave(self, path: str, min_interval: float = 5.0) -> None:
        """Enable post-mutation autosave. Saves to disk at most once per min_interval seconds."""
        with self._lock:
            self._autosave_path = path
            self._autosave_min_interval = min_interval

    def _trigger_autosave(self) -> None:
        """Fire-and-forget background save if enough time has passed since last save."""
        if not self._autosave_path:
            return
        now = time.monotonic()
        if now - self._autosave_last < self._autosave_min_interval:
            return
        self._autosave_last = now
        path = self._autosave_path
        t = threading.Thread(target=self.save_snapshot, args=(path,), daemon=True)
        t.start()

    # -------------------------
    # Mutasjoner (skriving)
    # -------------------------

    def set_daily_summary(self, text: str) -> None:
        """Inject yesterday's compressed summary. Called once at API startup."""
        with self._lock:
            self._daily_summary = (text or "").strip()

    def add_dialog(self, role: str, text: str, user_id: str = "global") -> None:
        """Legg til en dialog-turn."""
        if not role:
            role = "system"
        with self._lock:
            self._dialog.append(
                DialogTurn(
                    ts=_utc_ts(),
                    role=str(role),
                    text=_clip(text, self.dialog_max_text),
                    user_id=user_id,
                )
            )
        self._trigger_autosave()

    def add_pending_system_note(self, text: str, user_id: str = "global") -> None:
        """One-shot system notice — fallback state change, timer fired, tool summary.
        Delivered exactly once via get_pending_system_notes(), then cleared by
        ack_pending_system_notes(), mirroring the mechanic_note/pending_note convention
        in router_generate.py. Not dialog history — never replayed on later turns."""
        if not text:
            return
        with self._lock:
            self._pending_system_notes.append((user_id, text))
        self._trigger_autosave()

    def get_pending_system_notes(self, user_id: str = "global") -> list[str]:
        """Undelivered one-shot system notices for this user. Non-destructive — call
        ack_pending_system_notes() once the result has been folded into the prompt."""
        with self._lock:
            return [text for uid, text in self._pending_system_notes if uid == user_id]

    def ack_pending_system_notes(self, user_id: str = "global") -> None:
        """Clear delivered system notices for this user."""
        with self._lock:
            self._pending_system_notes = deque(
                ((uid, text) for uid, text in self._pending_system_notes if uid != user_id),
                maxlen=_PENDING_SYSTEM_NOTES_MAXLEN,
            )

    def mark_activity(self) -> datetime | None:
        """
        Refresh "day_started" (calendar-day boundary) and "session_started" (session-gap
        boundary — a new request after _SESSION_GAP_MINUTES of inactivity counts as a new
        session) as ordinary `_state` entries.

        Unlike plain dialog turns, `_state` entries are always shown in full in every
        prompt — they don't compete for a spot in get_dialog_pairs()'s last-4-pairs replay
        window. Without this, a long same-day conversation gives the model no signal at
        all that older turns exist beyond what it currently sees, or that a session was
        resumed after a real gap — exactly what caused Kåre to misidentify his own
        conversation's opening turn (see BUG-log 2026-09-06 / P105).

        Returns the previous activity time (UTC) before this call overwrote it, or None on
        the very first activity. The caller uses it to tell the model how long ago the
        replayed turns happened (P105 Phase 6, BUG-log 2026-09-28).
        """
        now = datetime.now(UTC)
        local = now.astimezone(now_local().tzinfo)  # day and clock as the household sees them (N16)
        today_str = local.date().isoformat()

        with self._lock:
            day_entry = self._state.get("day_started")
            is_new_day = not day_entry or day_entry.value != today_str

            previous = self._last_activity_ts
            is_new_session = (
                previous is None or (now - previous).total_seconds() / 60 > _SESSION_GAP_MINUTES
            )
            self._last_activity_ts = now

        if is_new_day:
            self.set_state("day_started", today_str, source="system")
        if is_new_session:
            self.set_state("session_started", local.strftime("%H:%M"), source="system")
        return previous

    def record_action(
        self,
        action: str,
        entity_id: str,
        ok: bool,
        result: str = "",
        user_id: str = "global",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Logg en HA-handling (eller forsøkt handling) med utfall."""
        with self._lock:
            self._actions.append(
                ActionEvent(
                    ts=_utc_ts(),
                    action=_clip(action, 80),
                    entity_id=_clip(entity_id, 160),
                    ok=bool(ok),
                    result=_clip(result, self.actions_max_text),
                    user_id=user_id,
                    meta=dict(meta or {}),
                )
            )
        self._trigger_autosave()

    def set_state(
        self,
        key: str,
        value: Any,
        source: str = "unknown",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """
        Sett en state key. Dette er "siste sannhet vinner".
        Eviction: hvis antall keys > state_max_keys droppes eldste keys.
        """
        k = (key or "").strip()
        if not k:
            return

        with self._lock:
            entry = StateEntry(
                ts=_utc_ts(), key=k, value=value, source=source, meta=dict(meta or {})
            )
            is_new = k not in self._state
            self._state[k] = entry

            if is_new:
                self._state_order.append(k)

            # eviction hvis for mange keys
            while len(self._state_order) > self.state_max_keys:
                old = self._state_order.popleft()
                if old in self._state:
                    del self._state[old]

    def set_entity_state(
        self,
        entity_id: str,
        state_value: Any,
        source: str = "ha",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """
        Convenience: lagrer state for entity.
        key format: "entity:<entity_id>"
        """
        ent = (entity_id or "").strip()
        if not ent:
            return
        self.set_state(key=f"entity:{ent}", value=state_value, source=source, meta=meta)

    # -------------------------
    # Lesing / kontekstbygging
    # -------------------------

    def last_action(self, user_id: str = "global") -> ActionEvent | None:
        with self._lock:
            for ev in reversed(list(self._actions)):
                if ev.user_id == user_id:
                    return ev
            return None

    def get_state(self, key: str) -> StateEntry | None:
        with self._lock:
            return self._state.get(key)

    def snapshot_counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "dialog_turns": len(self._dialog),
                "actions": len(self._actions),
                "state_keys": len(self._state),
            }

    def get_dialog_pairs_window(
        self,
        user_id: str,
        *,
        token_budget: int,
        max_pairs: int,
        prior_session_pairs: int,
        session_gap_minutes: float,
        chars_per_token: float,
        now: datetime | None = None,
    ) -> tuple[list, list]:
        """
        Dynamic replay window (P105 Phase 7 A) instead of a fixed n.

        Walks the (user, assistant, user_ts) pairs newest-first. Pairs belong to the current
        session while consecutive turns (and the newest turn vs. now) are less than
        session_gap_minutes apart; they are taken until token_budget (estimated from
        characters) or max_pairs is reached. Once a gap is crossed, at most
        prior_session_pairs more are taken. Selection stops at the first pair that doesn't
        fit, so the replayed turns are always one contiguous tail of the conversation.

        Returns (included, omitted) — both oldest-first lists of 3-tuples. Pairing logic is
        get_dialog_pairs() unchanged (P105 Decision 1).
        """
        all_pairs = self.get_dialog_pairs(user_id=user_id, n=len(self._dialog) or 1, with_ts=True)
        now = now or datetime.now(UTC)
        included: list = []
        used_tokens = 0.0
        prior_taken = 0
        in_session = True
        anchor = now
        cut = len(all_pairs)
        for idx in range(len(all_pairs) - 1, -1, -1):
            u_text, a_text, u_ts = all_pairs[idx]
            try:
                ts = datetime.fromisoformat(u_ts)
                ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                ts = None
            if in_session and (
                ts is None or (anchor - ts).total_seconds() / 60 >= session_gap_minutes
            ):
                in_session = False
            cost = (len(u_text) + len(a_text)) / max(chars_per_token, 0.1)
            if len(included) >= max_pairs or used_tokens + cost > token_budget:
                break
            if not in_session:
                if prior_taken >= prior_session_pairs:
                    break
                prior_taken += 1
            included.append(all_pairs[idx])
            used_tokens += cost
            cut = idx
            if ts is not None:
                anchor = ts
        included.reverse()
        return included, all_pairs[:cut]

    def get_dialog_pairs(self, user_id: str = "global", n: int = 4, with_ts: bool = False) -> list:
        """
        Returns last n (user_text, assistant_text) pairs from dialog for this user.
        System turns are excluded. Returns list of (user, assistant) tuples, oldest first.
        Unpaired turns (no following assistant response) are skipped.

        with_ts=True returns (user_text, assistant_text, user_ts) instead, user_ts being the
        user turn's UTC ISO timestamp — so replayed turns can be dated (P105 Phase 6).
        The pairing logic itself is identical either way (P105 Decision 1).
        """
        with self._lock:
            relevant = [
                t for t in self._dialog if t.user_id == user_id and t.role in ("user", "assistant")
            ]
            pairs: list = []
            i = 0
            while i < len(relevant):
                if (
                    relevant[i].role == "user"
                    and i + 1 < len(relevant)
                    and relevant[i + 1].role == "assistant"
                ):
                    if with_ts:
                        pairs.append((relevant[i].text, relevant[i + 1].text, relevant[i].ts))
                    else:
                        pairs.append((relevant[i].text, relevant[i + 1].text))
                    i += 2
                else:
                    i += 1
            return pairs[-n:] if pairs else []

    def build_prompt_context(
        self,
        user_text: str = "",
        user_id: str = "global",
        include_dialog: bool = True,
        lang: str = "nb",
    ) -> str:
        """
        Bygger en kompakt kontekst-blokk. Dette er ment å prepends til prompt før LLM.

        Heuristikk (enkelt og robust):
        - inkluder siste N dialog-turns (kan slås av med include_dialog=False)
        - inkluder siste N actions (med ok/feil)
        - inkluder relevante state keys:
            * alltid siste context_last_state_keys keys (etter insertion order)
            * i tillegg: state keys som matcher ord i user_text (substring-match)
        - hard-cap på context_max_chars
        """
        user_text_l = (user_text or "").lower().strip()

        with self._lock:
            parts: list[str] = []

            # 0) Daily summary – compressed context from previous day
            if self._daily_summary:
                parts.append(_t_i18n("stm_daily_summary", lang))
                parts.append(_clip(self._daily_summary, 800))

            # 1) Siste actions (filtrert på user_id)
            user_actions = [ev for ev in self._actions if ev.user_id == user_id]
            if user_actions:
                parts.append(_t_i18n("stm_last_actions", lang))
                for ev in user_actions[-self.context_last_actions :]:
                    status = (
                        _t_i18n("stm_action_ok", lang)
                        if ev.ok
                        else _t_i18n("stm_action_failed", lang)
                    )
                    line = f"- [{ev.ts}] {status}: {ev.action} {ev.entity_id}"
                    if ev.result:
                        line += f" ({ev.result})"
                    parts.append(_clip(line, 500))

            # 2) State – velg keys
            state_lines: list[str] = []
            if self._state:
                keys_last = list(self._state_order)[-self.context_last_state_keys :]
                keys_hit: list[str] = []
                if user_text_l:
                    # veldig enkel matching: entity_id/alias-ord dukker ofte opp direkte
                    keys_hit.extend(
                        k
                        for k in self._state
                        if k.lower() in user_text_l or user_text_l in k.lower()
                    )

                    # også: hvis user skriver "taklys verksted" og vi har entity:...
                    # (dette er fortsatt bare substring; ingen NLP)
                    keys_hit.extend(
                        k
                        for k in self._state
                        if any(tok in k.lower() for tok in user_text_l.split()[:6])
                    )

                # unik + prioriter: hits først, så siste keys
                seen = set()
                keys: list[str] = []
                for k in keys_hit + keys_last:
                    if k in self._state and k not in seen:
                        seen.add(k)
                        keys.append(k)

                for k in keys[: max(8, self.context_last_state_keys)]:
                    e = self._state.get(k)
                    if not e:
                        continue
                    val = e.value
                    # gjør value kort og trygt som tekst
                    val_s = _clip(str(val), 200)
                    state_lines.append(f"- [{e.ts}] {e.key} = {val_s} (src={e.source})")

            if state_lines:
                parts.append(_t_i18n("stm_state_header", lang))
                parts.extend(state_lines)

            # 3) Siste dialog-turns (filtrert på user_id) — kun hvis include_dialog=True
            if include_dialog:
                user_dialog = [t for t in self._dialog if t.user_id == user_id]
                if user_dialog:
                    parts.append(_t_i18n("stm_dialog_header", lang))
                    parts.extend(
                        f"- [{turn.ts}] {turn.role}: {_clip(turn.text, 300)}"
                        for turn in user_dialog[-self.context_last_dialog_turns :]
                    )

            out = "\n".join(parts).strip()

        if not out:
            return ""

        # hard cap
        if len(out) > self.context_max_chars:
            out = out[: self.context_max_chars - 1] + "…"
        return _t_i18n("stm_header", lang) + "\n" + out

    # -------------------------
    # Kontroll / reset
    # -------------------------

    def clear(self) -> None:
        """Tøm alt (brukes evt. ved admin/reload)."""
        with self._lock:
            self._dialog.clear()
            self._actions.clear()
            self._state.clear()
            self._state_order.clear()
            self._daily_summary = ""

    # -------------------------
    # Serialisering / disk
    # -------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialiser heile STM til ein dict (JSON-kompatibel)."""
        with self._lock:
            return {
                "version": 1,
                "saved_at": _utc_ts(),
                "daily_summary": self._daily_summary,
                "dialog": [
                    {"ts": t.ts, "role": t.role, "text": t.text, "user_id": t.user_id}
                    for t in self._dialog
                ],
                "actions": [
                    {
                        "ts": a.ts,
                        "action": a.action,
                        "entity_id": a.entity_id,
                        "ok": a.ok,
                        "result": a.result,
                        "user_id": a.user_id,
                        "meta": a.meta,
                    }
                    for a in self._actions
                ],
                "state": {
                    k: {
                        "ts": e.ts,
                        "key": e.key,
                        "value": e.value,
                        "source": e.source,
                        "meta": e.meta,
                    }
                    for k, e in self._state.items()
                },
                "pending_system_notes": [
                    {"user_id": uid, "text": text} for uid, text in self._pending_system_notes
                ],
                "last_activity_ts": self._last_activity_ts.isoformat()
                if self._last_activity_ts
                else None,
            }

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Gjenopprett STM frå ein serialisert dict. Eksisterande innhald vert erstatta."""
        with self._lock:
            self._dialog.clear()
            for t in data.get("dialog", []):
                self._dialog.append(
                    DialogTurn(
                        ts=t["ts"],
                        role=t["role"],
                        text=t["text"],
                        user_id=t.get("user_id", "global"),
                    )
                )

            self._actions.clear()
            for a in data.get("actions", []):
                self._actions.append(
                    ActionEvent(
                        ts=a["ts"],
                        action=a["action"],
                        entity_id=a["entity_id"],
                        ok=a["ok"],
                        result=a.get("result", ""),
                        user_id=a.get("user_id", "global"),
                        meta=a.get("meta", {}),
                    )
                )

            self._state.clear()
            self._state_order.clear()
            for k, e in data.get("state", {}).items():
                self._state[k] = StateEntry(
                    ts=e["ts"],
                    key=e["key"],
                    value=e["value"],
                    source=e.get("source", "unknown"),
                    meta=e.get("meta", {}),
                )
                self._state_order.append(k)

            if "daily_summary" in data:
                self._daily_summary = data["daily_summary"]

            self._pending_system_notes.clear()
            for n in data.get("pending_system_notes", []):
                self._pending_system_notes.append((n.get("user_id", "global"), n.get("text", "")))

            _last_ts = data.get("last_activity_ts")
            self._last_activity_ts = datetime.fromisoformat(_last_ts) if _last_ts else None

    def save_snapshot(self, path: str) -> bool:
        """Lagre STM til disk. Returnerer True ved suksess.

        If the user has a keypair, encrypts the JSON blob with SealedBox (public key only)
        and writes to a .enc file. Removes the old .json file if present.
        Global/system users (no keypair) are saved as plain .json.

        Guard: if the current STM has fewer dialog turns than what's on disk (e.g. after a
        blank restart), the existing file is kept. This prevents overwriting a rich snapshot
        with an empty one before the user has had a chance to log in and reload it.
        """
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            current_turns = self.snapshot_counts().get("dialog_turns", 0)

            # Don't let a blank-restart STM destroy a richer existing snapshot.
            # Guard 1: never overwrite an existing .enc unless it was successfully loaded
            # this session — catches the case where 1+ turns accumulate before login.
            enc_path = p.with_suffix(".enc")
            if enc_path.exists() and not self._enc_snapshot_loaded:
                return True
            existing = enc_path if enc_path.exists() else (p if p.exists() else None)
            if existing and current_turns == 0:
                return True

            json_text = _json.dumps(self.to_dict(), ensure_ascii=False)

            pub_b64 = get_public_key_b64(self._user_id) if self._user_id != "global" else None
            if pub_b64:
                pub_bytes = base64.b64decode(pub_b64)
                encrypted = seal(json_text, pub_bytes)
                enc_path.write_text(encrypted, encoding="utf-8")
                # This session wrote the file from its own full state, so later saves may
                # overwrite it. Before (DT03 R7), only load_snapshot() set the flag: a brand-new
                # user's first save created the .enc and Guard 1 then dropped every later save
                # silently until the next restart + login — the user lost the rest of the session.
                self._enc_snapshot_loaded = True
                if p.exists() and p.suffix == ".json":
                    p.unlink()
            else:
                p.write_text(json_text, encoding="utf-8")
            return True
        except Exception:
            return False

    def load_snapshot(self, path: str, allow_system_key: bool = False) -> bool:
        """Last STM frå disk. Returnerer True ved suksess, False viss fila ikkje finst.

        Checks for an encrypted .enc variant first. If found but no session key is
        available (server restart, user not yet logged in), returns False — empty STM,
        unless allow_system_key=True (P87 — restart-recovery calls this with the system
        key so a user's dialog history survives a kaare.service restart without them
        needing to be logged in at that moment).
        Falls back to plain .json for global/system users or pre-encryption files.
        """
        try:
            p = Path(path)
            enc_path = p.with_suffix(".enc")

            if enc_path.exists():
                private_key = resolve_key_sync(self._user_id, allow_system_key=allow_system_key)
                if private_key is None:
                    return False
                encrypted_blob = enc_path.read_text(encoding="utf-8")
                json_text = unseal(encrypted_blob, private_key)
                data = _json.loads(json_text)
                self.load_from_dict(data)
                self._enc_snapshot_loaded = True
                return True
            elif p.exists():
                data = _json.loads(p.read_text(encoding="utf-8"))
                self.load_from_dict(data)
                return True
            return False
        except Exception:
            return False


class STMRegistry:
    """Holds one ShortTermMemory per user_id (lazy create). Entity state is broadcast to all users."""

    def __init__(self, stm_kwargs: dict | None = None) -> None:
        self._kwargs = stm_kwargs or {}
        self._users: dict[str, ShortTermMemory] = {}
        self._lock = RLock()

    def get(self, user_id: str) -> ShortTermMemory:
        with self._lock:
            if user_id not in self._users:
                stm = ShortTermMemory(user_id=user_id, **self._kwargs)
                stm.configure_autosave(f"/kaare/state/stm_users/{user_id}.json")
                self._users[user_id] = stm
            return self._users[user_id]

    def set_entity_state(
        self, entity_id: str, state_value: Any, source: str = "ha", meta: dict | None = None
    ) -> None:
        with self._lock:
            for stm in self._users.values():
                stm.set_entity_state(entity_id, state_value, source, meta)

    def set_state(
        self, key: str, value: Any, source: str = "unknown", meta: dict | None = None
    ) -> None:
        with self._lock:
            for stm in self._users.values():
                stm.set_state(key, value, source, meta)

    def snapshot_all(self, directory: str) -> None:
        p = Path(directory)
        p.mkdir(parents=True, exist_ok=True)
        with self._lock:
            users_copy = dict(self._users)
        for uid, stm in users_copy.items():
            stm.save_snapshot(str(p / f"{uid}.json"))

    def load_snapshots(self, directory: str, allow_system_key: bool = False) -> None:
        p = Path(directory)
        if not p.exists():
            return
        # Collect unique stems from both .json and .enc files
        stems: set[str] = set()
        for f in p.glob("*.json"):
            stems.add(f.stem)
        for f in p.glob("*.enc"):
            stems.add(f.stem)
        for uid in stems:
            with self._lock:
                if uid not in self._users:
                    self._users[uid] = ShortTermMemory(user_id=uid, **self._kwargs)
            self._users[uid].load_snapshot(
                str(p / f"{uid}.json"), allow_system_key=allow_system_key
            )

    def migrate_legacy_snapshot(self, old_path: str, new_dir: str) -> bool:
        """Read old stm_snapshot.json and split into per-user files."""
        op = Path(old_path)
        if not op.exists():
            return False
        try:
            data = _json.loads(op.read_text(encoding="utf-8"))
        except Exception:
            return False
        users: dict[str, dict] = {}
        for t in data.get("dialog", []):
            uid = t.get("user_id", "global")
            users.setdefault(uid, {"dialog": [], "actions": [], "state": {}})
            users[uid]["dialog"].append(t)
        for a in data.get("actions", []):
            uid = a.get("user_id", "global")
            users.setdefault(uid, {"dialog": [], "actions": [], "state": {}})
            users[uid]["actions"].append(a)
        global_state = data.get("state", {})
        for uid in users:
            users[uid]["state"] = global_state
        p = Path(new_dir)
        p.mkdir(parents=True, exist_ok=True)
        for uid, udata in users.items():
            payload = {
                "version": 1,
                "saved_at": _utc_ts(),
                "daily_summary": "",
                "dialog": udata["dialog"],
                "actions": udata["actions"],
                "state": udata["state"],
            }
            (p / f"{uid}.json").write_text(
                _json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        op.rename(str(op) + ".migrated")
        return True

    def all_user_ids(self) -> list[str]:
        with self._lock:
            return list(self._users.keys())

    def snapshot_counts(self) -> dict:
        with self._lock:
            return {uid: stm.snapshot_counts() for uid, stm in self._users.items()}
