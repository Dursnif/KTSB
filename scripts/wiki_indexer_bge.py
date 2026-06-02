#!/usr/bin/env python3
"""
Norwegian Wikipedia → Qdrant (BGE-M3 hybrid: dense 1024-dim + sparse)

Improvements over v1:
- Template expansion: convert, formatnum, infobox, unit templates
- chunk_index stored in payload for reliable article reconstruction
- article_id stored in payload

Run:
    HF_HOME=/mnt/wiki/hf_cache PYTHONPATH=/kaare \
        CUDA_VISIBLE_DEVICES=0 /kaare/venv/bin/python /kaare/scripts/wiki_indexer_bge.py

GPU: CUDA_VISIBLE_DEVICES=0 isolates Blackwell (RTX PRO 4000, ECC GDDR7).
Stop ollama-kare before running to free Blackwell VRAM.
"""

import bz2
import json
import os
import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import mwparserfromhell
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, HnswConfigDiff, PointStruct, SparseIndexParams,
    SparseVector, SparseVectorParams, VectorParams,
)

# ── Config ─────────────────────────────────────────────────────────────────────
DUMP_FILE       = Path("/mnt/wiki/nowiki-latest-pages-articles-multistream.xml.bz2")
CHECKPOINT_FILE = Path("/mnt/wiki/nowiki_bge_checkpoint.json")
LOG_FILE        = Path("/mnt/wiki/nowiki_bge_indexer.log")

QDRANT_URL      = "http://localhost:6333"
COLLECTION      = "wiki_no"
LANGUAGE        = "no"

EMBED_DEVICE    = "cuda:0"   # Blackwell when CUDA_VISIBLE_DEVICES=0
EMBED_BATCH     = 128        # chunks per model.encode() call
UPSERT_BATCH    = 200        # points per Qdrant upsert
MIN_CHUNK       = 100        # minimum chars per chunk
MAX_CHUNK       = 800        # maximum chars per chunk
LOG_INTERVAL    = 500        # log progress every N articles

MW_NS = "{http://www.mediawiki.org/xml/export-0.11/}"

# Norwegian unit shorthand templates → "value unit"
_UNIT_TEMPLATES = {
    "km": "km", "m": "m", "cm": "cm", "mm": "mm",
    "km2": "km²", "m2": "m²", "ha": "ha",
    "kg": "kg", "g": "g", "t": "tonn",
    "nok": "kr", "moh": "moh", "knop": "knop",
    "kw": "kW", "mw": "MW", "gwh": "GWh",
}


# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ── Checkpoint ─────────────────────────────────────────────────────────────────
def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {"articles_done": 0, "chunks_done": 0}


