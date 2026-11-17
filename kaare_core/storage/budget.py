"""Storage budget — every data class that grows by itself, in one place (P150 phase 2, 2026-11-13).

«Alle mapper og filer som øker av seg selv må ha en kontrollfunksjon»: each class registers here with its Basic group, how
to measure it, and — once it has one — how to read and set its size limit (MB, 0 = off, oldest first when full; the
deletion itself stays with the class's own store: Argus in retention/argus_size, snapshots in domain/frigate_store, …).
A class without a size control yet is listed with `controlled: False` so the GUI can say so honestly.

A limit that already has a home in the GUI (Frigate's «Lagringsgrenser» card; the image cards on Logs/Storage) is set there only —
`home` names it, and this module refuses to set it (2026-11-13: «frigate ting skal i frigate fanen»).

Basic mode shows five groups; a group limit is shared proportionally («forholdsmessig») — every member with a limit
is scaled by the same factor, members that are off (0) or without a control are left alone. Expert mode sets one class.

Disk facts are read at call time (`shutil.disk_usage`, the mount from /proc/mounts) per folder — folders can sit on different
disks — and a disk whose limits add up to more than it can give (used by those classes + free) is flagged `over_commit`.
Qdrant-backed classes have no local folder (Qdrant may run on another machine, rule 27); they are measured through the API
and left out of the disk sums.

Sync and file-walking by design: callers run `report()` with `asyncio.to_thread`. Never raises on a missing folder.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from kaare_core import config, config_store
from kaare_core.domain import frigate_store
from kaare_core.frigate_user import settings as fu_settings
from kaare_core.frigate_user import store as fu_store
from kaare_core.memory import memory_embed_models
from kaare_core.retention import argus_size
from kaare_core.storage import limits, rawlog
from kaare_core.tools import think_cache

log = logging.getLogger(__name__)

STATE_DIR = Path("/kaare/state")  # read per call (tests redirect it)
LOGS_DIR = Path("/kaare/logs")
MB = 1_048_576
GB = 1024**3

GROUPS = ("user_files", "logs", "frigate_images", "frigate_text", "kare_files")


@dataclass(frozen=True)
class DataClass:
    key: str
    group: str
    paths: Callable[[], list[Path]]  # local folders/files measured (and whose disk is reported)
    size: Callable[[], int] | None = None  # bytes; default: walk `paths`
    get_limit: Callable[[], float | None] | None = None  # MB, 0 = off; None = no control yet
    set_limit: Callable[[float], None] | None = None
    per_user: bool = False
    phase: str = ""  # where its control is planned, when it has none yet (shown in Expert)
    home: str = ""  # a GUI place that already owns this limit ("frigate", "images"); "" = set on Logs/Storage


# ── measuring ─────────────────────────────────────────────────────────────────

def _disk_usage(path: Path) -> tuple[int, int, int]:
    return tuple(shutil.disk_usage(path))  # type: ignore[return-value]


def _device_of(path: Path) -> int:
    return os.stat(path).st_dev


# Two mounts are the same disk when they report the same size and nearly the same free space (DT04 R0.1b): Docker
# Desktop's file sharing can give every bind mount its own device id. Free space may move between the two reads.
_SAME_DISK_FREE_SLACK = 256 * 1_048_576


def _same_disk(d: dict, dev: int, total: int, free: int) -> bool:
    return d["_dev"] == dev or (d["_total"] == total and abs(d["_free"] - free) <= _SAME_DISK_FREE_SLACK)


def _walk_bytes(paths: list[Path], pattern: str | None = None, top_only: bool = False) -> int:
    total = 0
    for p in paths:
        try:
            if p.is_file():
                total += p.stat().st_size
                continue
            if not p.is_dir():
                continue
            if top_only:
                it = (f for f in p.iterdir() if f.is_file() and (pattern is None or f.match(pattern)))
                total += sum(f.stat().st_size for f in it)
                continue
            for root, _dirs, files in os.walk(p):
                for name in files:
                    if pattern is None or Path(name).match(pattern):
                        try:
                            total += (Path(root) / name).stat().st_size
                        except OSError:
                            pass
        except OSError:
            continue
    return total


def _qdrant_collection_bytes(name: str) -> int:
    """Vector + payload bytes of one collection from Qdrant's telemetry; 0 when unknown. Never raises."""
    try:
        key = config.get_qdrant_api_key(write=False)
        r = httpx.get(f"{config.get_service('storage', 'qdrant')}/telemetry", params={"details_level": 3},
                      headers={"api-key": key} if key else {}, timeout=15)
        r.raise_for_status()
        for c in ((r.json().get("result") or {}).get("collections") or {}).get("collections") or []:
            if c.get("id") == name:
                return sum(int((s.get("local") or {}).get("vectors_size_bytes") or 0)
                           + int((s.get("local") or {}).get("payloads_size_bytes") or 0) for s in c.get("shards") or [])
    except Exception as e:
        log.info("[STORAGE] no size for Qdrant collection %s: %s", name, e)
    return 0


