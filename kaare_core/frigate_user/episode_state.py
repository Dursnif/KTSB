"""Frame-by-frame episode memory held by code (P144-s25, Design today #1).

The 9B never has to remember. Code picks the best first frame (Frigate's largest person box), the 9B describes it in a
fixed schema → that is the episode state (persons P1…, per camera: in the image, place, posture, carried; doors; loose
things). Every other frame goes to the 9B as one image + the state written out by code; the 9B answers only what is
different, and code applies it. The state may be corrected. State is kept per camera (a place on one camera means
nothing on another); who is who across cameras is code's call (never more persons than Frigate tracked at once on one
camera). Presence is a Frigate fact too: a track covers a frame from its start to its last sighting (the timeline's
`gone` time — the event's own end lingers 6–8 s after the person left, s25 log). Frames where Frigate saw no movement
on that camera since its previous frame are skipped by code (the state stands).

Pure code: no model call, no I/O — `conclude` runs the calls and stores `EpisodeState.to_dict()` on the episode.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from kaare_core.tools.i18n import t as _t_i18n

# CLAUDE.md rule 8 — narrow, measured exception (P144-s25, the 2026-10-27 decision «measure English for the 9B's
# internal fields»): the frame-by-frame calls to the 9B (prompt, the code-written state it is shown, the corrections it
# gets, the language its answer is checked for) are English whatever the GUI language. This is model-to-model data
# only: what a person sees — the timeline's fixed words (posture, door state, «nobody», «gone»), the GUI, Kåre and the
# 27B's memory line — is in the household language; the 9B's own free words (place, clothing, carried) stay as written
# and reach a person only in the admin's technical episode detail. Measured 2026-10-31 (s25, 11 episodes × 3 rounds):
# same scores (copy-forward 4/36 vs 3/36, wrongly gone 6/183 vs 8/183, persons 30/33 both, bottle 3/3 both), language
# consistency 100 % vs 34 %, 25.6 vs 28.7 s per episode. NOT for the L3 or the closed look: those measured worse in
# English (look: prose instead of JSON in 5/27, 10 vs 2 invented animals, 2× slower; L3 headwear 15/12 vs 22/5).
MODEL_LANG = "en"
POSTURES = ("standing", "sitting", "lying", "bent", "unclear")
DOOR_STATES = ("open", "closed", "unclear")
AGES = ("adult", "child", "unclear")
GENDERS = ("man", "woman", "unclear")
START_TOLERANCE_S = 0.3
END_TOLERANCE_S = 0.05  # s25: ep 108 frame 9 lies 0.68 s after the timeline's «gone» — the person is gone
LINGER_FALLBACK_S = 6.0  # no timeline: Frigate keeps a track ~max_disappeared/fps (50 frames / 8 fps) after the last sight
LOOKS_FACE_MIN_PX = 100  # P144-s20's face limit (checks.FACE_ATTRS_MIN_PX): an expression needs a readable face
LOOKS_GESTURE_MIN_PX = 60  # P144-s20's head limit (checks.HEAD_ATTRS_MIN_PX): a gesture needs arms the 9B can make out
_MOVE_CLASSES = ("visible", "active", "entered_zone", "attribute")
_EMPTY = ("", "none", "null", "ingen", "ingenting", "nothing", "nichts", "keine", "-", "...", "…", "unclear", "uklart",
          "unknown", "ukjent", "unbekannt", "usynlig", "invisible", "unsichtbar",
          "unklar")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


# ── Frigate facts ─────────────────────────────────────────────────────────────


@dataclass
class Track:
    event_id: str
    camera: str
    label: str
    start: float
    last_seen: float
    marks: list[tuple[float, str, list | None]] = field(default_factory=list)  # (ts, timeline class, box)

    def covers(self, ts: float) -> bool:
        return self.start - START_TOLERANCE_S <= ts <= self.last_seen + END_TOLERANCE_S

    def box_at(self, ts: float) -> list | None:
        box = None
        for mts, _cls, b in self.marks:
            if mts <= ts + START_TOLERANCE_S and b:
                box = b
        return box or next((b for _t, _c, b in self.marks if b), None)


def track_from(event_id: str, camera: str, label: str, start: float, end: float | None,
               timeline: list[dict] | None) -> Track:
    """One Frigate event as a track. `timeline` = Frigate's /api/timeline rows for it (any order; may be empty)."""
    marks = []
    for row in timeline or []:
        try:
            box = (row.get("data") or {}).get("box")
            marks.append((float(row["timestamp"]), str(row.get("class_type") or ""),
                          list(box) if isinstance(box, (list, tuple)) and len(box) == 4 else None))
        except (KeyError, TypeError, ValueError):
            continue
    marks.sort(key=lambda m: m[0])
    gone = [m[0] for m in marks if m[1] == "gone"]
    end = end if end is not None else start
    last = max(gone) if gone else end - LINGER_FALLBACK_S
    return Track(event_id, camera, label, start, max(start, last), marks)