def save_checkpoint(articles_done: int, chunks_done: int) -> None:
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({
            "articles_done": articles_done,
            "chunks_done": chunks_done,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f)


# ── Template expansion ─────────────────────────────────────────────────────────
def _get_param(template, key: str | int, default: str = "") -> str:
    """Get template parameter by name or 1-based position."""
    key_str = str(key)
    for p in template.params:
        if p.name.strip_code().strip() == key_str:
            return p.value.strip_code().strip()
    try:
        idx = int(key)
        positional = [p for p in template.params if not p.showkey]
        if 1 <= idx <= len(positional):
            return positional[idx - 1].value.strip_code().strip()
    except (ValueError, TypeError):
        pass
    return default


def expand_template(template) -> str:
    """
    Expand a Wikipedia template to readable text.
    Returns '' for unknown templates (strip_code will handle them).
    """
    raw_name = template.name.strip_code().strip()
    name_lc  = raw_name.lower()

    # Magic word: {{formatnum:12345}} — colon is part of the name
    if ":" in raw_name:
        prefix, _, value = raw_name.partition(":")
        if prefix.strip().lower() == "formatnum":
            # Remove thousand-separator spaces/commas, keep the number
            return re.sub(r"[\s,\xa0]", "", value.strip())

    # {{convert|value|from_unit|to_unit|...}} → "value from_unit"
    if name_lc == "convert":
        val  = _get_param(template, 1)
        unit = _get_param(template, 2)
        return f"{val} {unit}" if val else ""

    # {{formatnum|N}}
    if name_lc == "formatnum":
        return _get_param(template, 1)

    # Norwegian unit shorthand templates: {{km|24.5}} → "24.5 km"
    if name_lc in _UNIT_TEMPLATES:
        val = _get_param(template, 1)
        return f"{val} {_UNIT_TEMPLATES[name_lc]}" if val else ""

    # Date templates: {{dato|YYYY|MM|DD}} or {{dato|DD|MM|YYYY}}
    if name_lc in ("dato", "date", "fødselsdato", "dødsdato"):
        p1 = _get_param(template, 1)
        p2 = _get_param(template, 2)
        p3 = _get_param(template, 3)
        if p3:
            return f"{p1}.{p2}.{p3}"
        if p2:
            return f"{p1}.{p2}"
        return p1

    # Year/number passthrough
    if name_lc in ("år", "year", "fødselsår", "dødsår", "alder"):
        return _get_param(template, 1)

    # Infobox — render named params as "key: value" text block
    if "infobox" in name_lc or "infoboks" in name_lc:
        parts = []
        for p in template.params:
            k = p.name.strip_code().strip()
            v = p.value.strip_code().strip()
            if k and v and not k.isdigit():
                parts.append(f"{k}: {v}")
        return "\n".join(parts) if parts else ""

    # Coordinate templates: {{coord|59.9|10.7|...}} → "59.9°N 10.7°E"
    if name_lc in ("coord", "koordinat", "coords"):
        lat = _get_param(template, 1)
        lon = _get_param(template, 2)
        return f"{lat}°N {lon}°E" if lat and lon else ""

    return ""  # unknown → stripped by strip_code()


def extract_text(wikitext: str) -> str:
    """Parse wikitext, expand known templates, then strip remaining markup."""
    try:
        parsed = mwparserfromhell.parse(wikitext)
        # Expand templates before stripping
        for template in parsed.filter_templates():
            expanded = expand_template(template)
            if expanded:
                try:
                    parsed.replace(template, " " + expanded + " ")
                except Exception:
                    pass
        return parsed.strip_code().strip()
    except Exception:
        return wikitext[:5000]


# ── Text chunking ──────────────────────────────────────────────────────────────
def chunk_text(text: str, title: str) -> list[str]:
    chunks = []
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) >= MIN_CHUNK]
    current = f"{title}: "
    for para in paragraphs:
        if len(current) + len(para) <= MAX_CHUNK:
            current += para + " "
        else:
            if len(current) >= MIN_CHUNK:
                chunks.append(current.strip())
            current = para + " "
    if len(current) >= MIN_CHUNK:
        chunks.append(current.strip())
    return chunks


# ── Qdrant helpers ─────────────────────────────────────────────────────────────
def to_sparse_vector(lexical_weights: dict) -> SparseVector:
    indices = [int(k) for k in lexical_weights.keys()]
    values  = [float(v) for v in lexical_weights.values()]
    return SparseVector(indices=indices, values=values)


def setup_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION in existing:
        log(f"Deleting existing collection '{COLLECTION}'")
        client.delete_collection(COLLECTION)

    log(f"Creating collection '{COLLECTION}' (dense 1024-dim + sparse, cosine, payload on disk)")
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": VectorParams(size=1024, distance=Distance.COSINE)
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(index=SparseIndexParams(on_disk=False))
        },
        hnsw_config=HnswConfigDiff(m=16, ef_construct=100, on_disk=False),
        on_disk_payload=True,
    )