def _mount_of(path: Path) -> str:
    """The mount point a path lives on (longest matching entry in /proc/mounts); '' when unknown."""
    try:
        real = str(path.resolve())
        best = ""
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 2:
                    continue
                mnt = parts[1].replace("\\040", " ")
                if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
                    best = mnt
        return best
    except OSError:
        return ""


# ── limits that already exist (reused, not rebuilt) ───────────────────────────

def _snapshots_limit() -> float:
    return float((frigate_store.camera_config().get("storage") or {}).get("snapshots_max_mb", 500))


def _set_snapshots_limit(mb: float) -> None:
    val = round(mb)
    val = 0 if val == 0 else max(frigate_store.SNAPSHOTS_MIN_MB, min(val, frigate_store.SNAPSHOTS_MAX_MB))

    def _apply(data):
        storage = data.get("storage")
        data["storage"] = {**(storage if isinstance(storage, dict) else {}), "snapshots_max_mb": val}

    config_store.update_yaml(Path(frigate_store.CONFIG_PATH), _apply, "gui:storage_budget")
    frigate_store.load_camera_config()


def _frames_limit() -> float:
    return float(fu_settings.episode_frames_caps()[1])  # the hard cap (oldest episode folders go)


def _set_frames_limit(mb: float) -> None:
    """The keep cap is the limit; the thinning cap (cut to one frame) keeps its ratio to it."""
    cap_max, cap_keep = fu_settings.episode_frames_caps()
    new_keep = round(mb)
    ratio = (cap_max / cap_keep) if cap_keep else 1.0
    fu_settings.update({"episode_frames_keep_mb": new_keep, "episode_frames_max_mb": round(new_keep * ratio)})


def _images_limit() -> float:
    try:
        data = config_store.read_yaml_cached(config.CONFIG_DIR / "settings.yaml", copy_result=False) or {}
        return float((data.get("images") or {}).get("max_per_user_mb", 200))
    except Exception:
        return 200.0


def _set_images_limit(mb: float) -> None:
    def _apply(data):
        img = data.get("images")
        data["images"] = {**(img if isinstance(img, dict) else {}), "max_per_user_mb": max(1, round(mb))}

    config_store.update_yaml(config.CONFIG_DIR / "settings.yaml", _apply, "gui:storage_budget")


def _set_argus_limit(mb: float) -> None:
    argus_size.save_limit_mb(round(mb))


# ── the registry ──────────────────────────────────────────────────────────────