def present(tracks: list[Track], camera: str, ts: float, label: str | None = "person") -> list[Track]:
    return [k for k in tracks if k.camera == camera and (label is None or k.label == label) and k.covers(ts)]


def moved_between(tracks: list[Track], camera: str, t0: float, t1: float) -> bool:
    """Did Frigate see anything start, end or move on this camera after t0 up to t1? (Else the frame is skipped.)"""
    for k in tracks:
        if k.camera != camera:
            continue
        if t0 < k.start <= t1 or t0 < k.last_seen <= t1:
            return True
        if not (k.start <= t1 and k.last_seen >= t0):
            continue
        before = [m for m in k.marks if m[0] <= t0]
        if any(t0 < m[0] <= t1 and m[1] in _MOVE_CLASSES for m in k.marks):
            return True
        if not before or before[-1][1] != "stationary":
            return True
    return False


def best_first(frames: list[tuple[str, float]], tracks: list[Track]) -> int:
    """Index of the frame with Frigate's largest person box (any label only when no person is tracked); ties → the
    earliest; nothing tracked → 0."""
    best, best_area = 0, 0.0
    for i, (camera, ts) in enumerate(frames):
        for k in present(tracks, camera, ts, None):
            box = k.box_at(ts)
            if not box:
                continue
            area = box[2] * box[3] * (1.0 if k.label == "person" else 0.001)
            if area > best_area:
                best, best_area = i, area
    return best


def max_at_once(tracks: list[Track]) -> int:
    """Most persons Frigate tracked at the same time on ONE camera (one person seen by two cameras is still one)."""
    best = 1
    for camera in {k.camera for k in tracks}:
        pts = sorted(p for k in tracks if k.label == "person" and k.camera == camera
                     for p in ((k.start, 1), (k.last_seen, -1)))
        cur = 0
        for _t, d in pts:
            cur += d
            best = max(best, cur)
    return best


# ── the 9B's answer ───────────────────────────────────────────────────────────


def parse_answer(text: str) -> tuple[dict | None, str]:
    """(object, '') or (None, why). Accepts a ```json fence and text around the one object."""
    raw = _FENCE_RE.sub("", (text or "").strip()).strip()
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b <= a:
        return None, "no_json_object" if raw else "empty"
    try:
        data = json.loads(raw[a:b + 1])
    except json.JSONDecodeError as e:
        return None, f"json_error: {e.msg}"
    return (data, "") if isinstance(data, dict) else (None, "not_an_object")


def _text(value) -> str:
    if isinstance(value, (list, dict)):
        value = ", ".join(str(v) for v in (value if isinstance(value, list) else value.values()) if v)
    v = " ".join(str(value if value is not None else "").split())
    return "" if v.strip(".… ").lower() in _EMPTY else v[:80]


def text_value(value) -> str:
    """A model text value as code stores it (placeholders → '', squeezed, capped)."""
    return _text(value)


def enum_value(value, allowed: tuple[str, ...]) -> str:
    return _enum(value, allowed)


