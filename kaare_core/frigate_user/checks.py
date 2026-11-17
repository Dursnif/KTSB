"""The code layers of the Frigate user's claim check (P144-s06a; P144 Research «hybrid claim check»).

Frigate's face name is a claim; KTSB checks it itself. The LLM only perceives (L3 fills fixed fields from ONE image,
without the claim); code compares and decides:
  L0 quality  — face size in px, IR/night image, Frigate's score → too poor to judge = «unsure», no LLM call;
  L1 context  — what KTSB can see: the same person on another camera at the same time in a different episode is a
                clear impossibility (strong); the household being away is not (they may be coming home);
  L3 attributes — the image's fields vs the person's attribute profile (faces.attribute_profile); «unknown» never
                contradicts;
  decision    — one strong contradiction never rejects alone (P144-s06c): it gives «unverified» with a `doubt`;
                rejected needs two layers in the event, or two observations (events) of the same person in the
                same episode that agree on the contradiction; two independent yes → confirmed; a check ran
                without contradiction → unverified; no check possible → none.
Pure functions, no I/O — the pipe gathers the facts.
"""

import json
import re
from dataclasses import asdict, dataclass, field

from PIL import Image, ImageStat

# A frame whose mean HSV saturation (0–255) is this low has no colour: an IR/night image.
_IR_MAX_SATURATION = 8.0

L3_FIELDS = {
    # Same set as faces.ATTRIBUTES["beard"] (2026-10-26); "yes" = some beard, type not seen.
    "beard": ("no", "moustache", "circle_beard", "full", "yes"),
    "glasses": ("yes", "no"),
    "age_band": ("child", "teen", "adult", "senior"),
    "headwear": ("yes", "no"),
}
_AGE_ORDER = ("child", "teen", "adult", "senior")
_BEARD_TYPES = ("moustache", "circle_beard", "full")
_BEARD_STRONG_VS_NONE = ("circle_beard", "full")  # can't be missed on a visible face, can't grow overnight
UNKNOWN = "unknown"
# Size rule (P144-s20 item 1): below these person heights (px in the image L3 saw) the fields are set to unknown by code.
# A face is ~1/7 of a standing person: at 100 px it is ~14 px high (glasses/beard not readable below that); the head
# shape (cap, child vs adult) goes below 60 px (head ~8 px). Measured: the 9B's own beard answer goes unknown at
# ≤ 60 px, its glasses/headwear/age answers never do (s20 m1); s18: the scene misses persons < 60 px 3/5.
FACE_ATTRS_MIN_PX = 100
HEAD_ATTRS_MIN_PX = 60
FACE_ATTRS = ("beard", "glasses")
HEAD_ATTRS = ("headwear", "age_band")


@dataclass
class Vote:
    layer: str  # l0 | l1 | l3
    verdict: str  # pass | unsure | yes | no | info
    strong: bool
    reason: str
    # Strong contradictions in a form two observations can be compared by: {layer, field, seen, profile}.
    contradictions: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Seen:
    """Another stored event of the same claimed person (for L1)."""

    camera: str
    start_ts: float
    end_ts: float | None
    episode_closed: bool


# ── L0 ─────────────────────────────────────────────────────────────────────────


def face_px(event_data: dict, image_size: tuple[int, int]) -> int | None:
    """Area in px of the largest face Frigate found on the snapshot (`data.attributes[].box` = relative
    [x, y, w, h], Frigate 0.18), or None without a face box."""
    attrs = event_data.get("attributes") if isinstance(event_data, dict) else None
    if not isinstance(attrs, list):
        return None
    width, height = image_size
    best = None
    for a in attrs:
        box = a.get("box") if isinstance(a, dict) and a.get("label") == "face" else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            continue
        px = round(box[2] * width) * round(box[3] * height)
        best = px if best is None else max(best, px)
    return best


def person_px(event_data: dict, image_size: tuple[int, int]) -> int | None:
    """Height in px of Frigate's object box (`data.box` = relative [x, y, w, h], Frigate 0.18) in the image L3 saw, or
    None without a box. Relative × the frame's own height, so a 1080 p detect stream gives bigger numbers than 360 p —
    the information a frame carries about a person is its px height (P144-s20 item 1)."""
    box = event_data.get("box") if isinstance(event_data, dict) else None
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return None
    return round(box[3] * image_size[1])


