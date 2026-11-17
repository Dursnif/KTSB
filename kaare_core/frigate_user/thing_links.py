"""Is this the same thing? — stationary things seen by two cameras (P144 R139, run-queue J12).

The stationary gate (J3) gives every stationary object a key per camera (`camera|label|col,row`) and an empty
`thing_id`. The waste bins stand on «nedside øst» (near) and «nedside vest» (far): two keys, one thing. Code proposes a
pair when, over the last WINDOW_DAYS:
  - both keys have the same label and stood unchanged on at least MIN_STILL_DAYS different days,
  - and at least once both changed within CO_CHANGE_S of each other (moved together — bin day),
  - and (checked when the question is made) the daytime colour inside both boxes is alike.
Each such day is *world* evidence for the pair in KTSB's weight system, so the machine side never passes 0.5 (P149 D1).
The pair becomes ONE house question for admin in the G17 list — «Er dette det samme?» with one image from each camera.
«ja» links them (one `thing_id` for both keys, kept in weights.db as the human answer); «nei» keeps them apart; both are
kept. Nothing is written to the place register (P137) — that needs the main session first.
Read-only toward Frigate and frigate.db (the caller passes events); images come from Frigate through the existing admin
snapshot proxy.
"""

from __future__ import annotations

import hashlib
import io
import logging
import time

from PIL import Image

from kaare_core import weights
from kaare_core.config import local_from_ts
from kaare_core.frigate_user import store

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
MIN_STILL_DAYS = 3
CO_CHANGE_S = 1800.0
COLOUR_MAX_DIST = 40.0  # mean RGB distance inside the boxes (0–441); daytime frames only
ANSWERS = ("yes", "no", "skip")


def _gates(events: list[store.FrigateUserEvent]) -> dict[str, list[tuple[float, str, str]]]:
    """key → [(ts, verdict, event_id)] for every judged event."""
    out: dict[str, list[tuple[float, str, str]]] = {}
    for ev in events:
        g = (ev.check or {}).get("gate")
        if isinstance(g, dict) and g.get("key") and g.get("verdict") in ("unchanged", "changed"):
            out.setdefault(g["key"], []).append((ev.start_ts, g["verdict"], ev.event_id))
    return out


def _day(ts: float) -> str:
    return local_from_ts(ts).strftime("%Y-%m-%d")  # the household's day (rule 24), not the machine clock