def _enum(value, allowed: tuple[str, ...]) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else "unclear"


def _yes_no(value) -> str:
    v = str(value).strip().lower()
    return "yes" if v in ("yes", "ja", "true") else "no" if v in ("no", "nei", "nein", "false") else "unclear"


def changed_ids(changes: list[dict]) -> set[str]:
    """Persons the 9B reported something newly carried for in one frame — the only change that proved sight on frames
    Frigate had no box for (s25: 102 #7, the bottle). A reworded place or a flipped posture did not: on empty frames
    the 9B rewords and flips while copying the state (offline over 81 such answers: carried-only kept 27/27 empty
    frames absent in (a), place+posture+carried only 22/27)."""
    return {c["id"] for c in changes if c.get("what") == "carried" and c.get("now") and c.get("id")}


def _clock(ts: float, tz=None) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S")


_LANG_WORDS = {
    "nb": re.compile(r"[æøå]|\b(og|med|ved|på|av|til|mot|foran|bak|inne|ute|står|sitter|jakke|genser|bukse|bukser|"
                     r"svart|mørk|mørke|hvit|grå|rød|blå|lys|bilen|døra|døren|trappa|veggen|stolen|vinduet|ingenting|"
                     r"uklart|ei|en|et)\b", re.IGNORECASE),
    "en": re.compile(r"\b(the|with|and|near|holding|standing|sitting|wearing|door|shirt|jacket|pants|trousers|black|"
                     r"white|dark|grey|gray|red|blue|car|bag|bottle|window|chair|left|right|front|of|on|at|by)\b",
                     re.IGNORECASE),
    "de": re.compile(r"[äöüß]|\b(und|mit|bei|vor|hinter|neben|steht|sitzt|jacke|hose|schwarz|weiß|dunkel|grau|rot|"
                     r"blau|auto|tasche|flasche|fenster|stuhl|tür|der|die|das|am|im)\b", re.IGNORECASE),
}


def answer_texts(data: dict | None) -> list[str]:
    """The free-text values of one answer (clothing, place, carried, doors, things, correction)."""
    out: list[str] = []
    if not isinstance(data, dict):
        return out
    for key in ("persons", "known", "new_persons"):
        for p in data.get(key) if isinstance(data.get(key), list) else []:
            if isinstance(p, dict):
                out += [t for t in (_text(p.get(k)) for k in ("clothing", "place", "carried", "expression", "gesture"))
                        if t]
    for key, fields in (("doors", ("door",)), ("things", ("what", "where"))):
        for x in data.get(key) if isinstance(data.get(key), list) else []:
            if isinstance(x, dict):
                out += [t for t in (_text(x.get(f)) for f in fields) if t]
    if _text(data.get("correction")):
        out.append(_text(data.get("correction")))
    return out


def text_language(values: list[str]) -> str | None:
    """Code's guess of the language most text values are written in (nb/en/de), None when too little to tell. Per
    value: the language whose marker words/letters it has (nb/de letters win over shared English-looking words)."""
    votes = {"nb": 0, "en": 0, "de": 0}
    for v in values:
        hits = {lang: bool(rx.search(v)) for lang, rx in _LANG_WORDS.items()}
        if hits["nb"] and not hits["de"]:
            votes["nb"] += 1
        elif hits["de"] and not hits["nb"]:
            votes["de"] += 1
        elif hits["en"]:
            votes["en"] += 1
    total = sum(votes.values())
    if total < 2:
        return None
    lang, n = max(votes.items(), key=lambda kv: kv[1])
    return lang if n * 2 > total else None


# ── the state ─────────────────────────────────────────────────────────────────


