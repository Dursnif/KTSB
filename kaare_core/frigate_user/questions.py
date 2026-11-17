"""«Var det deg?» — the camera's verification questions (P144 #9, P149 phase 2).

A person event whose identity is uncertain becomes one question to the person a sensor guessed: «Var det deg på
[kamera] kl. [tid]?» — no image, no detail, so a wrong guess reveals nobody. The answer is stored in KTSB's one weight
system (`kaare_core.weights`):
  - the event: one human answer settles it (P149 D3) — subject ("event", <frigate event id>);
  - each sensor that voted on it: one small step of trust, per sensor × camera × light (D3 two layers) — subject
    ("sensor", "<sensor>|<camera>|<day|ir>"). «ja»/«privat»: a sensor that named this person was right, a sensor that named
    someone else was wrong. «nei»: only «not you» — a sensor that named this person was wrong, nothing about the others.
  - the person's willingness to be asked: ("asking", "<user>|<channel>") — «hopp over» lowers it, an answer raises it; it
    sets how many questions the list offers (D12 «hvis bruker sier nei hver gang, så skal KTSB slutte å spørre»).
Rules: an opted-out person is never asked (W21/W22); a person is asked only about events a sensor named *them* in, so a
child is only asked about themselves; the question and answer are private to that person (admin does not see them);
«privat» keeps that it was them and nothing else. Read-only toward Frigate and Double Take; questions are computed from
frigate.db on demand (no background job).
"""

from __future__ import annotations

import logging
import time

from kaare_core import receipts, weights
from kaare_core.frigate_user import query, settings, store
from kaare_core.tools.i18n import t
from kaare_core.users import store as user_store
from kaare_core.weights import model as wmodel

logger = logging.getLogger(__name__)

CHANNELS = ("gui", "chat")
WINDOW_DAYS = 7  # questions about the last week; older events are not asked about (memory fades, P144 #8)
# How many questions the list offers, by how willing the person has been (word of the ("asking", …) weight).
BUDGET = {"very_likely": 10, "likely": 8, "uncertain": 5, "unlikely": 1}
# N39 c: «unsure» = «vet ikke» — closes the card, moves no Validated (P149 D1), counts as engagement; «skip» = «not now».
ANSWERS = ("yes", "no", "private", "unsure", "skip")


def sensor_subject(sensor: str, camera: str, ir: bool | None) -> tuple[str, str]:
    light = "ir" if ir else "day"
    return ("sensor", f"{sensor}|{camera}|{light}")


def _user(username: str) -> dict | None:
    try:
        return user_store.get_user(username)
    except Exception as e:
        logger.warning("[FRIGATE_Q] user lookup failed: %s", e)
        return None


def _refs(user: dict) -> set[str]:
    return {n.casefold() for n in (user.get("username"), *(user.get("frigate_faces") or [])) if isinstance(n, str) and n}


def _votes(ev: store.FrigateUserEvent) -> list[tuple[str, str]]:
    """(sensor, named ref casefolded or "") for every sensor that voted on this event. Frigate counts as a sensor."""
    s = (ev.check or {}).get("sensors") or {}
    out: list[tuple[str, str]] = []
    fr = s.get("frigate_face") or s.get("frigate")
    if fr or s:
        out.append(("frigate", str(fr).casefold() if fr else ""))
    for d in s.get("detectors") or []:
        if isinstance(d, dict) and d.get("detector") and not d.get("discarded"):
            name = d.get("ref") or d.get("face") or ""
            out.append((str(d["detector"]), str(name).casefold()))
    if not out and ev.person_ref:
        out.append(("frigate", ev.person_ref.casefold()))
    return out


def _uncertain(ev: store.FrigateUserEvent) -> bool:
    if ev.label != "person" or ev.person_kind in ("untracked", "unknown") or (ev.check or {}).get("human"):
        return False  # a person already answered: settled for everyone
    rel = ((ev.check or {}).get("sensors") or {}).get("relation")
    return ev.claim_status != "confirmed" or rel in ("disagree", "split")


def _faceless(ev: store.FrigateUserEvent) -> bool:
    """A person event no sensor could name (no readable face) — the «sist sett» follow-up (P144 #9)."""
    return ev.label == "person" and ev.person_kind == "unknown" and not any(ref for _, ref in _votes(ev))