def classes() -> list[DataClass]:
    s, lg = Path(STATE_DIR), Path(LOGS_DIR)
    snaps = Path(frigate_store.SNAPSHOT_DIR)
    return [
        DataClass("images", "user_files", lambda: [s / "images"], get_limit=_images_limit,
                  set_limit=_set_images_limit, per_user=True, home="images"),
        # P150 phase 5: the raw event streams are their own classes (day files in logs/raw/, rawlog); «logs» is the rest
        DataClass("raw_ha_events", "logs", lambda: [Path(rawlog.RAW_DIR)], size=lambda: rawlog.total_bytes("ha_events"),
                  **_limit_here("raw_ha_events_mb")),
        DataClass("raw_frigate_mqtt", "logs", lambda: [Path(rawlog.RAW_DIR)],
                  size=lambda: rawlog.total_bytes("frigate_mqtt"), **_limit_here("raw_frigate_mqtt_mb")),
        DataClass("logs", "logs", lambda: [lg], size=lambda: _top_logs_bytes(lg), phase="5"),
        DataClass("frigate_snapshots", "frigate_images", lambda: [snaps],
                  size=lambda: _walk_bytes([snaps], "*.jpg", top_only=True),
                  get_limit=_snapshots_limit, set_limit=_set_snapshots_limit, home="frigate"),
        DataClass("episode_frames", "frigate_images", lambda: [snaps / "episodes"], get_limit=_frames_limit,
                  set_limit=_set_frames_limit, home="frigate"),
        DataClass("frigate_db", "frigate_text", lambda: [Path(fu_store.DB_PATH).parent], size=fu_store.db_logical_bytes,
                  get_limit=lambda: float(fu_settings.get_settings()["frigate_db_max_mb"]),
                  set_limit=lambda mb: fu_settings.update({"frigate_db_max_mb": round(mb)}), home="frigate"),
        DataClass("argus", "kare_files", lambda: _local_qdrant(s), size=lambda: int(argus_size.measure()["bytes"]),
                  get_limit=lambda: float(argus_size.get_limit_mb()), set_limit=_set_argus_limit),
        DataClass("argus_ids", "kare_files", lambda: [s / "argus" / "ids"], phase="Argus"),
        DataClass("memory_dbs", "kare_files", lambda: [s / "memory"],
                  size=lambda: _walk_bytes([s / "memory"], "*.db*", top_only=True), phase="4"),
        DataClass("reflections", "kare_files", lambda: [s / "memory" / "reflections", s / "memory" / "dev_meetings"],
                  phase="4"),
        DataClass("stm_history", "kare_files", lambda: [s / "stm_history"], **_limit_here("stm_history_mb")),
        DataClass("think_cache", "kare_files", lambda: [Path(think_cache._CACHE_PATH)], **_limit_here("think_cache_mb")),
        DataClass("inner_thoughts", "kare_files", lambda: [s / "inner_thoughts.txt"], **_limit_here("inner_thoughts_mb")),
        DataClass("observations", "kare_files", lambda: [s / "users"],
                  size=lambda: _walk_bytes([s / "users"], "observations*"), per_user=True,
                  **_limit_here("observations_mb")),
        DataClass("media_history", "kare_files", lambda: [s / "media"], phase="4"),
        DataClass("semantic_memory", "kare_files", lambda: _local_qdrant(s),
                  size=lambda: _qdrant_collection_bytes(_semantic_collection()), phase="4"),
    ]


def _top_logs_bytes(lg: Path) -> int:
    """The ordinary logs in logs/ — not the raw streams (their old single files and logs/raw/ count as raw_*)."""
    legacy = {Path(p).name for p in rawlog.LEGACY.values()}
    return sum(_walk_bytes([p]) for p in lg.glob("*.log*") if p.is_file() and p.name not in legacy) if lg.is_dir() else 0


def _limit_here(key: str) -> dict:
    """get/set for a limit that lives on this tab (kaare_core.storage.limits)."""
    return {"get_limit": lambda: float(limits.get_mb(key)), "set_limit": lambda mb: limits.save_mb(key, round(mb))}


def _local_qdrant(state: Path) -> list[Path]:
    """Qdrant's storage folder when it is KTSB's own (the shipped compose mounts state/qdrant) — then its disk is known;
    a Qdrant elsewhere (rule 27) has no local folder and stays out of the disk sums."""
    p = state / "qdrant"
    return [p] if p.is_dir() else []


def _semantic_collection() -> str:
    try:
        return str(memory_embed_models.active_model().get("collection") or "")
    except Exception:
        return ""


def _by_key() -> dict[str, DataClass]:
    return {c.key: c for c in classes()}


def _limit(c: DataClass) -> float | None:
    if c.get_limit is None:
        return None
    try:
        return float(c.get_limit())
    except Exception as e:
        log.warning("[STORAGE] limit of %s unreadable: %s", c.key, e)
        return None


def _users_count() -> int:
    try:
        return max(1, sum(1 for d in (Path(STATE_DIR) / "images").iterdir() if d.is_dir()))
    except OSError:
        return 1


# ── report / set ──────────────────────────────────────────────────────────────