class EpisodeState:
    """The episode memory: persons (who), per camera (where/posture/carried; doors; things), one row per frame."""

    def __init__(self) -> None:
        self.persons: dict[str, dict] = {}
        self.cams: dict[str, dict] = {}
        self.rows: list[dict] = []
        self.corrections: list[dict] = []

    # storage
    def to_dict(self) -> dict:
        return {"persons": self.persons, "cams": self.cams, "rows": self.rows, "corrections": self.corrections}

    @classmethod
    def from_dict(cls, data: dict | None) -> EpisodeState:
        st = cls()
        data = data if isinstance(data, dict) else {}
        st.persons = dict(data.get("persons") or {})
        st.cams = dict(data.get("cams") or {})
        st.rows = list(data.get("rows") or [])
        st.corrections = list(data.get("corrections") or [])
        return st

    def cam(self, camera: str) -> dict:
        return self.cams.setdefault(camera, {"persons": {}, "doors": {}, "things": {}})

    def present_ids(self, camera: str) -> list[str]:
        return [pid for pid, p in self.cam(camera)["persons"].items() if p.get("in_image") == "yes"]

    # what the 9B is shown (code-written)
    def prompt_text(self, camera: str, lang: str) -> str:
        cs = self.cam(camera)
        if not self.persons:
            lines = [_t_i18n("frigate_frames_st_no_persons", lang)]
        else:
            lines = [_t_i18n("frigate_frames_st_persons", lang)]
        for pid, p in self.persons.items():
            who = ", ".join(x for x in (_t_i18n(f"frigate_frames_age_{p.get('age')}", lang) if p.get("age") != "unclear"
                                        else "",
                                        _t_i18n(f"frigate_frames_gender_{p.get('gender')}", lang)
                                        if p.get("gender") != "unclear" else "") if x)
            line = _t_i18n("frigate_frames_st_person", lang, id=pid, who=who or _t_i18n("frigate_frames_unclear", lang),
                           clothing=p.get("clothing") or _t_i18n("frigate_frames_unclear", lang))
            here = cs["persons"].get(pid)
            if here is None:
                line += " " + _t_i18n("frigate_frames_st_not_here_yet", lang)
            elif here.get("in_image") != "yes":
                line += " " + _t_i18n("frigate_frames_st_last_absent", lang, time=here.get("time", "?"))
            else:
                line += " " + _t_i18n(
                    "frigate_frames_st_last", lang, time=here.get("time", "?"),
                    place=here.get("place") or _t_i18n("frigate_frames_unclear", lang),
                    posture=here.get("posture") or "unclear",
                    carried=here.get("carried") or _t_i18n("frigate_frames_nothing", lang))
            lines.append(line)
        doors = "; ".join(f"{d}: {v}" for d, v in cs["doors"].items()) or _t_i18n("frigate_frames_none_known", lang)
        lines.append(_t_i18n("frigate_frames_st_doors", lang, doors=doors))
        # No «things» line: s25 measured the field as noise on this 9B (furniture/plants appearing and vanishing); the
        # prompts no longer ask for it (variant f). Things a person handles come through `carried`.
        return "\n".join(lines)

    # applying answers
    def _add_person(self, p: dict, camera: str, clock: str) -> str:
        pid = f"P{len(self.persons) + 1}"
        self.persons[pid] = {"age": _enum(p.get("age"), AGES), "gender": _enum(p.get("gender"), GENDERS),
                             "clothing": _text(p.get("clothing"))}
        self.cam(camera)["persons"][pid] = {"in_image": "yes", "place": _text(p.get("place")),
                                            "posture": _enum(p.get("posture"), POSTURES),
                                            "carried": _text(p.get("carried")), "time": clock}
        return pid

    def _doors_things(self, data: dict, camera: str, changes: list[dict], with_status: bool) -> None:
        cs = self.cam(camera)
        for x in data.get("doors") if isinstance(data.get("doors"), list) else []:
            if isinstance(x, dict) and _text(x.get("door")):
                name, state = _text(x["door"]), _enum(x.get("state"), DOOR_STATES)
                if cs["doors"].get(name) != state:
                    changes.append({"what": "door", "door": name, "state": state, "was": cs["doors"].get(name)})
                    cs["doors"][name] = state
        for x in data.get("things") if isinstance(data.get("things"), list) else []:
            if not isinstance(x, dict) or not _text(x.get("what")):
                continue
            what = _text(x["what"])
            if with_status and str(x.get("status") or "").strip().lower() == "gone":
                if what in cs["things"]:
                    cs["things"].pop(what)
                    changes.append({"what": "thing_gone", "thing": what})
            elif what not in cs["things"]:
                cs["things"][what] = _text(x.get("where"))
                changes.append({"what": "thing_new", "thing": what, "where": cs["things"][what]})

    def apply_full(self, data: dict, camera: str, ts: float, frame: int, tz=None, looks: bool = False) -> list[dict]:
        clock = _clock(ts, tz)
        changes: list[dict] = []
        seen: dict[str, dict] = {}
        for p in data.get("persons") if isinstance(data.get("persons"), list) else []:
            if isinstance(p, dict):
                pid = self._add_person(p, camera, clock)
                changes.append({"what": "person_first", "id": pid})
                seen[pid] = p
        self._doors_things(data, camera, changes, with_status=False)
        self._row(frame, camera, ts, "full", changes, tz=tz)
        if looks:
            self._looks(seen)
        return changes

    def _looks(self, seen: dict[str, dict]) -> None:
        """P144-s27b: expression and gesture as the 9B wrote them for this frame — on the row only, never in the state the
        9B is shown (nothing to copy forward). Placeholders («unclear», «none») are not kept."""
        out = {}
        for pid, p in seen.items():
            got = {k: v for k in ("expression", "gesture") if (v := _text(p.get(k)))}
            if got:
                out[pid] = got
        if out and self.rows:
            self.rows[-1]["looks"] = out

    def apply_change(self, data: dict, camera: str, ts: float, frame: int, max_persons: int, tz=None,
                     looks: bool = False) -> list[dict]:
        clock = _clock(ts, tz)
        cs = self.cam(camera)
        changes: list[dict] = []
        seen: dict[str, dict] = {}
        for k in data.get("known") if isinstance(data.get("known"), list) else []:
            if not isinstance(k, dict) or k.get("id") not in self.persons:
                continue
            pid = k["id"]
            cur = cs["persons"].get(pid)
            was_in = cur.get("in_image") if cur else None
            cur = cs["persons"].setdefault(pid, {"in_image": "unclear", "place": "", "posture": "unclear",
                                                 "carried": "", "time": clock})
            now_in = _yes_no(k.get("in_image"))
            cur.update(in_image=now_in, time=clock)
            if now_in != was_in:
                changes.append({"what": "presence", "id": pid, "in_image": now_in})
            if now_in != "yes":
                continue
            seen[pid] = k
            for f in ("place", "carried"):
                if f in k and _text(k.get(f)) != cur.get(f):
                    changes.append({"what": f, "id": pid, "now": _text(k.get(f)), "was": cur.get(f)})
                    cur[f] = _text(k.get(f))
            if "posture" in k and _enum(k.get("posture"), POSTURES) != cur.get("posture"):
                changes.append({"what": "posture", "id": pid, "now": _enum(k.get("posture"), POSTURES),
                                "was": cur.get("posture")})
                cur["posture"] = _enum(k.get("posture"), POSTURES)
        for p in data.get("new_persons") if isinstance(data.get("new_persons"), list) else []:
            if not isinstance(p, dict):
                continue
            if len(self.persons) >= max(1, max_persons):
                # Who is who is code's call: Frigate never tracked more persons at once on one camera → a known one.
                free = [pid for pid in self.persons if cs["persons"].get(pid, {}).get("in_image") != "yes"]
                pid = free[0] if free else next(iter(self.persons))
                was_in = cs["persons"].get(pid, {}).get("in_image")
                cs["persons"][pid] = {"in_image": "yes", "place": _text(p.get("place")),
                                      "posture": _enum(p.get("posture"), POSTURES), "carried": _text(p.get("carried")),
                                      "time": clock}
                if was_in != "yes":
                    changes.append({"what": "presence", "id": pid, "in_image": "yes"})
                changes.append({"what": "merged", "id": pid})
                seen.setdefault(pid, p)
                continue
            pid = self._add_person(p, camera, clock)
            changes.append({"what": "person_new", "id": pid})
            seen[pid] = p
        self._doors_things(data, camera, changes, with_status=True)
        fix = _text(data.get("correction"))
        if fix:
            self.corrections.append({"frame": frame, "time": clock, "text": fix})
            changes.append({"what": "correction", "text": fix})
        self._row(frame, camera, ts, "change", changes, tz=tz)
        if looks:
            self._looks(seen)
        return changes

    def set_absent(self, camera: str, ts: float, why: str, tz=None, ids: list[str] | None = None) -> list[str]:
        """Code overrides the 9B: these persons (all when `ids` is None) are not in this frame. Returns the ids it
        changed."""
        cs, clock, changed = self.cam(camera), _clock(ts, tz), []
        for pid, p in cs["persons"].items():
            if p.get("in_image") == "yes" and (ids is None or pid in ids):
                p.update(in_image="no", time=clock)
                changed.append(pid)
        if self.rows and self.rows[-1]["camera"] == camera and self.rows[-1]["ts"] == ts:
            row = self.rows[-1]
            row["override"] = why
            row["present"] = self.present_ids(camera)
            row["persons"] = {pid: v for pid, v in (row.get("persons") or {}).items() if pid not in changed}
            if row.get("looks"):  # s27b: what a person code says is not there showed is not kept (247 #7, «neutral»)
                row["looks"] = {pid: v for pid, v in row["looks"].items() if pid not in changed}
                if not row["looks"]:
                    row.pop("looks")
            row["changes"] = [c for c in row["changes"] if not (c["what"] == "presence" and c["id"] in changed)] + \
                [{"what": "presence", "id": pid, "in_image": "no", "by": why} for pid in changed]
        return changed

    def skip(self, camera: str, ts: float, frame: int, tz=None) -> None:
        """No Frigate movement on this camera since its previous frame: the state stands (code fact)."""
        clock = _clock(ts, tz)
        for pid in self.present_ids(camera):
            self.cam(camera)["persons"][pid]["time"] = clock
        self._row(frame, camera, ts, "skipped", [], tz=tz)

    def _row(self, frame: int, camera: str, ts: float, kind: str, changes: list[dict], tz=None) -> None:
        cs = self.cam(camera)["persons"]
        self.rows.append({"frame": frame, "camera": camera, "ts": ts, "time": _clock(ts, tz), "kind": kind,
                          "present": self.present_ids(camera), "changes": changes,
                          "persons": {pid: {k: cs[pid].get(k, "") for k in ("place", "posture", "carried")}
                                      for pid in self.present_ids(camera)}})


