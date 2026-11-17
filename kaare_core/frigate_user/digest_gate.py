"""Jang's digest skips Frigate lines for stationary objects the gate judged unchanged (run-queue J3b, P144 R138).

Argus indexes every log line (its store stays whole — I19, nothing dropped there). What Jang and the reflection read is
the rolling digest Argus writes (`state/argus/digest.txt`). A parked car or the waste bins re-detected again and again gave
Jang «Frigate: car på utekamera nedside øst» 37×. This module sits between Argus' line and the digest:

  - lines for persons and animals pass at once (the gate never judges them);
  - other Frigate lines are held until frigate.db has the gate's verdict for that event (`check.gate.verdict`, written by
    the Frigate user's pipe): «unchanged» → left out of the digest; anything else → released;
  - no verdict within HOLD_S after the event's last line (the gate did not judge it — not ticked, Frigate user off) →
    released as before; never held longer than HOLD_MAX_S;
  - frigate.db is read read-only (`mode=ro`), at most once per release round; a missing or unreadable db releases.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

from kaare_core.frigate_user import sealing, store
from kaare_core.storage import sqlite_files

log = logging.getLogger(__name__)

HOLD_S = 90.0  # after an event's last line: time for its end event to be judged
HOLD_MAX_S = 900.0  # a long stationary event's first line is never held longer than this
LIVING = ("person", *store.ANIMAL_LABELS)


def verdicts(event_ids: list[str], db_path: Path | None = None) -> dict[str, str | None]:
    """{event_id: gate verdict or None} from frigate.db, read-only. {} when the db cannot be read."""
    path = Path(db_path or store.DB_PATH)
    if not event_ids or not path.exists():
        return {}
    try:
        conn = sqlite_files.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return {}
    out: dict[str, str | None] = {}
    try:
        marks = ",".join("?" * len(event_ids))
        for eid, check in conn.execute(f'SELECT event_id, "check" FROM events WHERE event_id IN ({marks})',
                                       event_ids):
            try:
                gate = (json.loads(sealing.unseal(check) or "{}") or {}).get("gate") or {}  # sealed at rest (R0.6a)
            except (TypeError, ValueError):
                gate = {}
            out[eid] = gate.get("verdict") if isinstance(gate, dict) else None
    except sqlite3.Error as e:
        log.debug("digest gate: frigate.db unreadable (%s)", type(e).__name__)
        return {}
    finally:
        conn.close()
    return out


class DigestHold:
    """Holds Frigate digest lines per event until the gate's verdict is known."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path
        self._held: dict[str, dict] = {}  # event_id → {lines, first, last}
        self.skipped = 0

    def add(self, ev: dict, line: str, now: float | None = None) -> list[str]:
        """One frigate-mqtt line for the digest → the lines to append now ([line] or [] while held)."""
        eid, label = ev.get("event_id"), ev.get("label")
        if not eid or label in LIVING:
            return [line]
        now = time.monotonic() if now is None else now
        h = self._held.setdefault(eid, {"lines": [], "first": now, "last": now})
        h["lines"].append(line)
        h["last"] = now
        return []

    def release(self, now: float | None = None) -> list[str]:
        """Lines whose verdict is known (not «unchanged») or whose hold ran out, oldest first."""
        if not self._held:
            return []
        now = time.monotonic() if now is None else now
        known = verdicts(list(self._held), self.db_path)
        out: list[str] = []
        for eid in list(self._held):
            h = self._held[eid]
            v = known.get(eid)
            if v == "unchanged":
                self.skipped += len(h["lines"])
                del self._held[eid]
            elif v is not None or now - h["last"] >= HOLD_S or now - h["first"] >= HOLD_MAX_S:
                out.extend(h["lines"])
                del self._held[eid]
        return out

    def held(self) -> int:
        return sum(len(h["lines"]) for h in self._held.values())
