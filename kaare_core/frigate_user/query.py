"""Kåre's read view of the Frigate memory (P144-s08): «when was X here last», «what happened at the door today».

Read only — every read goes through `store`'s read helpers; nothing here writes. Behind the camera tool's actions
`last_seen` / `episodes` / `episode` (kaare_core/tools/executor_camera.py).

Frigate is a sensor, not fact (P144 decisions 2026-10-27): a name is Frigate's claim, so every name carries a
probability word computed here at read time from the stored check — never stored as a label:
  - claim checked, no contradiction, not confirmed (`unverified`) → «likely» (cap, whatever Frigate's score);
  - two independent layers/sensors agree (`confirmed`)            → «very likely» (never «know»);
  - doubt: one strong contradiction (`doubt`), or too poor to judge (L0 unsure: small crop / IR / low score), or a
    weak attribute mismatch (L3 «≠»)                                → «uncertain»;
  - rejected                                                       → «unlikely» it was that person.
A second face sensor (Double Take, P144-s19) lands in the stored check as `sensors` (checks.fuse_second_sensor):
agreeing sensors make the decision `confirmed` («very likely»); disagreeing ones (or a «split» second sensor) give
«uncertain», or «likely» when code's attribute test leans to one candidate — never more. This stays the one function.
Sensor readings themselves («the door camera registered a person 14:02, Frigate said Person A, score 0.82») are
stated as known. Slimmed events (older than the event tier) keep the decision, the doubt and the L0/L3 verdicts
(`store.slim_check`, P144-s14), so they get the same word; their descriptions are gone, and the answer names that.

Who sees names (N19 + P120): logged-in household users of any person role. The system identities (`household`
voice without ID, `stranger`) and service ids get times/places/counts only; known names are scrubbed from free text.
A person who has said «Frigate may not track me» is never returned (read-time `hidden_names`, fail closed).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta

from adapters import frigate_adapter
from kaare_core import rooms
from kaare_core.config import get_local_tz
from kaare_core.domain import frigate_store
from kaare_core.frigate_user import checks, faces, settings, store
from kaare_core.tools.i18n import t
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

CERTAINTY_KEYS = ("very_likely", "likely", "uncertain", "unlikely")
_RANK = {"very_likely": 3, "likely": 2, "uncertain": 1, "unlikely": 0}
MAX_LIMIT = 50
DEFAULT_LIMIT = 10
_PREFIX_MIN = 5
_WORD_TIERS = ("event", "slim")  # tiers whose stored check still carries the probability word (P144-s14)  # «inngangsdøra» finds the «inngangskamera»: words sharing this long a start match


# ── certainty ───────────────────────────────────────────────────────────────────


def certainty(check: dict | None, claim_status: str) -> str | None:
    """The probability word key for one stored claim, or None (no claim was checked → no name was stored)."""
    check = check if isinstance(check, dict) else {}
    status = claim_status or check.get("decision") or "none"
    if status == "rejected":
        return "unlikely"
    sensors = check.get("sensors")
    if isinstance(sensors, dict) and sensors.get("relation") in ("disagree", "split"):
        return "likely" if sensors.get("lean") else "uncertain"
    if status == "confirmed":
        # J14 / P149 D1: machines alone give at most «likely»; «very likely» needs a person's own «ja» (check.human)
        return "very_likely" if human_yes(check) else "likely"
    if status != "unverified":
        return None
    word = _admin_word(check)
    if check.get("doubt"):
        return _lower("uncertain", word)
    l0 = check.get("l0")
    if isinstance(l0, dict) and l0.get("verdict") == "unsure":
        return _lower("uncertain", word)
    l3 = check.get("l3")
    if isinstance(l3, dict) and l3.get("verdict") in ("unsure", "no") and "≠" in str(l3.get("reason") or ""):
        return _lower("uncertain", word)
    return word or "likely"


def human_yes(check: dict) -> bool:
    """J14 / P149 D1: a person's own «ja» on this event (`check.human`, J2) — the only thing above «likely»."""
    h = check.get("human") if isinstance(check, dict) else None
    return isinstance(h, dict) and h.get("verdict") == "yes" and bool(h.get("by"))


_STRONG_WORDS = ("likely", "very_likely")


def _sensor_names(check: dict) -> list[tuple[str, str]]:
    """(ref, trust word) for every sensor that named someone in this event's check (Frigate + each detector)."""
    s = check.get("sensors") if isinstance(check.get("sensors"), dict) else {}
    out = []
    if s.get("frigate"):
        out.append((s["frigate"], ((s.get("frigate_trust") or {}).get("word")) or "uncertain"))
    out.extend((d["ref"], d.get("trust") or "uncertain") for d in s.get("detectors") or []
               if isinstance(d, dict) and d.get("ref"))
    return out


