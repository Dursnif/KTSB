import asyncio
import io
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from kaare_core import config_migrations, config_store
from kaare_core.config import get_qdrant_api_key, get_service
from kaare_core.memory.memory_embed_models import MODELS as MEMORY_MODELS
from kaare_core.memory.memory_embed_models import active_model
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.users.store import get_user_with_hash, verify_pin
from kaare_core.version import get_version

_IN_DOCKER = os.path.exists("/.dockerenv")

router = APIRouter()

_KAARE_DIR = Path("/kaare")
_STATE_DIR = _KAARE_DIR / "state"
_CONFIGS_DIR = _KAARE_DIR / "configs"
# Save points live under state/ — the one directory every Docker service mounts. The old
# /kaare/backups/full was not mounted, so a container recreate lost every save point
# (P116 B10). Points already in the legacy dir are still listed and restorable.
_BACKUP_POINTS_DIR = _STATE_DIR / "backups" / "full"
_LEGACY_POINTS_DIR = _KAARE_DIR / "backups" / "full"
_MAX_POINTS = 5
# Taken automatically before an in-app update (P57). Listed and restorable like the others, but kept in their own
# folder, pruned to the newest few, and never counted against _MAX_POINTS — a full list must not block an update.
_PRE_UPDATE_DIR = _STATE_DIR / "backups" / "pre-update"
_PRE_UPDATE_KEEP = 2
_PRE_UPDATE_CATS = {"user_keys", "config", "ltm_database", "kaare_memory", "personality", "user_profiles", "notes_state"}

_ENCRYPTED_CATS = {"ltm_database", "kaare_memory", "personality", "user_profiles"}
_ALL_CATEGORIES = [
    "user_keys",
    "config",
    "ltm_database",
    "kaare_memory",
    "personality",
    "user_profiles",
    "notes_state",
    "argus_events",
    "secrets",
    "images",
]

# One table for the plain-file part of each category, read by BOTH backup and restore so the
# two can't drift apart again (P116 B9). Paths are relative to state/; a trailing "/" means
# the whole directory. A file entry also covers its ".enc" variant.
_STATE_PATHS: dict[str, list[str]] = {
    "personality": [
        "personality_self.md",
        "world.md",
        "world_vars.json",
        "inner_thoughts.txt",
        "mechanic_memory.md",
    ],
    "user_profiles": ["users/", "stm_users/", "stm_history/"],
    "notes_state": [
        "notater.json",
        "timers.json",
        "meeting_topics.json",
        "think_cache.jsonl",
        "household_state.yaml",
        "normalcy_baseline.json",
        "normalcy_corrections.json",
        "knowledge_reflection_last_run.json",
        "lister/",
        "meeting_comments/",
    ],
    "images": ["generated_images/", "images/"],
    "kaare_memory": ["semantic_memory_fallback.jsonl"],  # file backend when Qdrant is absent
    "secrets": ["vpn_clients/"],  # WireGuard client keys
}
# users.db travels in "user_keys"; system_master.key never leaves the machine (P87 Decision 1).
# Deliberately not backed up: hardware.json (machine-specific), frigate_snapshots/ (cache,
# re-fetchable from Frigate), argus/ (rolling digest), pending_*/ and the Jing/Jang buffer
# files (transient), metrics/, models/ (downloadable), qdrant/ (covered by snapshots),
# backups/ (save points themselves).
_STATE_EXCLUDE = {"users/users.db", "system_master.key"}


def _state_rel_in_category(rel: str, cat: str) -> bool:
    if rel in _STATE_EXCLUDE:
        return False
    for entry in _STATE_PATHS.get(cat, []):
        if entry.endswith("/"):
            if rel.startswith(entry):
                return True
        elif rel in (entry, entry + ".enc"):
            return True
    return False


_save_semaphore = asyncio.Semaphore(1)


# ── low-level helpers ──────────────────────────────────────────────────────────


def _qdrant_url() -> str:
    return get_service("storage", "qdrant") or "http://127.0.0.1:6333"