def _askable(ev: store.FrigateUserEvent, mine: set[str], channel: str) -> bool:
    """A sensor named this person on an uncertain event — or, in chat only (offered after «sist sett»), a person event
    with no readable face, which the person may claim as themselves."""
    if _uncertain(ev) and any(ref in mine for _, ref in _votes(ev) if ref):
        return True
    return channel == "chat" and _faceless(ev)


def _answered(event_id: str, username: str) -> bool:
    return weights.get(("event", event_id, username)).learned_n > 0


def _own_answered(event_id: str, username: str) -> bool:
    """J22: the person answered themselves (not admin about them)."""
    rows = weights.get_store().rows(("event", event_id, username))["answers"]
    return any(r.get("by_user") == username for r in rows)


def _admin_about_me(ev: store.FrigateUserEvent | None, username: str) -> bool:
    """J22: admin answered «it was <username>» on this event and the person has not answered yet → they may correct it."""
    human = (ev.check or {}).get("human") if ev else None
    return isinstance(human, dict) and human.get("about") == username and human.get("by") != username \
        and not _own_answered(ev.event_id, username)


def _willing(username: str, channel: str) -> weights.Weight:
    return weights.get(("asking", f"{username}|{channel}", username))


def budget(username: str, channel: str = "gui") -> int:
    return BUDGET.get(_willing(username, channel).word, BUDGET["uncertain"])


def _skipped(event_id: str, username: str) -> bool:
    return weights.get_store().has_pointer("evidence", f"skip:{username}:{event_id}")


def _unsure(event_id: str, username: str) -> bool:
    """N39 c: the person answered «vet ikke» — an evidence row, never a Validated answer (P149 D1)."""
    return weights.get_store().has_pointer("evidence", f"unsure:{username}:{event_id}")


def _closed(event_id: str, username: str) -> bool:
    """This person has answered, said «vet ikke» or skipped this event — not asked again."""
    return _answered(event_id, username) or _skipped(event_id, username) or _unsure(event_id, username)


def _kare_asked(event_id: str, username: str) -> bool:
    return weights.get_store().has_pointer("evidence", f"kare_ask:{username}:{event_id}")


def queue_for(username: str, event_id: str) -> dict:
    """J9: Kåre puts one camera event in this user's question list (his own «who was that?») — only an event the user
    may answer about themselves (a sensor named them, or no face could be read), never for an opted-out user."""
    if not settings.enabled():
        return {"ok": False, "error": "off"}
    user = _user(username)
    if not user or not user.get("frigate_tracking", True):
        return {"ok": False, "error": "not_allowed"}
    try:
        ev = store.event_by_id(event_id)
    except Exception:
        ev = None
    if not ev or not _askable(ev, _refs(user), "chat") or _answered(event_id, username):
        return {"ok": False, "error": "not_a_question"}
    if not _kare_asked(event_id, username):
        weights.record(("event", event_id, username), source_class="self", source="kare", context=f"frigate:{event_id}",
                       pointer=f"kare_ask:{username}:{event_id}")
    return {"ok": True, "saved": weights.flush_ok()}  # N39: the receipt must know whether it was saved


def candidates(username: str, channel: str = "gui", now: float | None = None) -> list[dict]:
    """Open questions for this person, most useful first (fewest answers behind the sensors that named them, then
    newest), cut to the person's budget. [] when the Frigate user is off, the person opted out or is unknown."""
    if channel not in CHANNELS or not settings.enabled():
        return []
    user = _user(username)
    if not user or not user.get("frigate_tracking", True):
        return []  # opted out: never asked (W21)
    mine = _refs(user)
    now = time.time() if now is None else now
    try:
        evs = store.events_between(now - WINDOW_DAYS * 86400, now)
    except Exception as e:
        logger.warning("[FRIGATE_Q] events unavailable: %s", e)
        return []
    out = []
    for ev in evs:
        asked = _kare_asked(ev.event_id, username)  # J9: Kåre queued it for this user
        if not _uncertain(ev) and not (asked and _askable(ev, mine, "chat")):
            continue
        votes = _votes(ev)
        named_me = [s for s, ref in votes if ref and ref in mine]
        if (not named_me and not asked) or _closed(ev.event_id, username):
            continue
        ir = bool((ev.check or {}).get("ir"))
        learned = min((weights.get(sensor_subject(s, ev.camera, ir)).learned_n for s in named_me), default=0)
        out.append({"event_id": ev.event_id, "camera": ev.camera, "ts": ev.start_ts, "episode_id": ev.episode_id,
                    "_learned": learned})
    out.sort(key=lambda q: (q["_learned"], -q["ts"]))
    for q in out:
        q.pop("_learned")
    return out[: budget(username, channel)]


