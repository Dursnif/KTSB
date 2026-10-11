# /kaare/adapters/lovdata_adapter.py
"""
Norwegian law as a local source, from Lovdata's open datasets (P123).

lovdata.no forbids bots in robots.txt; Lovdata instead publishes the current laws and central
regulations as free open datasets for machines (NLOD 2.0, «fritt for alle formål», no
authentication, new packages every night): https://api.lovdata.no/om-api-tjenesten/
So KTSB never fetches lovdata.no pages — it downloads the documented dataset (once per night or
on an admin's «update now»), splits it per paragraph and indexes it locally in SQLite FTS5.

Licence (NLOD 2.0 § 5): every answer built on this data carries ATTRIBUTION below.
"""

from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import re
import sqlite3
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

from lxml import html as lxml_html

from adapters import web_fetch
from kaare_core.config import get_service

log = logging.getLogger(__name__)

DB_PATH = Path("/kaare/state/law/lovdata.db")
DEFAULT_DATASETS = (
    "https://api.lovdata.no/v1/publicData/get/gjeldende-lover.tar.bz2",
    "https://api.lovdata.no/v1/publicData/get/gjeldende-sentrale-forskrifter.tar.bz2",
)
ATTRIBUTION = (
    "Inneholder data under Norsk lisens for offentlige data (NLOD) 2.0 tilgjengeliggjort av "
    "Lovdata — https://data.norge.no/nlod/no/2.0 — kilde: https://api.lovdata.no/"
)
_ARCHIVE_TYPES = (
    "application/x-bzip2",
    "application/x-bzip",
    "application/octet-stream",
    "application/x-tar",
    "application/bzip2",
)
_MAX_ARCHIVE_BYTES = 80_000_000
_WORD_RE = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
_refresh_lock = asyncio.Lock()


def config() -> dict:
    cfg = (get_service("law") or {}).get("lovdata") or {}
    datasets = cfg.get("datasets") or list(DEFAULT_DATASETS)
    return {"enabled": bool(cfg.get("enabled", False)), "datasets": [str(d) for d in datasets]}


def enabled() -> bool:
    return config()["enabled"] and DB_PATH.exists()


# ── Parsing ──────────────────────────────────────────────────────────────────


def _text(el) -> str:
    # itertext with a separator: block elements (ledd, list items) must not run together —
    # «…pensjonsalder» + «Aldersgrensen…» became «pensjonsalderAldersgrensen», unquotable.
    if el is None:
        return ""
    return " ".join(" ".join(el.itertext()).split())


def _public_url(lovdata_url: str) -> str:
    """«NL/lov/2005-05-20-28/§351» → «https://lovdata.no/lov/2005-05-20-28/§351» (for the user)."""
    path = lovdata_url.split("/", 1)[1] if "/" in lovdata_url else lovdata_url
    return "https://lovdata.no/" + path


def parse_document(raw: bytes) -> tuple[dict, list[dict]]:
    """One Lovdata file → (document info, paragraphs). Change notes are left out of the text."""
    # The files carry no charset declaration; they are UTF-8 (lxml would guess latin-1 on bytes).
    doc = lxml_html.fromstring(raw.decode("utf-8", errors="replace"))
    title = _text(doc.find(".//title"))
    short = doc.xpath('string(//dd[contains(concat(" ", @class, " "), " titleShort ")])').strip()
    dokid = doc.xpath('string(//dd[@class="dokid"])').strip()
    info = {"title": title, "short": short or title, "dokid": dokid}
    paragraphs = []
    for art in doc.xpath('//article[@class="legalArticle"]'):
        url = art.get("data-lovdata-url") or ""  # lxml.html lower-cases attribute names
        value = art.xpath('string(.//span[@class="legalArticleValue"])').strip()
        heading = art.xpath('string(.//span[@class="legalArticleTitle"])').strip()
        for note in art.xpath('.//*[@class="changesToParent" or @class="footnote"]'):
            note.getparent().remove(note)
        for header in art.xpath('.//*[contains(concat(" ", @class, " "), " legalArticleHeader ")]'):
            header.getparent().remove(header)
        body = _text(art)
        if value and body:
            paragraphs.append(
                {
                    "url": _public_url(url) if url else "",
                    "para": value,
                    "heading": heading,
                    "body": body,
                }
            )
    return info, paragraphs


# ── Index ────────────────────────────────────────────────────────────────────

_SCHEMA = (
    "CREATE VIRTUAL TABLE paragraphs USING fts5(law_title, short_title, para, heading, body, "
    'url UNINDEXED, dokid UNINDEXED, tokenize="unicode61 remove_diacritics 2")',
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)",
)


