#!/usr/bin/env python3
"""
Semantic memory embedding server (port 11500).

Reads memory_embed.enabled from services.yaml before starting.
If disabled or model files missing: sleeps and re-checks every 5 minutes.
Run with: python server.py  (not uvicorn server:app directly)
"""

import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import yaml
from fastapi import FastAPI
from pydantic import BaseModel

import local_embedder

SERVICES_PATH = Path("/kaare/configs/services.yaml")
CHECK_INTERVAL = 300


def _read_config() -> tuple[bool, str]:
    try:
        data = yaml.safe_load(SERVICES_PATH.read_text(encoding="utf-8")) or {}
        me = data.get("memory_embed", {})
        return bool(me.get("enabled", False)), str(me.get("model_dir", ""))
    except Exception:
        return False, ""


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
        local_embedder.load(model_dir)
        print(f"[memory-embed] Model loaded from {model_dir}", flush=True)
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
    return {"status": "ok", "message": "Semantic embed alive"}


@app.post("/embed")
def embed_texts(req: EmbedRequest):
    if not req.texts:
        return {"ok": False, "error": "no_texts"}
    vecs = local_embedder.embed(req.texts)
    return {"ok": True, "embeddings": vecs.tolist()}


if __name__ == "__main__":
    import uvicorn

    model_dir = _wait_for_ready()
    local_embedder.load(model_dir)
    print(f"[memory-embed] Model loaded from {model_dir} — starting on port 11500", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=11500)
