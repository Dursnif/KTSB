# /kaare/adapters/image_generation_adapter.py
"""
Provider-agnostic image generation and editing adapter.

Reads all config from configs/llm.yaml (image_edit section) and configs/models.yaml.
Supports any OpenAI-compatible /v1/images/generations and /v1/images/edits endpoint.
API key is stored in configs/llm_keys.env under the variable named by api_key_env.
Generated images are saved to state/generated_images/ and served via /api/image/{id}.
"""

import base64
import time
import uuid
import httpx
import logging
from pathlib import Path
from typing import Any

from kaare_core.config import get_model, get_llm_config

_log = logging.getLogger(__name__)

_IMAGES_DIR = Path("/kaare/state/generated_images")
_LLM_KEYS_PATH = Path("/kaare/configs/llm_keys.env")
_NVIDIA_ENV_PATH = Path("/kaare/configs/nvidia.env")


def _load_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def _get_api_key(cfg: dict) -> str:
    env_var = cfg.get("api_key_env", "IMAGE_EDIT_API_KEY")
    for path in (_LLM_KEYS_PATH, _NVIDIA_ENV_PATH):
        val = _load_env(path).get(env_var, "")
        if val:
            return val
    return ""


def _save_image(b64_data: str, user_id: str = "global") -> str:
    """Decode base64 image, save via image_store, return image_id."""
    from kaare_core.image_store import save_image as _store_save
    return _store_save(b64_data, user_id, "output")


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_b64(data: dict) -> str:
    """Extract base64 image from OpenAI-compatible response."""
    items = data.get("data") or []
    if not items:
        return ""
    item = items[0]
    return item.get("b64_json", "") or item.get("b64", "")


def _save_binary_image(raw: bytes, user_id: str = "global") -> str:
    """Save raw PNG/JPEG bytes via image_store, return image_id."""
    from kaare_core.image_store import save_image as _store_save
    return _store_save(raw, user_id, "output")


async def _call_huggingface(
    cfg: dict, api_key: str, model: str,
    prompt: str, negative_prompt: str = "", user_id: str = "global",
) -> dict[str, Any]:
    """
    HuggingFace Inference API for text-to-image.
    Format: {"inputs": prompt, "parameters": {...}} → binary PNG response.
    Base URL: https://router.huggingface.co/hf-inference/models
    """
    base_url = cfg.get("base_url", "https://router.huggingface.co/hf-inference/models").rstrip("/")
    timeout = float(cfg.get("timeout", 120))

    parameters: dict[str, Any] = {}
    if negative_prompt:
        parameters["negative_prompt"] = negative_prompt
    for key in ("num_inference_steps", "guidance_scale", "width", "height"):
        if key in cfg:
            parameters[key] = cfg[key]

    payload: dict[str, Any] = {"inputs": prompt}
    if parameters:
        payload["parameters"] = parameters

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url}/{model}",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "image/png",
            },
        )
    if r.status_code >= 400:
        _log.error("hf image generate http %s: %s", r.status_code, r.text[:200])
        return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:300], "image_id": None}

    image_id = _save_binary_image(r.content, user_id)
    return {"ok": True, "image_id": image_id, "model": model}


async def _call_openai_compat(
    cfg: dict, api_key: str, model: str,
    prompt: str, negative_prompt: str = "", user_id: str = "global",
) -> dict[str, Any]:
    """
    OpenAI-compatible /v1/images/generations endpoint.
    Used by: OpenAI, Together AI, and similar providers.
    """
    base_url = cfg.get("base_url", "").rstrip("/")
    timeout = float(cfg.get("timeout", 120))

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "response_format": cfg.get("response_format", "b64_json"),
    }
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    for key in ("num_inference_steps", "guidance_scale", "true_cfg_scale"):
        if key in cfg:
            payload[key] = cfg[key]

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url}/images/generations",
            json=payload,
            headers=_build_headers(api_key),
        )
    if r.status_code >= 400:
        _log.error("image generate http %s: %s", r.status_code, r.text[:200])
        return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:300], "image_id": None}

    b64 = _extract_b64(r.json())
    if not b64:
        return {"ok": False, "error": "empty_response", "image_id": None}

    image_id = _save_image(b64, user_id)
    return {"ok": True, "image_id": image_id, "model": model}


