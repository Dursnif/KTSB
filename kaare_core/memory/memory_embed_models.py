"""
Embedding models for semantic memory (P126) — the catalogue and the active choice.

Three tiers the GUI offers, plus the earlier default kept for existing installs:
  small  — MiniLM int8   (118 MB, 384-dim)
  medium — mpnet-base    (1.1 GB, 768-dim)
  large  — BGE-M3        (the separate embedding service, 1024-dim — nothing to download here)
  legacy — MiniLM fp32   (470 MB, 384-dim; what installs used before P126)

Each model has its own Qdrant collection, because vectors from different models cannot be
compared. Switching model therefore means building the new collection from the episodes in
SQLite (semantic_reindex.py); the old collection is left untouched, so switching back is instant.

min_score is calibrated per model (2026-10-07, 20 household facts × nb/en/de questions + 10
distractors): the threshold that lets the same share of wrong matches through as 0.35 does for
MiniLM. A single shared threshold is wrong — BGE-M3 scores unrelated text around 0.46.
"""

from pathlib import Path

from kaare_core.config import get_memory_embed_config, get_models_dir

MODELS: dict[str, dict] = {
    "minilm_int8": {
        "tier": "small",
        "repo": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "files": {"model.onnx": "onnx/model_quint8_avx2.onnx", "tokenizer.json": "tokenizer.json"},
        "dim": 384,
        "max_len": 128,
        "min_score": 0.35,
        "size_mb": 118,
        "backend": "local",
        "collection": "kaare_memory_minilm_int8",
    },
    "mpnet": {
        "tier": "medium",
        "repo": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
        "files": {"model.onnx": "onnx/model.onnx", "tokenizer.json": "tokenizer.json"},
        "dim": 768,
        "max_len": 128,
        "min_score": 0.41,
        "size_mb": 1110,
        "backend": "local",
        "collection": "kaare_memory_mpnet",
    },
    "bge_m3": {
        "tier": "large",
        "repo": "",
        "files": {},
        "dim": 1024,
        "max_len": 0,
        "min_score": 0.50,
        "size_mb": 0,
        "backend": "embedding_service",
        "collection": "kaare_memory_bge_m3",
    },
    "minilm": {
        "tier": "legacy",
        "repo": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "files": {"model.onnx": "onnx/model.onnx", "tokenizer.json": "tokenizer.json"},
        "dim": 384,
        "max_len": 128,
        "min_score": 0.35,
        "size_mb": 470,
        "backend": "local",
        # Name kept from before P126 so existing installs keep their memory without a rebuild.
        "collection": "kaare_memory",
    },
}

DEFAULT_MODEL = "minilm_int8"  # lightest tier (rule 24)


def active_model_key() -> str:
    """The model chosen in the GUI. A pre-P126 config has no `model` key: if it already points at
    model files it ran the old MiniLM fp32 — keep that, so nothing changes until the user switches."""
    cfg = get_memory_embed_config()
    key = cfg.get("model")
    if key in MODELS:
        return key
    return "minilm" if cfg.get("model_dir") else DEFAULT_MODEL


def active_model() -> dict:
    return {"key": active_model_key(), **MODELS[active_model_key()]}


def default_model_dir(key: str) -> Path:
    """Where the GUI download puts a model: <models_dir>/semantic-embed/<key>."""
    return get_models_dir() / "semantic-embed" / key


def model_dir_for(key: str) -> Path:
    """The folder the model is read from: the configured model_dir while that model is active
    (users may point it elsewhere in Expert mode), a folder remembered from when it was active
    (model_dirs), otherwise the download location."""
    cfg = get_memory_embed_config()
    if key == active_model_key() and cfg.get("model_dir"):
        return Path(cfg["model_dir"])
    saved = (cfg.get("model_dirs") or {}).get(key)
    return Path(saved) if saved else default_model_dir(key)


def is_downloaded(key: str) -> bool:
    spec = MODELS[key]
    if spec["backend"] != "local":
        return True
    folder = model_dir_for(key)
    return all((folder / name).exists() for name in spec["files"])
