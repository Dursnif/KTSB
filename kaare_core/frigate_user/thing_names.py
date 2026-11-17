"""«Hva er dette?» — admin names the still things KTSB sees, and says when Frigate misreads one (J20, P144 v2 #27/#28).

2026-11-06: one card «Hvem / Hva / Hvor?» for everything from places to the swan-shaped flower pot on the steps,
garden figures, bins, cars and trailers. Two kinds of «Hva» question, both admin only (a house matter):
  - a still, non-living thing with a stationary-gate key (J3) seen unchanged on ≥ MIN_DAYS days and ≥ MIN_SEEN times in
    WINDOW_DAYS — «what is it?», open text (#27: the vocabulary grows, no fixed list);
  - a misread: an animal label that stood still (Frigate's stationary flag) at the same spot on ≥ MIN_DAYS days — the swan
    pot as «bird». Its place comes from J19's boxes, so these grow from data stored after J19.
Key = `camera|label|col,row` on the gate's 10 × 10 grid. Answers are append-only rows in frigate.db (`thing_answers`):
named (a name) · not_label («ikke en <label>», optional name) · wrong («feil deteksjon / ikke en ting») · skip.
«not_label» and «wrong» are also a human answer on ("rule", "frigate_wrong|<key>") in KTSB's weight system: a **narrow**
confirmed-wrong rule — that camera, that label, that cell only. Code may hold such a detection back only when it did not
move and no other camera saw the same label within SUPPORT_S (a real animal still gets through); the pipe runs it in shadow
until the GUI switch «Hold tilbake bekreftet feil» (`questions.wrong_rules`) is on. Never a person; never a name of one.
"""

from __future__ import annotations

import logging
import re
import time

from kaare_core import weights
from kaare_core.config import local_from_ts
from kaare_core.frigate_user import settings, stationary_gate, store

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
MIN_DAYS = 3
MIN_SEEN = 5
MAX_OPEN = 3
SUPPORT_S = 30.0
STATUSES = ("named", "not_label", "wrong", "skip")
RULE_STATUSES = ("not_label", "wrong")
NAME_MAX = 60
_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}\|[a-z_]{1,40}\|\d,\d$")


def _day(ts: float) -> str:
    return local_from_ts(ts).strftime("%Y-%m-%d")  # the household's day (rule 24)


def _last_box(ev: store.FrigateUserEvent) -> list[float] | None:
    for b in reversed(ev.boxes or []):
        try:
            box = [float(v) for v in b[1:5]]
        except (TypeError, ValueError):
            continue
        if box[2] > 0 and box[3] > 0:
            return box
    return None


def key_of(camera: str, label: str, box) -> str | None:
    return stationary_gate.object_key(camera, label, box) if box else None


def _candidates(events: list[store.FrigateUserEvent], now: float) -> dict[str, dict]:
    """key → {camera, label, misread, days, seen} over the window."""
    out: dict[str, dict] = {}
    for ev in events:
        if ev.start_ts < now - WINDOW_DAYS * 86400 or ev.label == "person":
            continue
        gate = (ev.check or {}).get("gate") if isinstance(ev.check, dict) else None
        if ev.label in store.ANIMAL_LABELS:
            if not ev.stationary:
                continue
            key, misread = key_of(ev.camera, ev.label, _last_box(ev)), True
        elif isinstance(gate, dict) and gate.get("verdict") == "unchanged" and stationary_gate.applies(ev.label):
            key, misread = gate.get("key"), False
        else:
            continue
        if not key:
            continue
        c = out.setdefault(key, {"camera": ev.camera, "label": ev.label, "misread": misread, "days": set(), "seen": 0})
        c["days"].add(_day(ev.start_ts))
        c["seen"] += 1
    return out


def questions(events: list[store.FrigateUserEvent], answered: dict[str, dict], now: float | None = None) -> list[dict]:
    """The still things to ask admin about, most seen first, ≤ MAX_OPEN: [{kind: what, id, camera, label, cell, box,
    misread, days, seen}]."""
    now = time.time() if now is None else now
    out = []
    for key, c in _candidates(events, now).items():
        if key in answered or len(c["days"]) < MIN_DAYS or c["seen"] < MIN_SEEN:
            continue
        cell = key.rsplit("|", 1)[1]
        out.append({"kind": "what", "id": key, "camera": c["camera"], "label": c["label"], "cell": cell, "box": cell_box(cell),
                    "misread": c["misread"], "days": len(c["days"]), "seen": c["seen"]})
    out.sort(key=lambda q: (-q["days"], -q["seen"]))
    return out[:MAX_OPEN]


def cell_box(cell: str) -> list[float]:
    col, row = (int(v) for v in cell.split(","))
    g = stationary_gate.KEY_GRID
    return [col / g, row / g, 1 / g, 1 / g]


def answer(admin: str, key: str, status: str, name: str | None = None) -> dict:
    """Admin's answer (append-only). «not_label»/«wrong» also become the confirmed-wrong rule. {"ok": False} on bad
    input; {"ok": True, "saved": bool} otherwise — saved False when the rule row could not be written (N39)."""
    if not admin or status not in STATUSES or not _KEY.fullmatch(key or ""):
        return {"ok": False}
    name = (name or "").strip()[:NAME_MAX] or None
    if status == "named" and not name:
        return {"ok": False}
    if not store.add_thing_answer(key, status, name if status in ("named", "not_label") else None, admin):
        return {"ok": False}
    if status in RULE_STATUSES:
        weights.answer(_rule_subject(key), by_user=admin, verdict="yes", verbatim=True,
                       context=f"what:{int(time.time())}", pointer=f"what:{key}:{int(time.time() * 1000)}")
        return {"ok": True, "saved": weights.flush_ok()}
    return {"ok": True, "saved": True}


def _rule_subject(key: str) -> tuple[str, str]:
    return ("rule", f"frigate_wrong|{key}")  # P149's «rule» kind (#28: confirmed-wrong rules)


def answers() -> dict[str, dict]:
    return store.thing_answers()


def rule_for(camera: str, label: str, box) -> str | None:
    """The confirmed-wrong rule covering this exact camera + label + cell, or None (narrow on purpose)."""
    key = key_of(camera, label, box)
    if not key:
        return None
    w = weights.get(_rule_subject(key))
    return key if (w.yes or 0) > 0 and (w.validated or 0) > 0.5 else None


def hold(camera: str, label: str, box, path_data, start_ts: float,
         others: list[store.FrigateUserEvent]) -> dict | None:
    """None when no rule covers it; else {key, hold, why}: hold only when it did not move and no other camera saw the same
    label within SUPPORT_S."""
    key = rule_for(camera, label, box)
    if not key:
        return None
    span = stationary_gate.path_span(path_data) if path_data else None
    if span is not None and span >= settings.episode_min_movement():
        return {"key": key, "hold": False, "why": "moved"}
    if any(o.label == label and o.camera != camera and abs(o.start_ts - start_ts) <= SUPPORT_S for o in others):
        return {"key": key, "hold": False, "why": "seen_elsewhere"}
    return {"key": key, "hold": True, "why": "confirmed_wrong"}


def history(answered: dict[str, dict], limit: int = 20) -> list[dict]:
    rows = []
    for i, (key, a) in enumerate(answered.items()):
        camera, label, cell = key.split("|")
        rows.append((i, {"id": key, "camera": camera, "label": label, "cell": cell, "box": cell_box(cell),
                         "status": a.get("status"), "name": a.get("name"), "ts": a.get("ts")}))
    rows.sort(key=lambda r: (-(r[1]["ts"] or 0), -r[0]))
    return [r for _i, r in rows][:limit]
