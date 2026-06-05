import asyncio
import io
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from kaare_core.config import get_service, get_qdrant_api_key
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.users.store import get_user_with_hash, verify_pin

router = APIRouter()

_KAARE_DIR         = Path("/kaare")
_STATE_DIR         = _KAARE_DIR / "state"
_CONFIGS_DIR       = _KAARE_DIR / "configs"
_BACKUP_POINTS_DIR = _KAARE_DIR / "backups" / "full"
_MAX_POINTS        = 5

_ENCRYPTED_CATS = {"ltm_database", "kaare_memory", "personality", "user_profiles"}
_ALL_CATEGORIES = [
    "user_keys", "config", "ltm_database", "kaare_memory",
    "personality", "user_profiles", "notes_state", "argus_events", "secrets", "images",
]

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


def _write_atomic(data: bytes, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def _read_version() -> str:
    p = _KAARE_DIR / "VERSION"
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"


def _hostname() -> str:
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return "unknown"


def _restart_kaare_service() -> None:
    import time
    time.sleep(0.6)
    try:
        subprocess.run(["sudo", "systemctl", "restart", "kaare.service"], timeout=10)
    except Exception:
        pass


async def _qdrant_snapshot_bytes(collection: str) -> bytes | None:
    url  = _qdrant_url()
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
    url  = _qdrant_url()
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
            "created": datetime.now(timezone.utc).isoformat(),
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

        if "ltm_database" in cats:
            mem_dir = _STATE_DIR / "memory"
            for db_name in ("interactions.db", "digests.db"):
                data = await asyncio.to_thread(_sqlite_backup_bytes, mem_dir / db_name)
                if data:
                    zf.writestr(f"data/{db_name}", data)
            _add_dir_to_zip(zf, mem_dir / "dev_meetings", "data/memory/dev_meetings")
            _add_dir_to_zip(zf, mem_dir / "reflections",  "data/memory/reflections")
            for fname in ("dev_meeting_latest.md", "reflection_latest.md"):
                p = mem_dir / fname
                if p.exists():
                    zf.write(p, arcname=f"data/memory/{fname}")

        if "kaare_memory" in cats:
            data = await _qdrant_snapshot_bytes("kaare_memory")
            if data:
                zf.writestr("qdrant/kaare_memory.snapshot", data)

        if "argus_events" in cats:
            for col in ("argus_events", "vaktmester_events"):
                data = await _qdrant_snapshot_bytes(col)
                if data:
                    zf.writestr(f"qdrant/{col}.snapshot", data)

        if "personality" in cats:
            for fname in (
                "personality_self.md", "world.md", "world_vars.json",
                "inner_thoughts.txt", "mechanic_memory.md",
            ):
                for variant in (fname, fname + ".enc"):
                    p = _STATE_DIR / variant
                    if p.exists():
                        zf.write(p, arcname=f"state/{variant}")

        if "user_profiles" in cats:
            users_dir = _STATE_DIR / "users"
            if users_dir.exists():
                for p in sorted(users_dir.rglob("*")):
                    if p.is_file() and p.name != "users.db":
                        rel = p.relative_to(users_dir)
                        zf.write(p, arcname=f"state/users/{rel}")
            _add_dir_to_zip(zf, _STATE_DIR / "stm_users", "state/stm_users")

        if "notes_state" in cats:
            for fname in ("notater.json", "timers.json", "meeting_topics.json", "think_cache.jsonl"):
                p = _STATE_DIR / fname
                if p.exists():
                    zf.write(p, arcname=f"state/{fname}")

        if "secrets" in cats:
            for f in sorted(_CONFIGS_DIR.glob("*.env")):
                zf.write(f, arcname=f"secrets/{f.name}")

        if "images" in cats:
            _add_dir_to_zip(zf, _STATE_DIR / "generated_images", "state/generated_images")

    return buf.getvalue()


async def _restore_from_zip(content: bytes, cats: set, username: str) -> dict:
    """Core restore logic. Returns { restored, errors, restart_needed, meta }."""
    try:
        zf_check = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid zip file")

    names = zf_check.namelist()

    if "backup_meta.json" not in names:
        raise HTTPException(status_code=400, detail="Missing backup_meta.json — not a valid KTSB backup")

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

        if "config" in cats:
            ok_files = []
            for n in names:
                if n.startswith("config/") and n.endswith(".yaml"):
                    fname = Path(n).name
                    dest = _CONFIGS_DIR / fname
                    if dest.exists():
                        try:
                            _write_atomic(zf.read(n), dest)
                            ok_files.append(fname)
                        except Exception as e:
                            errors.append(f"config/{fname}: {e}")
            if ok_files:
                restored.append(f"config ({len(ok_files)} files)")

        if "ltm_database" in cats:
            mem_dir = _STATE_DIR / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            db_ok = 0
            for db_name in ("interactions.db", "digests.db"):
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
                    rel = n[len("data/memory/"):]
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
            arc = "qdrant/kaare_memory.snapshot"
            if arc in names:
                err = await _qdrant_restore_collection("kaare_memory", zf.read(arc))
                if err:
                    errors.append(f"kaare_memory: {err}")
                else:
                    restored.append("kaare_memory")

        if "argus_events" in cats:
            for col in ("argus_events", "vaktmester_events"):
                arc = f"qdrant/{col}.snapshot"
                if arc in names:
                    err = await _qdrant_restore_collection(col, zf.read(arc))
                    if err:
                        errors.append(f"{col}: {err}")
                    else:
                        restored.append(col)

        if "personality" in cats:
            ok_count = 0
            for n in names:
                if (n.startswith("state/")
                        and not n.startswith("state/users")
                        and not n.startswith("state/stm_users")
                        and not n.startswith("state/generated_images")):
                    fname = n[len("state/"):]
                    dest = _STATE_DIR / fname
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(n))
                        ok_count += 1
                    except Exception as e:
                        errors.append(f"personality/{fname}: {e}")
            if ok_count:
                restored.append(f"personality ({ok_count} files)")

        if "user_profiles" in cats:
            ok_count = 0
            for n in names:
                if n.startswith("state/users/") or n.startswith("state/stm_users/"):
                    fname = n[len("state/"):]
                    dest = _STATE_DIR / fname
                    if dest.name == "users.db":
                        continue
                    dest_safe = _safe_path(_STATE_DIR, fname)
                    if dest_safe is None:
                        continue
                    try:
                        dest_safe.parent.mkdir(parents=True, exist_ok=True)
                        dest_safe.write_bytes(zf.read(n))
                        ok_count += 1
                    except Exception as e:
                        errors.append(f"profiles/{fname}: {e}")
            if ok_count:
                restored.append(f"user_profiles ({ok_count} files)")

        if "notes_state" in cats:
            ok_count = 0
            for fname in ("notater.json", "timers.json", "meeting_topics.json", "think_cache.jsonl"):
                arc = f"state/{fname}"
                if arc in names:
                    try:
                        (_STATE_DIR / fname).write_bytes(zf.read(arc))
                        ok_count += 1
                    except Exception as e:
                        errors.append(f"notes/{fname}: {e}")
            if ok_count:
                restored.append(f"notes_state ({ok_count} files)")

        if "secrets" in cats:
            ok_count = 0
            for n in names:
                if n.startswith("secrets/") and n.endswith(".env"):
                    fname = Path(n).name
                    dest = _CONFIGS_DIR / fname
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(n))
                        ok_count += 1
                    except Exception as e:
                        errors.append(f"secrets/{fname}: {e}")
            if ok_count:
                restored.append(f"secrets ({ok_count} files)")

        if "images" in cats:
            img_dir = _STATE_DIR / "generated_images"
            img_dir.mkdir(parents=True, exist_ok=True)
            ok_count = 0
            for n in names:
                if n.startswith("state/generated_images/"):
                    rel = n[len("state/generated_images/"):]
                    if not rel:
                        continue
                    dest = _safe_path(img_dir, rel)
                    if dest is None:
                        continue
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(n))
                        ok_count += 1
                    except Exception as e:
                        errors.append(f"images/{rel}: {e}")
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
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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
    username = _u.get("username", "")
    user = get_user_with_hash(username)
    if not user or not verify_pin(pin, user.get("pin_hash", "")):
        raise HTTPException(status_code=403, detail="Invalid PIN")

    try:
        cats = set(json.loads(categories))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid categories format")

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
    if not _BACKUP_POINTS_DIR.exists():
        return []
    points = []
    for d in sorted(_BACKUP_POINTS_DIR.iterdir()):
        meta_f = d / "meta.json"
        if d.is_dir() and meta_f.exists():
            try:
                points.append(json.loads(meta_f.read_text()))
            except Exception:
                pass
    points.sort(key=lambda p: p.get("created", ""), reverse=True)
    return points


