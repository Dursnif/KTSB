"""The Frigate user's shadow pipe (P144-s06a): raw claim → L0/L1/L3 checks → code decision → store.

Runs beside today's responder for every Frigate end-event. It writes only to `frigate.db` (through store.py), sends
no alerts and puts nothing into Kåre's prompt; the old path is untouched. Switch: Integrations → Frigate →
«Frigate user (shadow mode)» (`configs/frigate_user.yaml` `enabled`), read per event so the GUI takes effect at once.

«Frigate is no answer key» (2026-10-26): Frigate's face name arrives here raw from the MQTT adapter — before
the consent strip — so KTSB can check the claim against the image; only then does the store decide what may be kept
(consent, claim status). The raw name lives in RAM for this one event: never logged, never put in the L3 prompt,
never written except as the person reference the store's gate allows.

The `frigate` account is KTSB's eyes — an identity and a data scope, not a persona: no personality prompt here.

The camera settings (Integrations → Frigate camera cards, `configs/frigate_cameras.yaml`) decide what drives (P144-s12):
a ticked object at/above its min. confidence may make an episode and gets the models; anything else is stored as a plain
event (time/place/what) with no episode and no model call. Fire/smoke and not-welcome faces are never filtered; a camera
without settings keeps the pre-s12 behaviour. Same ticks, same meaning as the old responder's `should_analyze`.
"""

import asyncio
import base64
import io
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

import httpx
from PIL import Image

from adapters import frigate_adapter, mqtt_adapter
from adapters.llm_adapter import ask_llm_role
from kaare_core import model_enforcement
from kaare_core.background import spawn
from kaare_core.domain import frigate_store
from kaare_core.frigate_user import (
    box_track,
    checks,
    face_votes,
    faces,
    sealing,
    settings,
    stationary_gate,
    store,
    thing_names,
    vehicles,
)
from kaare_core.rooms import get_register
from kaare_core.tools import household_state
from kaare_core.tools.i18n import get_lang
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users import store as user_store

log = logging.getLogger(__name__)

PIPE_VERSION = "shadow-v1"
_FETCH_TIMEOUT_S = 10
_SNAPSHOT_RETRY_S = 2
_RECORDING_RETRY_S = 12  # Frigate moves a finished recording segment out of its cache after ~10 s
_RECORDING_HEIGHT = 480
# P144-s25 item 0 (stopgap until s22's crops): the L3 snapshot height. Full main-stream snapshots (2304–2560 px wide)
# cost the 9B 3 100–3 800 prompt tokens / 11.8 s; 720 → 1 112 tokens / 4.1 s with persons unchanged (s25 log).
L3_SNAPSHOT_HEIGHT = 720
_CAP_JPEG_QUALITY = 92
_ALWAYS_URGENT_LABELS = ("fire", "smoke")  # s06b — never filtered by the camera ticks, whatever the GUI list says
_URGENT_WHYS = ("urgent_label", "not_welcome")

# s06b registers conclude_urgent() here (`set_urgent_handler`); the pipe hands it every stored event.
_urgent_handler: Callable[[store.FrigateUserEvent, str | None], Awaitable] | None = None


class _Queue:
    """One event at a time (a burst queues instead of stacking LLM calls), urgent ones first (P144-s28 item 0): first
    come, first served among equals. An analysis already running is never interrupted. Bound to no event loop."""

    def __init__(self) -> None:
        self._busy = False
        self._urgent: deque[asyncio.Future] = deque()
        self._normal: deque[asyncio.Future] = deque()

    async def acquire(self, urgent: bool = False) -> None:
        if not self._busy:
            self._busy = True
            return
        waiters = self._urgent if urgent else self._normal
        turn = asyncio.get_running_loop().create_future()
        waiters.append(turn)
        try:
            await turn
        except asyncio.CancelledError:
            if turn.done() and not turn.cancelled():
                self.release()  # the turn had just come: pass it on
            else:
                waiters.remove(turn)
            raise

    def release(self) -> None:
        for waiters in (self._urgent, self._normal):
            while waiters:
                turn = waiters.popleft()
                if not turn.done():
                    turn.set_result(None)  # stays busy: the turn goes straight to this waiter
                    return
        self._busy = False

    def turn(self, urgent: bool = False) -> "_Turn":
        return _Turn(self, urgent)


class _Turn:
    def __init__(self, queue: _Queue, urgent: bool) -> None:
        self._queue, self._urgent = queue, urgent

    async def __aenter__(self) -> None:
        await self._queue.acquire(self._urgent)

    async def __aexit__(self, *exc) -> None:
        self._queue.release()


_lock = _Queue()
# conclude.is_urgent, the one place that says what is urgent (conclude imports this module, so it registers itself).
_urgent_rule: Callable[[store.FrigateUserEvent], str] | None = None
_started = False
# P144-s26: end events that arrived but whose first row is not written yet (event id → arrival time, RAM). While one is
# here the conclude loop closes no episode — the write takes a REST GET (≤ _FETCH_TIMEOUT_S), normally well under 1 s.
_arrivals: dict[str, float] = {}
ARRIVAL_STALE_S = 60  # an arrival older than this no longer holds anything (a hung write must not block closing)
RECOVER_LOOKBACK_S = 86400  # pending skeletons older than this are left as they are (Frigate may have dropped them)


def set_urgent_handler(fn: Callable[[store.FrigateUserEvent, str | None], Awaitable] | None) -> None:
    global _urgent_handler
    _urgent_handler = fn


def set_urgent_rule(fn: Callable[[store.FrigateUserEvent], str] | None) -> None:
    global _urgent_rule
    _urgent_rule = fn


def start() -> None:
    """Register the raw-claim hook and start the visit folding loop. Call once, inside the running loop."""
    global _started
    if _started:
        return
    _started = True
    mqtt_adapter.register_frigate_user_hook(on_end_event)
    start_face_votes()
    spawn(_visit_loop(), name="frigate_user_visits")
    spawn(_recover_at_start(), name="frigate_user_recover_pending")


def start_face_votes() -> None:
    """The second face sensor (P144-s19): its MQTT topics join the existing listener; a vote that arrives after its event
    was stored re-decides that event in code (`_apply_late`)."""
    mqtt_adapter.register_topic_handler(face_votes.topics, face_votes.handles, face_votes.on_message)
    face_votes.set_late_handler(_late_vote)


async def on_end_event(event: dict, raw_name: str | None, raw_score: float | None) -> None:
    try:
        await process(event, raw_name, raw_score)
    except Exception as e:
        log.warning("[FRIGATE_USER] event %s failed: %s", event.get("event_id"), type(e).__name__)


