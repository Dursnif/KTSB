#!/usr/bin/env python3
"""
BGE-M3 embedding service — Ollama-compatible /api/embed endpoint.
Port 11446. Replaces the ollama-embed Docker container.

Reads config from configs/services.yaml (embedding section).
Backends (device config key):
  NPU   — OpenVINO NPU, static input shapes (Intel only)
  CPU   — OpenVINO CPU, dynamic shapes (Intel only)
  torch — FlagEmbedding/PyTorch, works on AMD/Intel/Apple Silicon

Run:
    PYTHONPATH=/kaare /kaare/services/embedding/convert_venv/bin/python \
        /kaare/services/embedding/server.py
"""

import asyncio
import logging
from typing import Union

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kaare_core.config import get_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("embedding")

# ── Config — read from configs/services.yaml via get_service() ─────────────────
_cfg               = get_service("embedding")
MODEL_PATH         = _cfg.get("model_path", "/mnt/wiki/bge-m3-ov")
HF_MODEL           = _cfg.get("hf_model", "BAAI/bge-m3")
SPARSE_LINEAR_PATH = _cfg.get(
    "sparse_linear_path",
    "/mnt/wiki/hf_cache/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181/sparse_linear.pt",
)
DEVICE     = _cfg.get("device", "NPU")
PORT       = int(_cfg.get("port", 11446))
MAX_LENGTH = int(_cfg.get("max_length", 512))

# ── Startup guard ──────────────────────────────────────────────────────────────
if not _cfg.get("enabled", True):
    import time as _time
    log.info("[embedding] Disabled via config (embedding.enabled: false) — sleeping.")
    while True:
        _time.sleep(300)

# ── Model loading ──────────────────────────────────────────────────────────────
class _NPUEmbedder:
    """Native OpenVINO NPU inference with static input shapes (Intel only)."""

    def __init__(self, model_path: str, max_length: int) -> None:
        import openvino as ov
        core = ov.Core()
        ov_model = core.read_model(f"{model_path}/openvino_model.xml")
        ov_model.reshape({inp.get_any_name(): [1, max_length] for inp in ov_model.inputs})
        self._compiled = core.compile_model(ov_model, "NPU")
        self._max_length = max_length
        log.info("BGE-M3 compiled on NPU (static shape 1×%d).", max_length)

    def encode(self, inputs: dict) -> np.ndarray:
        req = self._compiled.create_infer_request()
        req.infer({k: v.numpy() for k, v in inputs.items()})
        return req.get_output_tensor(0).data  # [1, seq, 1024]


class _CPUEmbedder:
    """OVModelForFeatureExtraction on CPU — dynamic shapes (Intel only)."""

    def __init__(self, model_path: str) -> None:
        from optimum.intel import OVModelForFeatureExtraction
        self._model = OVModelForFeatureExtraction.from_pretrained(model_path, device="CPU")
        log.info("BGE-M3 loaded via OpenVINO on CPU (dynamic shapes).")

    def encode(self, inputs: dict) -> np.ndarray:
        out = self._model(**inputs)
        return out.last_hidden_state.detach().numpy()


class _TorchEmbedder:
    """FlagEmbedding PyTorch backend — works on AMD/Intel/Apple Silicon, no OpenVINO required.

    Downloads BAAI/bge-m3 from HuggingFace on first use.
    Handles dense + sparse embeddings internally.
    """

    def __init__(self, hf_model: str) -> None:
        from FlagEmbedding import BGEM3FlagModel
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._model = BGEM3FlagModel(hf_model, use_fp16=False, device=device)
        log.info("BGE-M3 loaded via FlagEmbedding on %s (model: %s).", device, hf_model)

    def encode_texts(self, texts: list[str]) -> tuple[np.ndarray, list[dict]]:
        """Returns (dense_vecs [N,1024] float32, sparse list of {index: score} dicts)."""
        out = self._model.encode(texts, return_dense=True, return_sparse=True, batch_size=8)
        dense = np.array(out["dense_vecs"], dtype=np.float32)
        # Convert FlagEmbedding's {token_id: score} to Qdrant SparseVector format
        sparse = [
            {"indices": list(lw.keys()), "values": [float(v) for v in lw.values()]}
            for lw in out["lexical_weights"]
        ]
        return dense, sparse


def _load_model():
    if DEVICE == "torch":
        return _TorchEmbedder(HF_MODEL), None
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
    if DEVICE == "NPU":
        try:
            return _NPUEmbedder(MODEL_PATH, MAX_LENGTH), tokenizer
        except Exception as e:
            log.warning("NPU failed (%s), falling back to OpenVINO CPU.", e)
    return _CPUEmbedder(MODEL_PATH), tokenizer


_embedder, _tokenizer = _load_model()


# ── Sparse linear head (BGE-M3 lexical retrieval, OpenVINO backends only) ─────

