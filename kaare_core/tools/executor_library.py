import asyncio
import re
import yaml
from pathlib import Path
from urllib.parse import urlparse

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, Fusion, FusionQuery, MatchValue,
    Prefetch, SparseVector as QSparseVector,
)

from adapters.web_search_adapter import søk_nett as _søk_nett, _fetch_content as _direct_fetch, _is_trusted as _url_trusted
from kaare_core.config import get_service as _svc, get_model as _cfg_model, get_llm_config as _llm_cfg
from kaare_core.memory.long_term import get_ltm
from kaare_core.tools.executor_personality import PERSONALITY_CORE_TEXT
from kaare_core.tools.i18n import t, get_lang
from kaare_core.tools.think_cache import read_think_history, format_for_kare, log_think, extract_conclusion

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_TRUSTED_PATH  = Path("/kaare/configs/trusted_sources.yaml")
_KARE_MODEL    = _cfg_model("kare")
_OLLAMA_PROXY_URL = _llm_cfg("default")["base_url"]

# ── Miss Library LLM config ───────────────────────────────────────────────────

_lib_cfg         = _llm_cfg("library")
_LIBRARY_LLM_URL = _lib_cfg["base_url"] + "/api/chat"
_LIBRARY_MODEL   = _cfg_model("library")
_LIBRARY_TIMEOUT = _lib_cfg.get("timeout", 120.0)
_LIB_OPTIONS     = _lib_cfg.get("options", {"temperature": 0.3, "num_ctx": 8192, "num_predict": 1024})

# Serialise Miss Library LLM calls — one at a time, no separate process needed.
# Replaces the asyncio.Queue in the old agents-server.
_LIBRARY_SEM = asyncio.Semaphore(1)

# ── Qdrant + BGE-M3 config ────────────────────────────────────────────────────

_QDRANT_URL      = _svc("storage", "qdrant")
_WIKI_COLL       = "wiki_no"
_WIKI_TOP_K      = 8
_EMBED_HYBRID_URL = _svc("ollama", "embed") + "/api/embed/hybrid"
_EMBED_MODEL     = _cfg_model("embed")

_qdrant = QdrantClient(url=_QDRANT_URL)

# ── Miss Library personality ──────────────────────────────────────────────────

_LIBRARY_PERSONALITY_PATH = Path(__file__).parent.parent / "agents" / "miss_library" / "personlighet.md"


def _load_library_personality() -> str:
    if _LIBRARY_PERSONALITY_PATH.exists():
        return _LIBRARY_PERSONALITY_PATH.read_text(encoding="utf-8")
    return "Du er Miss Library – en støvet bibliotekar med dyp kjærlighet for fakta."


LIBRARY_TOOLS = {
    "søk_nett",
    "library",
    "spør_miss_library_online",
    "spør_miss_library",
    "hent_wiki_artikkel",
    "reason_freely",
}


# ── Location context ──────────────────────────────────────────────────────────

def _build_location_prefix() -> str:
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        loc = s.get("location") or s.get("lokasjon", {})
    except Exception:
        return ""
    city = loc.get("city") or loc.get("sted", "")
    country = loc.get("country") or loc.get("land", "")
    if city and country:
        return t("lib_context_city_country", "nb", city=city, country=country)
    if country:
        return t("lib_context_country", "nb", country=country)
    return ""

_LOCATION_PREFIX = _build_location_prefix()


def location_prefix() -> str:
    return _LOCATION_PREFIX


# ── BGE-M3 hybrid embedding ───────────────────────────────────────────────────

async def _embed_hybrid(text: str) -> tuple[list[float], dict]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(_EMBED_HYBRID_URL, json={"model": _EMBED_MODEL, "input": text})
        r.raise_for_status()
        data = r.json()
        return data["dense"][0], data["sparse"][0]


# ── Qdrant wiki search ────────────────────────────────────────────────────────