def limit_by_size(attrs: dict, px: int | None) -> tuple[dict, list[str]]:
    """(attributes, fields set to unknown) — what a person this small can show (P144-s20 item 1, measured on the 9B:
    it fills glasses/headwear/age even at 25 px, so the limit is code's). None = no box: the 9B's answer stands."""
    if px is None or px >= FACE_ATTRS_MIN_PX:
        return attrs, []
    fields = FACE_ATTRS if px >= HEAD_ATTRS_MIN_PX else (*FACE_ATTRS, *HEAD_ATTRS)
    out, limited = dict(attrs), []
    for name in fields:
        if out.get(name, UNKNOWN) != UNKNOWN:
            limited.append(name)
        out[name] = UNKNOWN
    return out, limited


# What the blind look may call each Frigate animal label (the look answers in free text, nb/en/de — P144-s28 item 5).
_ANIMAL_WORDS = {
    "deer": ("deer", "hjort", "rådyr", "radyr", "reh", "hirsch"), "dog": ("dog", "hund", "valp", "puppy"),
    "cat": ("cat", "katt", "katze"), "bird": ("bird", "fugl", "vogel", "due", "pigeon", "taube", "måke", "seagull"),
    "horse": ("horse", "hest", "pferd", "pony"), "sheep": ("sheep", "sau", "schaf", "lam", "lamb"),
    "cow": ("cow", "ku", "kuh", "storfe", "rind"), "bear": ("bear", "bjørn", "bär"), "fox": ("fox", "rev", "fuchs"),
    "raccoon": ("raccoon", "vaskebjørn", "waschbär"), "squirrel": ("squirrel", "ekorn", "eichhörnchen"),
    "rabbit": ("rabbit", "hare", "kanin", "bunny", "kaninchen"), "skunk": ("skunk", "stinkdyr", "stinktier"),
    "moose": ("moose", "elg", "elch"), "elk": ("elk", "elg", "elch", "wapiti"), "goat": ("goat", "geit", "ziege"),
    "badger": ("badger", "grevling", "dachs"), "hedgehog": ("hedgehog", "pinnsvin", "igel"),
}
_SOME_ANIMAL = ("unclear", "uklart", "unklar", "unknown", "ukjent", "unbekannt", "animal", "dyr", "tier")


def same_animal_kind(seen: str, frigate_labels) -> bool:
    """Does Frigate's animal label support what the look saw (P144-s28 item 5: «horse» beside a Frigate «dog» does not)?
    A word that says only «some animal» is supported by any Frigate animal; a word code cannot place is not."""
    labels = list(frigate_labels or [])
    if not labels:
        return False
    words = set(re.findall(r"[^\W\d_]+", str(seen or "").lower()))
    if any(label in words or words & set(_ANIMAL_WORDS.get(label, ())) for label in labels):
        return True
    return bool(words) and words <= {*_SOME_ANIMAL, "an", "a", "et", "ein", "eine"}


def is_ir(img: Image.Image) -> bool:
    """True for an IR/night frame: no colour — on average every pixel's channels are nearly equal."""
    if img.mode not in ("RGB", "RGBA"):
        return True
    small = img.convert("RGB").resize((64, 48))
    hsv_saturation = small.convert("HSV").getchannel("S")
    return ImageStat.Stat(hsv_saturation).mean[0] <= _IR_MAX_SATURATION


def l0_quality(*, face_px: int | None, ir: bool, claim_score: float | None, min_face_px: int,
               min_claim_score: float) -> Vote:
    problems = []
    if face_px is None:
        problems.append("no_face_box")
    elif face_px < min_face_px:
        problems.append(f"face_px={face_px}<{min_face_px}")
    if ir:
        problems.append("ir_night")
    if claim_score is not None and claim_score < min_claim_score:
        problems.append(f"claim_score={claim_score:.2f}<{min_claim_score:.2f}")
    if problems:
        return Vote("l0", "unsure", False, ", ".join(problems))
    return Vote("l0", "pass", False, f"face_px={face_px}")


# ── L1 ─────────────────────────────────────────────────────────────────────────