async def process(event: dict, raw_name: str | None, raw_score: float | None) -> dict | None:
    """Check and store one Frigate end-event in two phases (P144-s26). Phase 1, at once and outside the queue: the facts
    (Frigate REST, camera ticks) → a skeleton row with no name, already in its episode. Phase 2, one event at a time
    (the 9B): image, L0/L1/L3, claim decision → the same row re-recorded through the store's gate. None when the switch
    is off or the event is unusable."""
    if not settings.enabled():
        return None
    event_id = event.get("event_id")
    if event_id:
        _arrivals[event_id] = time.time()  # the conclude loop holds open episodes while this is unwritten
    try:
        a = await _arrive(event, raw_name, raw_score)
        if a is None:
            return None
        if a.analyse:
            a.skeleton_stored = await asyncio.to_thread(store.record_event, _skeleton(a))
    finally:
        _arrivals.pop(event_id, None)
    if not a.analyse and a.label != "person":
        return await _finish(a)  # nothing for a model: the first write is the final one (no queue)
    # persons as before: never at the same time as a late face vote re-deciding the same row; urgent ones go first
    async with _lock.turn(_urgent_arrival(a)):
        try:
            return await _finish(a)
        except asyncio.CancelledError:
            raise  # a stopping API: the skeleton stays pending and recover_pending() finishes it at the next start
        except Exception as e:
            log.warning("[FRIGATE_USER] event %s: analysis failed (%s) — kept in its episode", a.event_id,
                        type(e).__name__)
            await asyncio.to_thread(_mark_failed, a, type(e).__name__)
            return {"event_id": a.event_id, "claim": bool(a.claim), "decision": "none", "stored": a.skeleton_stored}


# ── facts ───────────────────────────────────────────────────────────────────────


async def _fetch_event(event_id: str) -> dict:
    try:
        ev = await asyncio.wait_for(frigate_adapter.get_event(event_id), timeout=_FETCH_TIMEOUT_S)
        return ev if isinstance(ev, dict) else {}
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        # 404 = Frigate did not keep the event (short/unretained objects, mostly road cars) — routine, not news.
        log.log(logging.DEBUG if code == 404 else logging.INFO, "[FRIGATE_USER] event %s: no REST data (HTTP %s)",
                event_id, code)
        return {}
    except Exception as e:
        log.info("[FRIGATE_USER] event %s: no REST data (%s)", event_id, type(e).__name__)
        return {}


def _cap_height(data: bytes, height: int = L3_SNAPSHOT_HEIGHT) -> bytes:
    """`data` scaled down to `height` (LANCZOS, JPEG) when it is taller; unchanged otherwise or when unreadable."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.height <= height:
            return data
        img = img.convert("RGB").resize((round(img.width * height / img.height), height), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=_CAP_JPEG_QUALITY)
        return buf.getvalue()
    except Exception:
        return data


async def _load_snapshot(event_id: str) -> bytes | None:
    """The event's snapshot, at most L3_SNAPSHOT_HEIGHT high (P144-s25 item 0): the local cache (frigate_store) if the
    responder already saved it — scaled down in code, it holds Frigate's full size — else Frigate at that height."""
    cached = frigate_store.SNAPSHOT_DIR / f"{event_id}.jpg"
    try:
        if cached.exists():
            data = await asyncio.to_thread(sealing.read_picture, cached)  # sealed at rest (R0.6a)
            if data:
                return await asyncio.to_thread(_cap_height, data)
    except OSError:
        pass
    for attempt in range(2):
        try:
            return await asyncio.wait_for(frigate_adapter.fetch_event_snapshot(event_id, height=L3_SNAPSHOT_HEIGHT),
                                          timeout=_FETCH_TIMEOUT_S)
        except Exception as e:
            if attempt == 0:
                await asyncio.sleep(_SNAPSHOT_RETRY_S)  # Frigate may not have written it yet
            else:
                log.info("[FRIGATE_USER] event %s: no snapshot (%s)", event_id, type(e).__name__)
    return None


# P144 6e: the event snapshot the GUI shows (J24 «Var det deg?», Cameras thumbnails, StillsList) — `state/frigate_snapshots/
# <event>.jpg`, the old responder's file name. Only the old path wrote it; the pipe writes it now for every selected event,
# at L3_SNAPSHOT_HEIGHT (≈ 0.15 MB instead of Frigate's ≈ 2 MB full size). A person's snapshot is the one the pipe already
# fetched (no extra GET); another selected label = one GET. The rotation (storage.snapshots_max_mb, 0 = keep none) runs at
# most every SNAPSHOT_CLEANUP_S — it lists the whole folder.
SNAPSHOT_CLEANUP_S = 600.0
_last_snapshot_cleanup = 0.0


def _snapshot_cap_mb() -> int:
    try:
        return int((frigate_store.camera_config().get("storage") or {}).get("snapshots_max_mb", 500))
    except (TypeError, ValueError):
        return 500


def _store_event_snapshot(event_id: str, data: bytes) -> bool:
    """Write the event's snapshot (atomic) and rotate the folder now and then. Never raises."""
    global _last_snapshot_cleanup
    cap = _snapshot_cap_mb()
    if not event_id or not data or cap == 0:
        return False
    try:
        frigate_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target = frigate_store.SNAPSHOT_DIR / f"{event_id}.jpg"
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(sealing.seal_bytes(data))  # sealed at rest (N19 point 3, R0.6a)
        tmp.replace(target)
    except OSError as e:
        log.info("[FRIGATE_USER] event %s: snapshot not kept (%s)", event_id, type(e).__name__)
        return False
    now = time.monotonic()
    if now - _last_snapshot_cleanup >= SNAPSHOT_CLEANUP_S:
        _last_snapshot_cleanup = now
        frigate_store.cleanup_snapshots(cap)
    return True


async def _keep_event_snapshot(event_id: str, label: str, selected: bool, img_bytes: bytes | None, img_src: str | None,
                               has_snapshot) -> bool:
    """Keep the snapshot of a selected event: the bytes the pipe already has (a person), else one Frigate GET."""
    if not selected or not event_id or _snapshot_cap_mb() == 0:
        return False
    data = img_bytes if img_src == "snapshot" else None
    if data is None and label != "person" and has_snapshot is not False:
        try:
            data = await asyncio.wait_for(frigate_adapter.fetch_event_snapshot(event_id, height=L3_SNAPSHOT_HEIGHT),
                                          timeout=_FETCH_TIMEOUT_S)
        except Exception as e:
            log.info("[FRIGATE_USER] event %s: no snapshot to keep (%s)", event_id, type(e).__name__)
            return False
    if not data:
        return False
    return await asyncio.to_thread(_store_event_snapshot, event_id, data)