def answer(username: str, event_id: str, verdict: str, channel: str = "gui", verbatim: bool = True) -> dict:
    """Store a person's answer. Only an event that is an open question *for this person* can be answered (fail closed).
    Returns {"ok": bool, "error"?: str, "sensors": n}."""
    if verdict not in ANSWERS or channel not in CHANNELS:
        return {"ok": False, "error": "bad_request"}
    if not settings.enabled():
        return {"ok": False, "error": "off"}
    user = _user(username)
    if not user or not user.get("frigate_tracking", True):
        return {"ok": False, "error": "not_allowed"}
    try:
        ev = store.event_by_id(event_id)
    except Exception:
        ev = None
    mine = _refs(user)
    queued = bool(ev) and _kare_asked(event_id, username) and _askable(ev, mine, "chat")
    correcting = _admin_about_me(ev, username)  # J22: the person's own answer outweighs admin's about them
    if not correcting and (not ev or not (_askable(ev, mine, channel) or queued) or _closed(event_id, username)):
        return {"ok": False, "error": "not_a_question"}
    asking = ("asking", f"{username}|{channel}", username)
    ctx = f"frigate:{event_id}"
    if verdict == "skip":
        weights.record(("event", event_id, username), source_class="user", source=channel, context=ctx,
                       pointer=f"skip:{username}:{event_id}")
        weights.answer(asking, by_user=username, verdict="no", verbatim=True, context=ctx)
        return {"ok": True, "sensors": 0, "saved": weights.flush_ok()}
    if verdict == "unsure":  # N39 c: closes the card, teaches nothing, but the person engaged (willingness up)
        weights.record(("event", event_id, username), source_class="user", source=channel, context=ctx,
                       pointer=f"unsure:{username}:{event_id}")
        weights.answer(asking, by_user=username, verdict="yes", verbatim=True, context=ctx)
        return {"ok": True, "sensors": 0, "saved": weights.flush_ok()}
    weights.answer(("event", event_id, username), by_user=username, verdict=verdict, verbatim=verbatim, context=ctx,
                   pointer=ctx)
    n = _teach_sensors(username, ev, mine, verdict, verbatim, ctx)
    weights.answer(asking, by_user=username, verdict="yes", verbatim=True, context=ctx)
    saved = weights.flush_ok()  # N39: the receipt must know whether it was saved
    if verdict == "yes" or (verdict == "no" and ev.person_kind != "unknown"):
        # «privat» stays in the weight system only
        saved = store.set_human_answer(event_id, username, verdict, channel) and saved
    return {"ok": True, "sensors": n, "saved": saved}


def _teach_sensors(username: str, ev: store.FrigateUserEvent, mine: set[str], verdict: str, verbatim: bool, ctx: str,
                   suffix: str = "") -> int:
    """One answer row per sensor that named someone: «ja»/«privat» — naming me was right, naming someone else wrong;
    «nei» — only «not you» for the sensors that named me. Returns how many sensors learned."""
    ir = bool((ev.check or {}).get("ir"))
    n = 0
    for sensor, ref in _votes(ev):
        if not ref:
            continue
        named_me = ref in mine
        if verdict in ("yes", "private"):
            right = named_me  # it was me: naming me was right, naming someone else was wrong
        elif named_me:
            right = False  # «nei» only teaches «not you»
        else:
            continue
        weights.answer(sensor_subject(sensor, ev.camera, ir), by_user=username, verdict="yes" if right else "no",
                       verbatim=verbatim, context=ctx, pointer=f"{ctx}:{sensor}{suffix}")
        n += 1
    return n


# ── N39 b: a person corrects their own earlier answer (append-only; the old rows stay, a marker withdraws them) ──

CORRECTABLE = ("yes", "no", "private", "unsure")


def _superseded(rows: dict) -> set[str]:
    return {str(r.get("pointer"))[len("supersede:"):] for r in rows["evidence"]
            if r.get("source_class") == "user" and str(r.get("pointer") or "").startswith("supersede:")}


