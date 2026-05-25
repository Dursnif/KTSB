"""
Lokal CPU-embedder for intent-serveren.

Bruker paraphrase-multilingual-MiniLM-L12-v2 (ONNX) fra Prism sin modellmappe.
Ingen Ollama, ingen Docker — kjører direkte i venv, alltid varm.

Modell: /kaare/state/prism/models/paraphrase-multilingual-MiniLM-L12-v2/
"""

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_DIR = "/kaare/state/prism/models/paraphrase-multilingual-MiniLM-L12-v2"
MAX_LEN = 128

# Lastes én gang ved import — ligger varm i RAM
_tokenizer = Tokenizer.from_file(f"{MODEL_DIR}/tokenizer.json")
_tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=MAX_LEN)
_tokenizer.enable_truncation(max_length=MAX_LEN)

_session = ort.InferenceSession(
    f"{MODEL_DIR}/model.onnx",
    providers=["CPUExecutionProvider"],
)


def embed(texts: list[str]) -> np.ndarray:
    """
    Returnerer normaliserte embeddings, shape [len(texts), 384].
    Kompatibel med FAISS IndexFlatIP.
    """
    if not texts:
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

    # last_hidden_state: [batch, seq_len, hidden_size]
    token_embeddings = outputs[0].astype(np.float32)

    # Mean pooling vektet mot attention mask
    mask = attention_mask[:, :, np.newaxis].astype(np.float32)
    summed = (token_embeddings * mask).sum(axis=1)
    counts = mask.sum(axis=1).clip(min=1e-9)
    embeddings = summed / counts

    # L2-normalisering
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-9)
    return (embeddings / norms).astype("float32")