async def _wiki_search(query: str) -> list[dict]:
    dense, sparse = await _embed_hybrid(query)
    sparse_vec = QSparseVector(indices=sparse["indices"], values=sparse["values"])
    hits = await asyncio.to_thread(
        _qdrant.query_points,
        collection_name=_WIKI_COLL,
        prefetch=[
            Prefetch(query=dense,      using="dense",  limit=_WIKI_TOP_K * 4),
            Prefetch(query=sparse_vec, using="sparse", limit=_WIKI_TOP_K * 4),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=_WIKI_TOP_K,
        with_payload=True,
    )
    return [
        {"title": h.payload.get("title", ""), "text": h.payload.get("text", "")}
        for h in hits.points
    ]


async def _wiki_fetch_article_qdrant(title: str, max_chars: int = 8000) -> dict:
    scroll_filter = Filter(must=[FieldCondition(key="title", match=MatchValue(value=title))])
    points, _ = await asyncio.to_thread(
        _qdrant.scroll,
        collection_name=_WIKI_COLL,
        scroll_filter=scroll_filter,
        limit=300,
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return {"title": title, "text": "", "chunk_count": 0}
    points.sort(key=lambda p: p.payload.get("chunk_index", p.id))
    full_text = "\n\n".join(p.payload.get("text", "") for p in points)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "…"
    return {"title": title, "text": full_text, "chunk_count": len(points)}


# ── Miss Library LLM call (serialised via Semaphore) ─────────────────────────

async def _library_llm_call(system: str, user: str) -> str:
    payload = {
        "model": _LIBRARY_MODEL,
        "stream": _lib_cfg.get("stream", False),
        "options": _LIB_OPTIONS,
        "messages": [
            {"role": "system", "content": f"/no_think\n{system}"},
            {"role": "user",   "content": user},
        ],
    }
    if "think" in _lib_cfg:
        payload["think"] = _lib_cfg["think"]
    try:
        async with _LIBRARY_SEM:
            async with httpx.AsyncClient(timeout=_LIBRARY_TIMEOUT) as client:
                r = await client.post(_LIBRARY_LLM_URL, json=payload)
                r.raise_for_status()
                return r.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"[Miss Library unavailable: {e}]"


# ── Trusted domain check (mirrors agents-server logic) ────────────────────────

def _load_trusted_domains() -> list[str]:
    try:
        data = yaml.safe_load(_TRUSTED_PATH.read_text(encoding="utf-8")) or {}
        trusted = []
        for category in data.get("sources", {}).values():
            for entry in category:
                d = entry.get("domain", "").lower().lstrip("www.")
                if d and "/" not in d:
                    trusted.append(d)
        return trusted
    except Exception:
        return []

_TRUSTED_DOMAINS: list[str] = _load_trusted_domains()


def _is_domain_trusted(url: str) -> bool:
    if not _TRUSTED_DOMAINS:
        return True
    try:
        host = (urlparse(url).hostname or "").lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in _TRUSTED_DOMAINS)
    except Exception:
        return False


# ── Miss Library wiki search + synthesis ─────────────────────────────────────

async def _ask_library_wiki(question: str, lang: str = "nb") -> str:
    """Wiki search + LLM synthesis via Miss Library. Replaces /ask/miss_library."""
    from kaare_core.config import is_agent_tool_enabled
    system = _load_library_personality()
    chunks = await _wiki_search(question) if is_agent_tool_enabled("miss_library", "wiki", default=False) else []

    if chunks:
        title_freq: dict[str, int] = {}
        for c in chunks:
            title_freq[c["title"]] = title_freq.get(c["title"], 0) + 1
        query_words = set(question.lower().split())

        def _article_score(title: str) -> tuple[int, int]:
            freq = title_freq[title]
            kw = sum(1 for w in query_words if len(w) > 3 and w in title.lower())
            return (freq, kw)

        best_title = max(title_freq, key=_article_score)
        best_article = await _wiki_fetch_article_qdrant(best_title, max_chars=4000)

        ctx_parts: list[str] = []
        if best_article["text"]:
            ctx_parts.append(f"[{best_title}]\n{best_article['text']}")

        seen: set[str] = {best_title}
        for c in chunks:
            if c["title"] not in seen:
                ctx_parts.append(f"[{c['title']}]\n{c['text']}")
                seen.add(c["title"])

        wiki_ctx = "\n\n".join(ctx_parts)
        user_msg = f"Wiki-utdrag:\n{wiki_ctx}\n\nSpørsmål: {question}"
    else:
        user_msg = f"Spørsmål: {question}\n\n(Ingen wiki-utdrag funnet.)"

    return await _library_llm_call(system, user_msg)


async def synthesize_web_results(question: str, sources: list[dict], lang: str = "nb") -> str:
    """Synthesize web search results via Miss Library. Called by web_search_adapter."""
    system = _load_library_personality()
    lines = []
    for i, s in enumerate(sources, 1):
        content = s.get("content", "").strip()
        if content:
            lines.append(f"[{i}] {s['title']} — {s['url']}\n{content}")
        else:
            lines.append(f"[{i}] {s['title']} — {s['url']}\n(Content unavailable.)")
    sources_text = "\n\n".join(lines) if lines else "(No sources retrieved.)"
    user_msg = (
        f"Web sources:\n{sources_text}\n\n"
        f"Question: {question}\n\n"
        "Answer ONLY from the sources above. If not found, say so and include the URLs."
    )
    return await _library_llm_call(system, user_msg)


# ── Dispatcher functions (called by dispatch() below) ─────────────────────────

async def _ask_library(question: str, arguments: dict, lang: str = "nb") -> str:
    if not question.strip():
        return t("lib_empty_query", lang)
    if not _lib_cfg.get("enabled", True):
        return t("lib_library_disabled", lang)
    loc = location_prefix()
    question_with_context = f"{loc}{question}" if loc else question
    try:
        answer = await _ask_library_wiki(question_with_context, lang)
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library",
                query=question,
                response=answer,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return answer if answer else t("lib_no_answer", lang)
    except Exception as e:
        return t("lib_unavailable", lang, error=e)


async def _ask_library_online(question: str, arguments: dict, lang: str = "nb") -> str:
    if not question.strip():
        return t("lib_empty_query", lang)
    if not _llm_cfg("cloud").get("enabled", True):
        return t("lib_online_disabled", lang)
    if not _lib_cfg.get("enabled", True):
        return t("lib_library_disabled", lang)
    try:
        system = _load_library_personality()
        from adapters.llm_adapter import ask_llm_cloud
        prompt = f"{system.strip()}\n\nSpørsmål: {question}"
        result = await ask_llm_cloud(prompt)
        if not result.get("ok"):
            answer = t("lib_online_no_answer", lang)
        else:
            answer = result["text"]
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library_online",
                query=question,
                response=answer,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return answer if answer else t("lib_online_no_answer", lang)
    except Exception as e:
        return t("lib_online_unavailable", lang, error=e)


async def _fetch_wiki_article(title: str, max_chars: int = 8000, lang: str = "nb") -> str:
    if not title.strip():
        return t("lib_empty_title", lang)
    try:
        data = await _wiki_fetch_article_qdrant(title, max_chars)
        if not data.get("text"):
            return t("lib_article_not_found", lang, title=title)
        return f"# {data['title']} ({data['chunk_count']} biter)\n\n{data['text']}"
    except Exception as e:
        return t("lib_article_error", lang, error=e)


async def _fetch_url(url: str, arguments: dict, lang: str = "nb") -> str:
    if not url.strip():
        return t("lib_empty_url", lang)
    if not _lib_cfg.get("enabled", True):
        if not _url_trusted(url):
            return t("lib_url_not_trusted", lang, url=url)
        content = await _direct_fetch(url)
        if not content:
            return t("lib_url_direct_no_content", lang)
        return content
    try:
        if _TRUSTED_DOMAINS:
            if not _is_domain_trusted(url):
                parsed_host = (urlparse(url).hostname or "").lower().lstrip("www.")
                return (
                    t("svc_empty_url", lang)
                    if not url else
                    f"{t('svc_url_fetch_failed', lang, url=url)}\n{t('svc_trusted_hint', lang)}"
                )
        import trafilatura
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Kaare/1.0)"},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return t("svc_url_fetch_failed", lang, url=url)
            text = (trafilatura.extract(r.text, include_comments=False, include_tables=True) or "").strip()
        if len(text) > 6000:
            text = text[:6000] + "…"
        if not text:
            return t("svc_url_fetch_failed", lang, url=url)

        system = _load_library_personality()
        user_msg = (
            f"Source: {url}\n\n{text}\n\n"
            "Summarize the content clearly and helpfully. "
            "If a specific question was asked, answer it based on the source."
        )
        answer = await _library_llm_call(system, user_msg)
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library",
                query=url,
                response=answer,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return answer if answer else t("lib_url_no_answer", lang)
    except Exception as e:
        return t("lib_url_error", lang, error=e)