def limit_looks(row: dict, px: int | None) -> None:
    """P144-s27b: what a person this small can show (the s20 limits). `px` = the largest person box on that camera at
    that frame, in px of the frame the 9B saw; None = no box, the 9B's words stand."""
    if px is None or not row.get("looks"):
        return
    keep = {}
    for pid, v in row["looks"].items():
        v = dict(v)
        if px < LOOKS_FACE_MIN_PX:
            v.pop("expression", None)
        if px < LOOKS_GESTURE_MIN_PX:
            v.pop("gesture", None)
        if v:
            keep[pid] = v
    if keep:
        row["looks"] = keep
    else:
        row.pop("looks")


# ── what the 27B and the GUI get (code-written) ───────────────────────────────


def _person_then(row: dict, pid: str, lang: str) -> str:
    """The person as that frame's row froze it (never the end state — s25: frame 1 read «sitter» otherwise)."""
    p = (row.get("persons") or {}).get(pid) or {}
    parts = [_t_i18n(f"frigate_frames_posture_{p.get('posture') or 'unclear'}", lang)]
    if p.get("place"):
        parts.append(p["place"])
    if p.get("carried"):
        parts.append(_t_i18n("frigate_frames_tl_carries", lang, what=p["carried"]))
    look = (row.get("looks") or {}).get(pid) or {}
    if look.get("expression"):
        parts.append(_t_i18n("frigate_frames_tl_expression", lang, what=look["expression"]))
    if look.get("gesture"):
        parts.append(_t_i18n("frigate_frames_tl_gesture", lang, what=look["gesture"]))
    return f"{pid} " + ", ".join(parts)