def pair_id(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"{x}~{y}"


def candidates(events: list[store.FrigateUserEvent]) -> list[dict]:
    """[{pair, a, b, still_days: (na, nb), co_changes: [(ta, tb)], events: (eid_a, eid_b)}] — pairs code proposes."""
    gates = _gates(events)
    keys = sorted(gates)
    out = []
    for i, a in enumerate(keys):
        cam_a, label_a, _ = a.split("|")
        for b in keys[i + 1:]:
            cam_b, label_b, _ = b.split("|")
            if cam_a == cam_b or label_a != label_b:
                continue
            still_a = {_day(ts) for ts, v, _ in gates[a] if v == "unchanged"}
            still_b = {_day(ts) for ts, v, _ in gates[b] if v == "unchanged"}
            if len(still_a) < MIN_STILL_DAYS or len(still_b) < MIN_STILL_DAYS:
                continue
            ch_a = [ts for ts, v, _ in gates[a] if v == "changed"]
            ch_b = [ts for ts, v, _ in gates[b] if v == "changed"]
            co = [(ta, tb) for ta in ch_a for tb in ch_b if abs(ta - tb) <= CO_CHANGE_S]
            if not co:
                continue
            last_a = max(gates[a])[2]
            last_b = max(gates[b])[2]
            out.append({"pair": pair_id(a, b), "a": a, "b": b, "still_days": (len(still_a), len(still_b)),
                        "co_changes": co, "events": (last_a, last_b)})
    return out


def colour_alike(jpeg_a: bytes | None, box_a, jpeg_b: bytes | None, box_b) -> bool | None:
    """Mean RGB inside each box on a daytime frame; None when a frame is missing or IR (no colour to compare)."""
    try:
        means = []
        for jpeg, box in ((jpeg_a, box_a), (jpeg_b, box_b)):
            if not jpeg or not box:
                return None
            im = Image.open(io.BytesIO(jpeg)).convert("RGB")
            w, h = im.size
            x, y, bw, bh = box
            crop = im.crop((int(x * w), int(y * h), max(int((x + bw) * w), int(x * w) + 1),
                            max(int((y + bh) * h), int(y * h) + 1)))
            px = list(crop.getdata())
            m = [sum(p[c] for p in px) / len(px) for c in range(3)]
            if max(m) - min(m) < 3.0:  # grey = IR at night
                return None
            means.append(m)
        dist = sum((means[0][c] - means[1][c]) ** 2 for c in range(3)) ** 0.5
        return dist <= COLOUR_MAX_DIST
    except Exception as e:
        logger.debug("thing_links: colour check failed (%s)", type(e).__name__)
        return None


def record(cands: list[dict], colour: dict[str, bool | None] | None = None) -> int:
    """Each co-change day and an alike colour → world evidence for the pair (machine side capped at 0.5). Returns
    how many rows were new."""
    st = weights.get_store()
    n = 0
    for c in cands:
        subj = ("thing_pair", c["pair"])
        for ta, tb in c["co_changes"]:
            ptr = f"cochange:{c['pair']}:{int(min(ta, tb))}"
            if not st.has_pointer("evidence", ptr):
                weights.record(subj, source_class="world", source="co_change", context=_day(min(ta, tb)),
                               polarity=+1, pointer=ptr)
                n += 1
        if colour and colour.get(c["pair"]) is not None:
            ptr = f"colour:{c['pair']}"
            if not st.has_pointer("evidence", ptr):
                weights.record(subj, source_class="world", source="colour", context="colour",
                               polarity=+1 if colour[c["pair"]] else -1, pointer=ptr)
                n += 1
    weights.flush()
    return n


def thing_id(key: str) -> str | None:
    """The thing a key belongs to once a human said «ja» to a pair with it (stable id from the pair), else None."""
    st = weights.get_store()
    for kind, pair, scope in st.subjects("thing_pair"):
        if key in pair.split("~") and (st.get((kind, pair, scope)).yes or 0) > 0 and \
                (st.get((kind, pair, scope)).validated or 0) > 0.5:
            return "thing-" + hashlib.sha1(pair.encode()).hexdigest()[:10]
    return None


def questions_for_admin(events: list[store.FrigateUserEvent], is_admin: bool) -> list[dict]:
    """The pairs still open for a human: [{kind: thing, id: pair, events: (a, b), cameras: (a, b), label}]. Only admin
    (a house matter, #9). Code still records the evidence it sees."""
    if not is_admin:
        return []
    cands = candidates(events)
    record(cands)
    st = weights.get_store()
    out = []
    for c in cands:
        w = st.get(("thing_pair", c["pair"]))
        if w.learned_n or st.has_pointer("evidence", f"skip:thing:{c['pair']}"):
            continue
        out.append({"kind": "thing", "id": c["pair"], "events": list(c["events"]),
                    "cameras": [c["a"].split("|")[0], c["b"].split("|")[0]], "label": c["a"].split("|")[1],
                    "belief": round(w.belief, 3)})
    return out


def answer(admin: str, pair: str, verdict: str) -> dict:
    """Admin's answer to «Er dette det samme?». ja → linked (thing_id), nei → kept apart, skip → not shown again."""
    if verdict not in ANSWERS or "~" not in pair:
        return {"ok": False, "error": "bad_request"}
    subj = ("thing_pair", pair)
    if verdict == "skip":
        weights.record(subj, source_class="user", source="gui", context="skip", pointer=f"skip:thing:{pair}")
    else:
        weights.answer(subj, by_user=admin, verdict=verdict, verbatim=True, context=f"thing:{int(time.time())}",
                       pointer=f"thing:{pair}")
    return {"ok": True, "saved": weights.flush_ok()}  # N39: never report success when the save failed


def history(events: list[store.FrigateUserEvent], limit: int = 20) -> list[dict]:
    """J18b: pairs admin has answered or skipped, newest first — [{id, answer, label, cameras, events, ts}]; `events`
    = the newest stored event of each key in `events` (thing events, never a person's), None when gone."""
    st = weights.get_store()
    gates = _gates(events)
    out = []
    for kind, pair, scope in st.subjects("thing_pair"):
        rows = st.rows((kind, pair, scope))
        skip = [e for e in rows["evidence"] if str(e.get("pointer") or "") == f"skip:thing:{pair}"]
        last = max([*rows["answers"], *skip], key=lambda r: str(r.get("ts") or ""), default=None)
        if last is None or "~" not in pair:
            continue
        a, b = pair.split("~", 1)
        newest = [max(gates[k])[2] if gates.get(k) else None for k in (a, b)]
        out.append({"id": pair, "answer": last.get("verdict") if last in rows["answers"] else "skip",
                    "label": a.split("|")[1] if a.count("|") == 2 else "", "cameras": [a.split("|")[0], b.split("|")[0]],
                    "events": newest, "ts": last.get("ts")})
    out.sort(key=lambda r: str(r["ts"] or ""), reverse=True)
    return out[:limit]
