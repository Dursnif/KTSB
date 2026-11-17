"""What is different after an episode than before it (P144-s27, Design today #5).

The walk test's jacket left on the car roof was missed by every model and variant. Code finds it by comparing the scene
before the episode with the scene after it, on the same camera. Code only: no model, no setting, no label.

  - structure, not brightness (the SSIM structure term per block): plain differencing gave 15 false regions on two empty
    frames (light, dark-sky noise, headlights);
  - two references and two after frames; a region must differ in all four comparisons: a person standing in view before
    Frigate's track starts (ep 102 −5 s) or passing headlights (ep 102 +66 s) fall away;
  - each pair must itself be stable there (dark bushes and blocks that flicker between frames);
  - never inside Frigate's own motion masks (the user drew them — no new setting), nor where a tracked object still stands;
  - only where a Frigate track has been: something left or moved has someone next to it. This one is what removed the bushes
    and the sky (s27 log, 299 idle pairs + real episodes);
  - guards: most of the scene changes (colour ↔ IR at dusk), or an IR frame with burnt-out blobs (spider at the lens).

A region is a place, never a claim: the 9B gets a neutral crop (nothing drawn on it), the 27B a position only.
Blocking work: callers use `asyncio.to_thread` (rule 10).
"""

from __future__ import annotations

import io
import logging
from collections import deque

import numpy as np
from PIL import Image, ImageDraw

from kaare_core.frigate_user import store

log = logging.getLogger(__name__)

BEFORE_S = (45.0, 15.0)  # references this long before the episode (ep 102: he stood in view −5 s, before Frigate's track)
AFTER_S = (6.0, 25.0)  # after frames: past Frigate's linger, and far enough apart to drop headlights and passers-by
BLOCK_PX = 12  # at 480 px: the jacket ≈ 0.0 against ≈ 0.86 in an empty pair; at 270 px too weak (s27)
ANALYSE_HEIGHT = 480
STRUCT_MAX = 0.4  # a block changed when its structure term is under this in all four comparisons
STABLE_MIN = 0.6  # …and over this between the two references and between the two after frames
SSIM_C2 = 58.5  # (0.03 × 255)²: dark sensor noise (variance ~2–5) stays «same»
TEXTURE_MIN = 30.0  # a block with no texture in either frame has no structure to compare
MIN_CELLS = 3
MAX_REGIONS = 3
WORDS_MIN_CELLS = 40  # measured: big regions read right (tarp, window, trailer); small ones invent (a frog, a horse, a man)
LIGHT_SHARE = 0.08  # more than this share of the blocks changed, scattered → the light changed, not a thing
LIGHT_MAX_SHARE = 0.30  # more than this → never one thing
ONE_THING_SHARE = 0.4  # …unless one connected region holds this much of them (ep 72: a loaded trailer drove off)
TRACK_PAD = 0.06  # how close to where a track has been (relative to the frame)
IR_CHROMA_MAX = 1.0  # mean colour distance under this = an IR frame (measured: 0.0 on IR, ≥ 3.7 on colour at night)
BURNT_LUMA = 235
IR_BURNT_SHARE = 0.02  # measured on a709's IR nights: 26 of 33 idle pairs under 2 %; spider nights 2.5–6 %
CROP_PAD = 0.5  # of the region's own size, each side
CROP_MIN_SIDE = 256
CROP_MIN_REL = 0.12  # a crop shows at least this much of the frame's width/height (a 3-block region alone is unreadable)
CROP_QUALITY = 92


def is_mover(label: str) -> bool:
    """Persons, animals, vehicles: what stands somewhere or lays something down (not a plate, a face or a sound)."""
    return label == "person" or label in store.ANIMAL_LABELS or (label in store.VEHICLE_LABELS and label != "license_plate")


def masks_from_camera(camera: dict | None) -> list[str]:
    """Enabled motion mask polygons of one camera in a Frigate /api/config body ('x,y,x,y…', relative). Frigate 0.18 has
    {name: {coordinates, enabled}}; older versions a string or a list of strings. Unreadable ones are left out."""
    mask = ((camera or {}).get("motion") or {}).get("mask")
    if isinstance(mask, str):
        items = [mask]
    elif isinstance(mask, list):
        items = [m for m in mask if isinstance(m, str)]
    elif isinstance(mask, dict):
        items = [v.get("coordinates") for v in mask.values() if isinstance(v, dict) and v.get("enabled", True)]
    else:
        items = []
    return [m for m in items if isinstance(m, str) and _polygon(m)]