def timeline_lines(st: EpisodeState, lang: str, camera_name=lambda c: c,
                   headers: list[tuple[float, str]] | None = None) -> list[str]:
    """One line per frame in time order, written by code from the per-frame answers and Frigate's presence. `headers`:
    (time, line) chapter headers from code, placed in time order (P144-s27)."""
    out = []
    pending = sorted(headers or [], key=lambda h: h[0])
    for row in sorted(st.rows, key=lambda r: r["ts"]):
        while pending and pending[0][0] <= row["ts"]:
            out.append(pending.pop(0)[1])
        cam = row["camera"]
        if row["kind"] == "skipped":
            continue
        bits = [_person_then(row, pid, lang) for pid in row["present"]] if row["present"] else \
            [_t_i18n("frigate_frames_tl_nobody", lang)]
        for c in row["changes"]:
            if c["what"] == "door":
                bits.append(_t_i18n("frigate_frames_tl_door", lang, door=c["door"],
                                    state=_t_i18n(f"frigate_frames_door_{c['state']}", lang)))
            elif c["what"] == "thing_new":
                bits.append(_t_i18n("frigate_frames_tl_thing_new", lang, thing=c["thing"], where=c.get("where") or "?"))
            elif c["what"] == "thing_gone":
                bits.append(_t_i18n("frigate_frames_tl_thing_gone", lang, thing=c["thing"]))
            elif c["what"] == "presence" and c["in_image"] == "no":
                bits.append(_t_i18n("frigate_frames_tl_gone_by" if c.get("by") else "frigate_frames_tl_gone", lang,
                                    id=c["id"]))
            elif c["what"] == "correction":
                bits.append(_t_i18n("frigate_frames_tl_correction", lang, text=c["text"]))
        out.append(f"{row['time']} {camera_name(cam)}: " + "; ".join(bits))
    out.extend(h[1] for h in pending)
    return out


