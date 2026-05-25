from fastapi import FastAPI
from pydantic import BaseModel
import numpy as np

from local_embedder import embed as _local_embed

app = FastAPI(title="Kåre Semantic Embed")


class EmbedRequest(BaseModel):
    texts: list[str]


def _embed_texts(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 384), dtype="float32")
    return _local_embed(texts)


@app.get("/")
def root():
    return {"status": "ok", "message": "Semantic embed alive"}


@app.post("/embed")
def embed_texts(req: EmbedRequest):
    if not req.texts:
        return {"ok": False, "error": "no_texts"}
    vecs = _embed_texts(req.texts)
    return {"ok": True, "embeddings": vecs.tolist()}