def _valid_point_id(point_id: str) -> bool:
    return (
        len(point_id) <= 20
        and all(c.isdigit() or c == "-" for c in point_id)
    )


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

    existing = _list_backup_points()
    if len(existing) >= _MAX_POINTS:
        raise HTTPException(status_code=409, detail="max_reached")

    async with _save_semaphore:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        point_dir = _BACKUP_POINTS_DIR / ts
        point_dir.mkdir(parents=True, exist_ok=True)

        try:
            content = await _build_backup_zip(cats)
            (point_dir / "backup.zip").write_bytes(content)
            meta = {
                "id": ts,
                "name": body.name.strip() or ts,
                "created": datetime.now(timezone.utc).isoformat(),
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
            raise HTTPException(status_code=500, detail=f"Save failed: {e}")

    return {"ok": True, "id": ts, "name": meta["name"]}


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

    username = _u.get("username", "")
    user = get_user_with_hash(username)
    if not user or not verify_pin(body.pin, user.get("pin_hash", "")):
        raise HTTPException(status_code=403, detail="Invalid PIN")

    point_dir = _BACKUP_POINTS_DIR / point_id
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
        except Exception:
            raise HTTPException(status_code=400, detail="Cannot read backup categories")

    result = await _restore_from_zip(content, cats, username)

    if result["restart_needed"]:
        background_tasks.add_task(_restart_kaare_service)

    return {"ok": len(result["errors"]) == 0, **result}


@router.delete("/api/backup/points/{point_id}")
async def api_backup_delete_point(point_id: str, _u: dict = Depends(_require_admin)):
    if not _valid_point_id(point_id):
        raise HTTPException(status_code=400, detail="Invalid point ID")

    point_dir = _BACKUP_POINTS_DIR / point_id
    if not point_dir.exists():
        raise HTTPException(status_code=404, detail="Backup point not found")

    try:
        shutil.rmtree(point_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    return {"ok": True}


@router.get("/api/backup/points/{point_id}/download")
async def api_backup_download_point(point_id: str, _u: dict = Depends(_require_admin)):
    if not _valid_point_id(point_id):
        raise HTTPException(status_code=400, detail="Invalid point ID")

    point_dir = _BACKUP_POINTS_DIR / point_id
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