def _polygon(coords: str) -> list[float] | None:
    try:
        vals = [float(v) for v in coords.split(",")]
    except ValueError:
        return None
    return vals if len(vals) >= 6 and len(vals) % 2 == 0 else None


def load_frame(jpeg: bytes, height: int = ANALYSE_HEIGHT) -> np.ndarray | None:
    """One frame as YCbCr at `height`; None when unreadable."""
    try:
        im = Image.open(io.BytesIO(jpeg)).convert("YCbCr")
        if im.height != height:
            im = im.resize((max(1, round(im.width * height / im.height)), height), Image.BILINEAR)
        return np.asarray(im, dtype=np.float32)
    except Exception as e:
        log.info("[FRIGATE_CHANGE] unreadable frame: %s", type(e).__name__)
        return None


def _is_ir(frame: np.ndarray) -> bool:
    return float(np.hypot(frame[..., 1] - 128, frame[..., 2] - 128).mean()) < IR_CHROMA_MAX


def _is_ir_with_blobs(frame: np.ndarray) -> bool:
    return _is_ir(frame) and float((frame[..., 0] >= BURNT_LUMA).mean()) >= IR_BURNT_SHARE


def _light_changed(grid: np.ndarray) -> bool:
    share = float(grid.mean())
    if share <= LIGHT_SHARE:
        return False
    if share > LIGHT_MAX_SHARE:
        return True
    biggest = max((r["cells"] for r in _regions(grid)), default=0)
    return biggest < ONE_THING_SHARE * int(grid.sum())


def _blocks(luma: np.ndarray, n: int) -> np.ndarray:
    h, w = luma.shape[0] // n, luma.shape[1] // n
    return luma[:h * n, :w * n].reshape(h, n, w, n).transpose(0, 2, 1, 3).reshape(h, w, -1)


def changed_blocks(a: np.ndarray, b: np.ndarray, limit: float, block: int = BLOCK_PX) -> np.ndarray:
    """Blocks whose structure term is under `limit` and that have texture in at least one of the frames."""
    ba, bb = _blocks(a, block), _blocks(b, block)
    ma, mb = ba.mean(2, keepdims=True), bb.mean(2, keepdims=True)
    va, vb = ba.var(2), bb.var(2)
    cov = ((ba - ma) * (bb - mb)).mean(2)
    return ((2 * cov + SSIM_C2) / (va + vb + SSIM_C2) < limit) & (np.maximum(va, vb) > TEXTURE_MIN)


def _boxes_grid(boxes, shape: tuple[int, int], pad: float) -> np.ndarray:
    """Grid of the blocks covered by Frigate boxes (x, y, w, h), grown by `pad`."""
    h, w = shape
    grid = np.zeros(shape, dtype=bool)
    for box in boxes or []:
        try:
            x, y, bw, bh = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        grid[max(0, int((y - pad) * h)):min(h, int((y + bh + pad) * h) + 1),
             max(0, int((x - pad) * w)):min(w, int((x + bw + pad) * w) + 1)] = True
    return grid