def _qdrant_headers() -> dict:
    key = get_qdrant_api_key(write=True)
    return {"api-key": key} if key else {}


def _sqlite_backup_bytes(source_path: Path) -> bytes | None:
    if not source_path.exists():
        return None
    src = sqlite3.connect(str(source_path))
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    data = mem.serialize()
    src.close()
    mem.close()
    return bytes(data)


def _add_dir_to_zip(zf: zipfile.ZipFile, src_dir: Path, arc_prefix: str) -> None:
    if not src_dir.exists():
        return
    for p in sorted(src_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(src_dir)
            zf.write(p, arcname=f"{arc_prefix}/{rel}")


def _safe_path(base: Path, arcname: str) -> Path | None:
    """Resolve arcname relative to base; return None on path traversal."""
    try:
        resolved = (base / arcname).resolve()
        if base.resolve() in resolved.parents or resolved == base.resolve():
            return resolved
        return None
    except Exception:
        return None


def _add_state_paths(zf: zipfile.ZipFile, cat: str) -> None:
    """Add every existing state/ file of a category to the ZIP under state/<rel>."""
    for entry in _STATE_PATHS.get(cat, []):
        if entry.endswith("/"):
            root = _STATE_DIR / entry.rstrip("/")
            if not root.exists():
                continue
            for f in sorted(root.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(_STATE_DIR).as_posix()
                    if rel not in _STATE_EXCLUDE:
                        zf.write(f, arcname=f"state/{rel}")
        else:
            for variant in (entry, entry + ".enc"):
                f = _STATE_DIR / variant
                if f.exists():
                    zf.write(f, arcname=f"state/{variant}")


def _restore_state_paths(zf: zipfile.ZipFile, names: list[str], cat: str, errors: list[str]) -> int:
    """Restore a category's state/ files from the ZIP. Only entries the table assigns to this
    category are written, always through _safe_path (no writes outside state/)."""
    ok = 0
    for n in names:
        if not n.startswith("state/") or n.endswith("/"):
            continue
        rel = n[len("state/") :]
        if not _state_rel_in_category(rel, cat):
            continue
        dest = _safe_path(_STATE_DIR, rel)
        if dest is None:
            errors.append(f"{cat}/{rel}: rejected path")
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(n))
            ok += 1
        except Exception as e:
            errors.append(f"{cat}/{rel}: {e}")
    return ok


def _write_atomic(data: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _read_version() -> str:
    # There is no VERSION file in an install; the release tag is baked into the image (KTSB_VERSION).
    return get_version()


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _restart_kaare_service() -> None:
    """Restart the API after a restore. Docker: SIGTERM to self — compose (`restart:
    unless-stopped`) starts it again, same as /api/admin/restart/kaare. Before (DT03 R7) this was
    systemctl only, which does not exist in the container, so a restore never took effect there."""
    time.sleep(0.6)
    if _IN_DOCKER:
        os.kill(os.getpid(), signal.SIGTERM)
        return
    try:
        subprocess.run(["sudo", "systemctl", "restart", "kaare.service"], timeout=10)
    except Exception:
        pass


async def _qdrant_snapshot_bytes(collection: str) -> bytes | None:
    url = _qdrant_url()
    hdrs = _qdrant_headers()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{url}/collections/{collection}/snapshots?wait=true",
                headers=hdrs,
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
            snap_name = r.json()["result"]["name"]

            dl = await client.get(
                f"{url}/collections/{collection}/snapshots/{snap_name}",
                headers=hdrs,
            )
            dl.raise_for_status()
            data = dl.content

            try:
                await client.delete(
                    f"{url}/collections/{collection}/snapshots/{snap_name}",
                    headers=hdrs,
                )
            except Exception:
                pass

            return data
    except Exception:
        return None


async def _qdrant_restore_collection(collection: str, data: bytes) -> str | None:
    url = _qdrant_url()
    hdrs = _qdrant_headers()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(
                f"{url}/collections/{collection}/snapshots/upload?priority=snapshot",
                content=data,
                headers={**hdrs, "Content-Type": "application/octet-stream"},
            )
            r.raise_for_status()
            return None
    except Exception as e:
        return str(e)


# ── ZIP build / restore core ───────────────────────────────────────────────────


async def _build_backup_zip(cats: set) -> bytes:
    """Build a KTSB backup ZIP for the given categories. Returns raw bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        meta = {
            "ktsb_version": _read_version(),
            "created": datetime.now(UTC).isoformat(),
            "categories": sorted(cats),
            "hostname": _hostname(),
        }
        zf.writestr("backup_meta.json", json.dumps(meta, indent=2, ensure_ascii=False))

        if "user_keys" in cats:
            users_db = _STATE_DIR / "users" / "users.db"
            if users_db.exists():
                zf.write(users_db, arcname="keys/users.db")

        if "config" in cats:
            for f in sorted(_CONFIGS_DIR.glob("*.yaml")):
                zf.write(f, arcname=f"config/{f.name}")
            # Earlier versions of each YAML file (P134 history) — for undo by hand; restore does not write them back.
            for f in sorted((_CONFIGS_DIR / config_store.HISTORY_DIRNAME).glob("*.yaml/*.yaml")):
                zf.write(
                    f, arcname=f"config/{config_store.HISTORY_DIRNAME}/{f.parent.name}/{f.name}"
                )

        if "ltm_database" in cats:
            mem_dir = _STATE_DIR / "memory"
            # knowledge.db (P105 Fase 3) added here rather than a new category —
            # same state/memory/ directory, same WAL+sqlite pattern as
            # interactions.db/digests.db, and avoids a frontend/i18n-only diff
            # for a fourth near-identical db file.
            for db_name in ("interactions.db", "digests.db", "knowledge.db"):
                data = await asyncio.to_thread(_sqlite_backup_bytes, mem_dir / db_name)
                if data:
                    zf.writestr(f"data/{db_name}", data)
            _add_dir_to_zip(zf, mem_dir / "dev_meetings", "data/memory/dev_meetings")
            _add_dir_to_zip(zf, mem_dir / "reflections", "data/memory/reflections")
            for fname in ("dev_meeting_latest.md", "reflection_latest.md"):
                p = mem_dir / fname
                if p.exists():
                    zf.write(p, arcname=f"data/memory/{fname}")

        if "kaare_memory" in cats:
            # The active model's collection (P126: one collection per embedding model).
            col = active_model()["collection"]
            data = await _qdrant_snapshot_bytes(col)
            if data:
                zf.writestr(f"qdrant/{col}.snapshot", data)

        if "argus_events" in cats:
            data = await _qdrant_snapshot_bytes("argus_events")
            if data:
                zf.writestr("qdrant/argus_events.snapshot", data)

        for cat in ("personality", "user_profiles", "notes_state", "kaare_memory"):
            if cat in cats:
                _add_state_paths(zf, cat)

        if "secrets" in cats:
            for f in sorted(_CONFIGS_DIR.glob("*.env")):
                zf.write(f, arcname=f"secrets/{f.name}")
            _add_state_paths(zf, "secrets")

        if "images" in cats:
            _add_state_paths(zf, "images")

    return buf.getvalue()


async def _restore_from_zip(content: bytes, cats: set, username: str) -> dict:
    """Core restore logic. Returns { restored, errors, restart_needed, meta }."""
    try:
        zf_check = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid zip file") from exc

    names = zf_check.namelist()

    if "backup_meta.json" not in names:
        raise HTTPException(
            status_code=400, detail="Missing backup_meta.json — not a valid KTSB backup"
        )

    meta = json.loads(zf_check.read("backup_meta.json"))
    zip_categories = set(meta.get("categories", []))

    # P02 invariant: encrypted categories in ZIP without user_keys → reject
    encrypted_in_zip = zip_categories & _ENCRYPTED_CATS
    has_user_keys = any(n.startswith("keys/") for n in names)
    if encrypted_in_zip and not has_user_keys:
        raise HTTPException(
            status_code=422,
            detail="Backup contains encrypted data but no user keys — unrestorable. Restore aborted.",
        )

    restored: list[str] = []
    errors: list[str] = []
    restart_needed = False

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        if "user_keys" in cats and "keys/users.db" in names:
            dest = _STATE_DIR / "users" / "users.db"
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                _write_atomic(zf.read("keys/users.db"), dest)
                restored.append("user_keys")
            except Exception as e:
                errors.append(f"user_keys: {e}")

        if cats & {"config", "secrets"}:
            # Everything a restore replaces in configs/ is kept first (P134).
            try:
                config_store.snapshot("pre-restore", _CONFIGS_DIR)
            except Exception as e:
                errors.append(f"config snapshot failed, configs not restored: {e}")
                cats = cats - {"config", "secrets"}

        if "config" in cats:
            ok_files = []
            for n in names:
                if n.startswith("config/") and n.endswith(".yaml") and n.count("/") == 1:
                    fname = Path(n).name
                    cfg_dest = _CONFIGS_DIR / fname
                    if cfg_dest.exists():
                        try:
                            config_store.write_bytes(cfg_dest, zf.read(n), "restore")
                            ok_files.append(fname)
                        except Exception as e:
                            errors.append(f"config/{fname}: {e}")
            if ok_files:
                restored.append(f"config ({len(ok_files)} files)")
                try:
                    # An older backup may predate migration steps: let the next start run them again.
                    config_migrations.request_rerun(_CONFIGS_DIR)
                except Exception as e:
                    errors.append(f"config version reset: {e}")

        if "ltm_database" in cats:
            mem_dir = _STATE_DIR / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            db_ok = 0
            for db_name in ("interactions.db", "digests.db", "knowledge.db"):
                arc = f"data/{db_name}"
                if arc in names:
                    try:
                        _write_atomic(zf.read(arc), mem_dir / db_name)
                        db_ok += 1
                        restart_needed = True
                    except Exception as e:
                        errors.append(f"{db_name}: {e}")
            for n in names:
                if n.startswith("data/memory/"):
                    rel = n[len("data/memory/") :]
                    if not rel:
                        continue
                    dest = _safe_path(mem_dir, rel)
                    if dest is None:
                        continue
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(n))
                    except Exception as e:
                        errors.append(f"memory/{rel}: {e}")
            if db_ok:
                restored.append(f"ltm_database ({db_ok} databases)")

        if "kaare_memory" in cats:
            # Archives hold one snapshot per memory collection; only catalogue names are restored.
            for col in sorted({spec["collection"] for spec in MEMORY_MODELS.values()}):
                arc = f"qdrant/{col}.snapshot"
                if arc not in names:
                    continue
                err = await _qdrant_restore_collection(col, zf.read(arc))
                if err:
                    errors.append(f"{col}: {err}")
                else:
                    restored.append(col)
            if _restore_state_paths(zf, names, "kaare_memory", errors):
                restored.append("kaare_memory (file backend)")

        if "argus_events" in cats:
            for col in ("argus_events",):
                arc = f"qdrant/{col}.snapshot"
                if arc in names:
                    err = await _qdrant_restore_collection(col, zf.read(arc))
                    if err:
                        errors.append(f"{col}: {err}")
                    else:
                        restored.append(col)

        for cat in ("personality", "user_profiles", "notes_state"):
            if cat in cats:
                ok_count = _restore_state_paths(zf, names, cat, errors)
                if ok_count:
                    restored.append(f"{cat} ({ok_count} files)")

        if "secrets" in cats:
            ok_count = 0
            for n in names:
                if n.startswith("secrets/") and n.endswith(".env"):
                    fname = Path(n).name
                    cfg_dest = _CONFIGS_DIR / fname
                    try:
                        config_store.write_bytes(cfg_dest, zf.read(n), "restore", mode=0o640)
                        ok_count += 1
                    except Exception as e:
                        errors.append(f"secrets/{fname}: {e}")
            ok_count += _restore_state_paths(zf, names, "secrets", errors)
            if ok_count:
                restored.append(f"secrets ({ok_count} files)")

        if "images" in cats:
            ok_count = _restore_state_paths(zf, names, "images", errors)
            if ok_count:
                restored.append(f"images ({ok_count} files)")

    return {
        "restored": restored,
        "errors": errors,
        "restart_needed": restart_needed,
        "meta": {
            "ktsb_version": meta.get("ktsb_version"),
            "created": meta.get("created"),
        },
    }


# ── export ─────────────────────────────────────────────────────────────────────


@router.get("/api/backup/export")
async def api_backup_export(categories: str = "", _u: dict = Depends(_require_admin)):
    cats = {c.strip() for c in categories.split(",") if c.strip()} if categories else set()
    if not cats:
        raise HTTPException(status_code=400, detail="No categories selected")

    if cats & _ENCRYPTED_CATS:
        cats.add("user_keys")

    content = await _build_backup_zip(cats)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"ktsb-backup-{ts}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── restore from uploaded file ─────────────────────────────────────────────────


@router.post("/api/backup/restore")
async def api_backup_restore(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    categories: str = Form(...),
    pin: str = Form(...),
    _u: dict = Depends(_require_admin),
):
    username = _u.get("sub") or _u.get("username", "")  # JWT carries the user in "sub" (DT03 R7)
    user = get_user_with_hash(username)
    if not user or not verify_pin(pin, user.get("pin_hash", "")):
        raise HTTPException(status_code=403, detail="Invalid PIN")

    try:
        cats = set(json.loads(categories))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid categories format") from exc

    content = await file.read()
    result = await _restore_from_zip(content, cats, username)

    if result["restart_needed"]:
        background_tasks.add_task(_restart_kaare_service)

    return {"ok": len(result["errors"]) == 0, **result}


# ── backup points ──────────────────────────────────────────────────────────────


class SavePointRequest(BaseModel):
    categories: list[str]
    name: str = ""


class RestorePointRequest(BaseModel):
    categories: list[str] = []
    pin: str


def _list_backup_points() -> list[dict]:
    points = []
    for base in (_BACKUP_POINTS_DIR, _LEGACY_POINTS_DIR, _PRE_UPDATE_DIR):
        if not base.exists():
            continue
        for d in sorted(base.iterdir()):
            meta_f = d / "meta.json"
            if d.is_dir() and meta_f.exists():
                try:
                    points.append(json.loads(meta_f.read_text()))
                except Exception:
                    pass
    points.sort(key=lambda p: p.get("created", ""), reverse=True)
    return points


def _point_dir(point_id: str) -> Path:
    """Directory of a save point — current location first, then the legacy one, then pre-update points."""
    for base in (_BACKUP_POINTS_DIR, _LEGACY_POINTS_DIR, _PRE_UPDATE_DIR):
        d = base / point_id
        if d.exists():
            return d
    return _BACKUP_POINTS_DIR / point_id


def _valid_point_id(point_id: str) -> bool:
    return len(point_id) <= 20 and all(c.isdigit() or c == "-" for c in point_id)


@router.post("/api/backup/save-point")
async def api_backup_save_point(
    body: SavePointRequest,
    _u: dict = Depends(_require_admin),
):
    cats = set(body.categories)
    if not cats:
        raise HTTPException(status_code=400, detail="No categories selected")

    if cats & _ENCRYPTED_CATS:
        cats.add("user_keys")

    existing = [p for p in _list_backup_points() if p.get("kind") != "pre_update"]
    if len(existing) >= _MAX_POINTS:
        raise HTTPException(status_code=409, detail="max_reached")

    async with _save_semaphore:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        point_dir = _BACKUP_POINTS_DIR / ts
        point_dir.mkdir(parents=True, exist_ok=True)

        try:
            content = await _build_backup_zip(cats)
            (point_dir / "backup.zip").write_bytes(content)
            meta = {
                "id": ts,
                "name": body.name.strip() or ts,
                "created": datetime.now(UTC).isoformat(),
                "categories": sorted(cats),
                "size_bytes": len(content),
                "ktsb_version": _read_version(),
            }
            (point_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        except Exception as e:
            try:
                shutil.rmtree(point_dir)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Save failed: {e}") from e

    return {"ok": True, "id": ts, "name": meta["name"]}


async def create_pre_update_point(to_version: str) -> str:
    """Save point taken before an in-app update; returns its id. Keeps only the newest _PRE_UPDATE_KEEP."""
    async with _save_semaphore:
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        point_dir = _PRE_UPDATE_DIR / ts
        point_dir.mkdir(parents=True, exist_ok=True)
        try:
            content = await _build_backup_zip(set(_PRE_UPDATE_CATS))
            (point_dir / "backup.zip").write_bytes(content)
            meta = {
                "id": ts,
                "name": f"Before update to {to_version}",
                "kind": "pre_update",
                "to_version": to_version,
                "created": datetime.now(UTC).isoformat(),
                "categories": sorted(_PRE_UPDATE_CATS),
                "size_bytes": len(content),
                "ktsb_version": _read_version(),
            }
            (point_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        except Exception:
            shutil.rmtree(point_dir, ignore_errors=True)
            raise
    old_points = sorted((d for d in _PRE_UPDATE_DIR.iterdir() if d.is_dir()), reverse=True)[_PRE_UPDATE_KEEP:]
    for d in old_points:
        shutil.rmtree(d, ignore_errors=True)
    return ts


@router.get("/api/backup/points")
async def api_backup_list_points(_u: dict = Depends(_require_admin)):
    return {"points": _list_backup_points()}


@router.post("/api/backup/points/{point_id}/restore")
async def api_backup_restore_point(
    point_id: str,
    body: RestorePointRequest,
    background_tasks: BackgroundTasks,
    _u: dict = Depends(_require_admin),
):
    if not _valid_point_id(point_id):
        raise HTTPException(status_code=400, detail="Invalid point ID")

    username = _u.get("sub") or _u.get("username", "")  # JWT carries the user in "sub" (DT03 R7)
    user = get_user_with_hash(username)
    if not user or not verify_pin(body.pin, user.get("pin_hash", "")):
        raise HTTPException(status_code=403, detail="Invalid PIN")

    point_dir = _point_dir(point_id)
    zip_path = point_dir / "backup.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Backup point not found")

    content = zip_path.read_bytes()

    cats = set(body.categories)
    if not cats:
        # restore all categories present in the backup
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                meta_raw = json.loads(zf.read("backup_meta.json"))
                cats = set(meta_raw.get("categories", []))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Cannot read backup categories") from exc

    result = await _restore_from_zip(content, cats, username)

    if result["restart_needed"]:
        background_tasks.add_task(_restart_kaare_service)

    return {"ok": len(result["errors"]) == 0, **result}


@router.delete("/api/backup/points/{point_id}")
async def api_backup_delete_point(point_id: str, _u: dict = Depends(_require_admin)):
    if not _valid_point_id(point_id):
        raise HTTPException(status_code=400, detail="Invalid point ID")

    point_dir = _point_dir(point_id)
    if not point_dir.exists():
        raise HTTPException(status_code=404, detail="Backup point not found")

    try:
        shutil.rmtree(point_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}") from e

    return {"ok": True}


@router.get("/api/backup/points/{point_id}/download")
async def api_backup_download_point(point_id: str, _u: dict = Depends(_require_admin)):
    if not _valid_point_id(point_id):
        raise HTTPException(status_code=400, detail="Invalid point ID")

    point_dir = _point_dir(point_id)
    zip_path = point_dir / "backup.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="Backup point not found")

    try:
        meta = json.loads((point_dir / "meta.json").read_text())
        safe_name = meta.get("name", point_id).replace(" ", "_")[:40]
    except Exception:
        safe_name = point_id
    filename = f"ktsb-backup-{safe_name}-{point_id}.zip"

    content = zip_path.read_bytes()
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