def _active_own(subject, username: str, prefix: str) -> list[dict]:
    """This person's answer rows on a subject whose pointer starts with `prefix` and that are not withdrawn."""
    rows = weights.get_store().rows(subject)
    gone = _superseded(rows)
    return [r for r in rows["answers"] if r.get("by_user") == username and str(r.get("pointer") or "").startswith(prefix)
            and f"{username}:{r.get('pointer')}" not in gone]


def current_answer(username: str, event_id: str) -> str | None:
    """The person's own answer standing on an event now: yes/no/private, «unsure», «skip», or None."""
    ctx = f"frigate:{event_id}"
    own = _active_own(("event", event_id, username), username, ctx)
    if own:
        return own[-1].get("verdict")
    if _unsure(event_id, username):
        return "unsure"
    return "skip" if _skipped(event_id, username) else None


def correct(username: str, event_id: str, verdict: str, channel: str = "chat") -> dict:
    """Replace this person's own answer on one event. Every row the old answer wrote (the event, each sensor) gets a
    withdraw marker from this person; the new answer is written with its own pointer; the sensors learn again from it.
    «vet ikke»/«privat» take the human answer off the event (kept in check.human_history). Nothing is deleted.
    {ok, before, sensors, saved} or {ok: False, error}."""
    if verdict not in CORRECTABLE or channel not in CHANNELS:
        return {"ok": False, "error": "bad_request"}
    if not settings.enabled():
        return {"ok": False, "error": "off"}
    user = _user(username)
    if not user or not user.get("frigate_tracking", True):
        return {"ok": False, "error": "not_allowed"}
    try:
        ev = store.event_by_id(event_id)
    except Exception:
        ev = None
    before = current_answer(username, event_id) if ev else None
    if not ev or before is None:
        return {"ok": False, "error": "not_answered"}
    if before == verdict:
        return {"ok": True, "before": before, "sensors": 0, "saved": True, "unchanged": True}
    ctx = f"frigate:{event_id}"
    st = weights.get_store()
    subjects = [("event", event_id, username)]
    ir = bool((ev.check or {}).get("ir"))
    subjects += [sensor_subject(s, ev.camera, ir) for s, ref in _votes(ev) if ref]
    for subj in subjects:  # withdraw every live row of the old answer
        prefix = ctx if subj[0] == "event" else f"{ctx}:"
        for r in _active_own(subj, username, prefix):
            weights.record(subj, source_class="user", source=channel, context="correct",
                           pointer=f"supersede:{username}:{r.get('pointer')}")
    rev = sum(1 for r in st.rows(("event", event_id, username))["evidence"]
              if str(r.get("pointer") or "").startswith(f"supersede:{username}:{ctx}"))
    suffix = f"#r{rev}"
    n = 0
    if verdict == "unsure":
        if not _unsure(event_id, username):
            weights.record(("event", event_id, username), source_class="user", source=channel, context=ctx,
                           pointer=f"unsure:{username}:{event_id}")
    else:
        weights.answer(("event", event_id, username), by_user=username, verdict=verdict, verbatim=True, context=ctx,
                       pointer=f"{ctx}{suffix}")
        n = _teach_sensors(username, ev, _refs(user), verdict, True, ctx, suffix)
    saved = weights.flush_ok()
    has_human = isinstance((ev.check or {}).get("human"), dict)
    if verdict == "yes" or (verdict == "no" and (has_human or ev.person_kind != "unknown")):
        saved = store.set_human_answer(event_id, username, verdict, channel) and saved
    elif verdict in ("private", "unsure") and has_human:
        saved = store.clear_human_answer(event_id, username, channel) and saved
    return {"ok": True, "before": before, "sensors": n, "saved": saved}


def corrected_text(username: str, event_id: str, verdict: str, res: dict, lang: str, channel: str = "chat") -> str:
    """The receipt of a correction: what it was, what it is now, how many sensors were counted again."""
    if not res.get("ok"):
        err = res.get("error")
        return t("fv_not_allowed", lang) if err in ("not_allowed", "off") else t("fv_not_question", lang)
    if res.get("saved") is False:
        return t("rcpt_not_saved", lang)
    try:
        ev = store.event_by_id(event_id)
    except Exception:
        ev = None

    def word(v):
        return t(f"fv_answer_{v}", lang) if v in ("yes", "no", "private", "unsure") else t("fv_answer_skip_word", lang)

    what = t("rcpt_cam_corrected", lang, before=word(res.get("before")), after=word(verdict), n=res.get("sensors", 0))
    receipt = receipts.build("camera_answer_corrected", ref=event_id, what=what,
                             where=_event_text(ev, lang) if ev else event_id, who=username, channel=channel, lang=lang)
    return f"{receipt}\n{receipts.instruction(lang)}"


