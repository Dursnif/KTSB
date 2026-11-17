"""The Frigate user's admin API (P144-s05): face classification list + attribute profiles.

Shown in Integrations → Frigate → Face classification. Every route is admin-only (household-level setting) and fails
closed. P144-s06a adds the shadow pipe's switch + settings (`/pipe`, configs/frigate_user.yaml); P144-s11 the episode
view (`/episodes*`, read through `frigate_user.store` only). The list joins Frigate's face library × the classification in configs/frigate_faces.yaml × the KTSB user a
face is linked to (users.db, s02 — read-only here, edited on the Users page).
"""

import asyncio
import logging
import re
import time
from datetime import datetime
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from adapters import frigate_adapter
from kaare_core.audit import audit_log as _audit
from kaare_core.config import get_local_tz, get_service
from kaare_core.domain import frigate_store
from kaare_core.frigate_user import (
    conclude,
    face_votes,
    faces,
    frames,
    labels,
    modules,
    places_learned,
    query,
    questions,
    thing_links,
    thing_names,
    vehicles,
)
from kaare_core.frigate_user import settings as pipe_settings
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import frigate_ltm, theme_questions
from kaare_core.tools.i18n import get_lang
from kaare_core.users import store
from kaare_core.users.auth import require_admin, require_auth
from kaare_core.weights import model as wmodel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/frigate_user")


def _frigate_base() -> str:
    return (get_service("frigate", "url") or "").rstrip("/")


def _timeout() -> float:
    try:
        return float(get_service("frigate", "timeout") or 10)
    except (TypeError, ValueError):
        return 10.0


async def _fetch_library_json(base: str):
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(f"{base}/api/faces")
        r.raise_for_status()
        return r.json()


async def _library() -> tuple[dict[str, list[str]], str]:
    """({face name: [image files]}, error). error = "" | "not_configured" | "unreachable". Frigate's
    «train» folder is not a person and is left out."""
    base = _frigate_base()
    if not base:
        return {}, "not_configured"
    try:
        data = await _fetch_library_json(base)
    except Exception as e:
        log.info("frigate faces: library unreachable: %s", type(e).__name__)
        return {}, "unreachable"
    if not isinstance(data, dict):
        return {}, ""
    return {
        n: [f for f in (files if isinstance(files, list) else []) if isinstance(f, str)]
        for n, files in data.items()
        if isinstance(n, str) and n != faces.FRIGATE_TRAIN_FOLDER
    }, ""


async def _fetch_image(name: str, file: str) -> tuple[bytes, str]:
    url = f"{_frigate_base()}/clips/faces/{quote(name, safe='')}/{quote(file, safe='')}"
    async with httpx.AsyncClient(timeout=_timeout()) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "image/webp")


def _linked_user(name: str) -> dict | None:
    try:
        username = store.get_username_for_frigate_face(name)
        if not username:
            return None
        user = store.get_user(username) or {}
        return {"username": username, "display_name": user.get("display_name") or username}
    except Exception as e:
        log.warning("frigate faces: could not read the user link: %s", e)
        return None


def _row(name: str, library: dict[str, list[str]]) -> dict:
    return {
        "name": name,
        "class": faces.classify(name),
        "attributes": faces.attribute_profile(name),
        "in_library": name in library,
        "images": len(library.get(name, [])),
        "linked_user": _linked_user(name),
    }


def _known_name(name: str, library: dict) -> str | None:
    """The face's spelling in Frigate's library or the config, or None."""
    wanted = name.casefold()
    for n in (*library, *faces.configured_faces()):
        if n.casefold() == wanted:
            return n
    return None


@router.get("/faces")
async def api_list_faces(_u: dict = Depends(require_admin)):
    library, error = await _library()
    names = sorted({*library, *faces.configured_faces()}, key=str.casefold)
    rows = [_row(n, library) for n in names]
    return {
        "faces": rows,
        "unclassified_count": len(faces.unclassified_faces(library)),
        "unclassified_name_days": faces.unclassified_name_days(),
        "classes": list(faces.CLASSES),
        "attribute_values": {k: list(v) for k, v in faces.ATTRIBUTES.items()},
        "error": error,
    }


class FaceUpdate(BaseModel):
    cls: str = Field(alias="class")  # `class` is a Python keyword
    attributes: dict = {}


