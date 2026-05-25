#!/usr/bin/env python3
"""
Norsk Wikipedia → Qdrant indeksering
Kan avbrytes og fortsettes — checkpoint lagres underveis.
Kjøres: /kaare/venv/bin/python /kaare/scripts/wiki_indexer.py
"""

import bz2
import json
import time
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import mwparserfromhell
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, OptimizersConfigDiff

# ── Konfigurasjon ──────────────────────────────────────────────────────────────
DUMP_FILE       = Path("/mnt/wiki/nowiki-latest-pages-articles-multistream.xml.bz2")
CHECKPOINT_FILE = Path("/mnt/wiki/nowiki_checkpoint.json")
LOG_FILE        = Path("/mnt/wiki/nowiki_indexer.log")

EMBED_URL    = "http://localhost:11446/api/embed"
EMBED_MODEL  = "qwen3-embedding:8b"
QDRANT_URL   = "http://localhost:6333"
COLLECTION   = "wiki_no"
VECTOR_DIM   = 4096
LANGUAGE     = "no"

BATCH_EMBED  = 32    # chunks per embed-kall
BATCH_UPSERT = 100   # punkter per qdrant-upsert
MIN_CHUNK    = 100   # minimum tegn per chunk
MAX_CHUNK    = 1000  # maksimum tegn per chunk
LOG_INTERVAL = 200   # print fremdrift hver N artikkel

MW_NS = "{http://www.mediawiki.org/xml/export-0.11/}"


# ── Logging ────────────────────────────────────────────────────────────────────
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Checkpoint ─────────────────────────────────────────────────────────────────
def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text())
    return {"articles_done": 0, "chunks_done": 0}

def save_checkpoint(articles: int, chunks: int):
    CHECKPOINT_FILE.write_text(json.dumps({
        "articles_done": articles,
        "chunks_done": chunks,
        "updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }))


# ── Tekst-rensing og chunking ───────────────────────────────────────────────────
def extract_text(wikitext: str) -> str:
    try:
        parsed = mwparserfromhell.parse(wikitext)
        return parsed.strip_code(normalize=True, collapse=True).strip()
    except Exception:
        return ""

def chunk_text(text: str) -> list[str]:
    chunks = []
    for para in text.split("\n\n"):
        para = para.strip()
        if len(para) < MIN_CHUNK:
            continue
        if len(para) <= MAX_CHUNK:
            chunks.append(para)
        else:
            # Del opp lange avsnitt på setningsgrenser
            current = ""
            for sentence in para.replace(". ", ".\n").split("\n"):
                if len(current) + len(sentence) > MAX_CHUNK and current:
                    if len(current) >= MIN_CHUNK:
                        chunks.append(current.strip())
                    current = sentence
                else:
                    current += " " + sentence
            if len(current.strip()) >= MIN_CHUNK:
                chunks.append(current.strip())
    return chunks


# ── Embedding ──────────────────────────────────────────────────────────────────
def embed_batch(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(EMBED_URL, json={"model": EMBED_MODEL, "input": texts}, timeout=120)
    resp.raise_for_status()
    return resp.json()["embeddings"]


# ── Qdrant ─────────────────────────────────────────────────────────────────────
def ensure_collection(client: QdrantClient):
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            optimizers_config=OptimizersConfigDiff(indexing_threshold=20000),
        )
        log(f"Opprettet Qdrant-collection '{COLLECTION}'")
    else:
        log(f"Collection '{COLLECTION}' finnes allerede — fortsetter")

def upsert_batch(client: QdrantClient, points: list[PointStruct]):
    client.upsert(collection_name=COLLECTION, points=points, wait=False)


# ── Hoved-parsing ──────────────────────────────────────────────────────────────
def iter_articles(dump_path: Path):
    """Leser Wikipedia XML-dump og yielder (article_id, title, wikitext)."""
    with bz2.open(dump_path, "rb") as f:
        title = ns = article_id = text = None
        for event, elem in ET.iterparse(f, events=("start", "end")):
            tag = elem.tag.replace(MW_NS, "")
            if event == "end":
                if tag == "title":
                    title = elem.text or ""
                elif tag == "ns":
                    ns = elem.text
                elif tag == "id" and article_id is None:
                    article_id = elem.text
                elif tag == "text":
                    text = elem.text or ""
                elif tag == "page":
                    if ns == "0" and title and text:
                        yield article_id, title, text
                    # Reset
                    title = ns = article_id = text = None
                    elem.clear()


# ── Hovedløkke ─────────────────────────────────────────────────────────────────
def main():
    checkpoint = load_checkpoint()
    skip = checkpoint["articles_done"]
    total_chunks = checkpoint["chunks_done"]

    log(f"Starter indeksering av {DUMP_FILE.name}")
    log(f"Hopper over {skip} allerede indekserte artikler")

    client = QdrantClient(url=QDRANT_URL, timeout=30)
    ensure_collection(client)

    pending_chunks: list[tuple[str, str, int]] = []  # (title, chunk_text, chunk_idx)
    pending_points: list[PointStruct] = []
    articles_done = skip
    start_time = time.time()

    def flush_pending():
        nonlocal total_chunks, pending_chunks, pending_points
        if not pending_chunks:
            return
        texts = [c[1] for c in pending_chunks]
        try:
            embeddings = embed_batch(texts)
        except Exception as e:
            log(f"FEIL embed: {e} — hopper over batch")
            pending_chunks.clear()
            return

        for (title, chunk_text, chunk_idx), vector in zip(pending_chunks, embeddings):
            point_id = abs(hash(f"{title}_{chunk_idx}")) % (2**63)
            pending_points.append(PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "title": title,
                    "text": chunk_text,
                    "chunk_index": chunk_idx,
                    "language": LANGUAGE,
                    "url": f"https://no.wikipedia.org/wiki/{title.replace(' ', '_')}",
                }
            ))
        pending_chunks.clear()

        if len(pending_points) >= BATCH_UPSERT:
            upsert_batch(client, pending_points)
            total_chunks += len(pending_points)
            pending_points.clear()

    for article_id, title, wikitext in iter_articles(DUMP_FILE):
        # Hopp over allerede prosesserte
        if articles_done < skip:
            articles_done += 1
            continue

        # Hopp over videresendings- og uklarhetssider
        if wikitext.strip().upper().startswith("#REDIRECT") or "(uklarhet)" in title:
            articles_done += 1
            continue

        plain = extract_text(wikitext)
        chunks = chunk_text(plain)

        for i, chunk in enumerate(chunks):
            pending_chunks.append((title, chunk, i))
            if len(pending_chunks) >= BATCH_EMBED:
                flush_pending()

        articles_done += 1

        if articles_done % LOG_INTERVAL == 0:
            elapsed = time.time() - start_time
            rate = (articles_done - skip) / elapsed if elapsed > 0 else 0
            log(f"Artikler: {articles_done} | Chunks: {total_chunks + len(pending_points)} | {rate:.1f} art/s")
            save_checkpoint(articles_done, total_chunks + len(pending_points))

    # Tøm resten
    flush_pending()
    if pending_points:
        upsert_batch(client, pending_points)
        total_chunks += len(pending_points)

    save_checkpoint(articles_done, total_chunks)

    # Tving indeksbygging
    log("Bygger vektorindeks i Qdrant...")
    client.update_collection(
        collection_name=COLLECTION,
        optimizers_config=OptimizersConfigDiff(indexing_threshold=0)
    )

    elapsed = time.time() - start_time
    log(f"Ferdig! {articles_done} artikler, {total_chunks} chunks på {elapsed/60:.1f} minutter")


if __name__ == "__main__":
    main()
