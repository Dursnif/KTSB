"""Extra face recognition — the second face sensor's port (P144-s19; P130 «extra face verification», first provider).

A second, independent face sensor gives its own vote on who a Frigate event shows. It is a claim on the same footing as
Frigate's face name, never a fact (P144 decision 2026-10-27); the fusion rules are `checks.fuse_second_sensor`, the
probability word is `query.certainty`. Set in Integrations → Frigate → «Extra face recognition»
(`configs/frigate_user.yaml` `face_votes:`), off by default; it only runs while the Frigate user runs.

Provider «double_take» (Double Take over the household's MQTT broker, read and subscribe only — KTSB never publishes):
  `<prefix>/matches/<name>` — one recognised face: `{id: <Frigate event id>, camera, match: {name, confidence, match,
                               type, …, base64}}` (base64 is never kept);
  `<prefix>/cameras/<camera>` — one attempt's summary: `{id, camera, matches[], misses[], unknowns[]}`;
  `<prefix>/available`        — `online` / `offline` (retained) → the status line.
Votes are joined to the Frigate event by its id and held in RAM per event (Double Take mostly answers before Frigate's
end event, step 0 of s19: median 10 s before, at most ~19 s after); the pipe reads them when it decides and re-decides a
stored event in code when a vote comes late. A name maps to a household person only through the face link on the user
(users.db) or the face classification (case-insensitive); any other name is «unknown person», never a guessed user.
`match: false` best guesses are kept raw for the phase-4b review list, never a vote. Consent: names are masked here
before anything is stored (`mask_for_storage`) and the store's gate applies to the stored person as for Frigate.
Missing, off or offline = no vote, never an error path (rule 24). Everything here is RAM; nothing is logged by name.

Per detector (P144-s29): every result carries Double Take's `detector`, `box` and `filename`, but Double Take merges the
detectors per name before MQTT (the stronger one wins), so `enrich()` reads its REST rows (`GET /api/match`, joined by
`filename`; optional, only with `rest_url`) for every detector's full answer and the image size. Each detector gets its
own vote and its GUI word (`face_sensors`, read only through `sensor_weight()`); `reading()` fuses them by weighted majority
and drops a torso-sized «face» no other detector saw (MAX_FACE_SHARE, measured).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from kaare_core.frigate_user import checks, faces, modules, questions, settings, store
from kaare_core.users import store as user_store

log = logging.getLogger(__name__)

PROVIDER = "double_take"
_TTL_S = 30 * 60  # a vote older than this belongs to no event the pipe will still decide
_MAX_EVENTS = 2000
_NO_MATCH_KEEP = 3
_TEST_TIMEOUT_S = 5


@dataclass
class _EventVotes:
    camera: str
    first_rx: float
    last_rx: float
    # P144-s29: per image Double Take checked (its `filename`; «_m<n>» when a message has none) → {size, rest,
    # results: {detector: [{name, confidence, match, box}]}}. MQTT fills it partly (Double Take merges the detectors
    # per name before publishing); the REST row of the same filename replaces it with every detector's full answer.
    images: OrderedDict = field(default_factory=OrderedDict)
    unknown_faces: int = 0
    types: set[str] = field(default_factory=set)
    messages: int = 0


_lock = threading.Lock()
_buffer: OrderedDict[str, _EventVotes] = OrderedDict()
_status: dict = {"available": "unknown", "available_ts": None, "votes_seen": 0, "last_vote_ts": None}
_late_handler: Callable[[str], None] | None = None
# What the pipe last decided each event with (RAM only — names never go to disk through this): a repeat is no news.
_decided: OrderedDict[str, tuple] = OrderedDict()
# Double Take REST rows by filename (P144-s29) — an image is read once; detectors seen (for the GUI weight list).
_rest_rows: OrderedDict[str, dict] = OrderedDict()
_REST_KEEP = 500
_REST_PAGES = 2  # newest first, 50 per page: the votes come seconds before the pipe decides (s19 step 0)
_REST_TIMEOUT_S = 3
_REST_MIN_INTERVAL_S = 1.0  # one install-wide read at most this often (Double Take runs on a small HA box)
_last_rest = {"ts": 0.0, "ok": None, "error": ""}
_detectors_seen: dict[str, float] = {}
# Face-box sanity check (P144-s29, measured on 696 Double Take images 2026-10-31…11-05): a «face» taller than this share
# of the image Double Take checked, that another detector which ran on the same image did not see at all, is a torso
# or a back (3 of 3 such boxes seen; 0 of 396 real InsightFace faces and 0 real CPAI-only faces — largest 0.39 —
# would be dropped). Real doorbell close-ups reach 0.57, so size alone never decides; with one detector the check is off.
MAX_FACE_SHARE = 0.40


def reset_for_tests() -> None:
    global _late_handler
    with _lock:
        _buffer.clear()
        _decided.clear()
        _rest_rows.clear()
        _detectors_seen.clear()
        _status.update({"available": "unknown", "available_ts": None, "votes_seen": 0, "last_vote_ts": None})
        _last_rest.update({"ts": 0.0, "ok": None, "error": ""})
    _late_handler = None


def set_late_handler(fn: Callable[[str], None] | None) -> None:
    """The pipe's hook for a vote that arrives after its event was stored: `fn(event_id)`, called from on_message."""
    global _late_handler
    _late_handler = fn


