"""
Semantic memory — model choice, download and switch (P126).

GUI: Settings → LLM/Model → Semantic memory, and Model storage (models_dir).

  GET  /api/settings/memory-embed/status      models with downloaded/active/points, the running job
  POST /api/settings/memory-embed/download    {model} → fetch the model files from Hugging Face
  POST /api/settings/memory-embed/activate    {model} → switch: config → restart embed service →
                                              wait until it runs the new model → rebuild the index
  GET/PUT /api/settings/models-dir            root folder for downloaded models

Only one download/switch runs at a time. Repositories and files come from the server-side
catalogue (memory_embed_models.MODELS), never from the request. Every route is admin-only.
"""

import asyncio
import shutil
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url
from qdrant_client import QdrantClient

from kaare_core import config_store
from kaare_core.config import (
    get_memory_embed_config,
    get_models_dir,
    get_qdrant_api_key,
    get_service,
)
from kaare_core.memory.memory_embed_models import (
    MODELS,
    active_model,
    active_model_key,
    default_model_dir,
    is_downloaded,
    model_dir_for,
)
from kaare_core.memory.semantic_reindex import reindex_all
from kaare_core.model_jobs import SingleJob
from kaare_core.routers.router_system import restart_service_blocking
from kaare_core.users.auth import require_admin as _require_admin

router = APIRouter()

_SERVICES_PATH = Path("/kaare/configs/services.yaml")
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
SERVICE_READY_TIMEOUT = 180.0
DOWNLOAD_TIMEOUT = 3600.0

_job = SingleJob()


# ── helpers ───────────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict:
    # Tracked by the config store: _write_yaml() applies only what the caller changed (P134).
    return config_store.load_for_update(path)


def _write_yaml(path: Path, data: dict) -> None:
    config_store.write_yaml(path, data, "gui:memory_embed")


def _collection_points() -> dict[str, int]:
    """Points per memory collection (missing collection → absent from the dict)."""
    try:
        client = QdrantClient(
            url=get_service("storage", "qdrant"), api_key=get_qdrant_api_key(write=False)
        )
        names = {c.name for c in client.get_collections().collections}
        return {
            spec["collection"]: client.count(spec["collection"], exact=True).count
            for spec in MODELS.values()
            if spec["collection"] in names
        }
    except Exception:
        return {}