def _load_sparse_linear() -> tuple[np.ndarray | None, np.ndarray | None]:
    if DEVICE == "torch":
        return None, None  # _TorchEmbedder handles sparse internally
    sl = torch.load(SPARSE_LINEAR_PATH, map_location="cpu", weights_only=True)
    w = sl["weight"].float().numpy()  # [1, 1024]
    b = sl["bias"].float().numpy()    # [1]
    log.info("Sparse linear loaded: w=%s b=%s from %s", w.shape, b.shape, SPARSE_LINEAR_PATH)
    return w, b

_sparse_w, _sparse_b = _load_sparse_linear()


def _compute_sparse(hidden: np.ndarray, attention_mask: np.ndarray, input_ids: np.ndarray) -> dict:
    """
    Compute BGE-M3 sparse vector from encoder hidden states.
    hidden:         [seq, 1024] float32
    attention_mask: [seq] int   (1 = real token, 0 = padding)
    input_ids:      [seq] int
    Returns {"indices": [...], "values": [...]} for Qdrant SparseVector.
    """
    scores = (hidden @ _sparse_w.T + _sparse_b).squeeze(-1)  # [seq]
    scores = np.maximum(scores, 0.0)                          # ReLU
    scores *= attention_mask                                  # zero out padding
    result: dict[int, float] = {}
    for tok_id, score in zip(input_ids, scores):
        if score > 0:
            tid = int(tok_id)
            if score > result.get(tid, 0.0):
                result[tid] = float(score)
    return {"indices": list(result.keys()), "values": list(result.values())}


# ── Embedding ──────────────────────────────────────────────────────────────────

def _embed(texts: list[str]) -> list[list[float]]:
    if isinstance(_embedder, _TorchEmbedder):
        dense, _ = _embedder.encode_texts(texts)
        return dense.tolist()

    padding = "max_length" if DEVICE == "NPU" else True
    if DEVICE == "NPU":
        # NPU is compiled with static batch size 1 — must process one text at a time
        vecs = []
        for text in texts:
            inputs = _tokenizer(
                [text],
                return_tensors="pt",
                padding=padding,
                truncation=True,
                max_length=MAX_LENGTH,
            )
            hidden = _embedder.encode(inputs)   # [1, seq, 1024]
            vec = hidden[0, 0, :]               # CLS token
            norm = np.linalg.norm(vec)
            vecs.append((vec / max(norm, 1e-9)).tolist())
        return vecs
    inputs = _tokenizer(
        texts,
        return_tensors="pt",
        padding=padding,
        truncation=True,
        max_length=MAX_LENGTH,
    )
    hidden = _embedder.encode(inputs)   # [batch, seq, 1024]
    vecs = hidden[:, 0, :]             # CLS token — BGE-M3 dense retrieval
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / np.clip(norms, 1e-9, None)
    return vecs.tolist()


def _embed_hybrid(texts: list[str]) -> tuple[list[list[float]], list[dict]]:
    """Returns (dense_list, sparse_list) for each input text."""
    if isinstance(_embedder, _TorchEmbedder):
        dense, sparse = _embedder.encode_texts(texts)
        return dense.tolist(), sparse

    padding = "max_length" if DEVICE == "NPU" else True
    dense_out, sparse_out = [], []
    for text in texts:
        inputs = _tokenizer(
            [text],
            return_tensors="pt",
            padding=padding,
            truncation=True,
            max_length=MAX_LENGTH,
        )
        hidden = _embedder.encode(inputs)          # [1, seq, 1024]
        # Dense: normalized CLS token
        vec = hidden[0, 0, :]
        norm = np.linalg.norm(vec)
        dense_out.append((vec / max(norm, 1e-9)).tolist())
        # Sparse: lexical weights per token
        input_ids   = inputs["input_ids"][0].numpy()
        att_mask    = inputs["attention_mask"][0].numpy()
        sparse_out.append(_compute_sparse(hidden[0], att_mask, input_ids))
    return dense_out, sparse_out


# ── API ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="BGE-M3 Embedding Service")

# One inference at a time — NPU is single-threaded. Callers wait, never rejected.
_npu_sem = asyncio.Semaphore(1)


class EmbedRequest(BaseModel):
    model: str
    input: Union[str, list[str]]


@app.post("/api/embed")
async def embed(req: EmbedRequest):
    texts = [req.input] if isinstance(req.input, str) else req.input
    if not texts:
        raise HTTPException(status_code=400, detail="input is empty")
    try:
        async with _npu_sem:
            embeddings = await asyncio.to_thread(_embed, texts)
        return JSONResponse({"embeddings": embeddings})
    except Exception as e:
        log.error("Embedding failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/embed/hybrid")
async def embed_hybrid(req: EmbedRequest):
    """Returns both dense (1024-dim) and sparse (BGE-M3 lexical) vectors."""
    texts = [req.input] if isinstance(req.input, str) else req.input
    if not texts:
        raise HTTPException(status_code=400, detail="input is empty")
    try:
        async with _npu_sem:
            dense, sparse = await asyncio.to_thread(_embed_hybrid, texts)
        return JSONResponse({"dense": dense, "sparse": sparse})
    except Exception as e:
        log.error("Hybrid embed failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    model = HF_MODEL if DEVICE == "torch" else MODEL_PATH
    return {"status": "ok", "model": model, "device": DEVICE}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
