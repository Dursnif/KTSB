import asyncio
import difflib
import json
import re
import time
from urllib.parse import urlparse

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    Prefetch,
)
from qdrant_client.models import (
    SparseVector as QSparseVector,
)

from adapters import lovdata_adapter, web_fetch
from adapters.llm_adapter import _current_rid, ask_llm_cloud, call_llm_chat
from adapters.web_search_adapter import web_search as _søk_nett
from kaare_core.background import spawn
from kaare_core.config import get_home_location, is_agent_tool_enabled
from kaare_core.config import get_llm_config as _llm_cfg
from kaare_core.config import get_model as _cfg_model
from kaare_core.config import get_qdrant_api_key as _qdrant_key
from kaare_core.config import get_service as _svc
from kaare_core.config import get_settings as _get_settings
from kaare_core.memory.long_term import get_ltm
from kaare_core.model_enforcement import STAKES, ModelEnforcer, grounded_detectors, norm_lang
from kaare_core.model_lock import LockTimeout, lock_agent_pool, pool_order, role_url
from kaare_core.think_strip import strip_think_block
from kaare_core.tools import source_confidence, wiki_registry, wiki_structured, wikidata, work_round
from kaare_core.tools.executor_personality import PERSONALITY_CORE_TEXT
from kaare_core.tools.i18n import get_lang, t
from kaare_core.tools.shared_tools import log_tool_call
from kaare_core.tools.think_cache import (
    extract_conclusion,
    log_think,
)

# After Miss Library has answered, wait at most this long for a still-running Wikidata subject
# lookup; past it the answer goes out unmarked (rule 10 — never hold Kåre up for a check).
_SUBJECT_MATCH_GRACE_S = 2.0
_KARE_MODEL = _cfg_model("kare")
_OLLAMA_PROXY_URL = _llm_cfg("default")["base_url"]

# ── Miss Library LLM config ───────────────────────────────────────────────────

_lib_cfg = _llm_cfg("library")
_LIBRARY_TIMEOUT = _lib_cfg.get("timeout", 120.0)
_TRACE_SOURCE = "miss_library"

# In-process fast path (avoids two coroutines in this process racing for the
# cross-process lock below); the cross-process lock_agent() is what actually
# serialises against Miss Kåre/Mechanic, which now share the same instance.
_LIBRARY_SEM = asyncio.Semaphore(1)

# ── Qdrant + BGE-M3 config ────────────────────────────────────────────────────

# A separate wiki Qdrant is optional (this install runs one on 6335); without the key the
# wiki collections live in the main Qdrant (P116 B23 — was None on a fresh install).
_QDRANT_URL = _svc("storage", "qdrant_wiki") or _svc("storage", "qdrant")
_WIKI_TOP_K = 8
_EMBED_HYBRID_URL = _svc("ollama", "embed") + "/api/embed/hybrid"
_EMBED_MODEL = _cfg_model("embed")

_qdrant = QdrantClient(url=_QDRANT_URL, api_key=_qdrant_key(write=False))

# ── Miss Library task prompt ──────────────────────────────────────────────────
# No persona: she is a lookup/filter tool for Kåre (P111 Phase 2, Stian 2026-09-28).
# The old kaare_core/agents/miss_library/personlighet*.md files are no longer loaded.


def _library_system(lang: str = "nb") -> str:
    return t("lib_task_system", lang)


LIBRARY_TOOLS = {
    "web_search",
    "søk_nett",
    "library",
    "spør_miss_library_online",
    "spør_miss_library",
    "hent_wiki_artikkel",
    "reason_freely",
}


# ── Home location for web search ──────────────────────────────────────────────
# Kåre decides per call whether the question is about the local area (near_home, enforced by
# NearHomeMissingDetector in kaare_core/model_enforcement.py); code only appends the place.
# Never a blanket prefix — that locked every search to home, even "election results in Sweden".


def parse_near_home(value) -> bool | None:
    """near_home as a bool, or None when missing/unreadable (models sometimes send "true")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def build_search_query(raw_query: str, near_home: bool) -> str:
    """Append the GUI-set home place to the query when near_home — unless it's already there."""
    query = raw_query.strip()
    if not near_home:
        return query
    loc = get_home_location()
    place = loc["city"] or loc["country"]
    if not place or place.lower() in query.lower():
        return query
    return f"{query} {place}"


# ── BGE-M3 hybrid embedding ───────────────────────────────────────────────────


async def _embed_hybrid(text: str) -> tuple[list[float], dict]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(_EMBED_HYBRID_URL, json={"model": _EMBED_MODEL, "input": text})
        r.raise_for_status()
        data = r.json()
        return data["dense"][0], data["sparse"][0]


# ── Qdrant wiki search ────────────────────────────────────────────────────────


def _trace(step: str, args: dict, result: str, t0: float) -> None:
    """Log one of Miss Library's internal steps to tool_calls.log under the current
    request's rid, so get_trace()/self_trace can see inside her work (P111 Phase 0)."""
    log_tool_call(
        step,
        args,
        result,
        int((time.perf_counter() - t0) * 1000),
        source=_TRACE_SOURCE,
        rid=_current_rid.get(),
    )