async def _reason_freely(query: str, lang: str = "nb") -> str:
    if not query.strip():
        return t("lib_empty_rf_query", lang)
    rf_cfg = _llm_cfg("reason_freely")
    system = (
        PERSONALITY_CORE_TEXT
        + "\n\n---\n\n"
        + t("lib_reason_freely_system", lang)
    )
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
            for src, dst in (("max_tokens", "max_tokens"), ("temperature", "temperature"),
                             ("top_p", "top_p"), ("presence_penalty", "presence_penalty")):
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

            if not think_text and "<think>" in text.lower():
                upper = text.upper()
                t_start = upper.find("<THINK>")
                t_end = upper.find("</THINK>")
                if t_start != -1 and t_end != -1:
                    think_text = text[t_start + len("<THINK>"):t_end].strip()
                    text = text[t_end + len("</THINK>"):].strip()

        if think_text:
            try:
                log_think(
                    think_text=think_text,
                    response=text,
                    role="reason_freely",
                    model=_KARE_MODEL,
                    prompt_preview=query[:200],
                    recovered=not bool(text),
                )
            except Exception:
                pass

        if not text and think_text:
            text = extract_conclusion(think_text)

        return text if text else t("lib_empty_model_answer", lang)
    except Exception as e:
        return t("lib_rf_error", lang, error=e)


# ── Tool dispatcher ───────────────────────────────────────────────────────────

async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "søk_nett":
        raw_query = arguments.get("query", "")
        loc = location_prefix()
        query = f"{loc}{raw_query}" if loc else raw_query
        response = await _søk_nett(query)
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library",
                query=query,
                response=response,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return response

    if name == "library":
        action = arguments.get("action", "")
        if action == "search":
            return await _ask_library(arguments.get("query", ""), arguments, lang)
        if action == "fetch_article":
            return await _fetch_wiki_article(
                arguments.get("title", ""),
                arguments.get("max_chars", 8000),
                lang,
            )
        if action == "fetch_url":
            return await _fetch_url(arguments.get("url", ""), arguments, lang)
        if action == "online":
            return await _ask_library_online(arguments.get("query", ""), arguments, lang)
        return f"Unknown action for library: '{action}'. Valid: search, fetch_article, fetch_url, online."

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