def l1_context(*, camera: str, start_ts: float, end_ts: float | None, seen: list[Seen], household_member: bool,
               household_away: bool) -> list[Vote]:
    votes = []
    end = end_ts if end_ts is not None else start_ts
    for s in seen:
        s_end = s.end_ts if s.end_ts is not None else s.start_ts
        overlaps = s.start_ts <= end and s_end >= start_ts
        if overlaps and s.camera != camera and s.episode_closed:
            votes.append(Vote("l1", "no", True, f"seen_elsewhere_same_time camera={s.camera}",
                              [{"layer": "l1", "field": "seen_elsewhere", "seen": s.camera, "profile": None}]))
            break
    if household_member and household_away:
        # Not a contradiction: a member may be coming home. Recorded so the 27B step sees it.
        votes.append(Vote("l1", "info", False, "household_away_member_claimed"))
    return votes


# ── L3 ─────────────────────────────────────────────────────────────────────────

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _norm(value, allowed: tuple[str, ...]) -> str:
    if value is True:
        value = "yes"
    elif value is False:
        value = "no"
    v = value.strip().lower() if isinstance(value, str) else ""
    return v if v in allowed else UNKNOWN


def parse_l3(text: str) -> tuple[dict | None, str]:
    """(fields, error). Fields: persons (int or None), beard/glasses/age_band/headwear (value or «unknown»),
    description, analysis. Accepts a fenced JSON block or JSON with text around it."""
    if not isinstance(text, str) or not text.strip():
        return None, "empty"
    m = _JSON_RE.search(text)
    if not m:
        return None, "no_json_object"
    try:
        data = json.loads(m.group(0))
    except ValueError as e:
        return None, f"json: {e.msg}"
    if not isinstance(data, dict):
        return None, "not_an_object"
    persons = data.get("persons")
    out: dict = {"persons": persons if isinstance(persons, int) and not isinstance(persons, bool) else None}
    for name, allowed in L3_FIELDS.items():
        out[name] = _norm(data.get(name), allowed)
    for name in ("description", "analysis"):
        v = data.get(name)
        out[name] = v.strip() if isinstance(v, str) else ""
    return out, ""


def l3_compare(attrs: dict, profile: dict) -> Vote:
    """The image's fields vs the claimed person's profile. Strong: no beard ↔ circle beard / full beard; glasses seen on a «no glasses»
    profile; age bands two or more apart. Weak (not a contradiction): glasses missing on a «glasses» profile, an
    adjacent age band, headwear. «unknown» on either side never contradicts."""
    if attrs.get("persons") != 1:
        return Vote("l3", "unsure", False, f"persons={attrs.get('persons')}")
    strong, weak, agree = [], [], []
    contradictions: list[dict] = []

    def _strong(name: str, seen: str, prof: str, text: str) -> None:
        strong.append(text)
        contradictions.append({"layer": "l3", "field": name, "seen": seen, "profile": prof})

    beard, p_beard = attrs.get("beard", UNKNOWN), profile.get("beard", UNKNOWN)
    if UNKNOWN not in (beard, p_beard):
        verdict = _beard_verdict(beard, p_beard)
        if verdict == "strong":
            _strong("beard", beard, p_beard, f"beard {beard}≠{p_beard}")
        else:
            (agree if verdict == "agree" else weak).append("beard" if verdict == "agree" else f"beard {beard}≠{p_beard}")

    glasses, p_glasses = attrs.get("glasses", UNKNOWN), profile.get("glasses", UNKNOWN)
    if UNKNOWN not in (glasses, p_glasses) and p_glasses != "sometimes":
        if glasses == p_glasses:
            agree.append("glasses")
        elif p_glasses == "no":
            _strong("glasses", glasses, p_glasses, "glasses yes≠no")
        else:
            weak.append("glasses no≠yes")

    age, p_age = attrs.get("age_band", UNKNOWN), profile.get("age_band", UNKNOWN)
    if age in _AGE_ORDER and p_age in _AGE_ORDER:
        gap = abs(_AGE_ORDER.index(age) - _AGE_ORDER.index(p_age))
        if gap == 0:
            agree.append("age_band")
        elif gap >= 2:
            _strong("age_band", age, p_age, f"age_band {age}≠{p_age}")
        else:
            weak.append(f"age_band {age}≈{p_age}")

    if strong:
        return Vote("l3", "no", True, "; ".join(strong), contradictions)
    if len(agree) >= 2 and not weak:
        return Vote("l3", "yes", False, "match: " + ", ".join(agree))
    return Vote("l3", "unsure", False, "; ".join(weak) or ("match: " + ", ".join(agree) if agree else "no_known_field"))