def build_index(archives: list[bytes], db_path: Path = DB_PATH) -> dict:
    """Parse archives into a NEW database file, then swap it in atomically."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".db.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    n_docs = n_paras = 0
    try:
        for stmt in _SCHEMA:
            con.execute(stmt)
        for archive in archives:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as tar:
                for member in tar:
                    if not member.isfile() or not member.name.endswith(".xml"):
                        continue
                    raw = tar.extractfile(member).read()
                    try:
                        info, paras = parse_document(raw)
                    except Exception as e:
                        log.warning("[lovdata] %s skipped: %s", member.name, e)
                        continue
                    n_docs += 1
                    con.executemany(
                        "INSERT INTO paragraphs VALUES (?,?,?,?,?,?,?)",
                        [
                            (
                                info["title"],
                                info["short"],
                                p["para"],
                                p["heading"],
                                p["body"],
                                p["url"],
                                info["dokid"],
                            )
                            for p in paras
                        ],
                    )
                    n_paras += len(paras)
        stats = {
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "documents": str(n_docs),
            "paragraphs": str(n_paras),
        }
        con.executemany("INSERT INTO meta VALUES (?,?)", stats.items())
        con.commit()
    finally:
        con.close()
    if n_docs == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("no documents parsed — keeping the previous index")
    os.replace(tmp, db_path)
    return stats


async def refresh() -> dict:
    """Download every configured dataset and rebuild the index. On any failure the previous
    index stays in place. One run at a time."""
    async with _refresh_lock:
        t0 = time.perf_counter()
        archives = []
        for url in config()["datasets"]:
            # api.lovdata.no's robots.txt disallows crawlers; this is the documented open-data
            # download Lovdata offers for exactly this use (P123 D1), so robots is not consulted.
            _, body, _ = await web_fetch.fetch_raw(
                url, respect_robots=False, max_bytes=_MAX_ARCHIVE_BYTES, accept=_ARCHIVE_TYPES
            )
            archives.append(body)
        stats = await asyncio.to_thread(build_index, archives)
        log.info(
            "[lovdata] index rebuilt: %s documents, %s paragraphs in %.1fs",
            stats["documents"],
            stats["paragraphs"],
            time.perf_counter() - t0,
        )
        return stats


def status(db_path: Path = DB_PATH) -> dict:
    if not db_path.exists():
        return {"built_at": None, "documents": 0, "paragraphs": 0}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        con.close()
        return {
            "built_at": meta.get("built_at"),
            "documents": int(meta.get("documents", 0)),
            "paragraphs": int(meta.get("paragraphs", 0)),
        }
    except Exception as e:
        log.warning("[lovdata] status unreadable: %s", e)
        return {"built_at": None, "documents": 0, "paragraphs": 0}


# ── Search ───────────────────────────────────────────────────────────────────


def _match_expression(query: str, keywords: list[str]) -> str:
    """FTS5 query: any of the words (≥ 4 letters) as prefixes — «skadeverk» also finds
    «skadeverket». Kåre's keywords carry the law's own terms (the 27B's language work)."""
    return " OR ".join(f'"{w}"*' for w in _terms(query, keywords))


_RERANK_POOL = 50


def _terms(query: str, keywords: list[str]) -> list[str]:
    words = []
    for text in [query, *keywords]:
        words += [w.lower() for w in _WORD_RE.findall(text or "")]
    return list(dict.fromkeys(words))[:20]


def search(
    query: str, keywords: list[str] | None = None, limit: int = 6, db_path: Path | None = None
) -> list[dict]:
    """Best-matching paragraphs. BM25 picks a pool of 50; then paragraphs that match more of the
    search words win, each word weighted by how rare it is in the collection (coverage first,
    BM25 second) — common words like «alder» or «eller» must not beat «øvingskjøring»."""
    db_path = db_path or DB_PATH
    terms = _terms(query, keywords or [])
    expr = " OR ".join(f'"{w}"*' for w in terms)
    if not expr or not db_path.exists():
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT short_title, para, heading, body, url, bm25(paragraphs, 3.0, 3.0, 1.0, 5.0, 1.0) "
            "FROM paragraphs WHERE paragraphs MATCH ? ORDER BY 6 LIMIT ?",
            (expr, _RERANK_POOL),
        ).fetchall()
        total = int(con.execute("SELECT count(*) FROM paragraphs").fetchone()[0]) or 1
        # How rare each word is in the whole collection — «eller» is everywhere and weighs
        # almost nothing, «øvingskjøring» is in a few dozen paragraphs and weighs a lot.
        # Language-free (no stop-word lists): plain document frequency.
        idf = {
            w: math.log(
                1
                + total
                / (
                    1
                    + con.execute(
                        "SELECT count(*) FROM paragraphs WHERE paragraphs MATCH ?", (f'"{w}"*',)
                    ).fetchone()[0]
                )
            )
            for w in terms
        }
    finally:
        con.close()

    def coverage(row) -> float:
        hay = f"{row[0]} {row[2]} {row[3]}".lower()
        return sum(idf[w] for w in terms if w in hay)

    # Reciprocal rank fusion of the two orders (BM25 as returned, rarity-weighted coverage):
    # a paragraph strong on either keeps a place; strong on both wins (k = 60, the usual RRF
    # constant). A query that mixes two needs (licence age + practice driving) keeps both.
    by_cov = sorted(range(len(rows)), key=lambda i: -coverage(rows[i]))
    cov_rank = {i: r for r, i in enumerate(by_cov)}
    fused = sorted(range(len(rows)), key=lambda i: -(1 / (60 + i) + 1 / (60 + cov_rank[i])))
    rows = [rows[i] for i in fused][:limit]
    return [
        {
            "title": f"{short} {para}" + (f" {heading}" if heading else ""),
            "url": url,
            "text": f"{para}. {heading}. {body}" if heading else f"{para}. {body}",
        }
        for short, para, heading, body, url, _ in rows
    ]
