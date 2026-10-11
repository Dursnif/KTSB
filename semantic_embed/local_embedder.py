"""
Local CPU embedder for semantic memory (ONNX, mean pooling).

Model-agnostic: works for every local model in kaare_core/memory/memory_embed_models.py
(MiniLM int8/fp32, mpnet-base). The vector size is read from the model itself at load time —
nothing here assumes a dimension.

Call load(model_dir, max_len) once at startup before calling embed().
"""

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

DEFAULT_MAX_LEN = 128

_tokenizer: Tokenizer | None = None
_session: ort.InferenceSession | None = None
_input_names: set[str] = set()
_dim: int = 0


def load(model_dir: str, max_len: int = DEFAULT_MAX_LEN) -> None:
    global _tokenizer, _session, _input_names, _dim
    tok = Tokenizer.from_file(f"{model_dir}/tokenizer.json")
    tok.enable_padding(pad_id=tok.token_to_id("<pad>") or 0, length=None)
    tok.enable_truncation(max_length=max_len)
    session = ort.InferenceSession(f"{model_dir}/model.onnx", providers=["CPUExecutionProvider"])
    _tokenizer = tok
    _session = session
    _input_names = {i.name for i in session.get_inputs()}
    _dim = int(_run(["dimension probe"]).shape[1])


def dim() -> int:
    """Vector size of the loaded model (0 if none is loaded)."""
    return _dim


def _run(texts: list[str]) -> np.ndarray:
    encodings = _tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    feed = {"input_ids": input_ids, "attention_mask": attention_mask}
    if "token_type_ids" in _input_names:
        feed["token_type_ids"] = np.zeros_like(input_ids)
    token_embeddings = _session.run(None, feed)[0].astype(np.float32)
    mask = attention_mask[:, :, np.newaxis].astype(np.float32)
    summed = (token_embeddings * mask).sum(axis=1)
    counts = mask.sum(axis=1).clip(min=1e-9)
    embeddings = summed / counts
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-9)
    return (embeddings / norms).astype("float32")


def embed(texts: list[str]) -> np.ndarray:
    """Returns normalized embeddings, shape [len(texts), dim()]."""
    if not texts or _tokenizer is None or _session is None:
        return np.zeros((0, _dim), dtype="float32")
    return _run(texts)