# ── settings ────────────────────────────────────────────────────────────────


def _cfg() -> dict:
    try:
        return settings.face_votes()
    except Exception:
        return {"enabled": False}


def enabled() -> bool:
    try:
        # J21 5e: module 2's panel switch — off = Double Take's votes are not used (auto = as configured here)
        return bool(settings.enabled() and _cfg().get("enabled") and modules.is_on(2))
    except Exception:
        return False


def prefix() -> str:
    return str(_cfg().get("topic_prefix") or "double-take")


def topics() -> list[str]:
    """The MQTT topics to subscribe to — none while off (the Frigate user or this sensor)."""
    if not enabled():
        return []
    p = prefix()
    return [f"{p}/matches/#", f"{p}/cameras/+", f"{p}/available"]


def handles(topic: str) -> bool:
    return enabled() and topic.startswith(prefix() + "/")


def sensor_weight(sensor: str, camera: str | None, light: str | None) -> dict:
    """THE one place the fusion reads how much a face sensor counts (P144-s29b). `sensor` = «frigate» or a detector by
    the name its provider reports. Returns {word, action, attention, source, learned, label}:
      - source «admin»: the word admin set in the GUI (Integrations → Frigate → «Ansiktssensorer») — always wins;
      - source «auto»: KTSB's own value. **KTSB's weight system plugs in here** (P115 decision 13 «Weight model v2»:
        action from Validated — starts cautious, rises with «ja» to «Var det deg?»; attention from the childhood weights
        P144 #7 — starts high). P149 phase 2 (shadow): `learned` = human «Var det deg?» answers behind this sensor
        and `learned_word` the word they give; auto still acts as «uncertain» and `attention` is None.
    `camera` + `light` («day»/«ir») select one learned cell (#21b); otherwise every cell of the sensor counts. Never
    changes anything (echo rule: agreement between sensors is not an answer)."""
    try:
        cfg = settings.face_sensors()
    except Exception:
        cfg = {"auto_learning": True, "frigate": {"mode": "auto"}, "detectors": {}}
    entry = cfg["frigate"] if sensor == "frigate" else (cfg.get("detectors") or {}).get(sensor) or {}
    label = entry.get("label") or ("Frigate" if sensor == "frigate" else sensor or "Double Take")
    learned, learned_word = _learned(sensor, camera, light)
    acting = False
    if entry.get("mode") == "admin" and entry.get("trust") in checks.TRUST_WEIGHTS:
        word, source = entry["trust"], "admin"
    else:
        # J2b (2026-11-06): Auto uses the learned word once ~5 answers stand behind it (per camera) and neither the
        # house switch nor this sensor's switch is off; else «uncertain».
        word, acting = auto_word(cfg, entry, learned, learned_word)
        source = "auto"
    return {"word": word, "action": checks.TRUST_WEIGHTS[word], "attention": None, "source": source,
            "learned": learned, "learned_word": learned_word, "acting": acting, "label": label}


