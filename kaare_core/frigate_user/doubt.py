"""The doubt flag (P144-s28, Design today #23): when is Frigate's own track worth a second opinion?

Since s25 Frigate's track is the presence truth, so nothing in the chain disagrees when Frigate itself is fooled: a white,
out-of-focus shape right at an IR camera's lens became a «person» with clothes and a hat (ep 134, 135, 137). This module
is code only — no model, no I/O beyond the JPEG bytes it is handed. It only decides whether the 27B looks at the images;
it takes nothing away by itself, and `conclude` decides on the answer.

The rule (measured on 510 night person tracks, 31 of them IR — `p144-s28` log): a person/animal track is doubtful when
most of its boxed frames are IR **and** at least BURNT_SHARE of the pixels inside Frigate's box are burnt out. It caught
the 4 false tracks and flagged 1 real person (white jacket, close to the camera). Not s18's general quality filter (no
whole-frame blur or brightness, which did not predict bad answers): one narrow check inside Frigate's own box. A camera
that is never IR never flags: the same behaviour as before.
"""

from __future__ import annotations

import io
import json
import logging
import re

import numpy as np
from PIL import Image

from kaare_core.frigate_user import checks, store
from kaare_core.frigate_user import episode_state as es
from kaare_core.frigate_user.frames import Frame

log = logging.getLogger(__name__)

BURNT_LUMA = 235  # a pixel at or above this (0–255) is burnt out
BURNT_SHARE = 0.15  # false tracks 0.19–0.39 per frame; real IR tracks ≤ 0.09, one real white jacket 0.20
MIN_BOX_PX = 8
BEING_LABELS = ("person", *store.ANIMAL_LABELS)
SIGNAL = "ir_burnt_box"

LOOK_FRAMES = 4  # separate frames for the 27B, plus the crop (P144-s09: separate frames beat a grid)
CROP_PAD = 0.15
CROP_MIN_SIDE = 256
CROP_QUALITY = 92

ANSWERS = ("nothing", "at_lens", "person", "animal", "unclear")
FALSE_ANSWERS = ("at_lens", "nothing")  # code's reading: no being in the scene
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _box_px(box, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    try:
        x, y, w, h = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x0, y0 = max(int(x * size[0]), 0), max(int(y * size[1]), 0)
    x1, y1 = min(int((x + w) * size[0]), size[0]), min(int((y + h) * size[1]), size[1])
    return None if x1 - x0 < MIN_BOX_PX or y1 - y0 < MIN_BOX_PX else (x0, y0, x1, y1)


def frame_signals(jpeg: bytes, box) -> dict | None:
    """{ir, burnt} for one frame and Frigate's relative box, None when it cannot be read."""
    try:
        img = Image.open(io.BytesIO(jpeg))
        img.load()
    except Exception:
        return None
    px = _box_px(box, img.size)
    if px is None:
        return None
    inside = np.asarray(img.convert("L").crop(px))
    return {"ir": checks.is_ir(img), "burnt": round(float((inside >= BURNT_LUMA).mean()), 3)}


def any_ir(picked: list[Frame]) -> bool:
    """Is any frame IR? The cheap first question: an ordinary episode stops here, before any timeline is fetched."""
    for frame in picked:
        try:
            if checks.is_ir(Image.open(io.BytesIO(frame.jpeg))):
                return True
        except Exception:  # one bad frame must not stop the check
            continue
    return False


def _covers(track: es.Track, ts: float) -> bool:
    return track.start - es.START_TOLERANCE_S <= ts <= track.last_seen + es.LINGER_FALLBACK_S


def assess(picked: list[Frame], tracks: list[es.Track]) -> dict:
    """{flag, signals, tracks: [{event_id, camera, label, frames, doubtful, burnt}]} — per person/animal track over the
    frames it covers. Never raises on a bad frame or a missing box: what cannot be read does not count."""
    out = []
    for track in tracks:
        if track.label not in BEING_LABELS:
            continue
        rows = []
        for frame in picked:
            if frame.camera != track.camera or not _covers(track, frame.ts):
                continue
            box = track.box_at(frame.ts)
            sig = frame_signals(frame.jpeg, box) if box else None
            if sig is not None:
                rows.append(sig)
        if not rows:
            continue
        doubtful = sum(r["ir"] and r["burnt"] >= BURNT_SHARE for r in rows)
        out.append({"event_id": track.event_id, "camera": track.camera, "label": track.label, "frames": len(rows),
                    "doubtful": doubtful, "burnt": round(float(np.median([r["burnt"] for r in rows])), 3)})
    flag = any(is_doubtful(t) for t in out)
    return {"flag": flag, "signals": [SIGNAL] if flag else [], "tracks": out}


def is_doubtful(row: dict) -> bool:
    return row.get("doubtful", 0) * 2 > row.get("frames", 0)


def _crop(jpeg: bytes, box) -> bytes | None:
    try:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:
        return None
    px = _box_px(box, img.size)
    if px is None:
        return None
    pad_x, pad_y = int((px[2] - px[0]) * CROP_PAD), int((px[3] - px[1]) * CROP_PAD)
    crop = img.crop((max(px[0] - pad_x, 0), max(px[1] - pad_y, 0), min(px[2] + pad_x, img.width),
                     min(px[3] + pad_y, img.height)))
    scale = CROP_MIN_SIDE / min(crop.size)
    if scale > 1:
        crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=CROP_QUALITY)
    return buf.getvalue()