def stronger_other(ev: store.FrigateUserEvent, episode_events: list[store.FrigateUserEvent]) -> str | None:
    """J14b (ep 266; main session: the window is the same episode): the other name a sensor at «likely» or higher gives
    somewhere else in this episode, when this event's name stands only on weaker sensors. None otherwise — and never
    against a person's own «ja»."""
    check = ev.check if isinstance(ev.check, dict) else {}
    if not ev.person_ref or human_yes(check):
        return None
    mine = [w for ref, w in _sensor_names(check) if ref == ev.person_ref]
    if any(w in _STRONG_WORDS for w in mine):
        return None
    for other in episode_events:
        if other.event_id == ev.event_id or not isinstance(other.check, dict):
            continue
        for ref, w in _sensor_names(other.check):
            if w in _STRONG_WORDS and ref != ev.person_ref:
                return ref
    return None


def certainty_in(ev: store.FrigateUserEvent, episode_events: list[store.FrigateUserEvent]) -> str | None:
    """The word for one event read inside its episode: lowered to «uncertain» when a stronger sensor named someone else."""
    key = certainty(ev.check, ev.claim_status)
    if key and key != "unlikely" and stronger_other(ev, episode_events):
        return "uncertain"
    return key


_SINGLE_SENSOR = (None, "none", "frigate_only", "double_take_only")


def _admin_word(check: dict) -> str | None:
    """P144-s29b: the word admin set for the one sensor a name stands behind («When this sensor alone says a name, it is
    …»), or None (auto, or more than one sensor). It replaces the one-sensor word «likely»; code's doubts still lower it."""
    ct = check.get("claim_trust")
    sensors = check.get("sensors") if isinstance(check.get("sensors"), dict) else {}
    if not isinstance(ct, dict) or ct.get("source") != "admin" or sensors.get("relation") not in _SINGLE_SENSOR:
        return None
    return ct.get("word") if ct.get("word") in checks.TRUST_RANK else None


def _lower(key: str, word: str | None) -> str:
    return key if word is None else min(key, word, key=checks.TRUST_RANK.index)


def certainty_word(key: str | None, lang: str) -> str:
    return t(f"fq_cert_{key or 'none'}", lang)


# ── the second face sensor (P144-s19) ───────────────────────────────────────────

PROVIDER_LABELS = {"double_take": "Double Take"}
_SHOWN_RELATIONS = ("agree", "disagree", "split", "double_take_only")


def _sensors_of(e: store.FrigateUserEvent) -> dict:
    s = e.check.get("sensors") if isinstance(e.check, dict) else None
    return s if isinstance(s, dict) else {}


def why_key(events: list[store.FrigateUserEvent], key: str | None) -> str:
    """The i18n key that explains `key` — the second sensor's reason when it is what decided the word."""
    rels = [_sensors_of(e) for e in events if e.tier in _WORD_TIERS]
    if key == "very_likely" and any(human_yes(e.check) for e in events if e.tier in _WORD_TIERS):
        return "fq_why_human_yes"  # J14: only a person's own «ja» lifts a word above «likely»
    if key == "very_likely" and any(s.get("relation") == "agree" for s in rels):
        return "fq_why_two_sensors"
    if key == "likely" and any(s.get("relation") == "agree" for s in rels):
        return "fq_why_sensors_agree_machine"  # J14 / P149 D1
    if key == "likely" and any(s.get("relation") in ("disagree", "split") and s.get("lean") for s in rels):
        return "fq_why_sensors_lean"
    if key == "uncertain" and any(s.get("relation") in ("disagree", "split") and not s.get("lean") for s in rels):
        return "fq_why_sensors_disagree"
    if key == "likely" and rels and all(s.get("relation") == "double_take_only" for s in rels):
        return "fq_why_second_sensor"
    return f"fq_why_{key or 'none'}"