def report() -> dict:
    rows, disks = [], {}
    n_users = _users_count()
    for c in classes():
        try:
            size = int(c.size()) if c.size else _walk_bytes(c.paths())
        except Exception as e:
            log.warning("[STORAGE] size of %s unreadable: %s", c.key, e)
            size = 0
        limit = _limit(c)
        rows.append({"key": c.key, "group": c.group, "used_mb": round(size / MB, 2), "limit_mb": limit,
                     "controlled": limit is not None, "per_user": c.per_user, "phase": c.phase,
                     "home": c.home})
        paths = c.paths()
        if not paths:
            continue
        probe = next((p for p in paths if p.exists()), None) or next((p.parent for p in paths if p.parent.exists()), None)
        if probe is None:
            continue
        mnt = _mount_of(probe) or str(probe)
        d = disks.get(mnt)
        if d is None:
            # One disk, many mounts (DT04 R0.1b): Docker Desktop shows each bind mount (configs/, logs/, state/ …) as
            # its own mount of the same drive — key by the disk, not the mount point, or it is listed and judged 5×.
            try:
                total, _used, free = _disk_usage(probe)
                dev = _device_of(probe)
            except OSError:
                continue
            d = next((x for x in disks.values() if _same_disk(x, dev, total, free)), None)
            if d is None:
                d = {"mount": mnt, "mounts": [], "total_gb": round(total / GB, 1), "free_gb": round(free / GB, 1),
                     "classes": [], "used_by_ktsb_gb": 0.0, "limits_gb": 0.0, "unlimited": [],
                     "_dev": dev, "_total": total, "_free": free}
            d["mounts"].append(mnt)
            disks[mnt] = d
        d["classes"].append(c.key)
        d["used_by_ktsb_gb"] += size / GB
        if limit:  # > 0
            d["limits_gb"] += (limit * (n_users if c.per_user else 1)) / 1024
        else:
            d["unlimited"].append(c.key)
    unique = list({id(d): d for d in disks.values()}.values())  # several mounts may share one disk entry
    for d in unique:
        can_give = d["used_by_ktsb_gb"] + d["free_gb"]
        d["over_commit"] = d["limits_gb"] > can_give
        d["used_by_ktsb_gb"] = round(d["used_by_ktsb_gb"], 2)
        d["limits_gb"] = round(d["limits_gb"], 2)
        for k in ("_dev", "_total", "_free"):
            d.pop(k, None)
    groups = []
    for g in GROUPS:
        members = [r for r in rows if r["group"] == g]
        limited = [r for r in members if r["limit_mb"]]
        groups.append({"key": g, "used_mb": round(sum(r["used_mb"] for r in members), 2),
                       "limit_mb": round(sum(r["limit_mb"] for r in limited), 2) if limited else None,
                       "controlled": bool(limited), "classes": [r["key"] for r in members],
                       "uncontrolled": [r["key"] for r in members if not r["controlled"]],
                       "per_user": any(r["per_user"] for r in limited),
                       "home": next((r["home"] for r in members if r["home"]), "")})
    return {"groups": groups, "classes": rows, "disks": unique, "users": n_users}


def set_class_limit(key: str, mb: float) -> None:
    c = _by_key().get(key)
    if c is None or c.set_limit is None:
        raise ValueError(f"{key} has no size control")
    if c.home:
        raise ValueError(f"{key} is set in its own place in the GUI ({c.home})")
    if mb < 0:
        raise ValueError("a limit is 0 (off) or more")
    c.set_limit(mb)


def set_group_limit(group: str, mb: float) -> None:
    """Scale every member with a limit by the same factor («forholdsmessig»). 0 / no control stay as they are."""
    if group not in GROUPS:
        raise ValueError(f"unknown group {group}")
    if mb <= 0:
        raise ValueError("a group limit is more than 0 — turn a single class off in Expert mode")
    members = [(c, _limit(c)) for c in classes() if c.group == group]
    if any(c.home for c, _ in members):
        raise ValueError(f"{group} is set in its own place in the GUI")
    limited = [(c, lim) for c, lim in members if lim and c.set_limit is not None]
    if not limited:
        raise ValueError(f"{group} has no data class with a size limit yet")
    factor = mb / sum(lim for _, lim in limited)
    new = [round(lim * factor) for _, lim in limited]
    # whole MB per class; the rounding rest goes to the biggest share, so the group adds up to exactly what was asked
    biggest = max(range(len(new)), key=lambda i: new[i])
    new[biggest] += round(mb) - sum(new)
    for (c, _lim), value in zip(limited, new, strict=True):
        c.set_limit(max(0, value))