def _learned(sensor: str, camera: str | None, light: str | None) -> tuple[int | None, str | None]:
    """(human answers behind this sensor, the word they give) from KTSB's weight system — one camera × light when both
    are known, one camera (both lights) when only the camera is, else every camera × light. (None, None) while no one has
    answered. Reads RAM; never raises."""
    try:
        sl = questions.sensor_learning(sensor, camera or None, light if camera and light in ("day", "ir") else None)
        return (sl["learned_n"] or None), sl["word"]
    except Exception as e:
        log.debug("sensor learning unavailable: %s", type(e).__name__)
        return None, None


AUTO_MIN_ANSWERS = 5  # model constant (P149 D3, 2026-11-06 «etter omtrent 5 svar»): Auto uses what it learned from here


def auto_word(cfg: dict, entry: dict, learned: int | None, learned_word: str | None) -> tuple[str, bool]:
    """(word, acting) for a sensor in Auto: the learned word once about five people's answers stand behind it and
    neither the house switch nor this sensor's switch (`auto_learning`) is off; else «uncertain». Never «verified»."""
    on = cfg.get("auto_learning", True) is not False and entry.get("auto_learning", True) is not False
    if on and learned and learned >= AUTO_MIN_ANSWERS and learned_word in checks.TRUST_WEIGHTS and learned_word != "off":
        return learned_word, True
    return "uncertain", False


# ── MQTT in ─────────────────────────────────────────────────────────────────