# ── XML article iterator ───────────────────────────────────────────────────────
def iter_articles(skip: int = 0):
    count = 0
    with bz2.open(DUMP_FILE, "rb") as f:
        context = ET.iterparse(f, events=("end",))
        for event, elem in context:
            if elem.tag != f"{MW_NS}page":
                continue
            ns = elem.findtext(f"{MW_NS}ns")
            if ns != "0":
                elem.clear()
                continue
            title    = elem.findtext(f"{MW_NS}title") or ""
            page_id  = elem.findtext(f"{MW_NS}id") or ""
            text_el  = elem.find(f".//{MW_NS}text")
            wikitext = text_el.text if text_el is not None and text_el.text else ""
            elem.clear()
            if not wikitext or wikitext.startswith("#REDIRECT"):
                continue
            count += 1
            if count <= skip:
                continue
            yield count, title, page_id, wikitext


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    log("Starting BGE-M3 wiki indexer v2 (template expansion + chunk_index)")
    log(f"Dump: {DUMP_FILE}")
    log(f"Device: {EMBED_DEVICE} | Batch: {EMBED_BATCH} | Chunk max: {MAX_CHUNK} chars")

    checkpoint       = load_checkpoint()
    skip_articles    = checkpoint["articles_done"]
    chunk_id_offset  = checkpoint["chunks_done"]
    log(f"Checkpoint: skip {skip_articles} articles, chunk_id offset {chunk_id_offset}")

    client = QdrantClient(url=QDRANT_URL, timeout=60)

    if skip_articles == 0:
        setup_collection(client)
    else:
        log(f"Resuming into existing collection '{COLLECTION}'")

    log(f"Loading BGE-M3 on {EMBED_DEVICE} (fp16) ...")
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True, device=EMBED_DEVICE)
    log("Model loaded. Starting indexing.")

    articles_done = skip_articles
    chunks_done   = chunk_id_offset
    batch_texts: list[str]  = []
    batch_meta:  list[dict] = []
    t_start      = time.time()

    def flush_batch() -> None:
        nonlocal chunks_done
        if not batch_texts:
            return
        output = model.encode(
            batch_texts,
            batch_size=EMBED_BATCH,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
            max_length=512,
        )
        dense_vecs      = output["dense_vecs"]
        lexical_weights = output["lexical_weights"]

        points = [
            PointStruct(
                id=chunks_done + i,
                vector={
                    "dense":  dense_vecs[i].tolist(),
                    "sparse": to_sparse_vector(lexical_weights[i]),
                },
                payload={
                    "title":       meta["title"],
                    "article_id":  meta["article_id"],
                    "chunk_index": meta["chunk_index"],
                    "text":        meta["text"],
                    "lang":        LANGUAGE,
                },
            )
            for i, meta in enumerate(batch_meta)
        ]

        for offset in range(0, len(points), UPSERT_BATCH):
            client.upsert(collection_name=COLLECTION, points=points[offset:offset + UPSERT_BATCH])

        chunks_done += len(batch_texts)
        batch_texts.clear()
        batch_meta.clear()

    for art_count, title, page_id, wikitext in iter_articles(skip=skip_articles):
        text   = extract_text(wikitext)
        chunks = chunk_text(text, title)

        for idx, chunk in enumerate(chunks):
            batch_texts.append(chunk)
            batch_meta.append({
                "title":       title,
                "article_id":  page_id,
                "chunk_index": idx,
                "text":        chunk,
            })
            if len(batch_texts) >= EMBED_BATCH:
                flush_batch()

        articles_done += 1

        if articles_done % LOG_INTERVAL == 0:
            elapsed  = time.time() - t_start
            rate     = (articles_done - skip_articles) / elapsed if elapsed > 0 else 0
            eta_min  = ((1_020_000 - articles_done) / rate / 60) if rate > 0 else 0
            log(f"Articles: {articles_done:,} | Chunks: {chunks_done:,} | "
                f"{rate:.1f} art/s | ETA: {eta_min:.0f} min")
            save_checkpoint(articles_done, chunks_done)

    flush_batch()
    save_checkpoint(articles_done, chunks_done)

    elapsed = time.time() - t_start
    log(f"Done! {articles_done:,} articles, {chunks_done:,} chunks in {elapsed/60:.1f} min")
    log(f"Collection '{COLLECTION}': {client.get_collection(COLLECTION).points_count:,} points")


if __name__ == "__main__":
    main()
