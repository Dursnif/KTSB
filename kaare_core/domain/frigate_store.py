"""Frigate camera config + local snapshot store (P144 phase 2).

Shared by Kåre's camera tool (executor_camera), the camera pipe and the Frigate
adapter. Lives on its own because the old responder (removed in P144 phase 6) made
the import circular (function-level imports with
PLC0415 notes). Depends on nothing but the config file and the snapshot folder.
"""

import base64
import logging
from pathlib import Path

import yaml

from kaare_core.frigate_user import sealing

log = logging.getLogger("frigate_store")

CONFIG_PATH = Path("/kaare/configs/frigate_cameras.yaml")
SNAPSHOT_DIR = Path("/kaare/state/frigate_snapshots")

SNAPSHOTS_MIN_MB = 100
SNAPSHOTS_MAX_MB = 10_000

# Cached config — reloaded via load_camera_config() on /api/reload and GUI saves
_camera_cfg: dict = {}


def load_camera_config() -> dict:
    global _camera_cfg
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        _camera_cfg = raw
        log.info("frigate_cameras.yaml loaded (%d cameras)", len(raw.get("cameras", {})))
    except Exception as e:
        log.warning("Could not load frigate_cameras.yaml: %s", e)
        _camera_cfg = {}
    return _camera_cfg


def camera_config() -> dict:
    if not _camera_cfg:
        load_camera_config()
    return _camera_cfg


def save_snapshot(event_id: str, img_b64: str) -> Path | None:
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        img_bytes = base64.b64decode(img_b64)
        path = SNAPSHOT_DIR / f"{event_id}.jpg"
        path.write_bytes(sealing.seal_bytes(img_bytes))  # sealed at rest (N19 point 3, R0.6a)
        return path
    except Exception as e:
        log.warning("Could not save snapshot for %s: %s", event_id, e)
        return None


def cleanup_snapshots(max_mb: int) -> None:
    if max_mb == 0:
        return
    limit = max(SNAPSHOTS_MIN_MB, min(max_mb, SNAPSHOTS_MAX_MB)) * 1024 * 1024
    try:
        files = sorted(SNAPSHOT_DIR.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in files)
        for f in files:
            if total <= limit:
                break
            total -= f.stat().st_size
            f.unlink()
            log.debug("Snapshot cleanup: removed %s", f.name)
    except Exception as e:
        log.warning("Snapshot cleanup error: %s", e)
