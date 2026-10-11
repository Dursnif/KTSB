"""
Whisper (speech-to-text) — model choice and download for faster-whisper (P126).

GUI: Settings → LLM/Model → Whisper.

  GET  /api/settings/stt-models/status     presets for the household language, downloaded/active, job
  POST /api/settings/stt-models/download   {repo} → preset or user-typed HF repo/URL (CTranslate2 only)
  POST /api/settings/stt-models/activate   {repo} → voice.stt = faster_whisper + this model, restart voice

The voice service reads models from the same folder (stt_models.cache_dir()), so a downloaded model
is used as-is. OpenVINO stays a manual folder (Expert) and is not offered in Docker, where the voice
image has no OpenVINO. Every route is admin-only.
"""

import asyncio
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends, HTTPException
from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import RepositoryNotFoundError

from kaare_core import config_store
from kaare_core.model_jobs import SingleJob
from kaare_core.routers.router_system import _IN_DOCKER, restart_service_blocking
from kaare_core.tools.i18n import get_speech_lang
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.voice.stt_models import (
    FW_ALLOW_PATTERNS,
    cache_dir,
    is_ct2_layout,
    normalize_repo,
    presets_for,
    resolve_model,
)

router = APIRouter()

_SERVICES_PATH = Path("/kaare/configs/services.yaml")
DOWNLOAD_TIMEOUT = 3 * 3600.0

_job = SingleJob()


def _stt_config() -> dict:
    data = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    return data.get("voice", {}).get("stt", {}) or {}


def _is_downloaded(repo: str) -> bool:
    if repo.startswith("/"):  # a local CTranslate2 folder set in Expert mode
        return (Path(repo) / "model.bin").exists()
    try:
        path = snapshot_download(
            repo,
            cache_dir=str(cache_dir()),
            allow_patterns=FW_ALLOW_PATTERNS,
            local_files_only=True,
        )
        return (Path(path) / "model.bin").exists()
    except Exception:
        return False


def _repo_folder(repo: str) -> Path:
    return cache_dir() / ("models--" + repo.replace("/", "--"))


async def _run_download(repo: str) -> None:
    job = _job.state
    job["phase"] = "check"
    try:
        info = await asyncio.to_thread(HfApi().model_info, repo, files_metadata=True)
    except RepositoryNotFoundError:
        _job.fail("not_found", repo)
        return
    except Exception as e:
        _job.fail("download_failed", str(e))
        return
    root_files = {s.rfilename: (s.size or 0) for s in info.siblings or [] if "/" not in s.rfilename}
    if not is_ct2_layout(set(root_files)):
        _job.fail("not_ct2", repo)
        return
    job["total_bytes"] = sum(
        size
        for name, size in root_files.items()
        if name in FW_ALLOW_PATTERNS or name.startswith("vocabulary.")
    )

    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _job.fail("permission", str(cache_dir()))
        return

    job["phase"] = "download"
    watcher = asyncio.create_task(_job.watch_folder(_repo_folder(repo)))
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                snapshot_download,
                repo,
                cache_dir=str(cache_dir()),
                allow_patterns=FW_ALLOW_PATTERNS,
            ),
            timeout=DOWNLOAD_TIMEOUT,
        )
        job["bytes"] = job["total_bytes"]
    except PermissionError:
        _job.fail("permission", str(cache_dir()))
    except Exception as e:
        _job.fail("download_failed", str(e))
    finally:
        job["phase"] = "done"
        await watcher


async def _run_activate(repo: str) -> None:
    job = _job.state
    job["phase"] = "config"
    data = config_store.load_for_update(_SERVICES_PATH)
    stt = data.setdefault("voice", {}).setdefault("stt", {})
    stt["backend"] = "faster_whisper"
    stt["faster_whisper_model"] = repo
    config_store.write_yaml(_SERVICES_PATH, data, "gui:run_activate")
    job["phase"] = "restart"
    result = await asyncio.to_thread(restart_service_blocking, "voice")
    if not result.get("ok"):
        _job.fail("restart_failed", result.get("error") or "")


@router.get("/api/settings/stt-models/status")
async def api_stt_models_status(_u=Depends(_require_admin)):
    lang = get_speech_lang()
    stt = _stt_config()
    backend = stt.get("backend", "faster_whisper")
    active = (
        resolve_model(stt.get("faster_whisper_model", ""), lang)
        if backend == "faster_whisper"
        else None
    )
    presets = presets_for(lang)
    repos = [p["repo"] for p in presets] + (
        [active] if active and active not in {p["repo"] for p in presets} else []
    )
    downloaded = await asyncio.gather(*(asyncio.to_thread(_is_downloaded, r) for r in repos))
    by_repo = dict(zip(repos, downloaded, strict=False))
    return {
        "lang": lang,
        "backend": backend,
        "active": active,
        "presets": [
            {**p, "downloaded": by_repo[p["repo"]], "active": p["repo"] == active} for p in presets
        ],
        "custom": (
            {"repo": active, "downloaded": by_repo[active], "active": True}
            if active and active not in {p["repo"] for p in presets}
            else None
        ),
        "cache_dir": str(cache_dir()),
        "openvino_available": not _IN_DOCKER,
        "job": _job.snapshot(),
    }


@router.post("/api/settings/stt-models/download")
async def api_stt_models_download(payload: dict, _u=Depends(_require_admin)):
    repo = normalize_repo(str(payload.get("repo", "")))
    if not repo:
        raise HTTPException(400, "bad_repo")
    return await _job.start("download", repo, lambda: _run_download(repo))


@router.post("/api/settings/stt-models/activate")
async def api_stt_models_activate(payload: dict, _u=Depends(_require_admin)):
    repo = normalize_repo(str(payload.get("repo", "")))
    if not repo:
        raise HTTPException(400, "bad_repo")
    if not await asyncio.to_thread(_is_downloaded, repo):
        raise HTTPException(400, "not_downloaded")
    return await _job.start("activate", repo, lambda: _run_activate(repo))
