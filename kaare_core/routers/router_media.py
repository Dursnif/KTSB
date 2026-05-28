import asyncio
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

import kaare_core.app_state as app_state
from kaare_core.users.auth import require_admin as _require_admin, require_auth as _require_auth, require_image_auth as _require_image_auth

_LLM_PATH      = Path("/kaare/configs/llm.yaml")
_SERVICES_PATH = Path("/kaare/configs/services.yaml")

_OLLAMA_ROLE_KEYS    = {"kare", "miss_kare", "library", "mechanic", "fallback"}
_SERVICES_OLLAMA_KEYS = {"kare", "library", "miss_kare", "proxy"}

router = APIRouter()


def _ollama_base_url(role: str) -> str:
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    if role not in data:
        raise HTTPException(404, f"Role {role} not in llm.yaml")
    return data[role].get("base_url", "").rstrip("/")


async def _stream_ollama_pull(role: str, base_url: str, model: str) -> None:
    import json
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            async with client.stream("POST", f"{base_url}/api/pull", json={"model": model}) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except Exception:
                        continue
                    done = data.get("status") in ("success",)
                    app_state._OLLAMA_PULL_STATUS[role] = {
                        "pulling": not done,
                        "status": data.get("status", ""),
                        "completed": data.get("completed", 0),
                        "total": data.get("total", 0),
                        "error": None,
                    }
        app_state._OLLAMA_PULL_STATUS[role]["pulling"] = False
    except Exception as e:
        app_state._OLLAMA_PULL_STATUS[role] = {"pulling": False, "status": "", "completed": 0, "total": 0, "error": str(e)}


@router.get("/api/ollama/models/{role}")
async def api_get_ollama_models(role: str, _u=Depends(_require_auth)):
    """List models installed in the Ollama instance used by this role."""
    base_url = _ollama_base_url(role)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            return {"models": models}
    except Exception as e:
        return {"models": [], "error": str(e)}


@router.delete("/api/ollama/models/{role}/{model_name:path}")
async def api_delete_ollama_model(role: str, model_name: str, _u=Depends(_require_admin)):
    """Delete a model from the Ollama instance used by this role."""
    base_url = _ollama_base_url(role)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request("DELETE", f"{base_url}/api/delete", json={"model": model_name})
            return {"ok": resp.status_code in (200, 204)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/ollama/pull/{role}")
async def api_ollama_pull(role: str, payload: dict, _u=Depends(_require_admin)):
    """Start pulling an Ollama model in the background. Poll /status for progress."""
    model = payload.get("model", "").strip()
    if not model:
        raise HTTPException(400, "model is required")
    base_url = _ollama_base_url(role)
    app_state._OLLAMA_PULL_STATUS[role] = {"pulling": True, "status": "Starter nedlasting…", "completed": 0, "total": 0, "error": None}
    asyncio.create_task(_stream_ollama_pull(role, base_url, model))
    return {"ok": True}


@router.get("/api/ollama/pull/status/{role}")
async def api_ollama_pull_status(role: str, _u=Depends(_require_auth)):
    """Return current pull progress for a role."""
    return app_state._OLLAMA_PULL_STATUS.get(role, {"pulling": False, "status": "", "completed": 0, "total": 0, "error": None})


@router.get("/api/settings/ollama_source")
async def api_get_ollama_source(_u=Depends(_require_auth)):
    data = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    url = ""
    for role in ("kare", "miss_kare", "library", "mechanic", "fallback"):
        role_data = data.get(role, {})
        if role_data.get("provider", "ollama") == "ollama" and role_data.get("base_url"):
            url = role_data["base_url"]
            break
    builtin = "ollama:11434" in url
    return {"url": url or "http://ollama:11434", "builtin": builtin}


@router.put("/api/settings/ollama_source")
async def api_put_ollama_source(payload: dict, _u=Depends(_require_admin)):
    """Update Ollama base URL for all Ollama-backed roles in llm.yaml and services.yaml."""
    url = (payload.get("url") or "http://ollama:11434").rstrip("/")

    llm = yaml.safe_load(_LLM_PATH.read_text(encoding="utf-8")) or {}
    for role in _OLLAMA_ROLE_KEYS:
        if role in llm and llm[role].get("provider", "ollama") == "ollama":
            llm[role]["base_url"] = url
    _LLM_PATH.write_text(yaml.dump(llm, allow_unicode=True, default_flow_style=False), encoding="utf-8")

    svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
    if "ollama" not in svc:
        svc["ollama"] = {}
    for key in _SERVICES_OLLAMA_KEYS:
        svc["ollama"][key] = url
    _SERVICES_PATH.write_text(yaml.dump(svc, allow_unicode=True, default_flow_style=False), encoding="utf-8")

    return {"ok": True, "url": url}


@router.get("/api/image/{image_id}")
async def api_serve_image(image_id: str, _u=Depends(_require_image_auth)):
    from kaare_core.image_store import find_image
    safe_id = "".join(c for c in image_id if c.isalnum() or c in "_-")
    path = find_image(safe_id)
    if not path:
        raise HTTPException(404, "Image not found")
    suffix = path.suffix.lower()
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(suffix.lstrip("."), "image/png")
    return FileResponse(path, media_type=media)


@router.get("/api/frigate_snapshot/{event_id:path}")
async def api_serve_frigate_snapshot(event_id: str, _u=Depends(_require_image_auth)):
    import re as _re
    safe = _re.sub(r"[^A-Za-z0-9._\-]", "", event_id)
    path = Path("/kaare/state/frigate_snapshots") / f"{safe}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Frigate snapshot not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/api/admin/images/stats")
async def api_image_stats(_user=Depends(_require_admin)):
    from kaare_core.image_store import all_users_stats
    return all_users_stats()


@router.get("/api/settings/images")
async def api_get_image_settings(_user=Depends(_require_admin)):
    cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text()) or {}
    img = cfg.get("images", {})
    return {
        "max_per_user_count": int(img.get("max_per_user_count", 500)),
        "max_per_user_mb": float(img.get("max_per_user_mb", 200)),
    }


@router.put("/api/settings/images")
async def api_put_image_settings(payload: dict, _user=Depends(_require_admin)):
    p = Path("/kaare/configs/settings.yaml")
    cfg = yaml.safe_load(p.read_text()) or {}
    img = cfg.setdefault("images", {})
    if "max_per_user_count" in payload:
        img["max_per_user_count"] = int(payload["max_per_user_count"])
    if "max_per_user_mb" in payload:
        img["max_per_user_mb"] = float(payload["max_per_user_mb"])
    p.write_text(yaml.dump(cfg, allow_unicode=True, sort_keys=False))
    return {"ok": True}