def scene_fields(st: EpisodeState, lang: str) -> dict:
    """The scene fields the GUI and Kåre's episode lookup already read (persons, people, scene, at_door), from code."""
    rows = sorted(st.rows, key=lambda r: r["ts"])
    people = []
    for pid, p in st.persons.items():
        # everything the person carried at any frame (the bottle in frame 7 is not lost when frame 8 says nothing)
        carried = [r["persons"][pid]["carried"] for r in rows if (r.get("persons") or {}).get(pid, {}).get("carried")]
        people.append({"id": pid, "age": p.get("age", "unclear"), "gender": p.get("gender", "unclear"),
                       "clothing": p.get("clothing", ""), "carried": "; ".join(dict.fromkeys(carried))})
    doors = [f"{c['door']}: {_t_i18n('frigate_frames_door_' + c['state'], lang)} ({r['time']})"
             for r in rows for c in r["changes"] if c["what"] == "door"]
    # Kåre's episode lookup and the GUI read `scene` as one text: a short code summary of the timeline (one bit per
    # frame that changed something, repeats dropped), capped.
    bits: list[str] = []
    for r in rows:
        if r["kind"] in ("skipped", "unreadable"):
            continue
        now = "; ".join(_person_then(r, pid, lang) for pid in r["present"]) or _t_i18n("frigate_frames_tl_nobody", lang)
        if not bits or bits[-1].split(" ", 1)[1] != now:
            bits.append(f"{r['time']} {now}")
    summary = " → ".join(bits)
    return {"persons": len(st.persons), "people": people, "at_door": "; ".join(doors[:4]),
            "scene": summary[:400] if summary else _t_i18n("frigate_frames_scene_text", lang, n=len(st.persons),
                                                           frames=sum(1 for r in rows if r["kind"] != "skipped"))}
