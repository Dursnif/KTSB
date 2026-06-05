# /mnt/ai_disk/kaare/kaare_core/memory/short_term.py
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
from dataclasses import dataclass, field
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Deque, Dict, List, Optional, Tuple

from kaare_core.crypto import seal, unseal
from kaare_core.session_keys import get_session_key_sync
from kaare_core.users.store import get_public_key_b64


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(s: str, max_len: int) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


@dataclass
class DialogTurn:
    ts: str
    role: str   # "user" | "assistant" | "system"
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
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StateEntry:
    ts: str
    key: str
    value: Any
    source: str = "unknown"
    meta: Dict[str, Any] = field(default_factory=dict)


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

        self._dialog: Deque[DialogTurn] = deque(maxlen=self.dialog_max_turns)
        self._actions: Deque[ActionEvent] = deque(maxlen=self.actions_max)
        self._daily_summary: str = ""  # Loaded at startup from SQLite (yesterday's compressed context)

        # state: key -> StateEntry
        self._state: Dict[str, StateEntry] = {}
        # key insertion order for eviction
        self._state_order: Deque[str] = deque()

        # autosave
        self._autosave_path: Optional[str] = None
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
                DialogTurn(ts=_utc_ts(), role=str(role), text=_clip(text, self.dialog_max_text), user_id=user_id)
            )
        self._trigger_autosave()

    def record_action(
        self,
        action: str,
        entity_id: str,
        ok: bool,
        result: str = "",
        user_id: str = "global",
        meta: Optional[Dict[str, Any]] = None,
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
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Sett en state key. Dette er "siste sannhet vinner".
        Eviction: hvis antall keys > state_max_keys droppes eldste keys.
        """
        k = (key or "").strip()
        if not k:
            return

        with self._lock:
            entry = StateEntry(ts=_utc_ts(), key=k, value=value, source=source, meta=dict(meta or {}))
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
        meta: Optional[Dict[str, Any]] = None,
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

    def last_action(self, user_id: str = "global") -> Optional[ActionEvent]:
        with self._lock:
            for ev in reversed(list(self._actions)):
                if ev.user_id == user_id:
                    return ev
            return None

    def get_state(self, key: str) -> Optional[StateEntry]:
        with self._lock:
            return self._state.get(key)

    def snapshot_counts(self) -> Dict[str, int]:
        with self._lock:
            return {
                "dialog_turns": len(self._dialog),
                "actions": len(self._actions),
                "state_keys": len(self._state),
            }

    def get_dialog_pairs(self, user_id: str = "global", n: int = 4) -> list:
        """
        Returns last n (user_text, assistant_text) pairs from dialog for this user.
        System turns are excluded. Returns list of (user, assistant) tuples, oldest first.
        Unpaired turns (no following assistant response) are skipped.
        """
        with self._lock:
            relevant = [t for t in self._dialog
                        if t.user_id == user_id and t.role in ("user", "assistant")]
            pairs: list = []
            i = 0
            while i < len(relevant):
                if (relevant[i].role == "user"
                        and i + 1 < len(relevant)
                        and relevant[i + 1].role == "assistant"):
                    pairs.append((relevant[i].text, relevant[i + 1].text))
                    i += 2
                else:
                    i += 1
            return pairs[-n:] if pairs else []

    def build_prompt_context(self, user_text: str = "", user_id: str = "global",
                              include_dialog: bool = True) -> str:
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
            parts: List[str] = []

            # 0) Daily summary – compressed context from previous day
            if self._daily_summary:
                parts.append("Oppsummering fra i går:")
                parts.append(_clip(self._daily_summary, 800))

            # 1) Siste actions (filtrert på user_id)
            user_actions = [ev for ev in self._actions if ev.user_id == user_id]
            if user_actions:
                parts.append("Siste handlinger:")
                for ev in user_actions[-self.context_last_actions :]:
                    status = "OK" if ev.ok else "FEIL"
                    line = f"- [{ev.ts}] {status}: {ev.action} {ev.entity_id}"
                    if ev.result:
                        line += f" ({ev.result})"
                    parts.append(_clip(line, 500))

            # 2) State – velg keys
            state_lines: List[str] = []
            if self._state:
                keys_last = list(self._state_order)[-self.context_last_state_keys :]
                keys_hit: List[str] = []
                if user_text_l:
                    # veldig enkel matching: entity_id/alias-ord dukker ofte opp direkte
                    for k in self._state.keys():
                        if user_text_l and (k.lower() in user_text_l or user_text_l in k.lower()):
                            keys_hit.append(k)

                    # også: hvis user skriver "taklys verksted" og vi har entity:...
                    # (dette er fortsatt bare substring; ingen NLP)
                    for k in self._state.keys():
                        if user_text_l and any(tok in k.lower() for tok in user_text_l.split()[:6]):
                            keys_hit.append(k)

                # unik + prioriter: hits først, så siste keys
                seen = set()
                keys: List[str] = []
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
                parts.append("Tilstand (siste kjent):")
                parts.extend(state_lines)

            # 3) Siste dialog-turns (filtrert på user_id) — kun hvis include_dialog=True
            if include_dialog:
                user_dialog = [t for t in self._dialog if t.user_id == user_id]
                if user_dialog:
                    parts.append("Siste dialog (kort):")
                    for t in user_dialog[-self.context_last_dialog_turns :]:
                        parts.append(f"- [{t.ts}] {t.role}: {_clip(t.text, 300)}")

            out = "\n".join(parts).strip()

        if not out:
            return ""

        # hard cap
        if len(out) > self.context_max_chars:
            out = out[: self.context_max_chars - 1] + "…"
        return "### KORTTIDSMINNE\n" + out

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

    def to_dict(self) -> Dict[str, Any]:
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
                        "ts": a.ts, "action": a.action, "entity_id": a.entity_id,
                        "ok": a.ok, "result": a.result, "user_id": a.user_id, "meta": a.meta,
                    }
                    for a in self._actions
                ],
                "state": {
                    k: {"ts": e.ts, "key": e.key, "value": e.value, "source": e.source, "meta": e.meta}
                    for k, e in self._state.items()
                },
            }

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        """Gjenopprett STM frå ein serialisert dict. Eksisterande innhald vert erstatta."""
        with self._lock:
            self._dialog.clear()
            for t in data.get("dialog", []):
                self._dialog.append(
                    DialogTurn(ts=t["ts"], role=t["role"], text=t["text"], user_id=t.get("user_id", "global"))
                )

            self._actions.clear()
            for a in data.get("actions", []):
                self._actions.append(
                    ActionEvent(
                        ts=a["ts"], action=a["action"], entity_id=a["entity_id"],
                        ok=a["ok"], result=a.get("result", ""),
                        user_id=a.get("user_id", "global"), meta=a.get("meta", {}),
                    )
                )

            self._state.clear()
            self._state_order.clear()
            for k, e in data.get("state", {}).items():
                self._state[k] = StateEntry(
                    ts=e["ts"], key=e["key"], value=e["value"],
                    source=e.get("source", "unknown"), meta=e.get("meta", {}),
                )
                self._state_order.append(k)

            if "daily_summary" in data:
                self._daily_summary = data["daily_summary"]

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

            # Don't let a blank-restart STM destroy a richer existing snapshot
            enc_path = p.with_suffix(".enc")
            existing = enc_path if enc_path.exists() else (p if p.exists() else None)
            if existing and current_turns == 0:
                return True

            json_text = _json.dumps(self.to_dict(), ensure_ascii=False)

            pub_b64 = get_public_key_b64(self._user_id) if self._user_id != "global" else None
            if pub_b64:
                pub_bytes = base64.b64decode(pub_b64)
                encrypted = seal(json_text, pub_bytes)
                enc_path.write_text(encrypted, encoding="utf-8")
                if p.exists() and p.suffix == ".json":
                    p.unlink()
            else:
                p.write_text(json_text, encoding="utf-8")
            return True
        except Exception:
            return False

    def load_snapshot(self, path: str) -> bool:
        """Last STM frå disk. Returnerer True ved suksess, False viss fila ikkje finst.

        Checks for an encrypted .enc variant first. If found but no session key is
        available (server restart, user not yet logged in), returns False — empty STM.
        Falls back to plain .json for global/system users or pre-encryption files.
        """
        try:
            p = Path(path)
            enc_path = p.with_suffix(".enc")

            if enc_path.exists():
                private_key = get_session_key_sync(self._user_id)
                if private_key is None:
                    return False
                encrypted_blob = enc_path.read_text(encoding="utf-8")
                json_text = unseal(encrypted_blob, private_key)
                data = _json.loads(json_text)
                self.load_from_dict(data)
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
                self._users[user_id] = ShortTermMemory(user_id=user_id, **self._kwargs)
            return self._users[user_id]

    def set_entity_state(self, entity_id: str, state_value: Any,
                         source: str = "ha", meta: dict | None = None) -> None:
        with self._lock:
            for stm in self._users.values():
                stm.set_entity_state(entity_id, state_value, source, meta)

    def set_state(self, key: str, value: Any, source: str = "unknown",
                  meta: dict | None = None) -> None:
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

    def load_snapshots(self, directory: str) -> None:
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
            self._users[uid].load_snapshot(str(p / f"{uid}.json"))

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
            payload = {"version": 1, "saved_at": _utc_ts(),
                       "daily_summary": "", "dialog": udata["dialog"],
                       "actions": udata["actions"], "state": udata["state"]}
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