def sensor_learning(sensor: str, camera: str | None = None, light: str | None = None) -> dict:
    """What KTSB has learned about one sensor from «Var det deg?» answers — one cell (camera + light), one camera (both
    lights), or every camera × light. The answers' «ja»/«nei» are added up and folded with the weight model's formula.
    {learned_n, goal, word, cells}; word None while no one has answered."""
    st = weights.get_store()
    total, yes, no, cells = 0, 0.0, 0.0, []
    for kind, key, scope in st.subjects("sensor"):
        parts = key.split("|")
        if len(parts) != 3 or parts[0] != sensor:
            continue
        if (camera and parts[1] != camera) or (light and parts[2] != light):
            continue
        w = st.get((kind, key, scope))
        total += w.learned_n
        yes += w.yes
        no += w.no
        cells.append({"key": f"{parts[1]}|{parts[2]}", "learned_n": w.learned_n, "word": w.word})
    v = wmodel._validated(yes, no)
    return {"learned_n": total, "goal": wmodel.LEARNED_GOAL.get("sensor"),
            "word": wmodel.word_for(v) if v is not None else None, "cells": cells}


def _event_text(ev: store.FrigateUserEvent, lang: str) -> str:
    return f"{query._camera_text(ev.camera, ev.place, query._camera_cards(), lang)} {query._stamp(ev.start_ts)}"


def open_events_in_episode(username: str, episode_id: int) -> list[store.FrigateUserEvent]:
    """The events of one episode this person can still answer «Var det deg?» about."""
    user = _user(username)
    if not user:
        return []
    mine = _refs(user)
    try:
        evs = store.episode_events(int(episode_id))
    except Exception:
        return []
    return [ev for ev in evs if _askable(ev, mine, "chat") and not _closed(ev.event_id, username)]


def verify(username: str, *, event_id: str | None, episode_id, verdict: str, lang: str,
           about: str | None = None) -> str:
    """The chat channel (camera tool action='verify'): the user's own answer for ONE event or episode. An episode with
    several open events is never answered as a block — Kåre is told to ask which (one at a time). J22: `about` = admin's
    «it was <member>» (only «yes»; one event); the reply says exactly what was stored."""
    if verdict not in ("yes", "no", "private", "unsure") or not (event_id or episode_id):
        return t("fv_need_target", lang)
    if about and about != username:
        if verdict != "yes":
            return t("fv_about_yes_only", lang)
        if not event_id:
            try:
                people = [e for e in store.episode_events(int(episode_id)) if e.label == "person"]
            except (TypeError, ValueError):
                return t("fv_need_target", lang)
            if len(people) != 1:
                items = "\n".join(f"- event_id {e.event_id}: {_event_text(e, lang)}" for e in people)
                return t("fv_which", lang, items=items) if people else t("fv_not_question", lang)
            event_id = people[0].event_id
        res = answer_about(username, about, str(event_id), channel="chat")
        return stored_text(username, str(event_id), "yes", res, lang, about=about if res.get("ok") else None)
    user = _user(username) if username else None
    if not user or not user.get("frigate_tracking", True) or not query.names_allowed(username):
        return t("fv_not_allowed", lang)
    if not event_id:
        try:
            open_evs = open_events_in_episode(username, int(episode_id))
        except (TypeError, ValueError):
            return t("fv_need_target", lang)
        if not open_evs:
            return t("fv_not_question", lang)
        if len(open_evs) > 1:
            items = "\n".join(f"- event_id {ev.event_id}: {_event_text(ev, lang)}" for ev in open_evs)
            return t("fv_which", lang, items=items)
        event_id = open_evs[0].event_id
    res = answer(username, str(event_id), verdict, channel="chat", verbatim=True)
    return stored_text(username, str(event_id), verdict, res, lang)