def _conf(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _results(payload: dict) -> list[dict]:
    """Every face result in one message, as {name, confidence, match, type, detector, filename, box}."""
    out = []
    m = payload.get("match")
    if isinstance(m, dict):
        out.append(m)
    for key in ("matches", "misses", "unknowns"):
        items = payload.get(key)
        if isinstance(items, list):
            out.extend(i for i in items if isinstance(i, dict))
    return out


def _box(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    vals = {k: value.get(k) for k in ("top", "left", "width", "height")}
    return vals if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals.values()) else None


def _result(r: dict) -> dict:
    name = r.get("name") if isinstance(r.get("name"), str) else ""
    return {"name": name.strip().casefold(), "confidence": _conf(r.get("confidence")) or 0.0,
            "match": r.get("match") is True, "box": _box(r.get("box"))}


def _image(ev: _EventVotes, filename: str) -> dict:
    img = ev.images.get(filename)
    if img is None:
        img = ev.images[filename] = {"size": None, "rest": False, "results": {}}
    return img


def _has_match(ev: _EventVotes) -> bool:
    return any(x["match"] and x["name"] and x["name"] != "unknown"
               for img in ev.images.values() for rs in img["results"].values() for x in rs)


def on_message(topic: str, payload: bytes, now: float | None = None) -> str | None:
    """Buffer one provider message. Returns the Frigate event id it belongs to, or None (status, ignored, junk).
    Sync and RAM-only — called from the MQTT listener, never blocks it."""
    if not handles(topic):
        return None
    now = time.time() if now is None else now
    rest = topic[len(prefix()) + 1:]
    if rest == "available":
        state = bytes(payload).decode("utf-8", errors="replace").strip().lower()
        with _lock:
            _status["available"] = state if state in ("online", "offline") else "unknown"
            _status["available_ts"] = now
        return None
    if not (rest.startswith("matches/") or (rest.startswith("cameras/") and rest.count("/") == 1)):
        return None  # e.g. cameras/<camera>/person — a count, not a vote
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    event_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(event_id, str) or not event_id:
        return None
    camera = data.get("camera") if isinstance(data.get("camera"), str) else ""
    with _lock:
        ev = _buffer.get(event_id)
        if ev is None:
            ev = _buffer[event_id] = _EventVotes(camera=camera, first_rx=now, last_rx=now)
        ev.last_rx, ev.messages = now, ev.messages + 1
        had_vote = _has_match(ev)
        unknown_here = 0
        for r in _results(data):
            if isinstance(r.get("type"), str):
                ev.types.add(r["type"])
            res = _result(r)
            if not res["name"] or res["name"] == "unknown":
                unknown_here += 1
            detector = r.get("detector") if isinstance(r.get("detector"), str) else ""
            filename = r.get("filename") if isinstance(r.get("filename"), str) and r.get("filename") else None
            img = _image(ev, filename or f"_m{ev.messages}")
            if detector:
                _detectors_seen[detector] = now
            if img["rest"]:
                continue  # the REST row already holds every detector's full answer for this image
            known = img["results"].setdefault(detector, [])
            if not any((k["name"], k["confidence"], k["match"]) == (res["name"], res["confidence"], res["match"])
                       for k in known):
                known.append(res)
        # Double Take repeats an attempt's unknowns on `matches/unknown` and `cameras/<camera>` — the most one
        # message saw, not the sum.
        ev.unknown_faces = max(ev.unknown_faces, unknown_here)
        if _has_match(ev):
            _status["votes_seen"] += 0 if had_vote else 1  # events with a vote, not messages
            _status["last_vote_ts"] = now
        _buffer.move_to_end(event_id)
        _expire(now)
    hook = _late_handler
    if hook is not None:
        try:
            hook(event_id)
        except Exception as e:
            log.debug("[FACE_VOTES] late handler failed: %s", type(e).__name__)
    return event_id


def _expire(now: float) -> None:
    while _buffer:
        oldest_id, oldest = next(iter(_buffer.items()))
        if len(_buffer) <= _MAX_EVENTS and now - oldest.last_rx <= _TTL_S:
            break
        _buffer.pop(oldest_id)


# ── Double Take's REST rows (P144-s29, GET only) ────────────────────────────


async def _fetch_page(base: str, page: int) -> list[dict]:
    """One page of Double Take's match history (`GET /api/match?page=N`, newest first). Raises on any failure."""
    async with httpx.AsyncClient(timeout=_REST_TIMEOUT_S) as client:
        resp = await client.get(f"{base}/api/match", params={"page": page})
    resp.raise_for_status()
    rows = resp.json().get("matches")
    return rows if isinstance(rows, list) else []


def _apply_row(ev: _EventVotes, filename: str, row: dict) -> None:
    resp = row.get("response")
    if isinstance(resp, str):
        try:
            resp = json.loads(resp)
        except ValueError:
            return
    if not isinstance(resp, list):
        return
    f = row.get("file") if isinstance(row.get("file"), dict) else {}
    w, h = f.get("width"), f.get("height")
    size = (w, h) if all(isinstance(v, (int, float)) and v > 0 for v in (w, h)) else None
    results: dict[str, list[dict]] = {}
    for attempt in resp:
        if not isinstance(attempt, dict) or not isinstance(attempt.get("detector"), str):
            continue
        rs = attempt.get("results") if isinstance(attempt.get("results"), list) else []
        results.setdefault(attempt["detector"], []).extend(_result(r) for r in rs if isinstance(r, dict))
        _detectors_seen[attempt["detector"]] = time.time()
    ev.images[filename] = {"size": size, "rest": True, "results": results}


def _missing(event_id: str) -> list[str]:
    ev = _buffer.get(event_id)
    if ev is None:
        return []
    return [fn for fn, img in ev.images.items() if not img["rest"] and not fn.startswith("_m")]


def _use_cached(event_id: str) -> list[str]:
    """Fill the event's images from rows already read; returns the filenames still missing."""
    with _lock:
        ev = _buffer.get(event_id)
        for fn in _missing(event_id):
            if fn in _rest_rows and ev is not None:
                _apply_row(ev, fn, _rest_rows[fn])
        return _missing(event_id)


async def enrich(event_id: str) -> None:
    """Read Double Take's REST rows for the images this event's votes came from (P144-s29): restores the detector
    Double Take's MQTT merge hid and the image size the face-box check needs. At most one install-wide read per
    second, ≤ 2 pages, each image once; no `rest_url`, off, or any failure → the MQTT reading stands. Never raises."""
    try:
        if not enabled() or not event_id:
            return
        base = str(_cfg().get("rest_url") or "").strip().rstrip("/")
        if not base or not _use_cached(event_id):
            return
        wait = _REST_MIN_INTERVAL_S - (time.time() - _last_rest["ts"])
        if wait > 0:
            await asyncio.sleep(wait)
        _last_rest["ts"] = time.time()
        for page in range(1, _REST_PAGES + 1):
            rows = await _fetch_page(base, page)
            with _lock:
                for row in rows:
                    fn = (row.get("file") or {}).get("filename") if isinstance(row, dict) else None
                    if isinstance(fn, str) and fn:
                        _rest_rows[fn] = row
                        _rest_rows.move_to_end(fn)
                while len(_rest_rows) > _REST_KEEP:
                    _rest_rows.popitem(last=False)
            if not _use_cached(event_id) or not rows:
                break
        _last_rest.update({"ok": True, "error": ""})
    except Exception as e:
        _last_rest.update({"ok": False, "error": type(e).__name__})
        log.debug("[FACE_VOTES] REST read failed: %s", type(e).__name__)


# ── names ───────────────────────────────────────────────────────────────────


def _known_faces() -> list[str]:
    names = list(faces.configured_faces())
    try:
        for u in user_store.list_users():
            names.extend(f for f in (u.get("frigate_faces") or []) if isinstance(f, str))
    except Exception as e:
        log.debug("[FACE_VOTES] user faces unreadable: %s", type(e).__name__)
    return names


def map_name(name: str) -> tuple[str | None, str | None, str]:
    """(face spelling, person ref, kind) for a provider name — the same mapping the pipe uses for Frigate's claim:
    a household face → its linked KTSB user, any other classified face → its face spelling. A name KTSB does not know
    → (None, None, «unknown»); never a guessed user."""
    wanted = (name or "").strip().casefold()
    face = next((n for n in _known_faces() if n.casefold() == wanted), None) if wanted else None
    if face is None:
        return None, None, "unknown"
    kind = faces.classify(face)
    try:
        linked = user_store.get_username_for_frigate_face(face)
    except Exception:
        linked = ""
    return face, (linked or face) if kind == "household" else face, kind  # as the pipe does for Frigate's claim


def _torso_like(img: dict, detector: str, res: dict) -> bool:
    """The face-box sanity check (MAX_FACE_SHARE): only on a REST image (its size known) where another detector ran
    and found no face at all."""
    size, box = img.get("size"), res.get("box")
    if not img.get("rest") or not size or not box or box["height"] <= MAX_FACE_SHARE * size[1]:
        return False
    return any(d != detector and not rs for d, rs in img["results"].items())


def _per_detector(ev: _EventVotes, min_conf: float) -> tuple[dict, dict, dict, dict]:
    """(matches {det: {name: conf}}, misses {name: conf}, discarded {det: n}, face_found {det: True/False/None})."""
    matches: dict[str, dict[str, float]] = {}
    misses: dict[str, float] = {}
    discarded: dict[str, int] = {}
    face_found: dict[str, bool | None] = {}
    for img in ev.images.values():
        for det, rs in img["results"].items():
            matches.setdefault(det, {})
            discarded.setdefault(det, 0)
            if rs:
                face_found[det] = True
            elif img["rest"] and face_found.get(det) is None:
                face_found[det] = False
            else:
                face_found.setdefault(det, None)
            for res in rs:
                name = res["name"]
                if not name or name == "unknown":
                    continue
                if _torso_like(img, det, res):
                    discarded[det] += 1
                elif res["match"] and res["confidence"] >= min_conf:
                    matches[det][name] = max(res["confidence"], matches[det].get(name, 0.0))
                else:
                    misses[name] = max(res["confidence"], misses.get(name, 0.0))
    return matches, misses, discarded, face_found


def reading(event_id: str, claim_source: str | None = None) -> dict | None:
    """What the second sensor said about one Frigate event (RAM, names not yet consent-masked), or None (off / nothing).
    {provider, names: [{face, ref, kind, confidence}] (the fused vote), split, unmapped, support, detectors: [{detector,
    label, weight, names, split, unmapped, face_found, discarded}], no_match: [{ref, kind, confidence}] (best guesses +
    votes under the minimum, mapped only, ≤ 3), unknown_faces, types, first_rx, last_rx}.

    The fused vote (P144-s29) is a weighted majority: a detector with exactly one name gives its weight to it; the name
    with more than half of the weight of all detectors that named anyone wins; otherwise «split». Weight 0 = shown,
    never counts. Equal weights = the s19 behaviour (same name → a vote, different names → split)."""
    if not enabled() or not event_id:
        return None
    cfg = _cfg()
    with _lock:
        ev = _buffer.get(event_id)
        if ev is None:
            return None
        matches, misses, discarded, face_found = _per_detector(ev, float(cfg.get("min_confidence") or 0.0))
        unknown_faces, types, first_rx, last_rx = ev.unknown_faces, sorted(ev.types), ev.first_rx, ev.last_rx
        camera = ev.camera
    frigate_w = sensor_weight("frigate", camera, None)
    detectors, scores, best, total, trusts = [], {}, {}, 0.0, {}
    for det in sorted(matches):
        sw = sensor_weight(det, camera, None)
        label, weight = sw["label"], sw["action"]
        trusts[det] = sw
        names, unmapped = [], 0
        for n, c in sorted(matches[det].items(), key=lambda kv: -kv[1]):
            face, ref, kind = map_name(n)
            if ref is None:
                unmapped += 1
            else:
                names.append({"face": face, "ref": ref, "kind": kind, "confidence": c})
        keys = {x["ref"] for x in names} | ({f"?{n}" for n, _c in matches[det].items() if map_name(n)[1] is None})
        split = len(keys) > 1
        detectors.append({"detector": det, "label": label, "weight": weight, "trust": sw["word"],
                          "source": sw["source"], "names": names, "split": split,
                          "unmapped": unmapped, "face_found": face_found.get(det), "discarded": discarded.get(det, 0)})
        if weight <= 0 or not keys:
            continue
        total += weight
        if split:
            continue
        key = next(iter(keys))
        scores[key] = scores.get(key, 0.0) + weight
        trusts.setdefault(("winner", key), []).append(sw)
        if names and (key not in best or names[0]["confidence"] > best[key]["confidence"]):
            best[key] = names[0]
    winner = next((k for k, v in scores.items() if v > total / 2), None)
    if winner is not None:
        names_out = [best[winner]] if winner in best else []
        split_out, unmapped_out, support = False, 0 if winner in best else 1, scores[winner]
    else:
        cand = {k for d in detectors if d["weight"] > 0 for k in ({x["ref"] for x in d["names"]}
                                                                    | {f"?{i}" for i in range(d["unmapped"])})}
        names_out = []
        for d in detectors:
            for x in d["names"]:
                if d["weight"] > 0 and x["ref"] not in {y["ref"] for y in names_out}:
                    names_out.append(x)
        unmapped_out = sum(1 for k in cand if k.startswith("?"))
        split_out, support = len(cand) > 1, None
    refs = {n["ref"] for n in names_out}
    no_match = []
    for n, c in sorted(misses.items(), key=lambda kv: -kv[1]):
        _face, ref, kind = map_name(n)
        if ref is not None and ref not in refs and len(no_match) < _NO_MATCH_KEEP:
            no_match.append({"ref": ref, "kind": kind, "confidence": c})
    # The word of a vote one provider alone stands behind (s29b): admin's best word when every backing detector is set by
    # admin; otherwise auto.
    backers = trusts.get(("winner", winner), []) if winner is not None else []
    if backers and all(b["source"] == "admin" for b in backers):
        best_word = max((b["word"] for b in backers), key=checks.TRUST_RANK.index)
        winner_trust = {"word": best_word, "source": "admin"}
    else:
        winner_trust = {"word": "uncertain", "source": "auto"}
    out = {"provider": PROVIDER, "names": names_out, "split": split_out, "unmapped": unmapped_out, "support": support,
           "total_weight": total, "winner_trust": winner_trust,
           "frigate_trust": {k: frigate_w[k] for k in ("word", "action", "source")},
           "detectors": detectors, "no_match": no_match, "unknown_faces": unknown_faces, "types": types,
           "first_rx": first_rx, "last_rx": last_rx}
    if claim_source:
        out["claim_source"] = claim_source
    return out


def reading_sig(r: dict | None) -> tuple:
    """What a reading said, comparable: names + confidences, split, unmapped, best guesses, and per detector."""
    if not r:
        return ()
    return (tuple((n["ref"], n["confidence"]) for n in r.get("names") or []), bool(r.get("split")), r.get("unmapped"),
            tuple((n["ref"], n["confidence"]) for n in r.get("no_match") or []),
            tuple((d["detector"], tuple(x["ref"] for x in d["names"]), d["unmapped"], d["discarded"], d["face_found"],
                   d["weight"]) for d in r.get("detectors") or []))


def remember_decided(event_id: str, r: dict | None) -> None:
    with _lock:
        _decided[event_id] = reading_sig(r)
        _decided.move_to_end(event_id)
        while len(_decided) > _MAX_EVENTS:
            _decided.popitem(last=False)


def decided_sig(event_id: str) -> tuple | None:
    with _lock:
        return _decided.get(event_id)


def single_vote(r: dict | None) -> dict | None:
    """The one mapped candidate of a reading, or None (no vote, split, or only unknown names)."""
    if not r or r.get("split") or r.get("unmapped") or len(r.get("names") or []) != 1:
        return None
    return r["names"][0]


# ── consent before storage ──────────────────────────────────────────────────


def _hidden(refs) -> set[str]:
    """Names of people who said «Frigate may not track me» (fails closed: the store's own lookup)."""
    return store.hidden_names({r for r in refs if r})


def mask_for_storage(fused: dict, r: dict | None = None) -> dict:
    """The sensor block + reading as they may be stored: an opted-out person on either side is kept as «untracked»
    without a name; a Frigate claim KTSB rejected keeps no name. Fails closed."""
    sensors = dict(fused.get("sensors") or {})
    refs = {sensors.get("frigate"), sensors.get("double_take"), sensors.get("lean")}
    refs |= {sensors.get("frigate_face"), sensors.get("double_take_face")}
    for d in sensors.get("detectors") or []:
        refs |= {d.get("ref"), d.get("face")}
    if r:
        refs |= {n.get("ref") for n in r.get("names") or []} | {n.get("face") for n in r.get("names") or []}
        refs |= {n.get("ref") for n in r.get("no_match") or []}
        for d in r.get("detectors") or []:
            refs |= {n.get("ref") for n in d.get("names") or []} | {n.get("face") for n in d.get("names") or []}
    try:
        hidden = _hidden(refs)
    except Exception:
        hidden = {x for x in refs if x}
    hidden_fold = {h.casefold() for h in hidden}

    def _gone(x) -> bool:
        return isinstance(x, str) and x.casefold() in hidden_fold

    for side in ("frigate", "double_take"):
        if _gone(sensors.get(side)) or _gone(sensors.get(f"{side}_face")):
            sensors[side] = sensors[f"{side}_face"] = None
            sensors[f"{side}_kind"] = "untracked"
    if sensors.get("frigate_status") == "rejected" and sensors.get("frigate"):
        sensors["frigate"] = sensors["frigate_face"] = None
        sensors["frigate_rejected"] = True
    elif sensors.get("frigate_status") == "rejected":
        sensors["frigate_rejected"] = True
    if _gone(sensors.get("lean")):
        sensors["lean"] = "untracked"
    if isinstance(sensors.get("detectors"), list):
        sensors["detectors"] = [{**d, "ref": None, "face": None, "kind": "untracked"}
                                if _gone(d.get("ref")) or _gone(d.get("face")) else dict(d)
                                for d in sensors["detectors"]]
    out = {**fused, "sensors": sensors}
    if r is not None:
        l2 = {k: r[k] for k in ("provider", "split", "unmapped", "unknown_faces", "types", "first_rx", "last_rx")
              if k in r}
        l2["names"] = [{"ref": None, "kind": "untracked", "confidence": n["confidence"]} if _gone(n["ref"])
                       or _gone(n.get("face")) else {"ref": n["ref"], "kind": n["kind"], "confidence": n["confidence"]}
                       for n in r.get("names") or []]
        l2["no_match"] = [n for n in r.get("no_match") or [] if not _gone(n["ref"])]
        if "support" in r:
            l2["support"] = r["support"]
        l2["detectors"] = [
            {**{k: d.get(k) for k in ("detector", "label", "weight", "split", "unmapped", "face_found", "discarded")},
             "names": [{"ref": None, "kind": "untracked", "confidence": n["confidence"]}
                       if _gone(n.get("ref")) or _gone(n.get("face"))
                       else {"ref": n["ref"], "kind": n["kind"], "confidence": n["confidence"]}
                       for n in d.get("names") or []]}
            for d in r.get("detectors") or []]
        out["l2"] = l2
    return out


# ── status (GUI) ────────────────────────────────────────────────────────────


def status() -> dict:
    cfg = _cfg()
    with _lock:
        st = dict(_status)
        st["buffered_events"] = len(_buffer)
        seen = dict(_detectors_seen)
        rest = dict(_last_rest)
    try:
        configured = set(settings.face_sensors().get("detectors") or {})
    except Exception:
        configured = set()
    detectors = [{"detector": d, "label": sensor_weight(d, None, None)["label"], "configured": d in configured,
                  "last_seen_ts": seen.get(d)} for d in sorted(set(seen) | configured)]
    return {**st, "enabled": enabled(), "configured": bool(cfg.get("enabled")), "provider": cfg.get("provider"),
            "topic_prefix": prefix(), "detectors": detectors,
            "rest_read": {"ok": rest["ok"], "error": rest["error"], "last_ts": rest["ts"] or None}}


async def test_rest(url: str | None = None) -> dict:
    """GUI test button: is the provider's REST address reachable? Double Take's `/api/status/auth` (no secrets).
    The address is the one the user set (rule 27 — never filtered here). {ok, status, error}."""
    base = (url if url is not None else _cfg().get("rest_url") or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "status": None, "error": "not_configured"}
    try:
        async with httpx.AsyncClient(timeout=_TEST_TIMEOUT_S) as client:
            resp = await client.get(f"{base}/api/status/auth")
        return {"ok": resp.status_code == 200, "status": resp.status_code, "error": ""}
    except Exception as e:
        return {"ok": False, "status": None, "error": type(e).__name__}