def _mask_grid(masks, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape
    im = Image.new("1", (w, h), 0)
    draw = ImageDraw.Draw(im)
    for coords in masks or []:
        vals = _polygon(coords)
        if vals:
            draw.polygon([(vals[i] * w, vals[i + 1] * h) for i in range(0, len(vals), 2)], fill=1)
    return np.asarray(im, dtype=bool)


def _regions(grid: np.ndarray) -> list[dict]:
    """Connected blocks (8-neighbours) of at least MIN_CELLS, largest first: {cells, box: (x0, y0, x1, y1) relative}."""
    h, w = grid.shape
    seen = np.zeros(grid.shape, dtype=bool)
    out = []
    for y, x in zip(*np.nonzero(grid), strict=True):
        if seen[y, x]:
            continue
        seen[y, x] = True
        queue, pts = deque([(int(y), int(x))]), []
        while queue:
            cy, cx = queue.popleft()
            pts.append((cy, cx))
            for ny in range(max(0, cy - 1), min(h, cy + 2)):
                for nx in range(max(0, cx - 1), min(w, cx + 2)):
                    if grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        queue.append((ny, nx))
        if len(pts) >= MIN_CELLS:
            ys, xs = [p[0] for p in pts], [p[1] for p in pts]
            out.append({"cells": len(pts), "box": (round(min(xs) / w, 3), round(min(ys) / h, 3),
                                                   round((max(xs) + 1) / w, 3), round((max(ys) + 1) / h, 3))})
    return sorted(out, key=lambda r: -r["cells"])


def detect(before: list[bytes], after: list[bytes], near, masks=None, skip=None) -> dict:
    """{regions: [{cells, box}], guard: None | light | ir_lens | unreadable}. `before`, `after`: two JPEGs each from one
    camera; `near`: Frigate boxes (x, y, w, h) of where tracks were during the episode; `masks`: Frigate motion mask
    polygons; `skip`: Frigate boxes of what is still tracked in the after frames. Never raises."""
    try:
        frames = [load_frame(j) for j in (*before, *after)]
        if len(frames) != 4 or any(f is None for f in frames):
            return {"regions": [], "guard": "unreadable"}
        if any(_is_ir_with_blobs(f) for f in frames):
            return {"regions": [], "guard": "ir_lens"}
        if len({_is_ir(f) for f in frames}) > 1:  # colour ↔ IR at dusk: every block differs, nothing happened
            return {"regions": [], "guard": "light"}
        a0, a1, b0, b1 = (f[..., 0] for f in frames)
        grid = None
        for a in (a0, a1):
            for b in (b0, b1):
                part = changed_blocks(a, b, STRUCT_MAX)
                grid = part if grid is None else grid & part
        if _light_changed(grid):
            return {"regions": [], "guard": "light"}
        grid &= ~changed_blocks(a0, a1, STABLE_MIN) & ~changed_blocks(b0, b1, STABLE_MIN)
        grid &= ~_mask_grid(masks, grid.shape)
        grid &= ~_boxes_grid(skip, grid.shape, 0.0)
        zone = _boxes_grid(near, grid.shape, TRACK_PAD)
        h, w = grid.shape
        kept = []
        for r in _regions(grid):
            x0, y0, x1, y1 = r["box"]
            if zone[int(y0 * h):max(int(y1 * h), int(y0 * h) + 1), int(x0 * w):max(int(x1 * w), int(x0 * w) + 1)].any():
                kept.append(r)
        return {"regions": kept[:MAX_REGIONS], "guard": None}
    except Exception as e:  # a secondary signal never stops an analysis
        log.warning("[FRIGATE_CHANGE] detect failed: %s", type(e).__name__)
        return {"regions": [], "guard": "unreadable"}


def crop(jpeg: bytes, box) -> bytes | None:
    """A neutral crop around a region (nothing drawn on it — a drawn box is a claim): placement only."""
    try:
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        x0, y0, x1, y1 = (float(v) for v in box)
        pad_w = max((x1 - x0) * CROP_PAD, (CROP_MIN_REL - (x1 - x0)) / 2)
        pad_h = max((y1 - y0) * CROP_PAD, (CROP_MIN_REL - (y1 - y0)) / 2)
        part = im.crop((int(max(0.0, x0 - pad_w) * im.width), int(max(0.0, y0 - pad_h) * im.height),
                        int(min(1.0, x1 + pad_w) * im.width), int(min(1.0, y1 + pad_h) * im.height)))
        if max(part.size) < CROP_MIN_SIDE:
            scale = CROP_MIN_SIDE / max(part.size)
            part = part.resize((max(1, round(part.width * scale)), max(1, round(part.height * scale))), Image.LANCZOS)
        out = io.BytesIO()
        part.save(out, "JPEG", quality=CROP_QUALITY)
        return out.getvalue()
    except Exception as e:
        log.info("[FRIGATE_CHANGE] crop failed: %s", type(e).__name__)
        return None