def _ts(v) -> float | None:
    p = wmodel.parse_ts(v) if isinstance(v, str) else None
    return p.timestamp() if p else (float(v) if isinstance(v, (int, float)) else None)


def history(username: str, limit: int = 20) -> list[dict]:
    """J18b: this person's own «Var det deg?» answers, newest first — [{event_id, camera, ts, answer, answered_ts}];
    camera + time only, like the questions (no image, no name). [] for an unknown or opted-out person (fail closed)."""
    user = _user(username) if username else None
    if not user or not user.get("frigate_tracking", True):
        return []
    st = weights.get_store()
    out = []
    for kind, key, scope in st.subjects("event"):
        if scope != username:
            continue
        rows = st.rows((kind, key, scope))
        mine = [a for a in rows["answers"] if a.get("by_user") == username]
        skip = [e for e in rows["evidence"] if str(e.get("pointer") or "").startswith(
            (f"skip:{username}:", f"unsure:{username}:"))]
        last = max([*mine, *skip], key=lambda r: _ts(r.get("ts")) or 0, default=None)
        if last is None:
            continue
        try:
            ev = store.event_by_id(key)
        except Exception:
            ev = None
        out.append({"event_id": key, "camera": ev.camera if ev else "", "ts": ev.start_ts if ev else None,
                    "answer": last.get("verdict") if last in mine else str(last.get("pointer")).split(":", 1)[0],
                    "answered_ts": _ts(last.get("ts"))})
    out.sort(key=lambda r: -(r["answered_ts"] or 0))
    return out[:limit]


# ── J24: who may load an event's picture (/api/frigate_snapshot) ───────────────────────────────────────────────


def may_see_snapshot(username: str, role: str | None, event_id: str) -> bool:
    """Admin: every picture (the house is never blind; an untracked event simply never carries a name). A normal user:
    only the events of their own «Var det deg?» questions — open in the GUI or chat, or answered by them — and never an
    event holding an opted-out (untracked) person. Tracking off → their lists are empty → nothing. Fail closed."""
    if role == "admin":
        return True
    if not username or not event_id:
        return False
    try:
        ev = store.event_by_id(event_id)
    except Exception as e:
        logger.warning("[FRIGATE_Q] snapshot access lookup failed: %s", e)
        return False
    if ev is None or ev.person_kind == "untracked":
        return False
    try:
        own = {q["event_id"] for ch in CHANNELS for q in candidates(username, ch)}
        if event_id in own:
            return True
        return any(h["event_id"] == event_id for h in history(username, limit=10_000))
    except Exception as e:
        logger.warning("[FRIGATE_Q] snapshot access check failed: %s", e)
        return False


# ── J22: admin may verify any household member with tracking on; the reply says exactly what was stored ─────────

ADMIN_WEIGHT_VERBATIM = False  # admin's word about someone = one human confirmation at VAGUE_WEIGHT; the person's own = 1.0


def answer_about(admin: str, member: str, event_id: str, channel: str = "chat") -> dict:
    """Admin's «it was <member>» for one person event. Refused (nothing stored) when the caller is not admin, the member
    is unknown or has turned tracking off, the member already answered themselves, or the event is not a person event.
    Stored on the event (that name, check.human {by: admin, about: member}) and in the member's weight subject by=admin
    (weaker than the member's own); the sensors that voted learn from it at the same lower weight."""
    if channel not in CHANNELS or not settings.enabled():
        return {"ok": False, "error": "off"}
    caller = _user(admin)
    if not caller or caller.get("role") != "admin":
        return {"ok": False, "error": "not_admin"}
    user = _user(member) or _member_by_name(member)
    if not user:
        return {"ok": False, "error": "unknown_member"}
    member = user["username"]
    if not user.get("frigate_tracking", True) or not query.names_allowed(member):
        return {"ok": False, "error": "tracking_off"}  # fail closed: never named, never stored
    try:
        ev = store.event_by_id(event_id)
    except Exception:
        ev = None
    if not ev or ev.label != "person":
        return {"ok": False, "error": "not_a_question"}
    if _own_answered(event_id, member):
        return {"ok": False, "error": "own_answer"}
    ctx = f"frigate:{event_id}"
    weights.answer(("event", event_id, member), by_user=admin, verdict="yes", verbatim=ADMIN_WEIGHT_VERBATIM,
                   context=ctx, pointer=f"{ctx}:by:{admin}")
    mine, ir, n = _refs(user), bool((ev.check or {}).get("ir")), 0
    for sensor, ref in _votes(ev):
        if not ref:
            continue
        weights.answer(sensor_subject(sensor, ev.camera, ir), by_user=admin, verdict="yes" if ref in mine else "no",
                       verbatim=ADMIN_WEIGHT_VERBATIM, context=ctx, pointer=f"{ctx}:{sensor}:by:{admin}")
        n += 1
    saved = weights.flush_ok()
    saved = store.set_human_answer(event_id, admin, "yes", channel, about=member) and saved
    return {"ok": True, "sensors": n, "about": member, "saved": saved}