def _beard_verdict(seen: str, profile: str) -> str:
    """agree / strong / weak. Strong only no ↔ circle_beard/full; no ↔ moustache and type ↔ type are weak (a small
    moustache can be missed, beards get trimmed); legacy "yes" matches every beard type and is weak against "no"."""
    if seen == profile:
        return "agree"
    pair = {seen, profile}
    if "no" in pair:
        return "strong" if pair & set(_BEARD_STRONG_VS_NONE) else "weak"
    if "yes" in pair:
        return "agree"  # «some beard» vs a beard type
    return "weak"  # two different beard types


# ── decision ───────────────────────────────────────────────────────────────────


def strong_contradictions(votes: list[Vote]) -> list[dict]:
    out = []
    for v in votes:
        if v.verdict == "no" and v.strong:
            out.extend(v.contradictions or [{"layer": v.layer, "field": v.reason, "seen": None, "profile": None}])
    return out


def _agrees(mine: dict, other: dict) -> bool:
    """Two observations agree on a strong contradiction: the same field seen the same way — or a different layer
    adding its own strong contradiction."""
    if mine.get("layer") != other.get("layer"):
        return True
    if mine.get("field") != other.get("field"):
        return False
    return mine.get("layer") == "l1" or mine.get("seen") == other.get("seen")


def matching_doubts(strong: list[dict], episode_doubts: list[dict]) -> list[str]:
    """Event ids of other observations in the same episode that agree with one of this event's strong
    contradictions (sorted, unique)."""
    return sorted({d["event_id"] for d in episode_doubts if d.get("event_id")
                   and any(_agrees(c, d) for c in strong)})


def doubt_of(votes: list[Vote]) -> dict | None:
    """The `doubt` block for the stored `check` (phase 4b's review list): the strong contradictions, readable."""
    strong = strong_contradictions(votes)
    if not strong:
        return None
    reason = "; ".join(v.reason for v in votes if v.verdict == "no" and v.strong)
    return {"strong": strong, "reason": reason, "rule": "two_observations"}


def decide(*, has_claim: bool, votes: list[Vote], check_ran: bool, episode_doubts: list[dict] = ()) -> str:
    """Fixed rules (P144 s06a task, s06c two-observation rule): confirmed / rejected / unverified / none.
    `episode_doubts` = strong contradictions (each with its `event_id`) of other events of the same claimed person
    in the same episode."""
    if not has_claim:
        return "none"
    strong = strong_contradictions(votes)
    if strong:
        if len({c["layer"] for c in strong}) >= 2 or matching_doubts(strong, list(episode_doubts)):
            return "rejected"
        return "unverified"  # one strong contradiction never rejects alone — a doubt, kept for review
    if len({v.layer for v in votes if v.verdict == "yes"}) >= 2:
        return "confirmed"
    return "unverified" if check_ran else "none"


# ── second face sensor (P144-s19, P130 port «extra face verification») ─────────
# P144 decision 2026-10-27 («hvis 2 instanser sier det samme så får det høyere vekt … og er de uenige så er det
# også gulldata»): a second identity sensor (Double Take first) stands on the same footing as Frigate — a claim, never a
# fact. Two independent sensors agree → confirmed («very likely», never «know»); they disagree (or the second sensor
# disagrees with itself across attempts — «split») → uncertain, and code tests the image's L3 fields against BOTH
# candidates' attribute profiles: exactly one strongly contradicted → a lean to the other, capped at «likely»; every
# disagreement is kept for the phase-4b review list. The 9B never sees either sensor's claim.