def look_material(picked: list[Frame], tracks: list[es.Track], assessment: dict) -> tuple[list[bytes], int]:
    """([up to LOOK_FRAMES frames of the doubtful track's camera in time order, the crop of its largest box], the number
    of the frame the crop comes from, 1-based). ([], 0) when nothing can be shown."""
    row = next((t for t in assessment.get("tracks") or [] if is_doubtful(t)), None)
    if row is None:
        return [], 0
    track = next((k for k in tracks if k.event_id == row["event_id"]), None)
    mine = sorted((f for f in picked if track is not None and f.camera == row["camera"] and _covers(track, f.ts)),
                  key=lambda f: f.ts)
    if not mine:
        return [], 0
    if len(mine) > LOOK_FRAMES:
        step = (len(mine) - 1) / (LOOK_FRAMES - 1)
        mine = [mine[round(i * step)] for i in range(LOOK_FRAMES)]

    def area(frame: Frame) -> float:
        box = track.box_at(frame.ts)
        return float(box[2]) * float(box[3]) if box else 0.0

    best = max(range(len(mine)), key=lambda i: area(mine[i]))
    box = track.box_at(mine[best].ts)
    crop = _crop(mine[best].jpeg, box) if box else None
    images = [f.jpeg for f in mine]
    return ([*images, crop], best + 1) if crop else (images, 0)


def parse_answer(text: str) -> tuple[str | None, str]:
    """(answer, '') or (None, why it was refused). Prose in front of the JSON is fine (measured: a few answers have it)."""
    raw = _FENCE_RE.sub("", (text or "").strip()).strip()
    if not raw:
        return None, "empty"
    start, end = raw.rfind("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "no_json_object"
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        return None, f"json_error: {e.msg}"
    answer = str(data.get("answer") or "").strip().lower() if isinstance(data, dict) else ""
    return (answer, "") if answer in ANSWERS else (None, "answer_invalid")


def doubtful_ids(assessment: dict | None) -> set[str]:
    return {t["event_id"] for t in (assessment or {}).get("tracks") or [] if is_doubtful(t)}


def other_person(assessment: dict | None, events: list[store.FrigateUserEvent]) -> bool:
    """Is there a person in the episode that the flag does not doubt? A person event on another camera than every
    doubtful track, or a track with boxed frames that is not doubtful. A second event of the same doubtful camera with
    no boxed frame says nothing either way (ep 135's last short track)."""
    rows = (assessment or {}).get("tracks") or []
    doubted = {t["event_id"] for t in rows if is_doubtful(t)}
    doubted_cameras = {t["camera"] for t in rows if is_doubtful(t)}
    if any(t["label"] == "person" and t["event_id"] not in doubted for t in rows):
        return True
    return any(e.label == "person" and e.event_id not in doubted and e.camera not in doubted_cameras for e in events)
