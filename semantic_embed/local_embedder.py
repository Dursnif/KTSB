"""
Local CPU embedder for semantic memory (384-dim).
Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (ONNX)

Call load(model_dir) once at startup before calling embed().
"""

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MAX_LEN = 128

_tokenizer: Tokenizer | None = None
_session: ort.InferenceSession | None = None


def load(model_dir: str) -> None:
    global _tokenizer, _session
    tok = Tokenizer.from_file(f"{model_dir}/tokenizer.json")
    tok.enable_padding(pad_id=0, pad_token="[PAD]", length=MAX_LEN)
    tok.enable_truncation(max_length=MAX_LEN)
    _tokenizer = tok
    _session = ort.InferenceSession(
        f"{model_dir}/model.onnx",
        providers=["CPUExecutionProvider"],
    )


def embed(texts: list[str]) -> np.ndarray:
    """Returns normalized embeddings, shape [len(texts), 384]."""
    if not texts or _tokenizer is None or _session is None:
        return np.zeros((0, 384), dtype="float32")

    encodings = _tokenizer.encode_batch(texts)

    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

    outputs = _session.run(None, {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "token_type_ids": token_type_ids,
    })

    token_embeddings = outputs[0].astype(np.float32)
    mask = attention_mask[:, :, np.newaxis].astype(np.float32)
    summed = (token_embeddings * mask).sum(axis=1)
    counts = mask.sum(axis=1).clip(min=1e-9)
    embeddings = summed / counts

    norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-9)
    return (embeddings / norms).astype("float32")