# P144-s29: a winning name backed by less than this summed detector weight (face_votes.reading «support») is advisory:
# it agrees, but never makes a decision «confirmed» — agreement needs at least half a full sensor.
CONFIRM_SUPPORT = 0.5
# P144-s29b: the sensor words (2026-11-05) as numbers — code only; the user and the LLM see the words. «auto» counts
# as «uncertain» until KTSB's weight system fills it (face_votes.sensor_weight). Equal words = the s19/s29 behaviour.
TRUST_WEIGHTS = {"very_likely": 1.0, "likely": 0.75, "uncertain": 0.5, "unlikely": 0.2, "off": 0.0}
TRUST_RANK = ("unlikely", "uncertain", "likely", "very_likely")

SENSOR_RELATIONS = ("agree", "disagree", "split", "frigate_only", "double_take_only", "none")


# J14 (ep 266): «confirmed» from machines needs one agreeing sensor at «likely» or higher — sensors that are all
# «uncertain» or lower (Frigate and facebox often read the same image) agree, but never confirm.
CONFIRM_STRONG = TRUST_WEIGHTS["likely"]


def strongest_agreeing(reading: dict, ref: str | None, w_frigate: float) -> float:
    """The highest sensor weight that names `ref` in this reading: Frigate's own, or a detector whose single name is
    `ref`. Without per-detector data the reading's support stands in."""
    best = w_frigate
    dets = reading.get("detectors") or []
    if not dets:
        sup = reading.get("support")
        return max(best, float(sup)) if isinstance(sup, (int, float)) else best
    for d in dets:
        names = d.get("names") or []
        if len(names) == 1 and not d.get("split") and names[0].get("ref") == ref:
            best = max(best, float(d.get("weight") or 0.0))
    return best


def _detector_view(d: dict) -> dict:
    """One detector's vote as the sensor block keeps it (P144-s29): its single name, or none / split / unknown name."""
    names = d.get("names") or []
    one = names[0] if len(names) == 1 and not d.get("split") and not d.get("unmapped") else None
    kind = one.get("kind") if one else ("unknown" if d.get("unmapped") and not d.get("split") else None)
    return {"detector": d.get("detector"), "label": d.get("label"), "weight": d.get("weight"),
            "trust": d.get("trust"), "source": d.get("source"),
            "ref": one.get("ref") if one else None, "face": one.get("face") if one else None, "kind": kind,
            "split": bool(d.get("split")), "discarded": int(d.get("discarded") or 0), "face_found": d.get("face_found")}


def _strongly_contradicted(attributes: dict, profile: dict) -> bool:
    if not attributes:
        return False
    v = l3_compare(attributes, profile)
    return v.verdict == "no" and v.strong