def sensors_text(e: store.FrigateUserEvent, users: dict[str, dict], lang: str) -> str | None:
    """«Frigate: X · Double Take: Y» (s29: «Frigate: X · <detector>: Y · …» when the votes are per detector) for one
    event, or None (no second sensor). A name of someone who has since said
    «Frigate may not track me» is never shown (read-time consent, fails closed)."""
    s = _sensors_of(e)
    if s.get("relation") not in _SHOWN_RELATIONS:
        return None
    dets = [d for d in s.get("detectors") or [] if isinstance(d, dict)]
    refs = {s.get("frigate"), s.get("double_take")} | {d.get("ref") for d in dets}
    try:
        hidden = _fold(store.hidden_names(refs - {None}))
    except Exception:
        hidden = _fold(refs - {None})

    def one(ref, kind) -> str:
        if ref and ref.casefold() not in hidden:
            return _display(ref, users)
        if kind == "untracked" or ref:
            return t("fq_sensor_untracked", lang)
        if kind == "unknown":
            return t("fq_sensor_unknown", lang)
        return t("fq_sensor_no_name", lang)

    rel = s["relation"]
    if s.get("frigate_rejected"):
        frigate = t("fq_sensor_rejected", lang)
    elif rel == "double_take_only":
        frigate = t("fq_sensor_no_name", lang)
    else:
        frigate = one(s.get("frigate"), s.get("frigate_kind"))
    if dets:
        # P144-s29: one vote per detector — «Frigate: X · InsightFace: Y · CPAI: Z» (labels are the user's GUI names).
        parts = [(d.get("label") or d.get("detector") or "?",
                  t("fq_sensor_split", lang) if d.get("split") else one(d.get("ref"), d.get("kind"))) for d in dets]
        text = t("fq_sensors", lang, frigate=frigate, provider=parts[0][0], second=parts[0][1])
        return text + "".join(f" · {label}: {value}" for label, value in parts[1:])
    second = t("fq_sensor_split", lang) if rel == "split" else one(s.get("double_take"), s.get("double_take_kind"))
    provider = PROVIDER_LABELS.get(s.get("provider") or "", s.get("provider") or "?")
    return t("fq_sensors", lang, frigate=frigate, provider=provider, second=second)


def _combined(events: list[store.FrigateUserEvent]) -> str | None:
    """One word for several observations of the same person: a strong doubt anywhere → uncertain; else the best
    look counts. Full-detail and slimmed events (the slim check keeps what the word needs, s14)."""
    full = [e for e in events if e.tier in _WORD_TIERS]
    if not full:
        return None
    if any(e.check.get("doubt") and e.claim_status != "rejected" for e in full):
        return "uncertain"
    keys = [k for e in full if (k := certainty(e.check, e.claim_status))]
    return max(keys, key=_RANK.__getitem__) if keys else None


# ── who may see names ───────────────────────────────────────────────────────────


def names_allowed(user_id: str | None) -> bool:
    """True only for a logged-in household user (a person role, active). Fails closed."""
    if not user_id:
        return False
    try:
        u = user_store.get_user(user_id)
    except Exception as e:
        logger.warning("[FRIGATE_QUERY] role lookup failed, no names: %s", e)
        return False
    return bool(u and u.get("is_active") and u.get("role") in user_store.PERSON_ROLES)


def available() -> bool:
    try:
        return bool(settings.enabled()) and store.exists()
    except Exception:
        return False


# ── helpers ─────────────────────────────────────────────────────────────────────


def _camera_cards() -> dict:
    try:
        cams = frigate_store.camera_config().get("cameras") or {}
        return cams if isinstance(cams, dict) else {}
    except Exception:
        return {}


def _place_label(place: str | None) -> str:
    if not place:
        return ""
    try:
        return rooms.get_register().label_of_place(place)
    except Exception:
        return place.replace("_", " ")


def _role_meaning(role: str, lang: str) -> str:
    key = f"fq_role_{role}"
    text = t(key, lang)
    return "" if text == key else text


def _camera_text(camera: str, place: str | None, cards: dict, lang: str) -> str:
    card = cards.get(camera) or {}
    shown = frigate_adapter.camera_display_name(camera, cards, fetch=False)  # J24: never a raw id when a name exists
    meaning = _role_meaning(card.get("role") or "", lang)
    name = f"{meaning} «{shown}»" if meaning else f"«{shown}»"
    label = _place_label(place)
    return name + (f" ({label})" if label else "")


def _places_text(events: list[store.FrigateUserEvent], cameras: list[str], cards: dict, lang: str) -> str:
    place_of = {e.camera: e.place for e in events if e.place}
    seen: list[str] = []
    for cam in [e.camera for e in events] + list(cameras):
        if cam not in seen:
            seen.append(cam)
    return ", ".join(_camera_text(c, place_of.get(c), cards, lang) for c in seen) or "–"


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, get_local_tz())


def _weekday(ts: float, lang: str) -> str:
    return t("fq_weekdays", lang).split(",")[_local(ts).weekday()]


def _stamp(ts: float) -> str:
    return _local(ts).strftime("%d.%m.%Y %H:%M")


def _span(a: float, b: float) -> str:
    """«15.01.2027 08:00–08:20», or two full stamps across midnight."""
    la, lb = _local(a), _local(b)
    end = lb.strftime("%H:%M") if la.date() == lb.date() else _stamp(b)
    return f"{_stamp(a)}–{end}"