async def _service_dim() -> int | None:
    """Vector size the local embed service currently runs (None if it does not answer)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(get_service("internal", "semantic_embed") + "/")
            return int(r.json().get("dim", 0))
    except Exception:
        return None


def _embedding_service_enabled() -> bool:
    return bool(_read_yaml(_SERVICES_PATH).get("embedding", {}).get("enabled", False))


# ── download ──────────────────────────────────────────────────────────────────


async def _run_download(key: str) -> None:
    spec = MODELS[key]
    job = _job.state
    target = default_model_dir(key)
    staging = target.parent / f".{key}.part"
    try:
        staging.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _job.fail("permission", str(target.parent))
        return

    try:
        sizes = await asyncio.to_thread(
            lambda: [
                get_hf_file_metadata(hf_hub_url(spec["repo"], src)).size or 0
                for src in spec["files"].values()
            ]
        )
        job["total_bytes"] = sum(sizes)
    except Exception:
        job["total_bytes"] = spec["size_mb"] * 1_000_000

    def _fetch() -> None:
        for dest, src in spec["files"].items():
            path = hf_hub_download(spec["repo"], src, local_dir=str(staging))
            shutil.move(path, target / dest)

    job["phase"] = "download"
    watcher = asyncio.create_task(_job.watch_folder(staging))
    try:
        await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=DOWNLOAD_TIMEOUT)
        job["bytes"] = job["total_bytes"]
    except PermissionError:
        _job.fail("permission", str(target))
    except Exception as e:
        _job.fail("download_failed", str(e))
    finally:
        job["phase"] = "done"
        await watcher
        shutil.rmtree(staging, ignore_errors=True)


# ── switch ────────────────────────────────────────────────────────────────────


def _write_choice(key: str, model_dir: str) -> dict:
    """Store the model choice; returns the previous memory_embed section (for a rollback).

    A model read from a non-default folder keeps that folder in model_dirs, so switching back
    to it later finds its files again."""
    data = _read_yaml(_SERVICES_PATH)
    me = data.setdefault("memory_embed", {})
    previous = dict(me)
    old_key, old_dir = active_model_key(), me.get("model_dir") or ""
    if old_dir and old_dir != str(default_model_dir(old_key)):
        me.setdefault("model_dirs", {})[old_key] = old_dir
    me.update(
        model=key,
        model_dir=model_dir,
        max_len=MODELS[key]["max_len"] or me.get("max_len", 128),
        enabled=True,
    )
    _write_yaml(_SERVICES_PATH, data)
    return previous


def _restore_choice(previous: dict) -> None:
    data = _read_yaml(_SERVICES_PATH)
    data["memory_embed"] = previous
    _write_yaml(_SERVICES_PATH, data)


async def _wait_for_service(dim: int) -> bool:
    deadline = asyncio.get_running_loop().time() + SERVICE_READY_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        if await _service_dim() == dim:
            return True
        await asyncio.sleep(2.0)
    return False


async def _run_activate(key: str) -> None:
    spec = MODELS[key]
    local = spec["backend"] == "local"
    model_dir = str(model_dir_for(key)) if local else ""

    job = _job.state
    job["phase"] = "config"
    previous = await asyncio.to_thread(_write_choice, key, model_dir)

    if local:
        job["phase"] = "restart"
        restart = await asyncio.to_thread(restart_service_blocking, "semantic_embed")
        if not restart.get("ok") or not await _wait_for_service(spec["dim"]):
            # Put the previous model back so memory search keeps working.
            await asyncio.to_thread(_restore_choice, previous)
            await asyncio.to_thread(restart_service_blocking, "semantic_embed")
            _job.fail("service_not_ready", restart.get("error") or "")
            return

    job["phase"] = "reindex"
    try:
        job["result"] = await reindex_all(active_model(), job["progress"])
    except Exception as e:
        _job.fail("reindex_failed", str(e))


# ── routes ────────────────────────────────────────────────────────────────────


@router.get("/api/settings/memory-embed/status")
async def api_memory_embed_status(_u=Depends(_require_admin)):
    points = await asyncio.to_thread(_collection_points)
    active = active_model_key()
    models = [
        {
            "key": key,
            "tier": spec["tier"],
            "repo": spec["repo"],
            "dim": spec["dim"],
            "size_mb": spec["size_mb"],
            "backend": spec["backend"],
            "downloaded": is_downloaded(key),
            "active": key == active,
            "model_dir": str(model_dir_for(key)) if spec["backend"] == "local" else "",
            "points": points.get(spec["collection"]),
        }
        for key, spec in MODELS.items()
    ]
    return {
        "enabled": bool(get_memory_embed_config().get("enabled", False)),
        "active": active,
        "models": models,
        "models_dir": str(get_models_dir()),
        "service_dim": await _service_dim(),
        "embedding_service_enabled": _embedding_service_enabled(),
        "job": _job.snapshot(),
    }


@router.post("/api/settings/memory-embed/download")
async def api_memory_embed_download(payload: dict, _u=Depends(_require_admin)):
    key = str(payload.get("model", ""))
    if key not in MODELS or MODELS[key]["backend"] != "local":
        raise HTTPException(400, f"Unknown or non-downloadable model: {key}")
    return await _job.start("download", key, lambda: _run_download(key))


@router.post("/api/settings/memory-embed/activate")
async def api_memory_embed_activate(payload: dict, _u=Depends(_require_admin)):
    key = str(payload.get("model", ""))
    if key not in MODELS:
        raise HTTPException(400, f"Unknown model: {key}")
    if MODELS[key]["backend"] == "local" and not is_downloaded(key):
        raise HTTPException(400, "not_downloaded")
    if MODELS[key]["backend"] == "embedding_service" and not _embedding_service_enabled():
        raise HTTPException(400, "embedding_service_off")
    return await _job.start("activate", key, lambda: _run_activate(key))


@router.get("/api/settings/models-dir")
async def api_get_models_dir(_u=Depends(_require_admin)):
    return {"models_dir": str(get_models_dir())}


@router.put("/api/settings/models-dir")
async def api_put_models_dir(payload: dict, _u=Depends(_require_admin)):
    value = str(payload.get("models_dir", "")).strip()
    if not value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise HTTPException(400, "models_dir must be an absolute path")
    data = _read_yaml(_SETTINGS_PATH)
    data["models_dir"] = value
    _write_yaml(_SETTINGS_PATH, data)
    return {"ok": True, "models_dir": value}
