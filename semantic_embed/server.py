#!/usr/bin/env python3
"""
Semantic memory embedding server (port 11500).

Reads memory_embed.enabled from services.yaml before starting.
If disabled or model files missing: sleeps and re-checks every 5 minutes.
Run with: python server.py  (not uvicorn server:app directly)
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path

import local_embedder
import yaml
from fastapi import FastAPI
from pydantic import BaseModel

SERVICES_PATH = Path("/kaare/configs/services.yaml")
SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
DEFAULT_MODELS_DIR = "/kaare/models"
CHECK_INTERVAL = 300


def _read_config() -> tuple[bool, str]:
    """(enabled, model folder). An empty model_dir means the GUI download location for the chosen
    model — <models_dir>/semantic-embed/<model>, the same rule as default_model_dir() in
    kaare_core/memory/memory_embed_models.py (this service runs without kaare_core)."""
    try:
        data = yaml.safe_load(SERVICES_PATH.read_text(encoding="utf-8")) or {}
        me = data.get("memory_embed", {})
        model_dir = str(me.get("model_dir") or "")
        if not model_dir and me.get("model"):
            settings = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
            root = settings.get("models_dir") or DEFAULT_MODELS_DIR
            model_dir = str(Path(root) / "semantic-embed" / str(me["model"]))
        return bool(me.get("enabled", False)), model_dir
    except Exception:
        return False, ""


def _read_max_len() -> int:
    """Token limit of the chosen model (written by the GUI with the model choice)."""
    try:
        data = yaml.safe_load(SERVICES_PATH.read_text(encoding="utf-8")) or {}
        return int(data.get("memory_embed", {}).get("max_len") or local_embedder.DEFAULT_MAX_LEN)
    except Exception:
        return local_embedder.DEFAULT_MAX_LEN


def _model_ready(model_dir: str) -> bool:
    if not model_dir:
        return False
    p = Path(model_dir)
    return (p / "model.onnx").exists() and (p / "tokenizer.json").exists()


def _wait_for_ready() -> str:
    while True:
        enabled, model_dir = _read_config()
        if not enabled:
            print(f"[memory-embed] Disabled via settings — sleeping {CHECK_INTERVAL}s", flush=True)
            time.sleep(CHECK_INTERVAL)
            continue
        if not _model_ready(model_dir):
            print(
                f"[memory-embed] Model files not found at '{model_dir}' "
                f"(need model.onnx + tokenizer.json) — sleeping {CHECK_INTERVAL}s",
                flush=True,
            )
            time.sleep(CHECK_INTERVAL)
            continue
        return model_dir


@asynccontextmanager
async def lifespan(_app: FastAPI):
    enabled, model_dir = _read_config()
    if enabled and _model_ready(model_dir):
        local_embedder.load(model_dir, _read_max_len())
        print(
            f"[memory-embed] Model loaded from {model_dir} ({local_embedder.dim()}-dim)", flush=True
        )
    else:
        print(
            f"[memory-embed] WARNING: Model not loaded at startup "
            f"(enabled={enabled}, model_dir='{model_dir}')",
            flush=True,
        )
    yield


app = FastAPI(title="Kåre Semantic Embed", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]


@app.get("/")
def root():
    # dim lets the API check, after a model switch, that this service runs the chosen model.
    return {"status": "ok", "message": "Semantic embed alive", "dim": local_embedder.dim()}


@app.post("/embed")
def embed_texts(req: EmbedRequest):
    if not req.texts:
        return {"ok": False, "error": "no_texts"}
    vecs = local_embedder.embed(req.texts)
    return {"ok": True, "embeddings": vecs.tolist()}


if __name__ == "__main__":
    import uvicorn

    model_dir = _wait_for_ready()
    local_embedder.load(model_dir, _read_max_len())
    print(
        f"[memory-embed] Model loaded from {model_dir} ({local_embedder.dim()}-dim) — starting on port 11500",
        flush=True,
    )
    uvicorn.run(app, host="0.0.0.0", port=11500)