async def generate_image(
    prompt: str,
    negative_prompt: str = "",
    user_id: str = "global",
) -> dict[str, Any]:
    """
    Text-to-image generation. Provider selected from llm.yaml[image_edit][provider].
    Supports: huggingface, openai, nvidia, openai (any OpenAI-compatible endpoint).
    Model: models.yaml[image_edit_gen]. Returns {"ok": bool, "image_id": str|None, ...}.
    """
    cfg = get_llm_config("image_edit")

    if not cfg.get("enabled", True):
        return {"ok": False, "error": "image_edit_disabled", "image_id": None}

    api_key = _get_api_key(cfg)
    if not api_key:
        return {"ok": False, "error": "no_api_key", "image_id": None}

    provider = cfg.get("provider", "openai")
    model = get_model(cfg.get("model_role", "image_edit_gen"))

    try:
        if provider == "huggingface":
            return await _call_huggingface(cfg, api_key, model, prompt, negative_prompt, user_id)
        else:
            return await _call_openai_compat(cfg, api_key, model, prompt, negative_prompt, user_id)
    except Exception as e:
        _log.error("image generate error: %s", e)
        return {"ok": False, "error": str(e), "image_id": None}


async def _call_huggingface_edit(
    cfg: dict, api_key: str, model: str,
    image_b64: str, prompt: str, negative_prompt: str = "", user_id: str = "global",
) -> dict[str, Any]:
    """
    HuggingFace Inference API for image-to-image.
    Format: {"inputs": {"prompt": ..., "image": "<base64>"}, "parameters": {...}} → binary PNG.
    """
    base_url = cfg.get("base_url", "https://router.huggingface.co/hf-inference/models").rstrip("/")
    timeout = float(cfg.get("timeout", 120))

    parameters: dict[str, Any] = {}
    if negative_prompt:
        parameters["negative_prompt"] = negative_prompt
    for key in ("num_inference_steps", "guidance_scale", "width", "height"):
        if key in cfg:
            parameters[key] = cfg[key]

    payload: dict[str, Any] = {"inputs": {"prompt": prompt, "image": image_b64}}
    if parameters:
        payload["parameters"] = parameters

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url}/{model}",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "image/png",
            },
        )
    if r.status_code >= 400:
        _log.error("hf image edit http %s: %s", r.status_code, r.text[:200])
        return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:300], "image_id": None}

    image_id = _save_binary_image(r.content, user_id)
    return {"ok": True, "image_id": image_id, "model": model}


async def edit_image(
    image_b64: str,
    prompt: str,
    negative_prompt: str = "",
    user_id: str = "global",
) -> dict[str, Any]:
    """
    Image-to-image editing. Provider selected from llm.yaml[image_edit][provider].
    Supports: huggingface, openai, nvidia, and any OpenAI-compatible endpoint.
    Model: models.yaml[image_edit_edit]. Config: llm.yaml[image_edit].
    image_b64: base64-encoded PNG/JPEG input image (without data: prefix).
    Returns {"ok": bool, "image_id": str|None, "error": str|None, "model": str}.
    """
    cfg = get_llm_config("image_edit")

    if not cfg.get("enabled", True):
        return {"ok": False, "error": "image_edit_disabled", "image_id": None}

    api_key = _get_api_key(cfg)
    if not api_key:
        return {"ok": False, "error": "no_api_key", "image_id": None}

    provider = cfg.get("provider", "openai")
    model = get_model(cfg.get("model_role_edit", "image_edit_edit"))

    try:
        if provider == "huggingface":
            return await _call_huggingface_edit(cfg, api_key, model, image_b64, prompt, negative_prompt, user_id)

        base_url = cfg.get("base_url", "").rstrip("/")
        timeout = float(cfg.get("timeout", 120))

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "image": f"data:image/png;base64,{image_b64}",
            "n": 1,
            "response_format": cfg.get("response_format", "b64_json"),
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        for key in ("num_inference_steps", "guidance_scale", "true_cfg_scale"):
            if key in cfg:
                payload[key] = cfg[key]

        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{base_url}/images/edits",
                json=payload,
                headers=_build_headers(api_key),
            )
        if r.status_code >= 400:
            _log.error("image edit http %s: %s", r.status_code, r.text[:200])
            return {"ok": False, "error": f"http_{r.status_code}", "detail": r.text[:300], "image_id": None}

        b64 = _extract_b64(r.json())
        if not b64:
            return {"ok": False, "error": "empty_response", "image_id": None}

        image_id = _save_image(b64)
        return {"ok": True, "image_id": image_id, "model": model}

    except Exception as e:
        _log.error("image edit error: %s", e)
        return {"ok": False, "error": str(e), "image_id": None}