async def _wiki_search(query: str, collection: str, dense: list[float], sparse: dict) -> list[dict]:
    """Hybrid (dense + sparse, RRF) search in one wiki collection. The query is embedded once
    by the caller and reused for every wiki (P111 Phase 3)."""
    _t0 = time.perf_counter()
    sparse_vec = QSparseVector(indices=sparse["indices"], values=sparse["values"])
    hits = await asyncio.to_thread(
        _qdrant.query_points,
        collection_name=collection,
        prefetch=[
            Prefetch(query=dense, using="dense", limit=_WIKI_TOP_K * 4),
            Prefetch(query=sparse_vec, using="sparse", limit=_WIKI_TOP_K * 4),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=_WIKI_TOP_K,
        with_payload=True,
    )
    results = [
        {"title": h.payload.get("title", ""), "text": h.payload.get("text", "")}
        for h in hits.points
    ]
    _trace(
        "wiki_search",
        {"query": query, "collection": collection},
        f"{len(results)} hits: " + ", ".join(dict.fromkeys(r["title"] for r in results)),
        _t0,
    )
    return results


async def _wiki_fetch_article_qdrant(
    title: str, max_chars: int = 8000, collection: str = ""
) -> dict:
    _t0 = time.perf_counter()
    scroll_filter = Filter(must=[FieldCondition(key="title", match=MatchValue(value=title))])
    points, _ = await asyncio.to_thread(
        _qdrant.scroll,
        collection_name=collection,
        scroll_filter=scroll_filter,
        limit=300,
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        _trace("wiki_fetch_article", {"title": title, "collection": collection}, "not found", _t0)
        return {"title": title, "text": "", "chunk_count": 0, "article_id": None}
    points.sort(key=lambda p: p.payload.get("chunk_index", p.id))
    full_text = "\n\n".join(p.payload.get("text", "") for p in points)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "…"
    _trace(
        "wiki_fetch_article",
        {"title": title, "collection": collection},
        f"{len(points)} chunks, {len(full_text)} chars",
        _t0,
    )
    return {
        "title": title,
        "text": full_text,
        "chunk_count": len(points),
        "article_id": points[0].payload.get("article_id"),
    }


# ── Miss Library LLM call (serialised via Semaphore) ─────────────────────────


async def _library_llm_call(system: str, user: str) -> str:
    """Goes through llm_adapter.call_llm_chat so the call lands in llm_calls.log with the
    request's rid (P111 Phase 0). think/options/timeout come from llm.yaml's `library` role;
    the pool lock picks whichever 9B instance is free and the call is pinned to it."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        async with (
            _LIBRARY_SEM,
            lock_agent_pool(
                pool_order(role_url("library")), "library", max_wait=_LIBRARY_TIMEOUT
            ) as inst_url,
        ):
            result = await call_llm_chat(
                "library",
                messages,
                source=_TRACE_SOURCE,
                rid=_current_rid.get(),
                base_url=inst_url,
            )
        if result.get("error"):
            return f"[Miss Library unavailable: {result['error']}]"
        return (result.get("text") or "").strip()
    except LockTimeout:
        return "[Miss Library unavailable: model busy]"
    except Exception as e:
        return f"[Miss Library unavailable: {e}]"


# ── Quote-first answers (P111 Phase 5c) ──────────────────────────────────────
# Miss Library answers in a fixed JSON shape: every claim carries a verbatim quote + its
# source label. Code checks each quote against the source text already in memory (no LLM,
# no disk/Qdrant IO); a failed check gets exactly one correction round through the shared
# ModelEnforcer. Claims whose quote still isn't in the sources are dropped and Kåre is told.

_GROUNDED_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "claims": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "maxLength": 300},
                    # Short on purpose: a whole infobox quoted verbatim cut the answer at
                    # num_predict and forced a retry. A cut-off quote is still verbatim.
                    "quote": {"type": "string", "maxLength": 300},
                    "source": {"type": "string"},
                    # Language the quote is written in (P121): when it isn't the user's
                    # language, code drops the 9B's paraphrase and Kåre (27B) translates.
                    "quote_lang": {"type": "string", "pattern": "^[a-z]{2,3}$"},
                    # P121 Phase 4: same short label for claims about the same fact (code groups
                    # them and computes how likely each is); indices of claims this one conflicts with.
                    "topic": {"type": "string", "maxLength": 60},
                    "conflicts_with": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["claim", "quote", "source", "quote_lang", "topic"],
            },
        },
        "not_found": {"type": "boolean"},
        # Self-reported language of answer/claim — read by LanguageMismatchDetector (Phase 5d).
        "lang": {"type": "string", "pattern": "^[a-z]{2,3}$"},
    },
    "required": ["answer", "claims", "not_found", "lang"],
}

_QUOTE_CHARS = str.maketrans(dict.fromkeys("«»“”„\"'‘’*|", " "))
_DASHES = str.maketrans({"–": "-", "—": "-", "‑": "-", "−": "-"})


def _norm(text: str) -> str:
    return " ".join((text or "").translate(_DASHES).translate(_QUOTE_CHARS).lower().split())


def _quote_in_sources(quote: str, sources_norm: str) -> bool:
    """Verbatim after normalisation; an ellipsis may join several verbatim pieces; small
    copy slips are tolerated when the longest shared run covers ≥ 85 % of the quote."""
    pieces = [_norm(p) for p in re.split(r"\.\.\.|…", quote or "")]
    pieces = [p.strip(" .,:;") for p in pieces if len(p.strip(" .,:;")) >= 4]
    if not pieces:
        return False
    if all(p in sources_norm for p in pieces):
        return True
    q = " ".join(pieces)
    m = difflib.SequenceMatcher(None, sources_norm, q, autojunk=False).find_longest_match(
        0, len(sources_norm), 0, len(q)
    )
    return m.size >= 0.85 * len(q)


async def _library_chat(messages: list[dict], response_format: dict | None = None) -> dict:
    """One 9B call through the agent pool; returns call_llm_chat's result dict."""
    try:
        async with (
            _LIBRARY_SEM,
            lock_agent_pool(
                pool_order(role_url("library")), "library", max_wait=_LIBRARY_TIMEOUT
            ) as inst_url,
        ):
            return await call_llm_chat(
                "library",
                messages,
                source=_TRACE_SOURCE,
                rid=_current_rid.get(),
                base_url=inst_url,
                response_format=response_format,
            )
    except LockTimeout:
        return {"ok": False, "error": "model busy", "text": ""}
    except Exception as e:
        return {"ok": False, "error": str(e), "text": ""}


_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_NAME_RE = re.compile(r"\b[A-ZÆØÅÄÖÜ][\wæøåäöüé'-]{2,}")


_CLOCK_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def _numbers(text: str) -> set[str]:
    # A whole-hour clock time is the same number however it's written: «10:00», «10.00» and
    # «10» all count as 10 (P121 — «10:00» in a claim vs «10 bis 16 Uhr» in the quote).
    text = _CLOCK_RE.sub(lambda m: m.group(1) if m.group(2) == "00" else m.group(0), text or "")
    return {n.replace(",", ".") for n in _NUM_RE.findall(text)}


def _claim_gaps(claim: str, quote: str, sources_norm: str, allowed_norm: str) -> list[str]:
    """What the claim says that nothing backs: a number not in its own quote (or the task),
    or a name that occurs nowhere in the sources (or the task). Deterministic, no IO."""
    gaps = sorted(_numbers(claim) - _numbers(quote) - _numbers(allowed_norm))
    words = claim.split()
    for m in _NAME_RE.finditer(claim):
        if words and m.start() == 0:
            continue  # sentence-initial capital is not a name signal
        name = _norm(m.group(0))
        if name not in sources_norm and name not in allowed_norm:
            gaps.append(m.group(0))
    return gaps


_STEM_LEN = 5
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def _stems(text: str, min_len: int = 4) -> set[str]:
    """Crude, language-free word stems: lower-cased first five letters of each word. Good
    enough to see that «schody» (keyword) and «schodów» (source) are the same word."""
    return {w.lower()[:_STEM_LEN] for w in _WORDS.findall(text or "") if len(w) >= min_len}


def focus_stems(keywords: list[str], task: str, subject: str) -> set[str]:
    """What the task is about, as stems: Kåre's keywords (the 27B already did the language
    work), else words of ≥ 5 letters from the task/question. The subject's own words are left
    out — on the subject's pages every sentence names it (P121 D-P15)."""
    stems = set()
    for k in keywords:
        stems |= _stems(k)
    if not stems:
        stems = _stems(task, min_len=5)
    return stems - _stems(subject)


def _relevant_unquoted(claims: list[dict], per_source: list[dict], focus: set[str]) -> list[str]:
    """Input for IrrelevantQuoteDetector, judged per source: a source Miss Library quoted, where
    none of her quotes from it touches the focus but some of its sentences do. Those sentences
    come back (most focus stems first, at most 3). One good source can't hide a missed one."""
    if not focus:
        return []
    quotes_by_src: dict[int, list[str]] = {}
    for c in claims:
        if c.get("_verified") and c.get("_src") is not None:
            quotes_by_src.setdefault(c["_src"], []).append(str(c.get("quote", "")))
    scored = []
    for idx, quotes in quotes_by_src.items():
        if any(_stems(q, min_len=3) & focus for q in quotes):
            continue
        for sentence in _SENTENCE_SPLIT.split(per_source[idx].get("text", "")):
            sentence = sentence.strip()
            if 20 <= len(sentence) <= 400:
                hits = len(_stems(sentence, min_len=3) & focus)
                if hits:
                    scored.append((-hits, sentence))
    return [x for _, x in sorted(scored)[:3]]


def _locate_quote(quote: str, per_source: list[dict]) -> int | None:
    """Index of the one source whose own text holds the quote (P121 Phase A). Titles/URLs are
    not part of a source's text, so quoting a page title never verifies."""
    for i, src in enumerate(per_source):
        if _quote_in_sources(quote, src["_norm"]):
            return i
    return None


def _check_grounded(
    result: dict,
    sources_norm: str,
    allowed_norm: str = "",
    per_source: list[dict] | None = None,
    focus: set[str] | None = None,
    quote_only: bool = False,
) -> tuple[dict | None, dict]:
    """Parse + verify one answer → (parsed or None, meta for the detectors).
    With per_source, each quote must sit in ONE source's text; c["_src"] = that index, and
    names in the claim are checked against that source only — not the merged blob."""
    rmeta = result.get("meta") or {}
    meta = {
        "done_reason": rmeta.get("done_reason"),
        "think_truncated": bool(rmeta.get("think_truncated")),
    }
    try:
        parsed = json.loads(result.get("text") or "")
        claims = parsed.get("claims") or []
        assert isinstance(claims, list)
    except Exception:
        meta["parse_error"] = True
        return None, meta
    meta["reported_lang"] = str(parsed.get("lang") or "")
    bad: list[str] = []
    unsupported: list[tuple[str, list[str]]] = []
    for c in claims:
        quote = str(c.get("quote", ""))
        if per_source is not None:
            c["_src"] = _locate_quote(quote, per_source)
            c["_verified"] = c["_src"] is not None
            backing_norm = per_source[c["_src"]]["_norm"] if c["_verified"] else ""
        else:
            c["_verified"] = _quote_in_sources(quote, sources_norm)
            backing_norm = sources_norm
        if not c["_verified"]:
            bad.append(quote)
            continue
        if quote_only:
            continue  # the 9B's claim text is never shown — only the found quote counts
        # Numbers in the source's own title (set by code, e.g. «Straffeloven § 351 Skadeverk»)
        # may be named in the claim even when the quote is only the paragraph text (P123).
        title = per_source[c["_src"]].get("title", "") if per_source is not None else ""
        gaps = _claim_gaps(str(c.get("claim", "")), f"{quote} {title}", backing_norm, allowed_norm)
        if gaps:
            c["_verified"] = False
            c["_unsupported"] = True  # quote was found; the claim said more than it
            unsupported.append((str(c.get("claim", "")), gaps))
    meta["unverified_quotes"] = bad
    meta["unsupported_claims"] = unsupported
    if per_source is not None and focus:
        meta["relevant_unquoted"] = _relevant_unquoted(claims, per_source, focus)
    return parsed, meta


def _is_foreign(c: dict, lang: str) -> bool:
    return bool(c.get("quote_lang")) and norm_lang(str(c["quote_lang"])) != norm_lang(lang)


def _claim_view(c: dict, lang: str) -> str:
    """What may be shown/computed from a claim: the 9B's paraphrase, or — when the quote is in
    another language, or the source needs interpreting (law) — only the verbatim quote.
    Translation and interpretation are the 27B's job, never the 9B's."""
    quote_only = c.get("_quote_only") or _is_foreign(c, lang)
    return str(c.get("quote" if quote_only else "claim", "")).strip()


def _assess(
    parsed: dict, per_source: list[dict], match: dict[int, str], lang: str = "nb"
) -> list[dict]:
    """Group verified, on-subject claims by topic and compute how likely each side is (P121
    Phase 4, arithmetic in source_confidence). Sides inside a topic = claims with the same set of
    numbers; different numbers, or an explicit conflicts_with, make a conflict."""
    claims = parsed.get("claims") or []
    usable = [
        i
        for i, c in enumerate(claims)
        if c.get("_verified") and c.get("_src") is not None and match.get(c["_src"]) != "other"
    ]
    mode = source_confidence.caution()
    topics: dict[str, list[int]] = {}
    for i in usable:
        key = _norm(str(claims[i].get("topic") or _claim_view(claims[i], lang)))[:60]
        topics.setdefault(key, []).append(i)
    out = []
    for idxs in topics.values():
        sides: dict[frozenset, list[int]] = {}
        for i in idxs:
            sides.setdefault(frozenset(_numbers(_claim_view(claims[i], lang))), []).append(i)
        explicit = any(
            int(j) in idxs
            for i in idxs
            for j in (claims[i].get("conflicts_with") or [])
            if isinstance(j, int) or str(j).isdigit()
        )
        numbered = [k for k in sides if k]
        conflict = len(numbered) > 1 or (explicit and len(sides) > 1)
        if not conflict:
            # No disagreement: every claim on the topic backs the same thing («several hundred
            # steps» and «about 380 steps»), so all their sources count together.
            sides = {frozenset(): idxs}
        side_info = []
        for members in sides.values():
            supporters, seen = [], set()
            for i in members:
                src_idx = claims[i]["_src"]
                if src_idx in seen:
                    continue
                seen.add(src_idx)
                supporters.append(
                    {**per_source[src_idx], "_quote": str(claims[i].get("quote", ""))}
                )
            side_info.append(
                {
                    "claims": members,
                    "p": source_confidence.combine(supporters, mode),
                    "sources": [_source_label(s) for s in supporters],
                }
            )
        if conflict:
            for side in side_info:
                strongest = max((o["p"] for o in side_info if o is not side), default=0.0)
                side["p"] = source_confidence.weigh_conflict(side["p"], strongest)
        topic = (
            str(claims[idxs[0]].get("topic") or "").strip()
            or _claim_view(claims[idxs[0]], lang)[:60]
        )
        out.append({"topic": topic, "conflict": conflict, "sides": side_info})
    return out


def _render_assessment(
    groups: list[dict], parsed: dict, lang: str, stakes: str = "normal"
) -> list[str]:
    if not groups:
        return []
    claims = parsed.get("claims") or []

    def word(p: float) -> str:
        # Extraordinary claims stay unresolved however many pages assert them (P121 D6).
        return t(
            "prob_unresolved"
            if stakes == "extraordinary"
            else "prob_" + source_confidence.word_key(p),
            lang,
        )

    lines = [t("lib_assessment_header", lang)]
    for g in groups:
        if g["conflict"]:
            parts = [
                f"«{_claim_view(claims[s['claims'][0]], lang)}» = {word(s['p'])}"
                for s in g["sides"]
            ]
            lines.append(f"- {t('lib_conflict', lang)} {g['topic']}: " + " / ".join(parts))
        else:
            s = g["sides"][0]
            lines.append(
                f"- {g['topic']}: {word(s['p'])} ({t('lib_n_sources', lang, n=len(s['sources']))})"
            )
    return lines


def _source_label(src: dict, lang: str = "nb") -> str:
    if src.get("wiki"):
        return f"[wiki {src['wiki']}: {src['title']}]"
    host = (urlparse(src.get("url", "")).hostname or "").removeprefix("www.")
    label = f"{src.get('title', '')} — {host}" if host else src.get("title", "")
    if src.get("tier") in ("official", "trusted", "open"):
        label += " · " + t(f"lib_tier_{src['tier']}", lang)
    return f"[{label}]"


def _render_grounded(
    parsed: dict,
    lang: str,
    per_source: list[dict] | None = None,
    match: dict[int, str] | None = None,
    subject: str = "",
    stakes: str = "normal",
) -> str:
    verified = [c for c in parsed.get("claims") or [] if c.get("_verified")]
    removed = len(parsed.get("claims") or []) - len(verified)
    if parsed.get("not_found") and not verified:
        return t("lib_grounded_not_found", lang) + "\n" + t("lib_retry_hint", lang)
    match = match or {}
    off_subject = [c for c in verified if match.get(c.get("_src")) == "other"]
    # Translation is for the big model, never the 9B (Stian, 2026-10-03): a quote in another
    # language than the user's is passed on verbatim, without Miss Library's paraphrase.
    foreign = [c for c in verified if c.get("_quote_only") or _is_foreign(c, lang)]
    lines: list[str] = []
    # Miss Library's free-text summary may blend sources; leave it out when anything was
    # removed, comes from an article about something else (P121 Phase A), or needed translation.
    summary = (parsed.get("answer") or "").strip()
    if summary and verified:
        # The free summary is never quote-checked — keep it only if every number in it occurs in
        # a verified quote or its source's title (it once said «3 år» where § 352 says 6).
        backed = " ".join(
            f"{c.get('quote', '')} {per_source[c['_src']].get('title', '')}"
            if per_source is not None and c.get("_src") is not None
            else str(c.get("quote", ""))
            for c in verified
        )
        if _numbers(summary) - _numbers(backed):
            summary = ""
    # The summary states the matter, never who «says» it — «Kåre sier at …» passed source facts
    # off as the assistant's own words (P123 live run). The name comes from the GUI setting.
    assistant = str((_get_settings() or {}).get("assistant_name") or "Kåre").strip()
    if summary and assistant and re.search(rf"\b{re.escape(assistant)}\b", summary, re.IGNORECASE):
        summary = ""
    if not removed and not off_subject and not foreign and summary:
        lines.append(summary)
    if verified:
        lines.append(t("lib_grounded_evidence", lang))
        for c in verified:
            idx = c.get("_src")
            if per_source is not None and idx is not None:
                label = _source_label(per_source[idx], lang)
            else:
                label = c.get("source", "").strip()
            if c in foreign and c.get("_quote_only") and not _is_foreign(c, lang):
                line = f"- «{c.get('quote', '').strip()}» {label}"
            elif c in foreign:
                line = f"- «{c.get('quote', '').strip()}» {label} " + t(
                    "lib_quote_translate_yourself",
                    lang,
                    src_lang=str(c.get("quote_lang") or "?").lower(),
                )
            else:
                line = f"- {c.get('claim', '').strip()} — «{c.get('quote', '').strip()}» {label}"
            state = match.get(idx)
            if state == "other":
                line += " " + t(
                    "lib_claim_other_subject", lang, title=per_source[idx]["title"], subject=subject
                )
            elif state == "unmentioned":
                line += " " + t("lib_claim_unmentioned", lang, subject=subject)
            lines.append(line)
    if per_source is not None:
        groups = _assess(parsed, per_source, match, lang)
        lines += _render_assessment(groups, parsed, lang, stakes)
        if groups and stakes == "high":
            lines.append(t("lib_stakes_high", lang))
        elif groups and stakes == "extraordinary":
            lines.append(t("lib_stakes_extraordinary", lang))
        try:
            _trace(
                "assessment",
                {"subject": subject},
                "; ".join(
                    f"{g['topic']}: " + "/".join(f"{s['p']:.2f}" for s in g["sides"])
                    for g in groups
                ),
                time.perf_counter(),
            )
        except Exception:
            pass
    if off_subject:
        lines.append(t("lib_grounded_other_subject_note", lang, subject=subject))
    if per_source is not None and any(
        per_source[c["_src"]].get("tier") == "open" for c in verified if c.get("_src") is not None
    ):
        lines.append(t("lib_open_web_note", lang))
    n_unsupported = sum(1 for c in parsed.get("claims") or [] if c.get("_unsupported"))
    if removed - n_unsupported:
        lines.append(t("lib_grounded_removed", lang, n=removed - n_unsupported))
    if n_unsupported:
        lines.append(t("lib_grounded_removed_unsupported", lang, n=n_unsupported))
    # Thin result → tell Kåre what he can change; re-prompting is his call (P121 D11).
    if not verified or removed or off_subject:
        lines.append(t("lib_retry_hint", lang))
    return "\n".join(lines) if lines else t("lib_grounded_not_found", lang)


async def _subject_match(per_source: list[dict], subject: str, lang: str) -> dict[int, str]:
    """Per source index: "match" | "other" (wiki article with a different Wikidata QID) |
    "unmentioned" (web page naming none of the subject's labels/aliases). Missing index =
    unknown → nothing is marked. ID/string comparison only (P121 D-P14)."""
    ents = await wikidata.subject_entities(subject, lang)
    if not ents:
        return {}
    qids = {e["qid"] for e in ents}
    names = {n for n in (_norm(x) for x in ents[0]["names"]) if len(n) >= 4}
    titles_by_wiki: dict[str, list[str]] = {}
    for src in per_source:
        if src.get("wiki"):
            titles_by_wiki.setdefault(src["wiki"], []).append(src["title"])
    looked_up = await asyncio.gather(
        *(wikidata.article_qids(f"{wid}wiki", titles) for wid, titles in titles_by_wiki.items())
    )
    qid_of = dict(zip(titles_by_wiki, looked_up, strict=False))
    out: dict[int, str] = {}
    for i, src in enumerate(per_source):
        if src.get("tier") == "official":
            out[i] = "match"  # the subject's own website (Wikidata P856)
        elif src.get("wiki"):
            qid = qid_of.get(src["wiki"], {}).get(src["title"])
            if qid:
                out[i] = "match" if qid in qids else "other"
        elif names:
            hay = _norm(f"{src.get('title', '')} {src.get('text', '')}")
            out[i] = "match" if any(n in hay for n in names) else "unmentioned"
    return out


async def _grounded_answer(
    system: str,
    sources_text: str,
    user_msg: str,
    lang: str,
    task: str = "",
    sources: list[dict] | None = None,
    subject: str = "",
    keywords: list[str] | None = None,
    stakes: str = "normal",
    quote_only: bool = False,
) -> str:
    """Quote-first answer: JSON contract → verify quotes in code → at most one correction.
    sources ({title, text, wiki?, url?}) lets code trace each quote to one source and, with a
    subject, mark sources about something else (P121 Phase A). The Wikidata lookup runs while
    Miss Library thinks, so it adds no wait in the normal case."""
    _t0 = time.perf_counter()
    sources_norm = _norm(sources_text)
    allowed_norm = _norm(task)
    per_source = None
    if sources is not None:
        per_source = [{**src, "_norm": _norm(src.get("text", ""))} for src in sources]
    match_task = (
        asyncio.create_task(_subject_match(per_source, subject.strip(), lang))
        if per_source and subject.strip()
        else None
    )
    focus = focus_stems(keywords or [], task, subject)
    messages = [
        {"role": "system", "content": system + "\n\n" + t("lib_grounded_contract", lang)},
        {"role": "user", "content": user_msg},
    ]
    enforcer = ModelEnforcer(lang, rid=_current_rid.get(), detectors=grounded_detectors())
    parsed = None
    for _round in range(2):
        result = await _library_chat(messages, _GROUNDED_SCHEMA)
        if result.get("error"):
            return f"[Miss Library unavailable: {result['error']}]"
        parsed, meta = _check_grounded(
            result, sources_norm, allowed_norm, per_source, focus, quote_only
        )
        action = enforcer.check(text_out=result.get("text") or "", used_tools=[], meta=meta)
        if not action:
            break
        messages += [
            {"role": "assistant", "content": result.get("text") or ""},
            {"role": "user", "content": action.correction_message},
        ]
    match: dict[int, str] = {}
    if match_task is not None:
        try:
            match = await asyncio.wait_for(match_task, timeout=_SUBJECT_MATCH_GRACE_S)
        except Exception as e:
            print(f"[library] subject match skipped: {e!r}")
    n_claims = len((parsed or {}).get("claims") or [])
    n_ok = sum(1 for c in (parsed or {}).get("claims") or [] if c.get("_verified"))
    n_other = sum(
        1
        for c in (parsed or {}).get("claims") or []
        if c.get("_verified") and match.get(c.get("_src")) == "other"
    )
    _trace(
        "grounded_answer",
        {"retry": enforcer.retry_used, "subject": subject},
        (
            f"{n_ok}/{n_claims} quotes verified, {n_other} other-subject"
            if parsed
            else "invalid output"
        ),
        _t0,
    )
    if parsed is None:
        return t("lib_grounded_failed", lang)
    if quote_only:
        # The 9B found the sentences; interpreting them is the 27B's job (Stian, 2026-10-04).
        for c in parsed.get("claims") or []:
            c["_quote_only"] = True
        parsed["answer"] = ""
        return (
            _render_grounded(parsed, lang, per_source, match, subject.strip(), stakes)
            + "\n"
            + t("lib_interpret_yourself", lang)
        )
    return _render_grounded(parsed, lang, per_source, match, subject.strip(), stakes)


# ── Miss Library wiki search + synthesis ─────────────────────────────────────


def _inline_tables(wiki: dict, article: str, article_id, full_text: str, lang: str) -> str:
    """Resolve the markers of one article: small tables/lists (≤ auto_resolve_max_rows) are
    rendered inline, every row labelled with its table name; larger ones — or whatever no
    longer fits inline_max_chars — are listed by name and size so Kåre can fetch_table them.
    Deterministic: no LLM involved (P111 Phase 5)."""
    if not wiki["has_structured"] or article_id is None:
        return ""
    _t0 = time.perf_counter()
    markers = wiki_structured.parse_markers(full_text)
    if not markers:
        return ""
    try:
        rows = wiki_structured.fetch(wiki["structured_db"], article_id, [o for _, o in markers])
    except Exception as e:
        _trace("wiki_tables", {"article": article, "wiki": wiki["id"]}, f"error: {e}", _t0)
        return ""
    max_rows = wiki_registry.auto_resolve_max_rows()
    budget = wiki_registry.inline_max_chars()
    inline: list[str] = []
    listed: list[str] = []
    used = 0
    for label, ordinal in markers:
        row = rows.get(ordinal)
        if not row:
            continue
        name = wiki_structured.label_of(row, label)
        lines = (
            wiki_structured.render_lines(row, name) if (row.get("n_rows") or 0) <= max_rows else []
        )
        block = "\n".join(lines)
        if lines and used + len(block) <= budget:
            inline.append(block)
            used += len(block)
        else:
            listed.append(
                t("wiki_table_listed", lang, label=name, n=row.get("n_rows") or 0, article=article)
            )
    _trace(
        "wiki_tables",
        {"article": article, "wiki": wiki["id"]},
        f"{len(markers)} markers, {len(inline)} inline, {len(listed)} listed, {used} chars",
        _t0,
    )
    if not inline and not listed:
        return ""
    return t("wiki_tables_heading", lang, article=article) + "\n" + "\n".join(inline + listed)


def _best_title(chunks: list[dict]) -> str:
    """Article to read in full. `chunks` arrive in RRF order; each title scores
    sum(1 / (k + rank)) over its passages, so rank dominates and passage count only lifts a
    title that is also near the top (P111 Phase 6a). Counting passages first let long, loosely
    related articles win («Egg (mat)» over «Høna og egget»); the top passage alone is unstable,
    because RRF gives equal scores to equal dense/sparse ranks and their order is arbitrary.
    Earlier position breaks exact score ties."""
    k = wiki_registry.title_rank_k()
    score: dict[str, float] = {}
    first_rank: dict[str, int] = {}
    for rank, c in enumerate(chunks, start=1):
        score[c["title"]] = score.get(c["title"], 0.0) + 1.0 / (k + rank)
        first_rank.setdefault(c["title"], rank)
    return max(score, key=lambda title: (score[title], -first_rank[title]))


def _article_gist(text: str, max_chars: int = 90) -> str:
    """Short identifier for a chosen article (its opening words), so a mismatch between the
    search and the article is visible to Kåre and in the RID trace."""
    flat = " ".join(wiki_structured.strip_markers(text).split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[:max_chars]
    end = cut.rfind(". ")
    return cut[: end + 1] if end > max_chars // 2 else cut.rstrip() + "…"


async def _wiki_context(
    question: str, wiki: dict, dense: list[float], sparse: dict, lang: str
) -> tuple[str, str, list[dict]]:
    """One wiki's material for the task: best article (up to 4,000 chars) + one chunk from each
    other hit, under a header saying which wiki it is and what that wiki holds. Second value is
    the one-line search feedback for Kåre (what was searched → which article was chosen). Third
    value: the same material per article ({title, text, wiki}) so each quote can be traced to
    the one article it came from (P121 Phase A)."""
    _t0 = time.perf_counter()
    try:
        chunks = await _wiki_search(question, wiki["collection"], dense, sparse)
    except Exception as e:
        _trace(
            "wiki_search", {"query": question, "collection": wiki["collection"]}, f"error: {e}", _t0
        )
        return "", t("lib_search_line_none", lang, wiki=wiki["id"]), []
    if not chunks:
        return "", t("lib_search_line_none", lang, wiki=wiki["id"]), []
    best = _best_title(chunks)
    article = await _wiki_fetch_article_qdrant(
        best, max_chars=_FULL_ARTICLE_CHARS, collection=wiki["collection"]
    )
    line = t(
        "lib_search_line",
        lang,
        wiki=wiki["id"],
        title=best,
        gist=_article_gist(article["text"] or chunks[0]["text"]),
    )
    parts: list[str] = []
    per_article: list[dict] = []
    if article["text"]:
        prose = article["text"][:4000] + ("…" if len(article["text"]) > 4000 else "")
        body = wiki_structured.strip_markers(prose)
        parts.append(f"[{best}]\n{body}")
        tables = _inline_tables(wiki, best, article.get("article_id"), article["text"], lang)
        if tables:
            parts.append(tables)
            body += "\n" + tables
        per_article.append({"title": best, "text": body, "wiki": wiki["id"]})
    seen: set[str] = {best}
    for c in chunks:
        if c["title"] not in seen:
            body = wiki_structured.strip_markers(c["text"])
            parts.append(f"[{c['title']}]\n{body}")
            per_article.append({"title": c["title"], "text": body, "wiki": wiki["id"]})
            seen.add(c["title"])
    return wiki_registry.result_label(wiki, lang) + "\n" + "\n\n".join(parts), line, per_article


async def _ask_library_wiki(
    question: str,
    lang: str = "nb",
    only: list[str] | None = None,
    task: str = "",
    subject: str = "",
) -> str:
    """Wiki search + quote-first answer via Miss Library. All enabled wikis are searched in
    parallel, primary wiki first (P111 Phase 3); `only` narrows the set. `question` is what is
    searched for (embedded); `task` is Kåre's instruction for what to do with it (P111 5c)."""
    system = (
        _library_system(lang)
        + "\n\n"
        + t("wiki_available_line", lang, wikis=wiki_registry.capability_text(lang))
    )
    selected = (
        wiki_registry.wikis(only)
        if is_agent_tool_enabled("miss_library", "wiki", default=False)
        else []
    )

    sections: list[str] = []
    search_lines: list[str] = []
    per_article: list[dict] = []
    if selected:
        dense, sparse = await _embed_hybrid(question)
        results = await asyncio.gather(
            *(_wiki_context(question, w, dense, sparse, lang) for w in selected)
        )
        sections = [s for s, _, _ in results if s]
        search_lines = [line for _, line, _ in results]
        per_article = [a for _, _, arts in results for a in arts]
    # Kåre sees what was searched and which article each wiki chose — the mechanism made
    # visible, so a weak query shows as a mismatch instead of a silent wrong answer.
    feedback = (
        t("lib_searched_header", lang, query=question.strip())
        + "\n"
        + "\n".join(search_lines)
        + "\n\n"
        if search_lines
        else ""
    )

    if not sections:
        return feedback + t("lib_grounded_not_found", lang)
    wiki_ctx = "\n\n".join(sections)
    user_msg = t(
        "lib_wiki_excerpt_prefix", lang, wiki_ctx=wiki_ctx, question=(task or question).strip()
    )
    return feedback + await _grounded_answer(
        system,
        wiki_ctx,
        user_msg,
        lang,
        task=f"{question} {task}",
        sources=per_article,
        subject=subject,
    )


async def synthesize_web_results(
    question: str,
    sources: list[dict],
    lang: str = "nb",
    subject: str = "",
    keywords: list[str] | None = None,
    stakes: str = "normal",
    quote_only: bool = False,
) -> str:
    """Synthesize web search results via Miss Library. Called by web_search_adapter."""
    system = _library_system(lang)
    lines = []
    for i, s in enumerate(sources, 1):
        content = s.get("content", "").strip()
        if content:
            lines.append(f"[{i}] {s['title']} — {s['url']}\n{content}")
        else:
            lines.append(
                f"[{i}] {s['title']} — {s['url']}\n{t('lib_web_content_unavailable', lang)}"
            )
    if not lines:
        return t("lib_web_no_sources", lang)
    sources_text = "\n\n".join(lines)
    user_msg = t("lib_web_synthesis_prompt", lang, sources_text=sources_text, question=question)
    per_source = [
        {
            "title": s.get("title", ""),
            "url": s.get("url", ""),
            "text": s.get("content", ""),
            "tier": s.get("tier", ""),
            "type": s.get("type", ""),
            "trust": s.get("trust", ""),
        }
        for s in sources
        if (s.get("content") or "").strip()
    ]
    return await _grounded_answer(
        system,
        sources_text,
        user_msg,
        lang,
        task=question,
        sources=per_source,
        subject=subject,
        keywords=keywords,
        stakes=stakes,
        quote_only=quote_only,
    )


# ── Dispatcher functions (called by dispatch() below) ─────────────────────────


async def _ask_library(question: str, arguments: dict, lang: str = "nb") -> str:
    if not question.strip():
        return t("lib_empty_query", lang)
    if not _lib_cfg.get("enabled", True):
        return t("lib_library_disabled", lang)
    try:
        only, err = _selected_wikis(arguments, lang)
        if err:
            return err
        answer = await _ask_library_wiki(
            question,
            lang,
            only,
            str(arguments.get("task") or ""),
            subject=str(arguments.get("subject") or ""),
        )
        try:
            spawn(
                get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library",
                    query=question,
                    response=answer,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                )
            )
        except Exception:
            pass
        return answer if answer else t("lib_no_answer", lang)
    except Exception as e:
        return t("lib_unavailable", lang, error=e)


async def _ask_library_law(question: str, arguments: dict, lang: str = "nb") -> str:
    """Norwegian law from the local Lovdata index (P123): matching paragraphs become sources of
    a grounded answer — public authority (P121: 90 %), law is high stakes by definition (D6) —
    and every answer carries Lovdata's NLOD attribution."""
    if not question.strip():
        return t("lib_empty_query", lang)
    if not lovdata_adapter.enabled():
        return t("law_not_enabled", lang)
    keywords = _keyword_list(arguments.get("keywords"))
    hits = await asyncio.to_thread(lovdata_adapter.search, question, keywords)
    if not hits:
        return t("law_no_hits", lang) + "\n" + t("lib_retry_hint", lang)
    sources = [
        {
            "title": h["title"],
            "url": h["url"],
            "content": h["text"],
            "tier": "trusted",
            "type": "public_authority",
            "trust": "normal",
        }
        for h in hits
    ]
    find_task = f"{question}\n{t('lib_law_find_task', lang)}"
    answer = await synthesize_web_results(
        find_task, sources, lang, keywords=keywords, stakes="high", quote_only=True
    )
    return f"{answer}\n{lovdata_adapter.ATTRIBUTION}"


async def _ask_library_online(question: str, arguments: dict, lang: str = "nb") -> str:
    if not question.strip():
        return t("lib_empty_query", lang)
    if not _llm_cfg("cloud").get("enabled", True):
        return t("lib_online_disabled", lang)
    if not _lib_cfg.get("enabled", True):
        return t("lib_library_disabled", lang)
    try:
        system = _library_system(lang)
        prompt = t("lib_question_prefix", lang, system=system.strip(), question=question)
        result = await ask_llm_cloud(prompt)
        answer = t("lib_online_no_answer", lang) if not result.get("ok") else result["text"]
        try:
            spawn(
                get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library_online",
                    query=question,
                    response=answer,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                )
            )
        except Exception:
            pass
        return answer if answer else t("lib_online_no_answer", lang)
    except Exception as e:
        return t("lib_online_unavailable", lang, error=e)


_DEFAULT_ARTICLE_CHARS = 8000
# Whole article, read once to find every table/list marker (the prose itself is capped
# separately; the longest Norwegian articles are ~300 chunks × ~600 chars)
_FULL_ARTICLE_CHARS = 400_000


def _article_max_chars(requested) -> int:
    """The LLM sends max_chars as text as often as a number ("20000") — that raised
    "'>' not supported between 'int' and 'str'" (BUG-log 2026-09-21). Coerce, and clamp to
    settings.yaml context_budget.library_max_chars so one article can't swamp the context
    window (P105 Phase 7 C)."""
    try:
        value = int(float(requested))
    except (TypeError, ValueError):
        value = _DEFAULT_ARTICLE_CHARS
    try:
        ceiling = int((_get_settings().get("context_budget") or {}).get("library_max_chars", 20000))
    except (TypeError, ValueError):
        ceiling = 20000
    return max(500, min(value, ceiling))


def _selected_wikis(arguments: dict, lang: str) -> tuple[list[str] | None, str]:
    """The optional `wiki` argument → (ids or None for all, error text or "")."""
    only = wiki_registry.parse_wiki_arg(arguments.get("wiki"))
    if not only:
        return None, ""
    known = [w["id"] for w in wiki_registry.wikis()]
    unknown = [w for w in only if w not in known]
    if unknown:
        return None, t(
            "wiki_unknown", lang, wiki=", ".join(unknown), wikis=wiki_registry.capability_text(lang)
        )
    return only, ""


async def _fetch_wiki_article(
    title: str, max_chars=_DEFAULT_ARTICLE_CHARS, lang: str = "nb", only: list[str] | None = None
) -> str:
    """Exact-title fetch; tries the wikis in registry order (primary first) until one has it."""
    if not title.strip():
        return t("lib_empty_title", lang)
    try:
        for wiki in wiki_registry.wikis(only):
            data = await _wiki_fetch_article_qdrant(
                title, _article_max_chars(max_chars), wiki["collection"]
            )
            if data.get("text"):
                label = wiki_registry.result_label(wiki, lang)
                text = wiki_structured.strip_markers(data["text"])
                tables = _tables_index(wiki, data, lang)
                return f"# {data['title']} ({data['chunk_count']} biter) {label}\n\n{text}" + (
                    f"\n\n{tables}" if tables else ""
                )
        return t("lib_article_not_found", lang, title=title)
    except Exception as e:
        return t("lib_article_error", lang, error=e)


def _tables_index(wiki: dict, data: dict, lang: str) -> str:
    """One line naming the tables/lists behind the markers in this text, so Kåre knows what
    fetch_table can give him without ever seeing the marker format."""
    if not wiki["has_structured"] or data.get("article_id") is None:
        return ""
    markers = wiki_structured.parse_markers(data.get("text", ""))
    if not markers:
        return ""
    try:
        rows = wiki_structured.fetch(
            wiki["structured_db"], data["article_id"], [o for _, o in markers]
        )
    except Exception:
        return ""
    names = [
        f"«{wiki_structured.label_of(rows[o], lab)}» ({rows[o].get('n_rows') or 0})"
        for lab, o in markers
        if o in rows
    ]
    if not names:
        return ""
    return t(
        "wiki_tables_available",
        lang,
        article=data["title"],
        wiki=wiki_registry.result_label(wiki, lang),
        labels=", ".join(names),
    )


async def _find_article(title: str, only: list[str] | None) -> tuple[dict | None, dict]:
    """First wiki (registry order) that has the article → (wiki, article data incl. full text)."""
    for wiki in wiki_registry.wikis(only):
        data = await _wiki_fetch_article_qdrant(title, _FULL_ARTICLE_CHARS, wiki["collection"])
        if data.get("text"):
            return wiki, data
    return None, {}


def _match_marker(markers: list[tuple[str, int]], wanted: str) -> tuple[str, int] | None:
    w = wanted.strip().lower().lstrip("#")
    for label, ordinal in markers:
        if label.lower() == w or str(ordinal) == w:
            return label, ordinal
    for label, ordinal in markers:
        if w and w in label.lower():
            return label, ordinal
    return None


async def _fetch_table(arguments: dict, lang: str = "nb") -> str:
    """library(action='fetch_table'): one named table/list of an article, every row labelled.
    No label → list what the article has. mode='translated' → 9B translates words, keeps values."""
    title = (arguments.get("title") or "").strip()
    if not title:
        return t("lib_empty_title", lang)
    only, err = _selected_wikis(arguments, lang)
    if err:
        return err
    wiki, data = await _find_article(title, only)
    if not wiki:
        return t("lib_article_not_found", lang, title=title)
    label_w = wiki_registry.result_label(wiki, lang)
    if not wiki["has_structured"]:
        return t("wiki_no_structured", lang, wiki=wiki["id"])
    markers = wiki_structured.parse_markers(data["text"])
    try:
        rows = wiki_structured.fetch(
            wiki["structured_db"], data["article_id"], [o for _, o in markers]
        )
    except Exception as e:
        return t("lib_article_error", lang, error=e)
    available = [(lab, o) for lab, o in markers if o in rows]
    names = ", ".join(
        f"«{wiki_structured.label_of(rows[o], lab)}» ({rows[o].get('n_rows') or 0})"
        for lab, o in available
    )
    if not available:
        return t("wiki_article_has_no_tables", lang, article=data["title"], wiki=label_w)
    wanted = (arguments.get("label") or "").strip()
    if not wanted:
        return t("wiki_tables_available", lang, article=data["title"], wiki=label_w, labels=names)
    hit = _match_marker(available, wanted)
    if not hit:
        return t("wiki_table_not_found", lang, label=wanted, article=data["title"], labels=names)
    row = rows[hit[1]]
    name = wiki_structured.label_of(row, hit[0])
    section = wiki_structured.section_text(row)
    header = t(
        "wiki_table_header",
        lang,
        label=name,
        section=f" ({section})" if section else "",
        article=data["title"],
        wiki=label_w,
        n=row.get("n_rows") or 0,
    )
    lines = wiki_structured.render_lines(row, name)
    limit = _article_max_chars(arguments.get("max_chars", _DEFAULT_ARTICLE_CHARS))
    shown: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) > limit:
            break
        shown.append(line)
        used += len(line) + 1
    body = "\n".join(shown)
    if len(shown) < len(lines):
        body += "\n" + t("wiki_table_truncated", lang, more=len(lines) - len(shown), max=limit)
    _trace(
        "wiki_fetch_table",
        {"title": data["title"], "label": name, "wiki": wiki["id"]},
        f"{len(shown)}/{len(lines)} rows",
        time.perf_counter(),
    )
    if (arguments.get("mode") or "raw").lower() == "translated":
        translated = await _library_llm_call(t("lib_translate_table_system", lang), body)
        if translated and not translated.startswith("[Miss Library unavailable"):
            body = translated
    return f"{header}\n{body}"