def _duration(secs: float, lang: str) -> str:
    secs = max(0, round(secs))
    if secs < 60:
        return t("fq_dur_s", lang, s=secs)
    mins = secs // 60
    if mins < 60:
        return t("fq_dur_m", lang, m=mins)
    return t("fq_dur_hm", lang, h=mins // 60, m=mins % 60)


def _kind(kind: str, lang: str) -> str:
    return t(f"fq_kind_{kind}" if kind in ("household", "safe_guest", "not_welcome", "unclassified")
             else "fq_kind_unnamed", lang)


def _display(ref: str, users: dict[str, dict]) -> str:
    u = users.get(ref.casefold())
    return (u.get("display_name") or ref) if u else ref


def _users_by_name() -> dict[str, dict]:
    try:
        return {u["username"].casefold(): u for u in user_store.list_users()}
    except Exception as e:
        logger.warning("[FRIGATE_QUERY] user list failed: %s", e)
        return {}


def _all_names(users: dict[str, dict]) -> set[str]:
    """Every name that may stand in stored free text — scrubbed for callers who get no names."""
    names: set[str] = set()
    for u in users.values():
        names |= {u.get("username") or "", u.get("display_name") or "", *(u.get("frigate_faces") or [])}
    try:
        names |= set(faces.configured_faces()) | store.known_refs() | store.known_plates()
    except Exception as e:
        logger.warning("[FRIGATE_QUERY] name list failed: %s", e)
    return {n for n in names if n}


def _fold(names) -> set[str]:
    return {n.casefold() for n in names if n}


def _resolve(person: str, users: dict[str, dict]) -> tuple[set[str], set[str], str]:
    """(stored person refs, known plate names, the name to show) for what the model asked about."""
    q = person.strip().casefold()
    refs: set[str] = set()
    shown = person.strip()
    for u in users.values():
        spellings = {u.get("username") or "", u.get("display_name") or "", *(u.get("frigate_faces") or [])}
        if q in _fold(spellings):
            refs |= {u["username"], *(u.get("frigate_faces") or [])}
            shown = u.get("display_name") or u["username"]
    refs |= {f for f in faces.configured_faces() if f.casefold() == q}
    refs |= {r for r in store.known_refs() if r.casefold() == q}
    plates = {p for p in store.known_plates() if p.casefold() == q}
    if refs and not plates and shown == person.strip():
        shown = sorted(refs)[0]
    return refs, plates, shown


def _words(q: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", q.casefold()) if w]


def _matches(query: str, candidate: str) -> bool:
    q, c = query.casefold().strip(), candidate.casefold().strip()
    if not q or not c:
        return False
    if q in c or c in q:
        return True
    for a in _words(q):
        for b in _words(c):
            n = 0
            while n < min(len(a), len(b)) and a[n] == b[n]:
                n += 1
            if n >= _PREFIX_MIN:
                return True
    return False


def _camera_hits(place: str, cards: dict, events: list[store.FrigateUserEvent], lang: str) -> set[str]:
    """Cameras the model's place/camera text points at: camera id, display name, role meaning or P137 place."""
    hits: set[str] = set()
    place_of = {e.camera: e.place for e in events if e.place}
    for cam in {*cards, *place_of}:
        card = cards.get(cam) or {}
        role = card.get("role") or ""
        candidates = [cam, card.get("display_name") or "", _role_meaning(role, lang),
                      place_of.get(cam) or "", _place_label(place_of.get(cam))]
        if any(_matches(place, c) for c in candidates if c):
            hits.add(cam)
    return hits


def _not_found(person: str, lang: str) -> str:
    """«Not seen» always says how far back the memory goes — a young memory must not sound like «never»."""
    since = store.first_ts()
    out = t("fq_not_found", lang, name=person)
    return out + (" " + t("fq_memory_since", lang, since=_stamp(since)) if since is not None else "")


def _source(level: str, lang: str) -> str:
    return t("fq_source", lang, level=t(f"fq_level_{level}", lang))


# ── last_seen ───────────────────────────────────────────────────────────────────


def last_seen(person: str, *, names: bool, lang: str, now: float | None = None) -> str:
    if not available():
        return t("fq_unavailable", lang)
    if not names:
        return t("fq_names_only_household", lang)
    person = (person or "").strip()
    if not person:
        return t("fq_need_name", lang)
    users = _users_by_name()
    refs, plates, shown = _resolve(person, users)
    hidden = _fold(store.hidden_names(refs | {person}))
    if person.casefold() in hidden or (refs and _fold(refs) <= hidden):
        return _not_found(person, lang)
    refs = {r for r in refs if r.casefold() not in hidden}
    cards = _camera_cards()

    visit = None
    for ref in refs:
        v = store.last_visit(ref)
        if v and (visit is None or v.last_ts > visit.last_ts):
            visit = v
    newer = store.named_events_after(refs, visit.last_ts if visit else 0.0)
    daily = store.last_daily(refs)
    plate_evs = store.plate_events(plates)

    candidates: list[tuple[float, str]] = []
    if newer:
        candidates.append((max(e.end_ts or e.start_ts for e in newer), "events"))
    if visit:
        candidates.append((visit.last_ts, "visit"))
    if daily and daily.get("last_ts") is not None:
        candidates.append((daily["last_ts"], "daily"))
    if plate_evs:
        candidates.append((plate_evs[0].end_ts or plate_evs[0].start_ts, "plate"))
    if not candidates:
        return _not_found(person, lang)
    _, src = max(candidates)

    if src == "daily":
        return _daily_answer(daily, shown, cards, lang)
    if src == "plate":
        return _events_answer(plate_evs[:1], shown, None, cards, lang, plate=True)
    if src == "events":
        last = newer[-1]
        gap = settings.visit_gap_s()
        group = [e for e in newer if (last.start_ts - (e.end_ts or e.start_ts)) <= gap]
        return _events_answer(group, shown, None, cards, lang) + _later_uncertain(group, shown, cards, lang)
    group = store.visit_events(visit.id)
    return _events_answer(group, shown, visit, cards, lang) + _later_uncertain(group, shown, cards, lang)


def _later_uncertain(group: list[store.FrigateUserEvent], shown: str, cards: dict, lang: str) -> str:
    """P144 #9 «sist sett» (2026-11-05): when a person event with no readable face follows the named sighting on
    the same camera or place within one visit gap, say so and offer to show it — the user's answer then settles it.
    Never an episode that holds an opted-out person's event (W21/W22). "" when there is none."""
    if not group:
        return ""
    end = max(e.end_ts or e.start_ts for e in group)
    cams = {e.camera for e in group}
    places = {e.place for e in group if e.place}
    try:
        later = [e for e in store.events_between(end + 0.001, end + settings.visit_gap_s())
                 if e.label == "person" and e.person_kind == "unknown" and e.episode_id is not None
                 and (e.camera in cams or (e.place and e.place in places))]
        for e in later:
            if any(o.person_kind == "untracked" for o in store.episode_events(e.episode_id)):
                continue
            return "\n" + t("fq_ls_later_uncertain", lang, name=shown, time=_local(e.start_ts).strftime("%H:%M"),
                            camera=_camera_text(e.camera, e.place, cards, lang), episode=e.episode_id)
    except Exception as ex:
        logger.warning("[FRIGATE_QUERY] later uncertain lookup failed: %s", ex)
    return ""


def _events_answer(events: list[store.FrigateUserEvent], shown: str, visit, cards: dict, lang: str,
                   plate: bool = False) -> str:
    if visit is not None:
        first, last, cameras, count, kind = (visit.first_ts, visit.last_ts, visit.cameras, visit.event_count,
                                             visit.person_kind)
    else:
        first = min(e.start_ts for e in events)
        last = max(e.end_ts or e.start_ts for e in events)
        cameras, count, kind = [], len(events), events[-1].person_kind
    full = [e for e in events if e.tier == "event"]
    level = "event" if full else "visit"
    kind_text = t("fq_kind_vehicle", lang) if plate else _kind(kind, lang)
    lines = [
        t("fq_ls_header", lang, name=shown, kind=kind_text),
        t("fq_ls_when", lang, weekday=_weekday(first, lang), span=_span(first, last),
          duration=_duration(last - first, lang)),
        t("fq_ls_where", lang, places=_places_text(events, cameras, cards, lang)),
    ]
    if plate:
        key = "likely"  # Frigate's plate reading alone: a claim, never a fact
        lines.append(t("fq_ls_cert", lang, name=shown, word=certainty_word(key, lang),
                       why=t("fq_why_plate", lang)))
        e = events[0]
        lines.append(t("fq_ls_plate_reading", lang, time=_local(e.start_ts).strftime("%H:%M"),
                       camera=_camera_text(e.camera, e.place, cards, lang), plate=e.plate_claim or shown))
    else:
        key = _combined(events)
        lines.append(t("fq_ls_cert", lang, name=shown, word=certainty_word(key, lang), why=t(why_key(events, key),
                                                                                            lang)))
        first_claim = next((e for e in events if e.person_ref), None)
        both = sensors_text(first_claim, _users_by_name(), lang) if first_claim is not None else None
        if both:
            lines.append(t("fq_ls_sensor_reading", lang, time=_local(first_claim.start_ts).strftime("%H:%M"),
                           camera=_camera_text(first_claim.camera, first_claim.place, cards, lang), sensors=both))
        elif first_claim is not None:
            scores = [e.claim_score for e in events if e.claim_score is not None]
            lines.append(t("fq_ls_reading", lang, time=_local(first_claim.start_ts).strftime("%H:%M"),
                           camera=_camera_text(first_claim.camera, first_claim.place, cards, lang),
                           claim=first_claim.person_ref,
                           score=f"{max(scores):.2f}" if scores else "–"))
    lines.append(t("fq_ls_count", lang, n=count))
    episodes = sorted({e.episode_id for e in events if e.episode_id is not None})
    if episodes:
        lines.append(t("fq_ls_episodes", lang, ids=", ".join(f"#{i}" for i in episodes)))
    if visit is not None and not visit.closed:
        lines.append(t("fq_ls_ongoing", lang))
    lines.append(_source(level, lang))
    return "\n".join(lines)


def _daily_answer(daily: dict, shown: str, cards: dict, lang: str) -> str:
    first, last = daily.get("first_ts"), daily.get("last_ts")
    when_ts = first if first is not None else last
    day = datetime.strptime(daily["day"], "%Y-%m-%d").strftime("%d.%m.%Y")
    places = ", ".join(_camera_text(p, None, cards, lang) if p in cards else _place_label(p)
                       for p in daily.get("places") or []) or "–"
    lines = [
        t("fq_ls_header", lang, name=shown, kind=_kind(daily.get("person_kind") or "", lang)),
        t("fq_ls_day_when", lang, weekday=_weekday(when_ts, lang) if when_ts else "", day=day,
          first=_local(first).strftime("%H:%M") if first else "–",
          last=_local(last).strftime("%H:%M") if last else "–"),
        t("fq_ls_where", lang, places=places),
        t("fq_ls_cert_day", lang, name=shown),
        t("fq_ls_day_counts", lang, events=daily.get("event_count") or 0, visits=daily.get("visit_count") or 0),
        _source("day", lang),
    ]
    return "\n".join(lines)


# ── episodes ────────────────────────────────────────────────────────────────────


def parse_local(text: str | None, tz, end: bool = False) -> float | None:
    """«YYYY-MM-DD» or «YYYY-MM-DD HH:MM» in the household's time zone → epoch; a date alone is the day's start (or,
    with end=True, the next day's start). None when it does not parse."""
    text = (text or "").strip().replace("T", " ")
    for fmt, whole_day in (("%Y-%m-%d %H:%M", False), ("%Y-%m-%d", True)):
        try:
            dt = datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
        if whole_day and end:
            dt = dt + timedelta(days=1)  # wall-clock arithmetic: a DST day is still one calendar day
        return dt.timestamp()
    return None


# ── person sessions (J21b reader 2: the camera tool's action='events', replacing Argus' face_events.txt) ──────────


def person_sessions(hours: float, name: str = "", *, names: bool, lang: str, now: float | None = None) -> list[str] | str:
    """Named person events of the last `hours`, one line per person per visit (events closer than visit_gap_s are one),
    oldest first: span · name (certainty word) — cameras · count [event_id of the surest event]. Names only for logged-in
    household users (else the fq_names_only_household text); a person hidden since (opted out afterwards) never shows.
    `name` narrows to one person (any spelling). [] = nothing in the window."""
    if not names:
        return t("fq_names_only_household", lang)
    now = time.time() if now is None else now
    users = _users_by_name()
    evs = [e for e in store.events_between(now - hours * 3600, now) if e.person_ref and e.label == "person"
           and e.person_kind != "untracked"]
    hidden = _fold(store.hidden_names({e.person_ref for e in evs}))
    evs = [e for e in evs if e.person_ref.casefold() not in hidden]
    if name.strip():
        refs, _plates, _shown = _resolve(name, users)
        want = _fold(refs) or {name.strip().casefold()}
        evs = [e for e in evs if e.person_ref.casefold() in want]
    cards, gap = _camera_cards(), settings.visit_gap_s()
    groups: dict[str, list[list[store.FrigateUserEvent]]] = {}
    for e in sorted(evs, key=lambda e: e.start_ts):
        runs = groups.setdefault(e.person_ref.casefold(), [])
        if runs and e.start_ts - max(x.end_ts or x.start_ts for x in runs[-1]) <= gap:
            runs[-1].append(e)
        else:
            runs.append([e])
    lines = []
    for runs in groups.values():
        for run in runs:
            first, last = run[0].start_ts, max(x.end_ts or x.start_ts for x in run)
            best = max(run, key=lambda x: x.claim_score if x.claim_score is not None else -1.0)
            cams = ", ".join(dict.fromkeys(frigate_adapter.camera_display_name(x.camera, cards, fetch=False) for x in run))
            lines.append((first, t("fq_session_line", lang, span=_span(first, last), name=_display(run[0].person_ref, users),
                                   word=certainty_word(_combined(run), lang), cams=cams, n=len(run), eid=best.event_id)))
    return [line for _ts, line in sorted(lines, key=lambda x: x[0])]


def _scrubbed(names: bool, hidden: set[str], users: dict[str, dict]) -> set[str]:
    return hidden if names else _all_names(users) | hidden


def episodes(start: float, end: float, *, place: str | None = None, person: str | None = None,
             limit: int = DEFAULT_LIMIT, names: bool, lang: str) -> str:
    if not available():
        return t("fq_unavailable", lang)
    limit = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
    users = _users_by_name()
    cards = _camera_cards()
    page = store.episodes_window(start, end)
    all_events = [e for _ep, evs in page for e in evs]
    hidden_raw = store.hidden_names({r for ep, evs in page for r in [*ep.person_refs, *(e.person_ref for e in evs)]})
    hidden = _fold(hidden_raw)
    filters = ""
    if place:
        cams = _camera_hits(place, cards, all_events, lang)
        if not cams:
            known = ", ".join(_camera_text(c, None, cards, lang) for c in cards) or "–"
            return t("fq_eps_place_unknown", lang, place=place, cams=known)
        page = [(ep, evs) for ep, evs in page if cams & {*ep.cameras, *(e.camera for e in evs)}]
        filters += t("fq_eps_filter_place", lang, place=place)
    if person:
        if not names:
            return t("fq_names_only_household", lang)
        refs, plates, shown = _resolve(person, users)
        if person.casefold() in hidden or _fold(store.hidden_names(refs | {person})) & {person.casefold()}:
            refs, plates = set(), set()
        wanted = _fold(refs) - hidden
        wanted_plates = _fold(plates)
        page = [(ep, evs) for ep, evs in page
                if wanted & _fold([*ep.person_refs, *(e.person_ref for e in evs)])
                or wanted_plates & _fold(e.plate_claim for e in evs if e.plate_known)]
        filters += t("fq_eps_filter_person", lang, person=shown)
    window = t("fq_window", lang, span=_span(start, end))
    if not page:
        return t("fq_eps_none", lang, window=window, filters=filters) + "\n" + _source("event", lang)
    shown_page = page[:limit]
    lines = [t("fq_eps_header", lang, window=window, filters=filters, shown=len(shown_page), total=len(page))]
    for ep, evs in shown_page:
        lines.append(_episode_lines(ep, evs, names, hidden, users, cards, lang))
    if len(page) > limit:
        lines.append(t("fq_eps_more", lang, n=len(page) - limit))
    # P150: detail is trimmed by the store's size limit, not by age — say what is true of the data itself
    since = store.detail_since()
    if (since is not None and start < since) or any(e.tier != "event" for _ep, evs in shown_page for e in evs):
        lines.append(t("fq_eps_old_note", lang, since=_stamp(since) if since is not None else "–"))
    lines.append(_source("event", lang))
    return store.scrub("\n".join(lines), _scrubbed(names, hidden_raw, users))


def _who(evs: list[store.FrigateUserEvent], names: bool, hidden: set[str], users: dict[str, dict], lang: str) -> str:
    persons = [e for e in evs if e.label == "person"]
    if not names:
        return t("fq_ep_persons_count", lang, n=len(persons)) if persons else ""
    by_ref: dict[str, list[store.FrigateUserEvent]] = {}
    unnamed = 0
    for e in persons:
        if e.person_ref and e.person_ref.casefold() not in hidden:
            by_ref.setdefault(e.person_ref, []).append(e)
        else:
            unnamed += 1
    parts = [t("fq_ep_who_entry", lang, name=_display(ref, users), kind=_kind(group[-1].person_kind, lang),
               word=certainty_word(_combined(group), lang))
             for ref, group in sorted(by_ref.items())]
    if unnamed:
        parts.append(t("fq_ep_unnamed", lang, n=unnamed))
    plates = sorted({e.plate_claim for e in evs if e.plate_known and e.plate_claim})
    if plates:
        parts.append(t("fq_ep_plates", lang, plates=", ".join(plates),
                       word=certainty_word("likely", lang)))
    return t("fq_ep_who", lang, who="; ".join(parts)) if parts else ""


def _episode_lines(ep: store.Episode, evs: list[store.FrigateUserEvent], names: bool, hidden: set[str],
                   users: dict[str, dict], cards: dict, lang: str) -> str:
    lines = [t("fq_ep_line", lang, id=ep.episode_id, weekday=_weekday(ep.start_ts, lang),
               span=_span(ep.start_ts, ep.end_ts), duration=_duration(ep.end_ts - ep.start_ts, lang),
               places=_places_text(evs, ep.cameras, cards, lang), labels=", ".join(ep.labels) or "–")]
    who = _who(evs, names, hidden, users, lang)
    if who:
        lines.append(who)
    c = ep.conclusion or {}
    if c.get("summary"):
        lines.append(t("fq_ep_memory", lang, by=t(f"fq_by_{'code' if c.get('by') == 'code' else 'model'}", lang),
                       summary=c["summary"]))
    else:
        lines.append(t("fq_ep_pending", lang))
    unusual = c.get("unusual") or {}
    if unusual.get("value"):
        lines.append(t("fq_ep_unusual", lang, reason=unusual.get("reason") or "–"))
    if any(e.tier != "event" for e in evs):
        lines.append(t("fq_ep_slim", lang))
    return "\n".join(lines)


# ── episode ─────────────────────────────────────────────────────────────────────


def episode(episode_id, *, names: bool, lang: str) -> str:
    if not available():
        return t("fq_unavailable", lang)
    try:
        episode_id = int(episode_id)
    except (TypeError, ValueError):
        return t("fq_need_episode_id", lang)
    ep = store.episode(episode_id)
    if ep is None:
        return t("fq_episode_missing", lang, id=episode_id)
    evs = store.episode_events(episode_id)
    users = _users_by_name()
    cards = _camera_cards()
    hidden_raw = store.hidden_names({*ep.person_refs, *(e.person_ref for e in evs)})
    hidden = _fold(hidden_raw)
    slim = any(e.tier != "event" for e in evs)
    lines = [
        t("fq_epd_header", lang, id=ep.episode_id, weekday=_weekday(ep.start_ts, lang),
          span=_span(ep.start_ts, ep.end_ts), duration=_duration(ep.end_ts - ep.start_ts, lang)),
        t("fq_epd_level", lang, level=t("fq_level_slim" if slim else "fq_level_event", lang)),
        t("fq_ls_where", lang, places=_places_text(evs, ep.cameras, cards, lang)),
    ]
    if ep.route:
        lines.append(t("fq_epd_route", lang, route=ep.route))
    scene = ep.scene or {}
    if scene.get("scene"):
        lines.append(t("fq_epd_scene", lang, scene=scene["scene"]))
    if isinstance(scene.get("persons"), int):
        lines.append(t("fq_epd_scene_persons", lang, n=scene["persons"]))
    lines.extend(t(f"fq_epd_{key}", lang, v=scene[key]) for key in ("at_door", "vehicles") if scene.get(key))
    c = ep.conclusion or {}
    if c.get("summary"):
        lines.append(t("fq_epd_conclusion", lang,
                       by=t(f"fq_by_{'code' if c.get('by') == 'code' else 'model'}", lang), summary=c["summary"]))
        unusual = c.get("unusual") or {}
        lines.append(t("fq_epd_unusual", lang, reason=unusual.get("reason") or "–") if unusual.get("value")
                     else t("fq_epd_not_unusual", lang))
        level = (c.get("confidence") or {}).get("level")
        if level:
            lines.append(t("fq_epd_model_conf", lang, level=level))
    else:
        lines.append(t("fq_ep_pending", lang))
    if names:
        claims = _claim_lines(evs, hidden, users, lang)
        if claims:
            lines.append(t("fq_epd_claims_header", lang))
            lines.extend(claims)
    else:
        n = sum(1 for e in evs if e.label == "person")
        if n:
            lines.append(t("fq_ep_persons_count", lang, n=n))
    lines.append(t("fq_epd_events_header", lang, n=len(evs)))
    for e in evs:
        who = ""
        if names and e.person_ref and e.person_ref.casefold() not in hidden:
            key = certainty_in(e, evs) if e.tier in _WORD_TIERS else None  # J14b: read inside its episode
            who = t("fq_epd_event_who", lang, name=_display(e.person_ref, users), word=certainty_word(key, lang))
            other = stronger_other(e, evs) if e.tier in _WORD_TIERS else None
            if other and other.casefold() not in hidden:
                who += t("fq_epd_stronger_other", lang, other=_display(other, users))
            both = sensors_text(e, users, lang)
            if both:
                who += f" ({both})"
        if names and e.plate_known and e.plate_claim:
            who += t("fq_epd_event_plate", lang, plate=e.plate_claim)
        lines.append(t("fq_epd_event", lang, time=_local(e.start_ts).strftime("%H:%M:%S"),
                       camera=_camera_text(e.camera, e.place, cards, lang), label=e.label, who=who))
    lines.append(_source("slim" if slim else "event", lang))
    return store.scrub("\n".join(lines), _scrubbed(names, hidden_raw, users))


def _claim_lines(evs: list[store.FrigateUserEvent], hidden: set[str], users: dict[str, dict], lang: str) -> list[str]:
    by_ref: dict[str, list[store.FrigateUserEvent]] = {}
    for e in evs:
        if e.person_ref and e.person_ref.casefold() not in hidden:
            by_ref.setdefault(e.person_ref, []).append(e)
    out = []
    for ref, group in sorted(by_ref.items()):
        key = _combined(group)
        scores = [e.claim_score for e in group if e.claim_score is not None]
        out.append(t("fq_epd_claim", lang, name=_display(ref, users), kind=_kind(group[-1].person_kind, lang),
                     word=certainty_word(key, lang), why=t(why_key(group, key), lang), n=len(group),
                     cameras=len({e.camera for e in group}), score=f"{max(scores):.2f}" if scores else "–"))
    return out