@router.put("/faces/{name}")
async def api_put_face(name: str, req: FaceUpdate, u: dict = Depends(require_admin)):
    try:
        faces.validate_name(name)
        faces.validate(req.cls, req.attributes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    library, _error = await _library()
    known = _known_name(name, library)
    if not known:
        raise HTTPException(status_code=404, detail="Face not in Frigate's face library.")
    try:
        faces.set_face(known, req.cls, req.attributes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        _audit("admin_user_action", u.get("sub", "?"), f"frigate_face_class face={known!r} class={req.cls}")
    except Exception:
        pass
    return _row(known, library)


class FaceSettings(BaseModel):
    unclassified_name_days: int


@router.put("/settings")
def api_put_settings(req: FaceSettings, _u: dict = Depends(require_admin)):
    try:
        faces.set_unclassified_name_days(req.unclassified_name_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"unclassified_name_days": faces.unclassified_name_days()}


# ── vehicles (P150 6b) — the same path as faces: pseudonymous id, class, household name; never a plate text ──────────

class VehicleUpdate(BaseModel):
    cls: str = Field(alias="class")
    name: str = ""


class VehicleSkip(BaseModel):
    vehicle_id: str


class VehicleSettings(BaseModel):
    question_days: int


@router.get("/vehicles")
async def api_list_vehicles(_u: dict = Depends(require_admin)):
    """Unknown vehicles by id (with class, household name, days seen, the newest event for the picture) and the vehicles
    Frigate already knows by name. Read-only toward Frigate."""
    rows = await asyncio.to_thread(vehicles.rows)
    known = await asyncio.to_thread(fstore.known_vehicles)
    return {"vehicles": rows, "known": known, "classes": list(vehicles.CLASSES),
            "question_days": vehicles.question_days()}


@router.put("/vehicles/{vehicle_id}")
async def api_put_vehicle(vehicle_id: str, req: VehicleUpdate, u: dict = Depends(require_admin)):
    try:
        vehicles.validate(vehicle_id, req.cls, req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    rows = {r["vehicle_id"]: r for r in await asyncio.to_thread(vehicles.rows)}
    if vehicle_id not in rows:
        raise HTTPException(status_code=404, detail="No such vehicle in KTSB's camera memory.")
    try:
        await asyncio.to_thread(vehicles.set_vehicle, vehicle_id, req.cls, req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        _audit("admin_user_action", u.get("sub", "?"), f"frigate_vehicle_class vehicle={vehicle_id} class={req.cls}")
    except Exception:
        pass
    return {**rows[vehicle_id], "class": vehicles.classify(vehicle_id), "name": vehicles.name_of(vehicle_id)}


@router.get("/questions/vehicles")
async def api_vehicle_questions(_u: dict = Depends(require_admin)):
    """«Hvilket kjøretøy er dette?» — unknown vehicles seen on several days, not classified, named or skipped yet."""
    return {"questions": await asyncio.to_thread(vehicles.questions)}


@router.post("/questions/vehicles/skip")
async def api_vehicle_skip(req: VehicleSkip, _u: dict = Depends(require_admin)):
    try:
        await asyncio.to_thread(vehicles.skip, req.vehicle_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.put("/vehicles_settings")
def api_put_vehicle_settings(req: VehicleSettings, _u: dict = Depends(require_admin)):
    try:
        vehicles.set_question_days(req.question_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"question_days": vehicles.question_days()}


@router.get("/faces/{name}/thumb")
async def api_face_thumb(name: str, _u: dict = Depends(require_admin)):
    """One training image of a face, proxied from Frigate. The file is picked here from the library list, so the
    client never chooses a path. Header auth only — the GUI loads it as a blob."""
    library, _error = await _library()
    files = library.get(name) or []
    if not files:
        raise HTTPException(status_code=404, detail="No image for this face.")
    try:
        content, ctype = await _fetch_image(name, files[0])
    except Exception as e:
        raise HTTPException(status_code=502, detail="Frigate did not return the image.") from e
    if not ctype.startswith("image/"):
        raise HTTPException(status_code=502, detail="Frigate did not return an image.")
    return Response(content=content, media_type=ctype, headers={"Cache-Control": "private, max-age=300"})


# ── the Frigate user's shadow pipe: switch + settings (P144-s06a) ──────────────


@router.get("/pipe")
def api_get_pipe(_u: dict = Depends(require_admin)):
    return pipe_settings.get_settings()


@router.put("/pipe")
def api_put_pipe(req: dict, u: dict = Depends(require_admin)):
    try:
        out = pipe_settings.update(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        _audit("admin_user_action", u.get("sub", "?"), f"frigate_user_pipe keys={sorted(req)}")
    except Exception:
        pass
    return out


# ── extra face recognition (P144-s19) ─────────────────────────────────────────


@router.get("/face_votes/status")
def api_face_votes_status(_u: dict = Depends(require_admin)):
    """The second face sensor's status line: on/off, provider, `available` (online/offline/unknown), votes seen."""
    return face_votes.status()


class FaceVotesTest(BaseModel):
    rest_url: str | None = None


@router.post("/face_votes/test")
async def api_face_votes_test(req: FaceVotesTest, _u: dict = Depends(require_admin)):
    """Test button: is the provider's REST address (the one in the form, else the saved one) reachable? GET only."""
    url = req.rest_url
    if url is not None and url.strip() and not url.strip().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="rest_url must start with http:// or https://")
    return {**await face_votes.test_rest(url), "mqtt": face_votes.status()}


# ── face sensors (P144-s29b) ─────────────────────────────────────────────────

SENSOR_COUNT_DAYS = 7


def _face_sensor_counts(events) -> dict[str, dict]:
    """Per sensor over the stored checks: events it named someone on, and whether another sensor agreed or not. Counts
    only — no names leave this function."""
    out: dict[str, dict] = {}

    def bump(sensor: str, key: str) -> None:
        out.setdefault(sensor, {"votes": 0, "agree": 0, "disagree": 0})[key] += 1

    for e in events:
        check = e.check if isinstance(e.check, dict) else {}
        s = check.get("sensors") if isinstance(check.get("sensors"), dict) else {}
        rel = s.get("relation")
        if s.get("frigate") or (not s and check.get("claim") and check.get("claim_source") in (None, "frigate")):
            bump("frigate", "votes")
            if rel == "agree":
                bump("frigate", "agree")
            elif rel in ("disagree", "split"):
                bump("frigate", "disagree")
        dets = [d for d in s.get("detectors") or [] if isinstance(d, dict) and d.get("detector")]
        for d in dets:
            if not (d.get("ref") or d.get("kind") in ("untracked", "unknown")):
                continue
            bump(d["detector"], "votes")
            others = {x.get("ref") for x in dets if x is not d} | {s.get("frigate")}
            others.discard(None)
            if d.get("ref") and d["ref"] in others:
                bump(d["detector"], "agree")
            elif d.get("ref") and others:
                bump(d["detector"], "disagree")
    return out


def _face_sensor_rows(now: float | None = None) -> list[dict]:
    """The «Ansiktssensorer» card: Frigate + every detector a provider reported or admin configured, generic (no
    detector name in code). `learned` is where KTSB's weight system will show its value (None until it exists)."""
    now = time.time() if now is None else now
    cfg = pipe_settings.face_sensors()
    seen = {d["detector"]: d for d in (face_votes.status().get("detectors") or []) if d.get("detector")}
    try:
        counts = _face_sensor_counts(fstore.events_between(now - SENSOR_COUNT_DAYS * 86400, now))
    except Exception as e:
        log.debug("face sensor counts unavailable: %s", type(e).__name__)
        counts = {}
    zero = {"votes": 0, "agree": 0, "disagree": 0}
    rows = []
    for sensor in ["frigate", *sorted(set(seen) | set(cfg.get("detectors") or {}))]:
        entry = cfg["frigate"] if sensor == "frigate" else (cfg.get("detectors") or {}).get(sensor) or {}
        w = face_votes.sensor_weight(sensor, None, None)
        rows.append({"sensor": sensor, "provider": None if sensor == "frigate" else "double_take",
                     "label": entry.get("label") or "", "mode": entry.get("mode") or "auto",
                     "trust": entry.get("trust") or "uncertain", "word": w["word"], "learned": w["learned"],
                     "learned_word": w.get("learned_word"), "learned_goal": wmodel.LEARNED_GOAL.get("sensor"),
                     "acting": w.get("acting", False), "auto_learning": entry.get("auto_learning", True) is not False,
                     "last_seen_ts": (seen.get(sensor) or {}).get("last_seen_ts"),
                     "counts": counts.get(sensor, dict(zero))})
    return rows


# ── «Var det deg?» — the person's own question list (P144 #9, P149 phase 2, GUI list G17) ─────────────────


class QuestionAnswer(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)  # the camera event, or a theme's chain id (kind «theme»)
    answer: str = Field(pattern="^(yes|no|private|unsure|skip)$")  # N39 c: «unsure» = «vet ikke»
    kind: str = Field(default="camera", pattern="^(camera|theme)$")


@router.get("/questions/mine/history")
def api_my_question_history(payload: dict = Depends(require_auth)):
    """J18b: the logged-in person's own «Var det deg?» answers (camera + time + answer; never an image, never anyone
    else's — admin sees only their own here too). [] for an opted-out person."""
    cards = _camera_cards()
    return {"answered": [{**h, "camera": _cam_name(h["camera"], cards) if h["camera"] else ""}
                         for h in questions.history(payload["sub"])]}


class WhoAnswer(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    member: str = Field(min_length=1, max_length=80)


@router.get("/questions/who")
def api_admin_who_questions(u: dict = Depends(require_admin)):
    """J22: person events nobody has settled that admin may name, and the members admin may name (tracking on only)."""
    cards = _camera_cards()
    qs = [{**q, "camera": _cam_name(q["camera"], cards)} for q in questions.admin_candidates(u.get("sub", ""))]
    return {"questions": qs, "members": questions.nameable_members()}


@router.post("/questions/who/answer")
def api_admin_who_answer(req: WhoAnswer, u: dict = Depends(require_admin)):
    """J22: admin's «det var <member>» from the card — refused (400) for a member with tracking off or who answered."""
    res = questions.answer_about(u.get("sub", ""), req.member, req.event_id, channel="gui")
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "refused")
    if res.get("saved") is False:  # N39: never report success when the save failed
        raise HTTPException(status_code=503, detail="not_saved")
    return {"ok": True, "member": res.get("about")}


@router.get("/questions/mine")
def api_my_questions(payload: dict = Depends(require_auth)):
    """The logged-in person's own open questions — never anyone else's (admin sees none of them). [] when the Frigate
    user is off or the person opted out. No image, no name, no detail: camera + time only."""
    username = payload["sub"]
    cards = _camera_cards()
    items = [{"kind": "camera", "id": q["event_id"], "event_id": q["event_id"], "camera": _cam_name(q["camera"], cards),
              "ts": q["ts"]} for q in questions.candidates(username, "gui")]
    # J9: the night meeting's parked themes about this user, in the same list and the same budget (D12)
    items += [{"kind": "theme", "id": th["chain"], "text": th["text"]} for th in theme_questions.candidates(username)]
    return {"questions": items[: questions.budget(username, "gui")]}


@router.post("/questions/answer")
def api_answer_question(req: QuestionAnswer, payload: dict = Depends(require_auth)):
    """Answer one of the caller's own questions. Only an open question for this caller is accepted (fail closed)."""
    if req.kind == "theme":
        res = theme_questions.answer(payload["sub"], req.event_id, req.answer)
    else:
        res = questions.answer(payload["sub"], req.event_id, req.answer, channel="gui")
    if not res.get("ok"):
        raise HTTPException(status_code=404 if res.get("error") == "not_a_question" else 403,
                            detail=res.get("error") or "refused")
    if res.get("saved") is False:  # N39: never report success when the save failed (the GUI shows «not saved»)
        raise HTTPException(status_code=503, detail="not_saved")
    return {"ok": True}


# ── «Er dette det samme?» — stationary things on two cameras (R139, J12; a house question → admin) ─────────


class ThingAnswer(BaseModel):
    pair: str = Field(min_length=3, max_length=400)
    answer: str = Field(pattern="^(yes|no|skip)$")


@router.get("/questions/things")
def api_thing_questions(_u: dict = Depends(require_admin)):
    """Pairs code proposes as one thing (two cameras), with the newest event of each for the two images."""
    now = time.time()
    evs = fstore.events_between(now - thing_links.WINDOW_DAYS * 86400, now)
    return {"questions": thing_links.questions_for_admin(evs, is_admin=True)}


@router.get("/questions/things/answered")
def api_thing_history(_u: dict = Depends(require_admin)):
    """J18b: pairs admin answered or skipped, newest first, with the newest thing event of each key for the images."""
    now = time.time()
    evs = fstore.events_between(now - thing_links.WINDOW_DAYS * 86400, now)
    cards = _camera_cards()
    return {"answered": [{**h, "cameras": [_cam_name(c, cards) for c in h["cameras"]]} for h in thing_links.history(evs)]}


@router.post("/questions/things/answer")
def api_thing_answer(req: ThingAnswer, u: dict = Depends(require_admin)):
    res = thing_links.answer(u.get("sub", "admin"), req.pair, req.answer)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error") or "refused")
    if res.get("saved") is False:  # N39: never report success when the save failed
        raise HTTPException(status_code=503, detail="not_saved")
    return {"ok": True}


# ── «Hva er her?» — spots where persons appear/disappear mid-view (J18; a house question → admin) ───────────────


class PlaceAnswer(BaseModel):
    id: str = Field(min_length=5, max_length=200, pattern=r"^[^|]+\|\d,\d$")
    answer: str = Field(pattern="^(named|not_a_place|skip)$")
    name: str | None = Field(default=None, max_length=fstore.SPOT_NAME_MAX)


@router.get("/questions/places")
def api_place_questions(_u: dict = Depends(require_admin)):
    """Spots code has seen often (anonymous counts) and nobody has answered yet, with the newest event for the image."""
    now = time.time()
    evs = fstore.events_between(now - places_learned.WINDOW_DAYS * 86400, now)
    cards = _camera_cards()
    qs = places_learned.questions(evs, fstore.spot_answers())
    return {"questions": [{**q, "camera_name": _cam_name(q["camera"], cards)} for q in qs]}


@router.get("/questions/places/answered")
def api_place_history(_u: dict = Depends(require_admin)):
    """J18b: registered places and other answers, newest first (no image here — see the image route)."""
    cards = _camera_cards()
    return {"answered": [{**h, "camera_name": _cam_name(h["camera"], cards)}
                         for h in places_learned.history(fstore.spot_answers())]}


_CAMERA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@router.get("/questions/places/image/{camera}/{cell}")
async def api_place_image(camera: str, cell: str, _u: dict = Depends(require_admin)):
    """J18b: one recording frame of the camera by day with NO active track (never a person, never an opted-out one) —
    the place's image. 404 when there is no such moment or no recording (the card then shows text). Fail closed: only a
    camera the store has events for."""
    if not _CAMERA_ID.fullmatch(camera) or not re.fullmatch(r"\d,\d", cell):
        raise HTTPException(status_code=404, detail="no_image")
    now = time.time()
    evs = await asyncio.to_thread(fstore.events_between, now - (places_learned.EMPTY_DAYS + 2) * 86400, now, None, camera)
    if not evs:
        raise HTTPException(status_code=404, detail="no_image")
    ts = places_learned.empty_moment(evs, camera, now, get_local_tz())
    if ts is None:
        raise HTTPException(status_code=404, detail="no_image")
    try:
        jpeg = await frigate_adapter.fetch_recording_snapshot(camera, ts, height=frames.FRAME_HEIGHT)
    except Exception:
        jpeg = None
    if not jpeg:
        raise HTTPException(status_code=404, detail="no_image")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/questions/places/answer")
def api_place_answer(req: PlaceAnswer, u: dict = Depends(require_admin)):
    camera, cell = req.id.rsplit("|", 1)
    if not fstore.add_spot_answer(camera, cell, req.answer, req.name, u.get("sub", "admin")):
        raise HTTPException(status_code=400, detail="refused")
    return {"ok": True}


# ── «Hva er dette?» — still things and Frigate's misreads (J20; a house question → admin) ───────────────────────


class WhatAnswer(BaseModel):
    id: str = Field(min_length=5, max_length=120, pattern=r"^[A-Za-z0-9_-]{1,64}\|[a-z_]{1,40}\|\d,\d$")
    answer: str = Field(pattern="^(named|not_label|wrong|skip)$")
    name: str | None = Field(default=None, max_length=thing_names.NAME_MAX)


def _what_rows(rows: list[dict]) -> list[dict]:
    cards, lang = _camera_cards(), get_lang("global")
    return [{**r, "camera_name": _cam_name(r["camera"], cards), "label_name": labels.display(r["label"], lang)} for r in rows]


@router.get("/questions/what")
def api_what_questions(_u: dict = Depends(require_admin)):
    """Still things code has seen often, and still «animals» (misreads), that nobody has answered yet."""
    now = time.time()
    evs = fstore.events_between(now - thing_names.WINDOW_DAYS * 86400, now)
    return {"questions": _what_rows(thing_names.questions(evs, thing_names.answers(), now))}


@router.get("/questions/what/answered")
def api_what_history(_u: dict = Depends(require_admin)):
    return {"answered": _what_rows(thing_names.history(thing_names.answers()))}


@router.post("/questions/what/answer")
def api_what_answer(req: WhatAnswer, u: dict = Depends(require_admin)):
    res = thing_names.answer(u.get("sub", "admin"), req.id, req.answer, req.name)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail="refused")
    if res.get("saved") is False:  # N39: never report success when the save failed
        raise HTTPException(status_code=503, detail="not_saved")
    return {"ok": True}


@router.get("/reads")
def api_frigate_model_reads(before: float | None = None, limit: int = 40, _u=Depends(require_admin)):
    """P144 6e «Siste deteksjoner»: the image model's own single-image reads (L3 per event, module 4 per frame), newest first —
    shown, never re-run."""
    rows, more = fstore.recent_model_reads(before=before, limit=limit)
    cards = _camera_cards()
    return {"reads": [{**r, "camera_name": _cam_name(r["camera"], cards)} for r in rows], "has_more": more}


@router.get("/modules")
def api_frigate_modules(_u: dict = Depends(require_admin)):
    """J21 5e: the module panel — every module's setting, state, needs and why; only modules.ACTING switch anything."""
    st = modules.status()
    rows = [{"n": m, "setting": st[m]["setting"], "on": st[m]["on"], "why": st[m]["why"],
             "needs": ["|".join(map(str, n)) if isinstance(n, tuple) else str(n) for n in modules.NEEDS[m]],
             "needs_missing": [str(x) for x in st[m]["needs_missing"]],
             # P144 6e (2026-11-13): no own still analysis — module 1 is replaced by L3 per event + module 4
             "switchable": m in modules.ACTING, "replaced": m == 1}
            for m in range(8)]
    return {"modules": rows, "warnings": st["warnings"]}


@router.get("/face_sensors")
def api_face_sensors(_u: dict = Depends(require_admin)):
    """Rows for Integrations → Frigate → «Ansiktssensorer» (P144-s29b). Admin only; counts without names."""
    return {"sensors": _face_sensor_rows(), "count_days": SENSOR_COUNT_DAYS,
            "auto_learning": pipe_settings.face_sensors().get("auto_learning", True) is not False}


# ── episode view (P144-s11) ───────────────────────────────────────────────────


def _camera_cards() -> dict:
    """The camera cards (Integrations → Frigate) — the display name per camera, same source as the route text."""
    try:
        cams = frigate_store.camera_config().get("cameras") or {}
        return cams if isinstance(cams, dict) else {}
    except Exception:
        return {}


def _cam_name(camera: str, cards: dict) -> str:
    """J24: card → services.yaml camera_names → Frigate's friendly_name → raw id (never a raw id when a name exists)."""
    return frigate_adapter.camera_display_name(camera, cards)


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, get_local_tz())


def _person_name(ref: str, kind: str) -> str:
    """A household member is stored by username; show the display name."""
    if kind != "household":
        return ref
    try:
        return (store.get_user(ref) or {}).get("display_name") or ref
    except Exception:
        return ref


def _persons(events: list, hidden: set[str]) -> tuple[list[dict], int]:
    """Named persons as the storage gate stored them (best claim status per name, first seen first) + how many person
    events carry no name (unknown, untracked, rejected — or hidden now)."""
    named: dict[str, dict] = {}
    unnamed = 0
    for e in events:
        if e.label != "person":
            continue
        if not e.person_ref or e.person_ref in hidden:
            unnamed += 1
            continue
        cur = named.get(e.person_ref)
        if cur is None or (e.claim_status == "confirmed" and cur["claim_status"] != "confirmed"):
            named[e.person_ref] = {"name": _person_name(e.person_ref, e.person_kind),
                                   "claim_status": e.claim_status, "kind": e.person_kind}
    return list(named.values()), unnamed


def _frame_list(ep: fstore.Episode, cards: dict) -> list[dict]:
    """The listed frames; `index` = position (the GUI's URL), `file` = the stored file number (P144-s24: the entry's
    `file`, else its position as before s24)."""
    out = []
    stored = set(fstore.stored_frame_indexes(ep.episode_id))
    for pos, f in enumerate((ep.scene or {}).get("frames") or []):
        try:
            camera, ts, file = str(f["camera"]), float(f["ts"]), int(f.get("file", pos))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        out.append({"index": len(out), "file": file, "camera": camera, "camera_name": _cam_name(camera, cards),
                    "ts": ts, "clock": _local(ts).strftime("%H:%M:%S"), "stored": file in stored})
    return out


def _check_summary(check: dict, hidden: set[str] | None = None) -> dict:
    layers = []
    for key in ("l0", "l1", "l3"):
        val = check.get(key)
        votes = val if isinstance(val, list) else [val] if isinstance(val, dict) else []
        layers.extend({k: v.get(k) for k in ("layer", "verdict", "strong", "reason")} for v in votes if isinstance(v, dict))
    return {"decision": check.get("decision"), "layers": layers, "doubt": check.get("doubt"),
            "storage_gate": check.get("storage_gate"), "sensors": _sensors_summary(check, hidden or set())}


def _sensors_summary(check: dict, hidden: set[str]) -> dict | None:
    """Both face sensors for the episode detail (P144-s19): «Frigate: X · Double Take: Y → word». Display names; a
    person who has since opted out is shown as untracked, never by name."""
    s = check.get("sensors")
    if not isinstance(s, dict) or s.get("relation") not in ("agree", "disagree", "split", "double_take_only"):
        return None
    folded = {h.casefold() for h in hidden}

    def side(ref, kind) -> tuple[str | None, str | None]:
        if ref and ref.casefold() in folded:
            return None, "untracked"
        return (_person_name(ref, "household") if ref else None), kind

    frigate, frigate_kind = side(s.get("frigate"), s.get("frigate_kind"))
    second, second_kind = side(s.get("double_take"), s.get("double_take_kind"))
    lean, _ = side(s.get("lean"), None)
    detectors = []
    for d in s.get("detectors") or []:  # P144-s29: one vote per detector, by the user's label; never a raw score
        if isinstance(d, dict):
            ref, kind = side(d.get("ref"), d.get("kind"))
            detectors.append({"detector": d.get("detector"), "label": d.get("label") or d.get("detector"),
                              "name": ref, "kind": kind, "split": bool(d.get("split")),
                              "discarded": int(d.get("discarded") or 0), "weight": d.get("weight")})
    return {"detectors": detectors, "provider": s.get("provider"), "provider_label": query.PROVIDER_LABELS.get(s.get("provider") or "", ""),
            "relation": s.get("relation"), "frigate": frigate, "frigate_kind": frigate_kind,
            "frigate_rejected": bool(s.get("frigate_rejected")), "double_take": second, "double_take_kind": second_kind,
            "confidence": s.get("double_take_confidence"), "lean": lean if s.get("lean") else None,
            "review": bool(check.get("review"))}


def _concluded_by(c: dict) -> str | None:
    """Who concluded a done episode. Conclusions from before s06e have no `by` (45 of 47 on 2026-10-28) — they were all
    the model's; a code conclusion carries role «code» (P144-s14: the list showed them as «venter på konklusjon»)."""
    if c.get("status") != "done":
        return None
    return c.get("by") or ("code" if c.get("role") == "code" else "model")


def _episode_item(ep: fstore.Episode, events: list, cards: dict, hidden: set[str]) -> dict:
    c = ep.conclusion or {}
    done = c.get("status") == "done"
    start, end = _local(ep.start_ts), _local(ep.end_ts)
    persons, unnamed = _persons(events, hidden)
    order = list(dict.fromkeys(e.camera for e in events)) or ep.cameras
    return {
        "id": ep.episode_id,
        "status": ep.status,
        "start_ts": ep.start_ts,
        "end_ts": ep.end_ts,
        "date": start.strftime("%Y-%m-%d"),
        "start": start.strftime("%H:%M:%S"),
        "end": end.strftime("%H:%M:%S"),
        "duration_s": max(0, round(ep.end_ts - ep.start_ts)),
        "cameras": [{"camera": cam, "name": _cam_name(cam, cards)} for cam in order],
        "labels": ep.labels,
        "event_count": ep.event_count,
        "persons": persons,
        "unnamed_persons": unnamed,
        "conclusion_status": c.get("status") or ("pending" if ep.status == "closed" else "open"),
        "by": _concluded_by(c),
        "false_detection": bool(c.get("false_detection")) if done else False,
        "summary": c.get("summary") or "" if done else "",
        "unusual": c.get("unusual") if done else None,
        "unusual_suppressed": c.get("unusual_suppressed") if done else None,
        "urgent": bool(c.get("urgent")),
        "frame_count": len((ep.scene or {}).get("frames") or []),
        "stuck": conclude.stuck_info(ep, time.time()),  # N40: only what code knows; None = not stuck
    }


def _hidden(eps) -> set[str]:
    refs = {e.person_ref for _ep, evs in eps for e in evs}
    for _ep, evs in eps:  # P144-s19: the second sensor's names are names too
        for e in evs:
            s = (e.check or {}).get("sensors") if isinstance(e.check, dict) else None
            if isinstance(s, dict):
                refs |= {s.get("frigate"), s.get("double_take"), s.get("lean")}
                refs |= {d.get("ref") for d in s.get("detectors") or [] if isinstance(d, dict)}  # P144-s29
    return fstore.hidden_names({r for r in refs if r})


def _list_view(limit: int, before: float | None) -> dict:
    page = fstore.episodes_page(limit + 1, before)
    more, page = len(page) > limit, page[:limit]
    cards, hidden = _camera_cards(), _hidden(page)
    items = [fstore.scrub(_episode_item(ep, evs, cards, hidden), hidden) for ep, evs in page]
    try:
        enabled = bool(pipe_settings.get_settings().get("enabled"))
    except Exception:
        enabled = False
    try:
        store_status = fstore.write_status()
    except Exception:
        store_status = {"ok": True}
    return {"episodes": items, "has_more": more, "enabled": enabled, "store": store_status}


def _detail_view(episode_id: int) -> dict | None:
    ep = fstore.episode(episode_id)
    if ep is None:
        return None
    events = fstore.episode_events(episode_id)
    cards, hidden = _camera_cards(), _hidden([(ep, events)])
    scene = {k: v for k, v in (ep.scene or {}).items() if k not in ("frames", "claims")} or None
    out = {
        **_episode_item(ep, events, cards, hidden),
        "route": ep.route or "",
        "scene": scene,
        "conclusion": ep.conclusion,
        "frames": _frame_list(ep, cards),
        "story": conclude.episode_story(ep.scene, cards, get_lang()),
        "history_count": len((ep.conclusion or {}).get("history") or []),
        "events": [
            {
                "event_id": e.event_id,
                "ts": e.start_ts,
                "clock": _local(e.active_start if e.active_start is not None else e.start_ts).strftime("%H:%M:%S"),
                "camera": e.camera,
                "camera_name": _cam_name(e.camera, cards),
                "label": e.label,
                "person": _person_name(e.person_ref, e.person_kind)
                if e.person_ref and e.person_ref not in hidden else None,
                "person_kind": e.person_kind,
                "claim_status": e.claim_status,
                "certainty": query.certainty(e.check, e.claim_status) if e.person_ref else None,
                "check": _check_summary(e.check or {}, hidden),
            }
            for e in events
        ],
    }
    return fstore.scrub(out, hidden)


@router.get("/episodes")
async def api_list_episodes(
    limit: int = Query(50, ge=1, le=200), before: float | None = None, _u: dict = Depends(require_admin)
):
    try:
        return await asyncio.to_thread(_list_view, limit, before)
    except Exception as e:
        log.warning("frigate episodes: list failed: %s", e)
        raise HTTPException(status_code=500, detail="episodes_unavailable") from e


@router.get("/episodes/{episode_id}")
async def api_get_episode(episode_id: int, _u: dict = Depends(require_admin)):
    try:
        out = await asyncio.to_thread(_detail_view, episode_id)
    except Exception as e:
        log.warning("frigate episodes: detail %s failed: %s", episode_id, e)
        raise HTTPException(status_code=500, detail="episodes_unavailable") from e
    if out is None:
        raise HTTPException(status_code=404, detail="no_episode")
    return out


@router.get("/episodes/{episode_id}/frames/{index}")
async def api_episode_frame(episode_id: int, index: int, _u: dict = Depends(require_admin)):
    """One of the frames the scene step showed the model: the copy stored on AI-pc first, else Frigate's recording at
    that time. Header auth only — the GUI loads it as a blob. 404 `recording_gone` when Frigate no longer has it."""
    ep = await asyncio.to_thread(fstore.episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="no_episode")
    listed = await asyncio.to_thread(_frame_list, ep, {})
    if not 0 <= index < len(listed):
        raise HTTPException(status_code=404, detail="no_frame")
    f = listed[index]
    if f["stored"]:
        jpeg = await asyncio.to_thread(fstore.read_episode_frame, episode_id, f["file"])
        if jpeg:
            return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
    try:
        jpeg = await frigate_adapter.fetch_recording_snapshot(f["camera"], f["ts"], height=frames.FRAME_HEIGHT)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="recording_gone") from e
        raise HTTPException(status_code=502, detail="frigate_error") from e
    except Exception as e:
        log.info("frigate episodes: frame %s/%s unavailable: %s", episode_id, index, type(e).__name__)
        raise HTTPException(status_code=502, detail="frigate_unreachable") from e
    if not jpeg:
        raise HTTPException(status_code=404, detail="recording_gone")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/episodes/{episode_id}/after/{n}")
async def api_episode_after_frame(episode_id: int, n: int, _u: dict = Depends(require_admin)):
    """J15: the stored frame after the episode on a camera where code saw a change (GUI only; no model sees it)."""
    ep = await asyncio.to_thread(fstore.episode, episode_id)
    after = (((ep.scene or {}).get("changes") or {}).get("after_frames") or []) if ep else []
    if not 0 <= n < len(after):
        raise HTTPException(status_code=404, detail="no_frame")
    jpeg = await asyncio.to_thread(fstore.read_episode_frame, episode_id, int(after[n]["file"]))
    if not jpeg:
        raise HTTPException(status_code=404, detail="no_frame")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/episode_frames/usage")
async def api_episode_frames_usage(_u: dict = Depends(require_admin)):
    """Disk use of the stored episode frames and of frigate.db's used part (P150), for the storage-limits bars
    (Integrations → Frigate)."""
    usage = await asyncio.to_thread(fstore.episode_frames_usage)
    db_bytes = await asyncio.to_thread(fstore.db_logical_bytes)
    return {**usage, "db_mb": round(db_bytes / 1_048_576, 1)}


# ── «Analyser» / «Analyser på nytt» (P144-s24 item 4) ─────────────────────────
#
# One run at a time for the whole install (a 9B look + scene + one 27B call), at most RATE_MAX starts per RATE_WINDOW_S,
# admin only. The job state lives in RAM (a restart ends it; the episode itself is in frigate.db either way).

RATE_WINDOW_S = 600  # the per-window count is the GUI setting `conclude.analyse_per_10min` (Expert)
_JOB: dict = {"state": "idle"}
_STARTS: list[float] = []
_TASKS: set[asyncio.Task] = set()


def _rate_max() -> int:
    """Starts allowed per RATE_WINDOW_S — read per request, so a GUI change needs no restart. Unreadable → the code
    default (the settings module never raises)."""
    return int(pipe_settings.get_settings()["conclude"]["analyse_per_10min"])


def _job_view() -> dict:
    return {k: _JOB.get(k) for k in ("state", "episode_id", "started_ts", "finished_ts", "result")}


async def _run_analysis(episode_id: int) -> None:
    try:
        result = await conclude.reanalyse(episode_id)
        if result.get("ok"):
            result["ltm"] = await asyncio.to_thread(frigate_ltm.reopen_episode, episode_id)
        _JOB.update(state="done" if result.get("ok") else "failed", result=result)
    except Exception as e:
        log.warning("frigate episodes: analysis of %s failed: %s", episode_id, type(e).__name__)
        _JOB.update(state="failed", result={"ok": False, "reason": "error"})
    finally:
        _JOB["finished_ts"] = time.time()


@router.post("/episodes/{episode_id}/analyse", status_code=202)
async def api_analyse_episode(episode_id: int, u: dict = Depends(require_admin)):
    """Re-run look → scene → conclusion on the episode's kept frames (no Frigate needed). 202 + the job; the card polls
    `GET /episode_analysis`. 409 while one runs / switch off / episode open, 429 over the rate, 404 no episode."""
    if _JOB.get("state") == "running":
        raise HTTPException(status_code=409, detail="analysis_running")
    now = time.time()
    _STARTS[:] = [t for t in _STARTS if now - t < RATE_WINDOW_S]
    if len(_STARTS) >= _rate_max():
        raise HTTPException(status_code=429, detail="analysis_rate_limited")
    ep = await asyncio.to_thread(fstore.episode, episode_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="no_episode")
    try:
        enabled = bool(pipe_settings.get_settings().get("enabled"))
    except Exception:
        enabled = False  # fail closed
    if not enabled or not conclude._settings()["enabled"]:
        raise HTTPException(status_code=409, detail="switch_off")
    if ep.status != "closed" and (conclude.stuck_info(ep, now) or {}).get("kind") != "not_closed":
        raise HTTPException(status_code=409, detail="episode_open")  # N40: a stuck open one may be analysed (closed first)
    _STARTS.append(now)
    _JOB.clear()
    _JOB.update(state="running", episode_id=episode_id, started_ts=now, finished_ts=None, result=None)
    try:
        _audit("admin_user_action", u.get("sub", "?"), f"frigate_episode_analyse episode={episode_id}")
    except Exception:
        pass
    task = asyncio.create_task(_run_analysis(episode_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return _job_view()


@router.get("/episode_analysis")
async def api_episode_analysis(_u: dict = Depends(require_admin)):
    """The «Analyser» job: idle | running | done | failed, with the episode and the result."""
    return _job_view()