async def _fetch_url(url: str, arguments: dict, lang: str = "nb") -> str:
    """Fetch one page Kåre names. Same safe fetcher and trusted list as web search, in both
    modes (Miss Library on: she summarises; off: the page text goes back directly)."""
    if not url.strip():
        return t("lib_empty_url", lang)
    if web_fetch.trust_list_state() == "unreadable":
        return t("web_trust_list_unreadable", lang)
    # P121 D-P12: with open web on, any public page that isn't blocked may be read — labelled
    # by tier. With it off, only whitelisted pages, as before.
    tier = "trusted" if web_fetch.listed_entry(url) else "open"
    if web_fetch.is_blocked(url) or (
        tier == "open" and not web_fetch.open_web_enabled() and web_fetch.trust_list_state() == "ok"
    ):
        return f"{t('lib_url_not_trusted', lang, url=url)}\n{t('svc_trusted_hint', lang)}"
    allow = web_fetch.is_trusted if tier == "trusted" else web_fetch.not_blocked
    source_line = f"{t('lib_fetch_source', lang)}: {web_fetch.normalize_domain(url)} · {t('lib_tier_' + tier, lang)}"
    try:
        _final_url, text = await web_fetch.fetch_page_text(url, allow=allow)
    except web_fetch.FetchError as e:
        print(f"[library] fetch_url refused/failed ({url}): {e.reason}")
        return t("svc_url_fetch_failed", lang, url=url)
    except Exception as e:
        return t("lib_url_error", lang, error=e)
    if len(text) > 6000:
        text = text[:6000] + "…"
    if not text:
        return (
            t("svc_url_fetch_failed", lang, url=url)
            if _lib_cfg.get("enabled", True)
            else t("lib_url_direct_no_content", lang)
        )
    if not _lib_cfg.get("enabled", True):
        return f"{source_line}\n{text}"
    try:
        system = _library_system(lang)
        user_msg = t("lib_url_summarize_prompt", lang, url=url, text=text)
        answer = await _library_llm_call(system, user_msg)
        try:
            spawn(
                get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library",
                    query=url,
                    response=answer,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                )
            )
        except Exception:
            pass
        return f"{source_line}\n{answer}" if answer else t("lib_url_no_answer", lang)
    except Exception as e:
        return t("lib_url_error", lang, error=e)