def fuse_second_sensor(*, frigate_ref: str | None, frigate_face: str | None, frigate_status: str, votes: list[Vote],
                       attributes: dict, reading: dict | None, profile_of, frigate_kind: str | None = None) -> dict | None:
    """Fold the second sensor's reading into one event's decision. Pure: `profile_of(face) -> profile`.

    `frigate_ref`/`frigate_face` = the claim the pipe checked (Frigate's, or the second sensor's own when Frigate said
    nothing — then the relation is «double_take_only»); `frigate_status`/`votes` = that check's decision and layer votes.
    Returns None without a reading (no second sensor → nothing changes), else {status, person_ref, person_kind, face,
    source, sensors, review}. `person_ref`/`person_kind`/`face` name the candidate that is stored."""
    if reading is None:
        return None
    names = reading.get("names") or []
    dt = names[0] if len(names) == 1 and not reading.get("split") else None
    claim_source = reading.get("claim_source") or "frigate"
    if not names and reading.get("unmapped") and not reading.get("split"):
        dt = {"face": None, "ref": None, "kind": "unknown", "confidence": None}  # a name KTSB does not know
    if claim_source == "double_take":
        relation = "double_take_only"
    elif dt is not None and dt["ref"] is None:
        relation = "disagree" if frigate_ref else "none"
    elif reading.get("split") or len(names) > 1:
        relation = "split" if frigate_ref else "none"
    elif dt is None:
        relation = "frigate_only" if frigate_ref else "none"
    elif not frigate_ref:
        relation = "double_take_only"
    else:
        relation = "agree" if dt["ref"] == frigate_ref else "disagree"

    status, lean, lean_why = frigate_status, None, None
    person_ref, person_kind, face, source = frigate_ref, frigate_kind, frigate_face, claim_source
    support = reading.get("support")
    frigate_trust = reading.get("frigate_trust") if isinstance(reading.get("frigate_trust"), dict) else {}
    w_frigate = float(frigate_trust.get("action", TRUST_WEIGHTS["uncertain"]))
    if relation == "agree":
        # P144-s29: a vote backed by less than half a full sensor's weight agrees, but never confirms (s29b: Frigate too).
        light = (isinstance(support, (int, float)) and support < CONFIRM_SUPPORT) or w_frigate < CONFIRM_SUPPORT
        # J14 (ep 266): only «uncertain» or lower sensors agreeing never confirms — one must stand at «likely» or higher
        weak = strongest_agreeing(reading, frigate_ref, w_frigate) < CONFIRM_STRONG
        if status != "rejected" and not strong_contradictions(votes) and not light and not weak:
            status = "confirmed"
    elif relation in ("disagree", "split"):
        frigate_bad = status == "rejected" or bool(strong_contradictions(votes)) or (
            bool(frigate_face) and _strongly_contradicted(attributes, profile_of(frigate_face)))
        dt_bad = dt is None or not dt.get("face") or _strongly_contradicted(attributes, profile_of(dt["face"]))
        if relation == "disagree" and frigate_bad and not dt_bad and dt.get("ref"):
            lean, lean_why = dt["ref"], "frigate_contradicted"
            person_ref, person_kind, face, source = dt["ref"], dt.get("kind"), dt["face"], "double_take"
            status = "unverified"  # the leaned candidate passed the attribute test; the cap is «likely»
        elif relation == "disagree" and dt_bad and not frigate_bad and dt is not None and dt.get("face"):
            lean, lean_why = frigate_ref, "double_take_contradicted"
            status = "unverified" if status == "confirmed" else status
        elif relation == "disagree" and not frigate_bad and not dt_bad and dt.get("ref") and isinstance(support, (int, float)):
            # P144-s29b: neither candidate contradicted by the image → the words decide; more than half of all the
            # weight that named someone leans to that side (a lean is never above «likely»).
            total = w_frigate + float(reading.get("total_weight") or support)
            if support > total / 2:
                lean, lean_why = dt["ref"], "weight"
                person_ref, person_kind, face, source = dt["ref"], dt.get("kind"), dt["face"], "double_take"
                status = "unverified"
            elif w_frigate > total / 2:
                lean, lean_why = frigate_ref, "weight"
                status = "unverified" if status == "confirmed" else status
        elif status == "confirmed":
            status = "unverified"  # sensors disagree: never above «likely»
    sensors = {
        "provider": reading.get("provider"),
        "relation": relation,
        "frigate": frigate_ref if claim_source == "frigate" else None,
        "frigate_face": frigate_face if claim_source == "frigate" else None,
        "frigate_status": frigate_status if claim_source == "frigate" else None,
        "double_take": dt.get("ref") if dt else (frigate_ref if claim_source == "double_take" else None),
        "double_take_face": dt.get("face") if dt else (frigate_face if claim_source == "double_take" else None),
        "double_take_kind": dt.get("kind") if dt else None,
        "double_take_confidence": dt.get("confidence") if dt else None,
        "lean": lean,
        "lean_why": lean_why,
    }
    if reading.get("detectors"):
        sensors["support"] = support
        sensors["detectors"] = [_detector_view(d) for d in reading["detectors"]]
    if frigate_trust:
        sensors["frigate_trust"] = {"word": frigate_trust.get("word"), "source": frigate_trust.get("source")}
    # P144-s29b: the one sensor a name stands behind, when only one does — its word decides what Kåre says (admin only).
    claim_trust = None
    if relation == "frigate_only" or (relation == "none" and frigate_ref and claim_source == "frigate"):
        claim_trust = sensors.get("frigate_trust")
    elif relation == "double_take_only":
        claim_trust = reading.get("winner_trust")
    return {"claim_trust": claim_trust, "status": status, "person_ref": person_ref, "person_kind": person_kind, "face": face, "source": source,
            "sensors": sensors, "review": relation in ("disagree", "split")}