async def _recording_frame(camera: str, ts: float) -> bytes | None:
    """A frame from Frigate's recordings at ts — the fallback when Frigate kept no snapshot (very short objects, s12).
    The segment may still be in Frigate's cache right after the event ends, so one retry."""
    for attempt in range(2):
        try:
            return await asyncio.wait_for(
                frigate_adapter.fetch_recording_snapshot(camera, ts, _RECORDING_HEIGHT), timeout=_FETCH_TIMEOUT_S)
        except Exception as e:
            if attempt == 0:
                await asyncio.sleep(_RECORDING_RETRY_S)
            else:
                log.info("[FRIGATE_USER] camera %s: no recording frame at %.1f (%s)", camera, ts, type(e).__name__)
    return None


def _open_image(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except Exception:
        return None


def _place_of(camera: str) -> str | None:
    """[[P137]] place of a camera: a `frigate:<camera>` thing, else Home Assistant's `camera.<camera>` entity."""
    try:
        reg = get_register()
        return reg.place_of(f"frigate:{camera}") or reg.place_of(f"camera.{camera}") or None
    except Exception:
        return None


def _seen_elsewhere(names: set[str], start_ts: float, end_ts: float | None, event_id: str) -> list[checks.Seen]:
    """Stored events of the same person (any of their names) that overlap this one in time (for L1)."""
    window_end = end_ts if end_ts is not None else start_ts
    out = []
    closed: dict[int, bool] = {}
    for name in names:
        for e in store.events_between(start_ts - 3600, window_end, person_ref=name):
            if e.event_id == event_id:
                continue
            if e.episode_id is not None and e.episode_id not in closed:
                ep = store.episode(e.episode_id)
                closed[e.episode_id] = bool(ep and ep.status == "closed")
            out.append(checks.Seen(camera=e.camera, start_ts=e.start_ts, end_ts=e.end_ts,
                                   episode_closed=closed.get(e.episode_id, False)))
    return out


def _camera_role(camera: str) -> str | None:
    """The camera's role from the camera cards (Integrations → Frigate) — the store's episode rule reads it."""
    try:
        role = ((frigate_store.camera_config().get("cameras") or {}).get(camera) or {}).get("role")
        return role if isinstance(role, str) and role else None
    except Exception:
        return None


def _camera_episodes(camera: str) -> bool:
    """The camera card's «Kan lage episoder» (P144-s14) — the one place the pipe reads it; the store applies it."""
    try:
        card = (frigate_store.camera_config().get("cameras") or {}).get(camera)
    except Exception:
        card = None
    return settings.camera_makes_episodes(card)


def _drive(camera: str, label: str, score: float, person_kind: str, has_plate: bool,
           duration: float | None = None) -> tuple[str, bool, str]:
    """(episode_rule, selected, why) from the camera settings — ticked and score ≥ min_confidence (P144-s12).
    A camera with no label settings keeps the old behaviour; a configured camera without the label = not ticked (the
    GUI shows it unticked, the old responder skips it). A plate that is not selected may still join an open episode."""
    try:
        urgent_labels = {*_ALWAYS_URGENT_LABELS, *(settings.conclude().get("urgent_labels") or [])}
    except Exception:
        urgent_labels = set(_ALWAYS_URGENT_LABELS)
    if label in urgent_labels:
        return "normal", True, "urgent_label"  # not even a camera with episodes off holds these back
    if person_kind == "not_welcome":
        return "normal", True, "not_welcome"
    try:
        labels = ((frigate_store.camera_config().get("cameras") or {}).get(camera) or {}).get("labels")
    except Exception:
        labels = None
    lcfg = labels.get(label) if isinstance(labels, dict) else None
    if not isinstance(labels, dict) or not labels:
        selected, why = True, "no_camera_config"
    elif not isinstance(lcfg, dict) or lcfg.get("analyze") is not True:
        selected, why = False, "label_not_selected"
    else:
        try:
            threshold = float(lcfg.get("min_confidence") or 0)
        except (TypeError, ValueError):
            threshold = 0.0
        selected, why = (True, "selected") if score >= threshold else (False, "below_min_confidence")
        try:  # P144 6e: the old responder's «min. varighet» per label, now the pipe's (0 / unset = no limit)
            min_dur = float(lcfg.get("min_duration_seconds") or 0)
        except (TypeError, ValueError):
            min_dur = 0.0
        if selected and duration is not None and min_dur > 0 and duration < min_dur:
            selected, why = False, "below_min_duration"
    if not selected:
        return ("join_only", False, "plate_join_only") if has_plate else ("none", False, why)
    return "normal", True, why if _camera_episodes(camera) else "episodes_off"


def _plate_text(value) -> str | None:
    """Frigate's recognized_license_plate: a string, or [plate, score] on some payloads."""
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _plate(label: str, raw_name: str | None, data: dict, event: dict) -> tuple[str | None, bool | None]:
    """(plate_claim, plate_known) — Frigate's reading of a plate, never a fact (P144-s12 Part B). A known plate comes
    as the sub_label name next to the recognised plate; REST data (complete) before the MQTT copy."""
    mqtt = event.get("plate") if isinstance(event.get("plate"), dict) else {}
    plate = _plate_text(data.get("recognized_license_plate")) or _plate_text(mqtt.get("plate"))
    if not plate or label == "person":
        return None, None
    known = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
    return (known, True) if known else (plate, False)


def _motion(event: dict, data: dict) -> tuple[list | None, bool | None]:
    """(path_data, stationary) for the store's episode rule: REST path_data (complete) before the MQTT copy."""
    motion = event.get("motion") if isinstance(event.get("motion"), dict) else {}
    path = data.get("path_data") if isinstance(data.get("path_data"), list) and data.get("path_data") else None
    if path is None and isinstance(motion.get("path_data"), list):
        path = motion["path_data"]
    stationary = motion.get("stationary")
    return path, stationary if isinstance(stationary, bool) else None


def _read_back(event_id: str, start_ts: float, camera: str) -> store.FrigateUserEvent | None:
    return next((e for e in store.events_between(start_ts, start_ts, camera=camera) if e.event_id == event_id), None)


# ── L3 ──────────────────────────────────────────────────────────────────────────


async def _l3(camera: str, label: str, img_b64: str, event_id: str,
              raw_out: dict | None = None) -> tuple[dict | None, str]:
    """One image to the `frigate` role, no claim and no reference in the prompt — not even Frigate's label (s16: a
    label in the prompt primes the count, s15 A_claim said «yes» on 35 of 40 empty frames); output through the P142
    `frigate` profile (shadow) and strip_language_tag. Returns (fields, error). `label` is kept for the caller's
    signature and logs only. `raw_out["text"]` gets the reply the fields came from, as the model gave it (P144 6e item 3)."""
    lang = get_lang("global")
    rid = f"rid-frigate-shadow-{event_id}"
    prompt = _t_i18n("frigate_user_l3_prompt", lang, camera=camera)
    result = await ask_llm_role("frigate", prompt, images=[img_b64], rid=rid)
    text = result.get("text") or ""
    if raw_out is not None:
        raw_out["text"] = text
    parsed, err = checks.parse_l3(model_enforcement.strip_language_tag(text))
    meta = result.get("meta") or {}
    enforcer = model_enforcement.ModelEnforcer.for_profile("frigate", lang, rid=rid)
    action = enforcer.check(text_out=text, used_tools=[],
                            meta={"done_reason": meta.get("done_reason"), "parse_error": err})
    if action:  # only when a detector is switched on in the GUI — in shadow mode check() logs and returns None
        retry = await ask_llm_role("frigate", f"{prompt}\n\n{action.correction_message}", images=[img_b64], rid=rid)
        parsed, err = checks.parse_l3(model_enforcement.strip_language_tag(retry.get("text") or ""))
        if raw_out is not None and retry.get("text"):
            raw_out["text"] = retry.get("text")
    return parsed, err


# ── the pipe ────────────────────────────────────────────────────────────────────


@dataclass
class _Arrival:
    """One Frigate end event between its two phases (P144-s26) — RAM only; `claim` is Frigate's raw name, never logged
    and never written except through the store's gate in phase 2."""
    event: dict
    event_id: str
    camera: str
    label: str
    claim: str | None
    raw_score: float | None
    claim_source: str
    second: dict | None
    data: dict
    start_ts: float
    end_ts: float
    score: float
    plate_claim: str | None
    plate_known: bool | None
    claim_kind: str
    episode_rule: str
    selected: bool
    why: str
    place: str | None = None
    camera_role: str | None = None
    camera_episodes: bool | None = None
    analyse: bool = False  # phase 2 has model work (an image to look at)
    skeleton_stored: bool = False
    frigate_ignored: bool = False  # P144-s29b: Frigate's face name dropped by admin's «teller ikke»
    gate: dict | None = None  # J3 / R138: the stationary gate's {verdict, key, thing_id} (None = not judged)
    wrong_rule: dict | None = None  # J20 / #28: {key, hold, why, mode} when a confirmed-wrong rule covers it
    boxes: list = field(default_factory=list)  # J19: Frigate's per-update boxes, relative (box_track.for_event)


async def _arrive(event: dict, raw_name: str | None, raw_score: float | None) -> _Arrival | None:
    """Phase 1's facts: Frigate's REST event, the camera ticks, place and camera card — no image, no model."""
    event_id, camera, label = event.get("event_id"), event.get("camera"), event.get("label")
    if not event_id or not camera or not label:
        return None
    claim = raw_name.strip() if label == "person" and isinstance(raw_name, str) and raw_name.strip() else None
    # P144-s19: the second face sensor's vote for this exact Frigate event (RAM). Frigate said no name but the second
    # sensor names one person → that becomes the claim the pipe checks (same footing, never a fact).
    claim_source = "frigate"
    # P144-s29b: admin set Frigate's face recognition to «teller ikke» → its name is never a vote (nothing kept of it).
    frigate_ignored = bool(claim) and face_votes.sensor_weight("frigate", camera, None)["action"] <= 0
    if frigate_ignored:
        claim, raw_score = None, None
    if label == "person":
        await face_votes.enrich(event_id)  # P144-s29: every detector's answer + image size (GET, ≤ 1/s, never raises)
    second = face_votes.reading(event_id) if label == "person" else None
    if claim is None and (dt_vote := face_votes.single_vote(second)) is not None:
        claim, raw_score, claim_source = dt_vote["face"], None, "double_take"
        second = {**second, "claim_source": "double_take"}

    rest = await _fetch_event(event_id)
    duration = float(event.get("duration") or 0)
    start_ts = float(rest.get("start_time") or (time.time() - duration))
    end_ts = float(rest.get("end_time") or (start_ts + duration))
    data = rest.get("data") if isinstance(rest.get("data"), dict) else {}
    score = float(event.get("top_score") or event.get("score") or data.get("top_score") or 0)
    plate_claim, plate_known = _plate(label, raw_name, data, event)
    claim_kind = faces.classify(claim) if claim else "unknown"
    episode_rule, selected, why = _drive(camera, label, score, claim_kind, plate_claim is not None, duration=duration)
    episode_rule, selected, why, gate = await _stationary_check(event_id, camera, label, data, episode_rule, selected,
                                                                why, rest=rest or None)
    episode_rule, selected, why, wrong_rule = await _wrong_rule_check(event_id, camera, label, data, start_ts,
                                                                      episode_rule, selected, why)  # J20 (shadow default)
    if why == "no_camera_config":
        log.info("[FRIGATE_USER] event %s camera=%s label=%s: no camera settings, old behaviour (%s)",
                 event_id, camera, label, why)
    elif not selected:
        log.debug("[FRIGATE_USER] event %s camera=%s label=%s score=%.2f: stored, no episode/models (%s)",
                 event_id, camera, label, score, why)

    a = _Arrival(event=event, event_id=event_id, camera=camera, label=label, claim=claim, raw_score=raw_score,
                 claim_source=claim_source, second=second, data=data, start_ts=start_ts, end_ts=end_ts, score=score,
                 plate_claim=plate_claim, plate_known=plate_known, claim_kind=claim_kind, episode_rule=episode_rule,
                 selected=selected, why=why, frigate_ignored=frigate_ignored, gate=gate, wrong_rule=wrong_rule)
    a.place = await asyncio.to_thread(_place_of, camera)
    a.boxes = await box_track.for_event(event.get("boxes") or [], camera)  # J19
    # Urgent events drive even on a camera with episodes off (s12/s14) — else conclude_urgent finds no episode for them.
    a.camera_role = None if why in _URGENT_WHYS else _camera_role(camera)
    a.camera_episodes = None if why in _URGENT_WHYS else _camera_episodes(camera)
    a.analyse = label == "person" and bool(selected or claim)
    return a


async def _stationary_check(event_id: str, camera: str, label: str, data: dict, episode_rule: str, selected: bool,
                            why: str, rest: dict | None = None) -> tuple[str, bool, str, dict | None]:
    """J3 / R138: an object that does not move by itself and whose pixels did not change is the same object standing
    there — no episode, no model. Only asked for a selected event that would drive; never persons, animals or urgent
    labels. The verdict is shared with the old responder; «unknown» / «changed» change nothing."""
    if episode_rule != "normal" or not selected or why in _URGENT_WHYS or not stationary_gate.applies(label):
        return episode_rule, selected, why, None
    word = await stationary_gate.verdict(event_id, camera, label, rest=rest)
    gate = stationary_gate.record(word, camera, label, data)
    if word == "unchanged":
        return "none", False, "unchanged_stationary", gate
    if word == "unknown" and stationary_gate.hold_unknown(label):
        return "none", False, "stationary_unknown", gate  # J13: a still thing nobody could judge never opens one alone
    return episode_rule, selected, why, gate


async def _wrong_rule_check(event_id: str, camera: str, label: str, data: dict, start_ts: float, episode_rule: str,
                            selected: bool, why: str) -> tuple[str, bool, str, dict | None]:
    """J20 / P144 v2 #28: admin said «ikke en <label>» / «feil deteksjon» about exactly this camera + label + cell. Code
    holds the detection back only when it did not move and no other camera saw the same label within 30 s; with the GUI
    switch «Hold tilbake bekreftet feil» off (default) it is only written on the event (shadow). Never persons, never
    urgent. Never raises."""
    if label == "person" or not selected or why in _URGENT_WHYS:
        return episode_rule, selected, why, None
    try:
        box = stationary_gate._box(data)
        others = await asyncio.to_thread(store.events_between, start_ts - thing_names.SUPPORT_S,
                                         start_ts + thing_names.SUPPORT_S)
        verdict = await asyncio.to_thread(thing_names.hold, camera, label, box, data.get("path_data"), start_ts, others)
    except Exception as e:
        log.info("[FRIGATE_WRONG] event %s: rule check skipped (%s)", event_id, type(e).__name__)
        return episode_rule, selected, why, None
    if verdict is None:
        return episode_rule, selected, why, None
    on = settings.questions_wrong_rules()
    out = {**verdict, "mode": "on" if on else "shadow"}
    log.info("[FRIGATE_WRONG] event %s camera=%s label=%s rule=%s hold=%s mode=%s", event_id, camera, label,
             verdict["key"], verdict["hold"], out["mode"])
    if verdict["hold"] and on:
        return "none", False, "confirmed_wrong", out
    return episode_rule, selected, why, out


def _urgent_arrival(a: _Arrival) -> bool:
    """Does this event go first in the queue (P144-s28 item 0)? The existing urgent rules, on what is known at arrival:
    the label, Frigate's raw face class, the household's away state. A rule that fails leaves it in the normal queue."""
    if _urgent_rule is None:
        return False
    try:
        return bool(_urgent_rule(_base_event(a, person_kind=a.claim_kind)))
    except Exception as e:
        log.info("[FRIGATE_USER] event %s: urgent rule unreadable (%s) — normal queue", a.event_id, type(e).__name__)
        return False


def _base_event(a: _Arrival, **fields) -> store.FrigateUserEvent:
    """The stored event's facts — the same in both phases, so the skeleton's episode window is the final one."""
    ev = store.FrigateUserEvent(
        event_id=a.event_id, camera=a.camera, label=a.label, start_ts=a.start_ts, end_ts=a.end_ts, place=a.place,
        episode_rule=a.episode_rule, plate_claim=a.plate_claim, plate_known=a.plate_known,
        vehicle_id=vehicles.id_for(a.plate_claim, plate_known=a.plate_known, event=a.event), **fields)  # P150 6b
    ev.path_data, ev.stationary = _motion(a.event, a.data)
    ev.boxes = list(a.boxes or [])
    ev.camera_role, ev.camera_episodes = a.camera_role, a.camera_episodes
    return ev


def _skeleton(a: _Arrival) -> store.FrigateUserEvent:
    """Phase 1's row (P144-s26): time, camera, label, path, drives — no name, no claim status, no model text. The
    store's gate runs on it like on every write; `check.pending` tells readers and the conclude loop it is not done."""
    check: dict = {"pipe": PIPE_VERSION, "drive": {"selected": a.selected, "why": a.why}, "pending": "analysis"}
    if a.gate:
        check["gate"] = a.gate
    if a.wrong_rule:
        check["wrong_rule"] = a.wrong_rule
    if a.episode_rule != "normal":
        check["episode_rule"] = a.episode_rule
    return _base_event(a, check=check)


def _mark_failed(a: _Arrival, reason: str) -> bool:
    """Phase 2 broke: the row stays in its episode, without the pending mark, saying the analysis failed."""
    ev = _skeleton(a)
    ev.check = {k: v for k, v in ev.check.items() if k != "pending"} | {"analysis": "failed", "analysis_error": reason}
    return store.record_event(ev)


async def _finish(a: _Arrival) -> dict:
    """Phase 2: image → L0/L1/L3 → claim decision → second sensor → the final row through the store's gate."""
    cfg = settings.get_settings()
    event_id, camera, label = a.event_id, a.camera, a.label
    claim, raw_score, claim_source, second = a.claim, a.raw_score, a.claim_source, a.second
    data, start_ts, end_ts = a.data, a.start_ts, a.end_ts
    plate_claim, claim_kind = a.plate_claim, a.claim_kind
    selected, why, event, episode_rule = a.selected, a.why, a.event, a.episode_rule
    # The image (and with it L3) only for what drives, or for a face claim — a claimed name is checked before it may
    # be stored. No Frigate snapshot (very short objects) → a frame from the recording at the event's middle.
    img_bytes, img_src = None, None
    if label == "person" and (selected or claim):
        if event.get("has_snapshot") is not False:
            img_bytes = await _load_snapshot(event_id)
            img_src = "snapshot" if img_bytes else None
        if img_bytes is None and ((selected and why != "episodes_off") or claim):
            img_bytes = await _recording_frame(camera, (start_ts + end_ts) / 2)
            img_src = "recording" if img_bytes else None
    image = _open_image(img_bytes)
    img_b64 = base64.b64encode(img_bytes).decode() if image is not None else None
    # P144 6e: the event snapshot the GUI shows (the old responder's file) — in the background, never delays the pipe
    spawn(_keep_event_snapshot(event_id, label, selected, img_bytes, img_src, event.get("has_snapshot")))

    votes: list[checks.Vote] = []
    check: dict = {"pipe": PIPE_VERSION, "claim": bool(claim), "drive": {"selected": selected, "why": why}}
    if a.gate:
        check["gate"] = a.gate
    if a.wrong_rule:
        check["wrong_rule"] = a.wrong_rule
    if claim_source != "frigate":
        check["claim_source"] = claim_source
    if a.frigate_ignored:
        check["frigate_ignored"] = True
    if claim and claim_source == "frigate":
        # P144-s29b: the word of the one sensor this name stands behind (the fusion replaces it when another votes).
        w = face_votes.sensor_weight("frigate", camera, None)
        check["claim_trust"] = {"word": w["word"], "source": w["source"]}
    if episode_rule != "normal":
        check["episode_rule"] = episode_rule
    if image is not None:
        check["image"] = img_src
    check_ran = False
    l0_ok = True
    if claim and image is not None:
        px = checks.face_px(data, image.size)
        ir = checks.is_ir(image)
        l0 = checks.l0_quality(face_px=px, ir=ir, claim_score=raw_score, min_face_px=cfg["l0_min_face_px"],
                               min_claim_score=cfg["l0_min_claim_score"])
        votes.append(l0)
        check.update({"l0": l0.as_dict(), "face_px": px, "ir": ir})
        check_ran, l0_ok = True, l0.passed

    linked_user = ""
    if claim:
        linked_user = await asyncio.to_thread(user_store.get_username_for_frigate_face, claim)
        seen = await asyncio.to_thread(_seen_elsewhere, {claim, linked_user} - {""}, start_ts, end_ts, event_id)
        l1 = checks.l1_context(camera=camera, start_ts=start_ts, end_ts=end_ts, seen=seen,
                               household_member=faces.classify(claim) == "household",
                               household_away=household_state.is_away())
        votes.extend(l1)
        check["l1"] = [v.as_dict() for v in l1]

    attributes: dict = {}
    description = analysis = None
    l3_raw: dict = {}
    if image is not None and l0_ok:
        parsed, err = await _l3(camera, label, img_b64, event_id, raw_out=l3_raw)
        check["l3_blind"] = True  # s16: the label gate trusts only blind L3 person counts (none before s16)
        if parsed:
            attributes = {k: parsed[k] for k in ("persons", *checks.L3_FIELDS)}
            description, analysis = parsed["description"] or None, parsed["analysis"] or None
            # P144-s20 item 1: what a person this small can show is code's call (Frigate's box × the frame's height);
            # the 9B is not told — it fills glasses/headwear even at 25 px. Below the head limit the appearance
            # sentence goes too (clothes/hat on a few px are guesses); what happens (`analysis`) stays.
            px = checks.person_px(data, image.size)
            check.update(person_px=px, frame_h=image.size[1])
            attributes, limited = checks.limit_by_size(attributes, px)
            if limited:
                check["size_limited"] = limited
            if px is not None and px < checks.HEAD_ATTRS_MIN_PX:
                description = None
        if claim:
            l3 = (checks.l3_compare(attributes, faces.attribute_profile(claim)) if parsed
                  else checks.Vote("l3", "unsure", False, f"no_answer: {err}"))
            votes.append(l3)
            check["l3"] = l3.as_dict()

    status = checks.decide(has_claim=bool(claim), votes=votes, check_ran=check_ran)
    check["decision"] = status
    doubt = checks.doubt_of(votes) if claim else None
    if doubt:
        check["doubt"] = doubt

    person_ref, person_kind = None, "unknown"
    if claim:
        person_kind = claim_kind
        person_ref = (linked_user or claim) if person_kind == "household" else claim
    if claim_source == "double_take":
        # The second sensor named one person and the first said nothing; its consent is applied by the store's gate.
        _, person_ref, person_kind = face_votes.map_name(claim)
    switched = False
    if second is not None and claim:
        status, person_ref, person_kind, switched = await asyncio.to_thread(
            _fold_second_sensor, check, votes, attributes, second, status, person_ref, person_kind, claim)
        doubt = check.get("doubt")
    elif second is not None:
        check["l2"] = face_votes.mask_for_storage({}, {**second, "names": []})["l2"]  # no claim → no name kept
    if label == "person":
        _remember_sig(event_id, second)  # also «no vote yet»: a later vote for this event is then a late vote

    ev = _base_event(a, person_ref=person_ref, person_kind=person_kind, claim_status=status,
                     claim_score=raw_score if claim else None, check=check, attributes=attributes,
                     description=description, analysis=analysis)
    ev.l3_raw = l3_raw.get("text") or None  # P144 6e (3): the image model's own reply, capped by the store
    if claim_source == "double_take":
        ev.claim_score = None
    stored = await asyncio.to_thread(store.record_event, ev)
    kept = await asyncio.to_thread(_read_back, event_id, start_ts, camera) if stored else None
    if kept is not None and doubt and status == "unverified" and person_ref and not switched:
        # The episode is only known once the store has placed the event; a second agreeing observation rejects.
        rejected = await asyncio.to_thread(_second_observation, ev, kept, doubt, {person_ref, claim})
        if rejected:
            status = "rejected"
            kept = await asyncio.to_thread(_read_back, event_id, start_ts, camera)
    # Events that drive nothing are routine (facts are in frigate.db) — keep them out of the journal at INFO.
    routine = not selected and not claim and plate_claim is None
    log.log(logging.DEBUG if routine else logging.INFO,
            "[FRIGATE_USER] event %s camera=%s label=%s claim=%s decision=%s drive=%s stored=%s",
            event_id, camera, label, bool(claim), status, why, stored)

    if kept is not None and _urgent_handler is not None:
        spawn(_urgent_handler(kept, img_b64), name=f"frigate_user_urgent_{event_id}")
    if kept is not None and _reading_sig(face_votes.reading(event_id)) != _reading_sig(second):
        spawn(_apply_late(event_id), name=f"frigate_user_late_vote_{event_id}")  # a vote came while we decided
    return {"event_id": event_id, "claim": bool(claim), "decision": status, "stored": stored}


# ── what is not stored yet (P144-s26) ───────────────────────────────────────────


def _track_window(track: dict, now: float) -> tuple[float, float] | None:
    """The activity window of one track Frigate still has open, by the same rules as a stored event (camera ticks, then
    the store's drive rule on its path) — None when it would not drive (not ticked, parked, stationary, episodes off)."""
    try:
        camera, label = str(track.get("camera") or ""), str(track.get("label") or "")
        start = float(track.get("start_time"))
    except (TypeError, ValueError):
        return None
    if not camera or not label:
        return None
    data = track.get("data") if isinstance(track.get("data"), dict) else {}
    score = float(data.get("top_score") or data.get("score") or track.get("top_score") or 0)
    rule, selected, why = _drive(camera, label, score, "unknown", False)
    if rule != "normal" or not selected:
        return None
    path = data.get("path_data") if isinstance(data.get("path_data"), list) and data.get("path_data") else None
    ev = store.FrigateUserEvent(event_id=str(track.get("id") or "in-progress"), camera=camera, label=label,
                                start_ts=start, end_ts=now, path_data=path, camera_role=_camera_role(camera),
                                camera_episodes=_camera_episodes(camera) if why not in _URGENT_WHYS else None)
    return store.activity_window(ev)


async def unstored_activity(now: float | None = None) -> list[tuple[float, float]]:
    """Activity the store has not seen yet (the conclude loop's close rule, P144-s26 item 3): an end event between its
    arrival and its first write holds every open episode (a few seconds at most); a track Frigate still has open holds
    the episodes its activity window touches. Frigate unreachable → only the first kind (= the old behaviour), logged."""
    now = time.time() if now is None else now
    wall = time.time()
    holds: list[tuple[float, float]] = []
    if any(wall - t < ARRIVAL_STALE_S for t in list(_arrivals.values())):
        holds.append((float("-inf"), float("inf")))
    try:
        tracks = await asyncio.wait_for(frigate_adapter.fetch_in_progress_events(), timeout=_FETCH_TIMEOUT_S)
    except Exception as e:
        log.info("[FRIGATE_USER] open Frigate tracks unreadable (%s) — episodes close on the clock", type(e).__name__)
        return holds
    windows = (_track_window(t, now) for t in tracks or [] if isinstance(t, dict))
    holds.extend(w for w in windows if w is not None)
    return holds


def _event_dict_from_rest(rest: dict, stored: store.FrigateUserEvent) -> dict:
    """The MQTT end-event dict rebuilt from Frigate's REST event (a pending skeleton after a restart); the stored row
    fills what REST leaves out."""
    data = rest.get("data") if isinstance(rest.get("data"), dict) else {}
    start, end = rest.get("start_time") or stored.start_ts, rest.get("end_time") or stored.end_ts
    return {"event_id": stored.event_id, "camera": rest.get("camera") or stored.camera,
            "label": rest.get("label") or stored.label,
            "score": float(data.get("score") or 0), "top_score": float(data.get("top_score") or 0),
            "duration": round(float(end) - float(start), 1) if start and end else 0.0,
            "has_snapshot": rest.get("has_snapshot"), "motion": {"path_data": data.get("path_data")}}


async def recover_pending(now: float | None = None) -> int:
    """Skeletons left pending by a stop in the middle of their analysis (P144-s26): each runs through both phases again
    from Frigate's REST event — Frigate's face name from REST `sub_label`, RAM only, the same footing as the MQTT copy
    (REST already strips opted-out names). Frigate no longer has the event → the row is marked «analysis failed», still
    in its episode. Returns how many were finished."""
    now = time.time() if now is None else now
    done = 0
    for ev in await asyncio.to_thread(store.pending_events, now - RECOVER_LOOKBACK_S):
        try:
            rest = await asyncio.wait_for(frigate_adapter.get_event(ev.event_id), timeout=_FETCH_TIMEOUT_S)
        except Exception:
            rest = None
        if not isinstance(rest, dict) or not rest.get("id"):
            failed = replace(ev, check={k: v for k, v in ev.check.items() if k != "pending"}
                             | {"analysis": "failed", "analysis_error": "lost_at_restart"})
            await asyncio.to_thread(store.record_event, failed)
            continue
        sub = rest.get("sub_label")
        name, score = (sub[0], sub[1] if len(sub) > 1 else None) if isinstance(sub, list) and sub else (sub, None)
        result = await process(_event_dict_from_rest(rest, ev), name if isinstance(name, str) else None,
                               float(score) if isinstance(score, int | float) else None)
        done += 1 if result else 0
    if done:
        log.info("[FRIGATE_USER] %d event(s) left pending at the last stop were analysed now", done)
    return done


async def _recover_at_start() -> None:
    try:
        # No Frigate address entered → nothing to recover and nothing to warn about (DT04 R3a).
        if settings.enabled() and frigate_adapter.is_configured():
            await recover_pending()
    except Exception as e:
        log.warning("[FRIGATE_USER] recovering pending events failed: %s", type(e).__name__)


# ── the second face sensor (P144-s19) ───────────────────────────────────────────


def _fold_second_sensor(check: dict, votes: list[checks.Vote], attributes: dict, second: dict, status: str,
                        person_ref: str | None, person_kind: str, claim: str) -> tuple[str, str | None, str, bool]:
    """Fuse the second sensor into `check` (in place): weights from checks.fuse_second_sensor, names consent-masked by
    face_votes before storage. Returns (status, person_ref, person_kind, switched to the other candidate)."""
    check["frigate_decision"] = status  # the first sensor's own decision — a late vote re-fuses from here
    fused = checks.fuse_second_sensor(
        frigate_ref=person_ref, frigate_face=claim, frigate_status=status, votes=votes, attributes=attributes,
        reading=second, profile_of=faces.attribute_profile, frigate_kind=person_kind)
    if fused is None:
        return status, person_ref, person_kind, False
    masked = face_votes.mask_for_storage(fused, second)
    check["sensors"], check["l2"] = masked["sensors"], masked["l2"]
    if fused.get("claim_trust"):
        check["claim_trust"] = fused["claim_trust"]  # P144-s29b: the one sensor's word (no name in it)
    else:
        check.pop("claim_trust", None)  # two sensors now: the word comes from the fusion rules
    check["decision"] = fused["status"]
    if fused["review"]:
        check["review"] = True  # phase 4b's review list: the two sensors disagreed
    switched = fused["source"] == "double_take" and fused["person_ref"] != person_ref
    if switched:
        # The leaned candidate is stored; the first sensor's doubt stays readable for the review, not as this one's.
        if check.get("doubt"):
            check["sensors"]["frigate_doubt"] = check.pop("doubt")
        return fused["status"], fused["person_ref"], fused["person_kind"] or "unknown", True
    return fused["status"], person_ref, person_kind, False


_reading_sig = face_votes.reading_sig
_remember_sig = face_votes.remember_decided


def _late_vote(event_id: str) -> None:
    """face_votes' hook (inside the MQTT listener): re-decide a stored event in the background. Never blocks."""
    if face_votes.decided_sig(event_id) is None:
        return  # not decided yet (the usual case: votes come before Frigate's end event) — the pipe will read it
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop (tests, shutdown)
    spawn(_apply_late(event_id), name=f"frigate_user_late_vote_{event_id}")


async def _apply_late(event_id: str) -> None:
    try:
        await face_votes.enrich(event_id)  # P144-s29: the late vote's image, read before the re-decision
        async with _lock.turn():  # never at the same time as the pipe deciding an event
            await asyncio.to_thread(_late_update, event_id)
    except Exception as e:
        log.warning("[FRIGATE_USER] late face vote for %s failed: %s", event_id, type(e).__name__)


def _face_of(ref: str | None) -> str | None:
    """The face name behind a stored person ref (a household username → its first linked face)."""
    if not ref:
        return None
    try:
        user = user_store.get_user(ref)
    except Exception:
        user = None
    linked = [f for f in (user or {}).get("frigate_faces") or [] if isinstance(f, str)]
    return linked[0] if linked else ref


def _vote_of(d: dict) -> checks.Vote:
    return checks.Vote(layer=d.get("layer", ""), verdict=d.get("verdict", ""), strong=bool(d.get("strong")),
                       reason=d.get("reason", ""), contradictions=list(d.get("contradictions") or []))


def _late_update(event_id: str) -> bool:
    """A vote that arrived after its event was stored: re-decide that one event in code from the stored check and
    attributes — no image, no LLM, no 27B re-run (s19 step 0: 0 of 97 votes came after a conclusion; the readers word it
    at read time) — and re-record it through the store's gate. False when nothing changed or nothing may change."""
    if not settings.enabled():
        return False
    ev = store.event_by_id(event_id)
    if ev is None or ev.tier != "event" or ev.label != "person":
        return False
    second = face_votes.reading(event_id)
    if second is None or face_votes.decided_sig(event_id) == _reading_sig(second):
        return False
    check = {k: v for k, v in ev.check.items() if k not in ("sensors", "l2", "review", "storage_gate")}
    votes = [_vote_of(d) for d in (check.get("l0"), *(check.get("l1") or []), check.get("l3")) if isinstance(d, dict)]
    attributes = ev.attributes or {}
    if check.get("claim") and check.get("claim_source") != "double_take":
        sensors = ev.check.get("sensors") or {}
        frigate_ref = sensors.get("frigate") if "sensors" in ev.check else ev.person_ref
        frigate_kind = ev.person_kind if frigate_ref == ev.person_ref else (faces.classify(_face_of(frigate_ref)))
        if not frigate_ref:
            return False  # Frigate's claim was dropped (rejected / opted out) — nothing to re-weigh by name
        status = check.get("frigate_decision") or ev.claim_status
        if sensors.get("frigate_doubt"):
            check["doubt"] = sensors["frigate_doubt"]
        claim_face = sensors.get("frigate_face") or _face_of(frigate_ref)
        person_ref, person_kind = frigate_ref, frigate_kind
    else:
        dt_vote = face_votes.single_vote(second)
        if dt_vote is None:
            check["l2"] = face_votes.mask_for_storage({}, {**second, "names": []})["l2"]
            return _rerecord(ev, check, ev.claim_status, ev.person_ref, ev.person_kind, second)
        claim_face, person_ref, person_kind = dt_vote["face"], dt_vote["ref"], dt_vote["kind"]
        second = {**second, "claim_source": "double_take"}
        check.update({"claim": True, "claim_source": "double_take"})
        l3 = (checks.l3_compare(attributes, faces.attribute_profile(claim_face)) if attributes.get("persons") is not None
              else None)
        votes = [l3] if l3 is not None else []
        if l3 is not None:
            check["l3"] = l3.as_dict()
        status = checks.decide(has_claim=True, votes=votes, check_ran=l3 is not None)
    status, person_ref, person_kind, _switched = _fold_second_sensor(
        check, votes, attributes, second, status, person_ref, person_kind, claim_face)
    return _rerecord(ev, check, status, person_ref, person_kind, second)


def _rerecord(ev: store.FrigateUserEvent, check: dict, status: str, person_ref: str | None, person_kind: str,
              second: dict) -> bool:
    _remember_sig(ev.event_id, second)
    if isinstance(check.get("l2"), dict):
        check["l2"]["late"] = True
    check["decision"] = status
    ok = store.record_event(replace(ev, claim_status=status, person_ref=person_ref, person_kind=person_kind or "unknown",
                                    check=check))
    log.info("[FRIGATE_USER] event %s: late face vote → decision=%s", ev.event_id, status)
    return ok


def _second_observation(ev: store.FrigateUserEvent, kept: store.FrigateUserEvent, doubt: dict,
                        names: set[str]) -> list[str]:
    """Two-observation rule (P144-s06c): other events of the same claimed person in the same episode whose doubt
    agrees with this one → both rejected (re-recorded through the store's gate, which then drops the name). Returns the
    agreeing event ids; [] leaves the event unverified with its doubt."""
    ep = store.episode(kept.episode_id) if kept.episode_id is not None else None
    if ep is None:
        return []
    others: dict[str, store.FrigateUserEvent] = {}
    for name in names - {None, ""}:
        for e in store.events_between(ep.start_ts, ep.end_ts, person_ref=name):
            if e.episode_id == ep.episode_id and e.event_id != ev.event_id and e.claim_status == "unverified":
                others[e.event_id] = e
    episode_doubts = [{**c, "event_id": e.event_id} for e in others.values()
                      for c in (e.check.get("doubt") or {}).get("strong", [])]
    matched = checks.matching_doubts(doubt["strong"], episode_doubts)
    if not matched:
        return []
    check = {**ev.check, "decision": "rejected", "doubt": {**doubt, "confirmed_by": matched}}
    store.record_event(replace(ev, claim_status="rejected", check=check))
    for other_id in matched:
        o = others[other_id]
        o_doubt = o.check.get("doubt") or {}
        o_check = {**o.check, "decision": "rejected",
                   "doubt": {**o_doubt, "confirmed_by": sorted({*o_doubt.get("confirmed_by", []), ev.event_id})}}
        store.record_event(replace(o, claim_status="rejected", check=o_check))
    return matched


async def _visit_loop() -> None:
    """Fold events into visits on startup and then periodically (the store's close_visits; episodes are s06b's)."""
    while True:
        try:
            if settings.enabled():
                await asyncio.to_thread(store.close_visits)
                await asyncio.to_thread(store.expire_unknown_plates)  # «Keep unknown plates» (s07)
        except Exception as e:
            log.warning("[FRIGATE_USER] close_visits failed: %s", e)
        await asyncio.sleep(settings.visit_fold_interval_s())