def _member_by_name(name: str) -> dict | None:
    """A household member by display name or Frigate face name (casefolded) — only one unique match, else None."""
    try:
        users = user_store.list_users()
    except Exception:
        return None
    key = (name or "").strip().casefold()
    hits = [u for u in users if key and key in {n.casefold() for n in (u.get("username"), u.get("display_name"),
                                                                        *(u.get("frigate_faces") or [])) if n}]
    return _user(hits[0]["username"]) if len(hits) == 1 else None


def stored_text(username: str, event_id: str, verdict: str, res: dict, lang: str, about: str | None = None,
                channel: str = "chat") -> str:
    """J22 parts 2 + 4: the one receipt (kaare_core.receipts) of exactly what was stored — the answer, the event, which
    name the event has now (or none), who, channel, when — plus the «repeat it» sentence. Nothing stored → why, no receipt."""
    if not res.get("ok"):
        err = res.get("error")
        if err in ("tracking_off", "unknown_member"):
            return t("fv_about_refused", lang)
        if err == "own_answer":
            return t("fv_about_own_answer", lang)
        if err == "not_admin":
            return t("fv_about_not_admin", lang)
        return t("fv_not_allowed", lang) if err in ("not_allowed", "off") else t("fv_not_question", lang)
    if res.get("saved") is False:  # N39: never «Lagret» when the save failed (feedback_rid_and_receipts, thread 9)
        return t("rcpt_not_saved", lang)
    try:
        ev = store.event_by_id(event_id)
    except Exception:
        ev = None
    where = _event_text(ev, lang) if ev else event_id
    sensors = t("rcpt_cam_sensors", lang, n=res.get("sensors", 0))
    if about:
        what = f"{t('rcpt_cam_about', lang, member=about, admin=username)}; {sensors}"
    else:
        answer_words = t(f"fv_answer_{verdict}", lang) if verdict in ("yes", "no", "private", "unsure") else verdict
        name = (t("fv_stored_name", lang, name=username) if verdict == "yes" else
                t("fv_stored_private", lang) if verdict == "private" else
                t("fv_stored_unsure", lang) if verdict == "unsure" else t("fv_stored_no_name", lang))
        what = f"{t('rcpt_cam_what', lang, answer=answer_words, name=name)}; {sensors}"
    receipt = receipts.build("camera_answer", ref=event_id, what=what, where=where, who=username, channel=channel,
                             lang=lang)
    return f"{receipt}\n{receipts.instruction(lang)}"


def admin_candidates(admin: str, now: float | None = None, limit: int = 10) -> list[dict]:
    """J22: person events of the last week nobody has settled (no human answer, not confirmed) that admin may name —
    camera + time only, newest first. [] for a non-admin or when the Frigate user is off."""
    caller = _user(admin)
    if not caller or caller.get("role") != "admin" or not settings.enabled():
        return []
    now = time.time() if now is None else now
    try:
        evs = store.events_between(now - WINDOW_DAYS * 86400, now)
    except Exception:
        return []
    out = [{"event_id": e.event_id, "camera": e.camera, "ts": e.start_ts} for e in evs
           if e.label == "person" and e.person_kind != "untracked" and not (e.check or {}).get("human")
           and e.claim_status != "confirmed"]
    return sorted(out, key=lambda q: -q["ts"])[:limit]


def nameable_members() -> list[dict]:
    """J22: household members admin may name on an event — tracking on only (fail closed: [] on a lookup error)."""
    try:
        users = user_store.list_users()
    except Exception:
        return []
    return [{"username": u["username"], "name": u.get("display_name") or u["username"]} for u in users
            if u.get("frigate_tracking", True) and query.names_allowed(u["username"])]