async def _reason_freely(query: str, lang: str = "nb") -> str:
    if not query.strip():
        return t("lib_empty_rf_query", lang)
    rf_cfg = _llm_cfg("reason_freely")
    system = PERSONALITY_CORE_TEXT + "\n\n---\n\n" + t("lib_reason_freely_system", lang)
    try:
        base_url = rf_cfg.get("base_url", _OLLAMA_PROXY_URL)
        options = rf_cfg.get("options", {})
        provider = rf_cfg.get("provider", "ollama")

        if provider == "vllm":
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ]
            payload: dict = {
                "model": _KARE_MODEL,
                "messages": messages,
                "stream": False,
            }
            for src, dst in (
                ("max_tokens", "max_tokens"),
                ("temperature", "temperature"),
                ("top_p", "top_p"),
                ("presence_penalty", "presence_penalty"),
            ):
                if src in options:
                    payload[dst] = options[src]
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{base_url}/v1/chat/completions",
                    json=payload,
                    headers={"x-kaare-source": "reason_freely"},
                )
                r.raise_for_status()
                data = r.json()
            msg = (data.get("choices") or [{}])[0].get("message", {})
            think_text = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
            text = (msg.get("content") or "").strip()
            think_closed = True
            # Defensive check: vLLM's own reasoning-parser is trusted to split cleanly,
            # but if it ever trips on a truncated block, a literal <think> tag could
            # still land in `content` (same class of bug as P88, llm_adapter.py).
            if "<think>" in text.lower():
                text, _extra_think, think_closed = strip_think_block(text)
                think_text = (
                    (think_text + "\n\n" + _extra_think).strip() if think_text else _extra_think
                )
        else:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{base_url}/api/generate",
                    json={
                        "model": _KARE_MODEL,
                        "prompt": query,
                        "system": system,
                        "stream": False,
                        "think": rf_cfg.get("think", True),
                        "options": options,
                    },
                    headers={"x-kaare-source": "reason_freely"},
                )
                r.raise_for_status()
                data = r.json()
            think_text = (data.get("thinking") or "").strip()
            text = (data.get("response") or "").strip()
            think_closed = True

            if not think_text and "<think>" in text.lower():
                text, think_text, think_closed = strip_think_block(text)

        recovered = bool(think_closed and not text and think_text)

        if think_text:
            try:
                log_think(
                    think_text=think_text,
                    response=text,
                    role="reason_freely",
                    model=_KARE_MODEL,
                    prompt_preview=query[:200],
                    recovered=recovered,
                )
            except Exception:
                pass

        # Never fabricate a conclusion from a truncated (unclosed) think fragment —
        # only a legitimately closed-but-empty think block is a real recovery candidate.
        if recovered:
            text = extract_conclusion(think_text)

        return text if text else t("lib_empty_model_answer", lang)
    except Exception as e:
        return t("lib_rf_error", lang, error=e)


# ── Tool dispatcher ───────────────────────────────────────────────────────────


def _stakes(value) -> str:
    """`stakes` from the model; still missing after the one correction → "normal"."""
    v = str(value or "").strip().lower()
    return v if v in STAKES else "normal"


def _keyword_list(value) -> list[str]:
    """`keywords` from the model: a list of strings, or one comma-separated string."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:12]


async def dispatch(name: str, arguments: dict) -> str:
    # Bind the request's rid for everything below (her internal steps and her own LLM
    # call, incl. web synthesis reached via web_search_adapter) — P111 Phase 0.
    rid_token = _current_rid.set(arguments.get("_rid", "") or _current_rid.get())
    try:
        if work_round.wants_background(name, arguments):
            return work_round.start(name, arguments, _dispatch)  # P122: answer now, result later
        return await _dispatch(name, arguments)
    finally:
        _current_rid.reset(rid_token)


async def _dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in ("web_search", "søk_nett"):
        raw_query = arguments.get("query", "")
        near_home = parse_near_home(arguments.get("near_home"))
        if near_home is None:
            # Still missing after the one correction round (or a caller outside the router):
            # default to no home place — over-locking to home was the bug this replaced.
            print(
                f"[web_search] near_home missing/invalid ({arguments.get('near_home')!r}) — using false"
            )
            near_home = False
        query = build_search_query(raw_query, near_home)
        print(f"[web_search] near_home={near_home} query={query!r}")
        deadline = float(arguments.get("_deadline") or web_fetch.setting("search_total_timeout_s"))
        try:
            response = await asyncio.wait_for(
                _søk_nett(
                    query,
                    user_id=arguments.get("_user_id", "global"),
                    subject=str(arguments.get("subject") or ""),
                    keywords=_keyword_list(arguments.get("keywords")),
                    stakes=_stakes(arguments.get("stakes")),
                ),
                timeout=deadline,
            )
        except TimeoutError:
            print(f"[web_search] stopped after {deadline:.0f}s (search_total_timeout_s)")
            response = (
                t("web_search_timeout", lang, s=int(deadline)) + "\n" + t("lib_retry_hint", lang)
            )
        try:
            spawn(
                get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library",
                    query=query,
                    response=response,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                )
            )
        except Exception:
            pass
        return response

    if name == "library":
        action = arguments.get("action", "")
        if action == "search":
            return await _ask_library(arguments.get("query", ""), arguments, lang)
        if action == "fetch_article":
            only, err = _selected_wikis(arguments, lang)
            if err:
                return err
            return await _fetch_wiki_article(
                arguments.get("title", ""),
                arguments.get("max_chars", 8000),
                lang,
                only,
            )
        if action == "fetch_table":
            return await _fetch_table(arguments, lang)
        if action == "fetch_url":
            deadline = float(
                arguments.get("_deadline") or web_fetch.setting("search_total_timeout_s")
            )
            try:
                return await asyncio.wait_for(
                    _fetch_url(arguments.get("url", ""), arguments, lang), timeout=deadline
                )
            except TimeoutError:
                return t("web_search_timeout", lang, s=int(deadline))
        if action == "online":
            return await _ask_library_online(arguments.get("query", ""), arguments, lang)
        if action == "law":
            return await _ask_library_law(arguments.get("query", ""), arguments, lang)
        return f"Unknown action for library: '{action}'. Valid: search, fetch_article, fetch_table, fetch_url, online."

    if name == "spør_miss_library_online":
        return await _ask_library_online(arguments.get("query", ""), arguments, lang)

    if name == "spør_miss_library":
        return await _ask_library(arguments.get("query", ""), arguments, lang)

    if name == "hent_wiki_artikkel":
        return await _fetch_wiki_article(
            arguments.get("title", ""),
            arguments.get("max_chars", 8000),
            lang,
        )

    if name == "reason_freely":
        return await _reason_freely(arguments.get("query", ""), lang)

    return f"[executor_library] Unknown tool: {name}"
