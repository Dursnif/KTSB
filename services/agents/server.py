"""
Kåre agent-server – Miss.Library og Mechanic
Port 11450.

Miss Library: queue-based LLM + Qdrant wiki search (uses library model, port 11447)
Mechanic:  tool-using agent (uses miss_kare model, port 11445 — shared with Miss Kåre)
"""

import asyncio
import logging
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition, Filter, Fusion, FusionQuery, MatchValue,
    Prefetch, SparseVector as QSparseVector,
)

sys.path.insert(0, "/kaare")
from kaare_core.tools.i18n import t, get_lang
from kaare_core.agents.mechanic.tools import (
    ask_with_tools, MECHANIC_URL, MECHANIC_MODEL,
    MECHANIC_TOOLS, UNDERSØKER_TOOLS, KRITIKER_TOOLS, ANALYTIKER_TOOLS,
    MEMORY_PATH as MECHANIC_MEMORY_PATH,
)
from kaare_core.config import get_model as _cfg_model, get_llm_config as _llm, is_agent_tool_enabled
from kaare_core.llm_fallback import is_fallback_active
from adapters.llm_adapter import ask_llm_cloud

# ── Konfig ───────────────────────────────────────────────────────────────────

from kaare_core.config import get_service as _svc

_lib_cfg    = _llm("library")
OLLAMA_URL  = _lib_cfg["base_url"] + "/api/chat"
AGENT_MODEL = _cfg_model("library")
EMBED_URL        = _svc("ollama", "embed") + "/api/embed"
EMBED_HYBRID_URL = _svc("ollama", "embed") + "/api/embed/hybrid"
EMBED_MODEL = _cfg_model("embed")
QDRANT_URL  = _svc("storage", "qdrant")
WIKI_COLL   = "wiki_no"
WIKI_TOP_K  = 8
TIMEOUT     = _lib_cfg["timeout"]

AGENTS_DIR   = Path(__file__).parent.parent.parent / "kaare_core" / "agents"

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("agents")

# ── Kø og app ────────────────────────────────────────────────────────────────

_queue: asyncio.Queue = asyncio.Queue()
app = FastAPI(title="Kåre agents", version="2.0")

# ── Mechanic job store (in-memory, ephemeral) ──────────────────────────────
# job_id → {"status": "running"|"done"|"error", "result": str|None, "created_at": float}
_JOB_TTL = 1800  # 30 minutes
_jobs: dict[str, dict] = {}

# ── Personligheter ───────────────────────────────────────────────────────────

def _load_personality(agent: str, role: str = "standard") -> str:
    if role and role != "standard":
        path = AGENTS_DIR / agent / f"personlighet_{role}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
    path = AGENTS_DIR / agent / "personlighet.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Du er {agent}."


def _load_mechanic_memory() -> str:
    try:
        content = MECHANIC_MEMORY_PATH.read_text(encoding="utf-8").strip()
        return content if content else ""
    except Exception:
        return ""


def _tools_for_role(role: str) -> list:
    return {
        "undersøker": UNDERSØKER_TOOLS,
        "kritiker":   KRITIKER_TOOLS,
        "analytiker": ANALYTIKER_TOOLS,
    }.get(role, MECHANIC_TOOLS)

# ── Qdrant / embedding ───────────────────────────────────────────────────────

_qdrant = QdrantClient(url=QDRANT_URL)

async def _embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "input": text})
        r.raise_for_status()
        return r.json()["embeddings"][0]


async def _embed_hybrid(text: str) -> tuple[list[float], dict]:
    """Returns (dense_vector, sparse_dict) from the BGE-M3 hybrid endpoint."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(EMBED_HYBRID_URL, json={"model": EMBED_MODEL, "input": text})
        r.raise_for_status()
        data = r.json()
        return data["dense"][0], data["sparse"][0]


async def _wiki_search(query: str) -> list[dict]:
    dense, sparse = await _embed_hybrid(query)
    sparse_vec = QSparseVector(indices=sparse["indices"], values=sparse["values"])
    hits = await asyncio.to_thread(
        _qdrant.query_points,
        collection_name=WIKI_COLL,
        prefetch=[
            Prefetch(query=dense,       using="dense",  limit=WIKI_TOP_K * 4),
            Prefetch(query=sparse_vec,  using="sparse", limit=WIKI_TOP_K * 4),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=WIKI_TOP_K,
        with_payload=True,
    )
    return [
        {"title": h.payload.get("title", ""), "text": h.payload.get("text", "")}
        for h in hits.points
    ]


async def _wiki_fetch_article(title: str, max_chars: int = 8000) -> dict:
    """Fetch all chunks for a wiki article title, sorted by point ID."""
    scroll_filter = Filter(must=[FieldCondition(key="title", match=MatchValue(value=title))])
    points, _ = await asyncio.to_thread(
        _qdrant.scroll,
        collection_name=WIKI_COLL,
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

# ── Miss Library LLM-kall (serialisert via kø) ───────────────────────────────

async def _llm_call(system: str, user: str) -> str:
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    await _queue.put((system, user, future))
    return await future

async def _queue_worker():
    while True:
        system, user, future = await _queue.get()
        try:
            payload = {
                "model": AGENT_MODEL,
                "stream": _lib_cfg.get("stream", False),
                "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": 600},
                "messages": [
                    {"role": "system", "content": f"/no_think\n{system}"},
                    {"role": "user",   "content": user},
                ],
            }
            if "think" in _lib_cfg:
                payload["think"] = _lib_cfg["think"]
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.post(OLLAMA_URL, json=payload)
                r.raise_for_status()
                content = r.json().get("message", {}).get("content", "").strip()
                future.set_result(content)
        except Exception as e:
            log.error("LLM-kall feilet: %s", e)
            future.set_result(f"[Agent utilgjengelig: {e}]")
        finally:
            _queue.task_done()

async def _job_cleanup():
    """Remove completed/errored jobs older than _JOB_TTL seconds."""
    while True:
        await asyncio.sleep(300)
        cutoff = time.monotonic() - _JOB_TTL
        expired = [jid for jid, j in _jobs.items() if j["created_at"] < cutoff]
        for jid in expired:
            del _jobs[jid]
        if expired:
            log.info("[jobs] Cleaned up %d expired jobs", len(expired))


@app.on_event("startup")
async def _startup():
    asyncio.create_task(_queue_worker())
    asyncio.create_task(_job_cleanup())
    log.info("Agent-server ready. Miss Library queue worker and job cleanup started.")

# ── API-modeller ──────────────────────────────────────────────────────────────

class AskRequest(BaseModel):
    question: str

class WebSource(BaseModel):
    title: str
    url: str
    content: str = ""

class WebAskRequest(BaseModel):
    question: str
    sources: list[WebSource]

class UrlRequest(BaseModel):
    url: str

class TaskRequest(BaseModel):
    task: str
    role: str = "standard"    # "standard" | "undersøker" | "kritiker" | "analytiker"
    context: str = ""         # optional extra context injected before the task

class AskResponse(BaseModel):
    answer: str
    agent: str

class ArticleRequest(BaseModel):
    title: str
    max_chars: int = 8000

class ArticleResponse(BaseModel):
    title: str
    text: str
    chunk_count: int

class JobResponse(BaseModel):
    job_id: str
    status: str          # "running" | "done" | "error" | "cancelled"
    result: str | None = None

class InjectRequest(BaseModel):
    comment: str

class SearchRequest(BaseModel):
    search_type: str = "files"      # "files" | "grep" | "log"
    files: list[str] = []           # absolute paths under /kaare
    from_line: int | None = None
    to_line: int | None = None
    pattern: str = ""               # grep pattern
    directory: str = "/kaare"
    service: str = ""               # journalctl service
    log_file: str = ""              # filename in /kaare/logs/
    lines: int = 100
    log_filter: str = ""
    question: str = ""

# ── Miss Library ──────────────────────────────────────────────────────────────

@app.post("/ask/miss_library", response_model=AskResponse)
async def ask_miss_library(req: AskRequest):
    log.info("[Miss.Library] %s", req.question[:80])
    system = _load_personality("miss_library")

    chunks = await _wiki_search(req.question) if is_agent_tool_enabled("miss_library", "wiki", default=False) else []
    if chunks:
        # Find the most-cited article in top-K results, with keyword overlap as tiebreaker.
        # Chunks from the same article can be spread across the ranking, so frequency signals
        # true relevance better than a single chunk's rank position.
        title_freq: dict[str, int] = {}
        for c in chunks:
            title_freq[c["title"]] = title_freq.get(c["title"], 0) + 1

        query_words = set(req.question.lower().split())

        def _article_score(title: str) -> tuple[int, int]:
            freq = title_freq[title]
            kw = sum(1 for w in query_words if len(w) > 3 and w in title.lower())
            return (freq, kw)

        best_title = max(title_freq, key=_article_score)
        best_article = await _wiki_fetch_article(best_title, max_chars=4000)

        # Best article goes first so the LLM sees the most relevant content immediately.
        ctx_parts: list[str] = []
        if best_article["text"]:
            ctx_parts.append(f"[{best_title}]\n{best_article['text']}")

        seen: set[str] = {best_title}
        for c in chunks:
            if c["title"] not in seen:
                ctx_parts.append(f"[{c['title']}]\n{c['text']}")
                seen.add(c["title"])

        wiki_ctx = "\n\n".join(ctx_parts)
        user_msg = f"Wiki-utdrag:\n{wiki_ctx}\n\nSpørsmål: {req.question}"
    else:
        user_msg = f"Spørsmål: {req.question}\n\n(Ingen wiki-utdrag funnet.)"

    answer = await _llm_call(system, user_msg)
    log.info("[Miss.Library] svar: %s", answer[:80])
    return AskResponse(answer=answer, agent="miss_library")


@app.post("/ask/miss_library/web", response_model=AskResponse)
async def ask_miss_library_web(req: WebAskRequest):
    """Web search synthesis — Library answers from fetched web sources, no wiki lookup."""
    log.info("[Miss.Library/web] %s", req.question[:80])
    system = _load_personality("miss_library")

    lines = []
    for i, s in enumerate(req.sources, 1):
        content = s.content.strip()
        if content:
            lines.append(f"[{i}] {s.title} — {s.url}\n{content}")
        else:
            lines.append(f"[{i}] {s.title} — {s.url}\n(Innhold utilgjengelig.)")

    sources_text = "\n\n".join(lines) if lines else "(Ingen kilder hentet.)"
    user_msg = (
        f"Nettkilder:\n{sources_text}\n\n"
        f"Spørsmål: {req.question}\n\n"
        "Svar KUN basert på kildene over. "
        "Hvis svaret ikke finnes, si det klart og oppgi URL-ene."
    )

    answer = await _llm_call(system, user_msg)
    log.info("[Miss.Library/web] svar: %s", answer[:80])
    return AskResponse(answer=answer, agent="miss_library")

@app.post("/ask/miss_library/hent_url", response_model=AskResponse)
async def ask_miss_library_hent_url(req: UrlRequest):
    """Fetch a specific URL (trusted domains only) and let Miss Library summarize it."""
    import yaml as _yaml
    from urllib.parse import urlparse

    _lang = get_lang("global")
    url = req.url.strip()
    if not url:
        return AskResponse(answer=t("svc_empty_url", _lang), agent="miss_library")

    # Trusted-domain check
    try:
        trusted_path = Path("/kaare/configs/trusted_sources.yaml")
        data = _yaml.safe_load(trusted_path.read_text(encoding="utf-8")) or {}
        trusted = []
        for category in data.get("sources", {}).values():
            for entry in category:
                d = entry.get("domain", "").lower().lstrip("www.")
                if d and "/" not in d:
                    trusted.append(d)
        if trusted:
            host = (urlparse(url).hostname or "").lower().lstrip("www.")
            if not any(host == d or host.endswith("." + d) for d in trusted):
                return AskResponse(
                    answer=f"Domenet «{host}» er ikke i listen over godkjente kilder. "
                           + t("svc_trusted_hint", _lang),
                    agent="miss_library",
                )
    except Exception as e:
        log.warning("[Miss.Library/hent_url] Kunne ikke laste trusted_sources: %s", e)

    # Fetch page content
    log.info("[Miss.Library/hent_url] %s", url[:100])
    try:
        import trafilatura
        async with httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Kaare/1.0)"},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return AskResponse(
                    answer=f"Fikk HTTP {r.status_code} fra {url}.",
                    agent="miss_library",
                )
            html = r.text
        text = (trafilatura.extract(html, include_comments=False, include_tables=True) or "").strip()
        if len(text) > 6000:
            text = text[:6000] + "…"
        if not text:
            return AskResponse(answer=t("svc_url_fetch_failed", _lang, url=url), agent="miss_library")
    except Exception as e:
        return AskResponse(answer=f"Henting av {url} feilet: {e}", agent="miss_library")

    system = _load_personality("miss_library")
    user_msg = (
        f"Kilde: {url}\n\n{text}\n\n"
        "Oppsummer innholdet på en klar og nyttig måte. "
        "Hvis brukeren stilte et spesifikt spørsmål, svar på det basert på kilden."
    )
    answer = await _llm_call(system, user_msg)
    log.info("[Miss.Library/hent_url] svar: %s", answer[:80])
    return AskResponse(answer=answer, agent="miss_library")


@app.post("/ask/miss_library/cloud", response_model=AskResponse)
async def ask_miss_library_cloud(req: AskRequest):
    """Library asks the configured cloud LLM — for questions beyond local wiki/web."""
    if not _llm("cloud").get("enabled", True):
        log.info("[Miss.Library/cloud] avvist — cloud LLM er deaktivert")
        return AskResponse(answer="Online LLM er deaktivert. Aktiver den under Innstillinger → LLM → Sky-modell.", agent="miss_library")
    log.info("[Miss.Library/cloud] %s", req.question[:80])
    system = _load_personality("miss_library")
    prompt = f"{system.strip()}\n\nSpørsmål: {req.question}"
    result = await ask_llm_cloud(prompt)
    if not result.get("ok"):
        answer = f"Online-modellen svarte ikke ({result.get('error', 'ukjent feil')})."
    else:
        answer = result["text"]
    log.info("[Miss.Library/cloud] svar: %s", answer[:80])
    return AskResponse(answer=answer, agent="miss_library")

# ── Wiki article fetch ────────────────────────────────────────────────────────

@app.post("/wiki/article", response_model=ArticleResponse)
async def wiki_article(req: ArticleRequest):
    """Return all chunks of a wiki article concatenated in order."""
    result = await _wiki_fetch_article(req.title, req.max_chars)
    return result

# ── Mechanic søk-og-summer ────────────────────────────────────────────────

_MAX_SØK_CHARS = 12000
_ALLOWED_BASE  = "/kaare"

async def _mechanic_fetch_content(req: SearchRequest) -> str:
    """Python reads files/runs grep/reads logs. No LLM involved."""
    if req.search_type in ("files", "filer"):
        if not req.files:
            return ""
        parts = []
        per_file = _MAX_SØK_CHARS // max(len(req.files), 1)
        for path_str in req.files[:5]:
            p = Path(path_str)
            if not p.is_absolute() or not str(p).startswith(_ALLOWED_BASE):
                parts.append(f"### {path_str}\n[Avvist: kun /kaare-stier tillatt]")
                continue
            try:
                all_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                if req.from_line and req.to_line:
                    chunk = all_lines[req.from_line - 1 : req.to_line]
                elif req.from_line:
                    chunk = all_lines[req.from_line - 1 : req.from_line + 299]
                else:
                    chunk = all_lines
                text = "\n".join(f"{i+1}: {l}" for i, l in enumerate(
                    chunk, start=(req.from_line or 1) - 1
                ))
                parts.append(f"### {path_str}\n{text[:per_file]}")
            except Exception as e:
                parts.append(f"### {path_str}\n[Lesefeil: {e}]")
        return "\n\n".join(parts)

    elif req.search_type == "grep":
        _lang_grep = get_lang("global")
        if not req.pattern:
            return t("svc_no_grep_pattern", _lang_grep)
        mappe = req.directory if req.directory.startswith(_ALLOWED_BASE) else _ALLOWED_BASE
        try:
            result = subprocess.run(
                ["grep", "-rn", "-E",
                 "--include=*.py", "--include=*.yaml", "--include=*.md",
                 "--include=*.json", "--include=*.sh", "--include=*.toml",
                 req.pattern, mappe],
                capture_output=True, text=True, encoding="utf-8", timeout=15,
            )
            out = result.stdout.strip()
            return out[:_MAX_SØK_CHARS] if out else t("svc_no_grep_results", _lang_grep, pattern=req.pattern, path=mappe)
        except Exception as e:
            return f"[Grep feilet: {e}]"

    elif req.search_type in ("log", "logg"):
        n = min(max(req.lines, 10), 500)
        if req.service:
            try:
                result = subprocess.run(
                    ["journalctl", "-u", req.service, "-n", str(n), "--no-pager"],
                    capture_output=True, text=True, timeout=15,
                )
                out = result.stdout.strip()
                if req.log_filter and out:
                    out = "\n".join(l for l in out.splitlines()
                                    if req.log_filter.lower() in l.lower())
                return out[:_MAX_SØK_CHARS] if out else f"[Tom logg for {req.service}]"
            except Exception as e:
                return f"[Journalctl feilet: {e}]"
        elif req.log_file:
            log_path = Path("/kaare/logs") / Path(req.log_file).name
            if not log_path.exists():
                return f"[Loggfil ikke funnet: {req.log_file}]"
            try:
                result = subprocess.run(
                    ["tail", "-n", str(n), str(log_path)],
                    capture_output=True, text=True, timeout=10,
                )
                out = result.stdout.strip()
                if req.log_filter and out:
                    out = "\n".join(l for l in out.splitlines()
                                    if req.log_filter.lower() in l.lower())
                return out[:_MAX_SØK_CHARS] if out else f"[Tom logg: {req.log_file}]"
            except Exception as e:
                return f"[Logglesing feilet: {e}]"
        return "[Feil: angi 'service' eller 'log_file' for type=logg]"

    return "[Ukjent search_type]"


async def _mechanic_llm_call(system: str, user: str) -> str:
    """One-shot call to Mechanic 9B — no tool use, just summarize."""
    from kaare_core.model_lock import lock_11445, LockTimeout
    payload = {
        "model": MECHANIC_MODEL,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.3,
            "num_ctx": 8192,
            "num_predict": 800,
        },
        "messages": [
            {"role": "system", "content": f"/no_think\n{system}"},
            {"role": "user",   "content": user},
        ],
    }
    _lang_mech = get_lang("global")
    try:
        async with lock_11445("mechanic_søk", max_wait=120):
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(MECHANIC_URL, json=payload,
                                      headers={"x-kaare-source": "mechanic_sok"})
                r.raise_for_status()
                content = r.json().get("message", {}).get("content", "").strip()
                return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip() \
                       or "[Ingen respons fra Mechanic]"
    except LockTimeout:
        return t("svc_mechanic_busy", _lang_mech)
    except Exception as e:
        return f"[Mechanic utilgjengelig: {e}]"


@app.post("/ask/mechanic/søk", response_model=AskResponse)
async def ask_mechanic_søk(req: SearchRequest):
    """
    Mechanic søk-og-summer: Python reads files/grep/logs, 9B summarizes.
    No tool-calling loop in Mechanic — one-shot summarization only.
    """
    if is_fallback_active():
        return AskResponse(
            answer="[Mechanic ikke tilgjengelig — reservemodus aktiv]",
            agent="mechanic",
        )
    log.info("[Mechanic/søk] type=%s spørsmål=%s", req.search_type, req.question[:60])

    content = await _mechanic_fetch_content(req)
    if not content or content.startswith("["):
        return AskResponse(answer=content or t("svc_no_content", get_lang("global")), agent="mechanic")

    system = _load_personality("mechanic")
    user_msg = (
        f"Du har fått følgende innhold:\n\n---\n{content}\n---\n\n"
        f"Spørsmål: {req.question}\n\n"
        "Svar kortfattet og presist. Henvis til konkrete linjenummer eller steder."
    )
    answer = await _mechanic_llm_call(system, user_msg)
    log.info("[Mechanic/søk] svar: %s", answer[:80])
    return AskResponse(answer=answer, agent="mechanic")


# ── Mechanic ───────────────────────────────────────────────────────────────

@app.post("/ask/mechanic", response_model=AskResponse)
async def ask_mechanic(req: TaskRequest):
    """
    Receives a task from Kåre and lets Mechanic solve it step by step.
    Returns unavailable while Kåre is in 9B fallback mode (shared GPU).
    """
    if is_fallback_active():
        log.info("[Mechanic] reservemodus aktiv — avviser forespørsel")
        return AskResponse(
            answer="[Mechanic er ikke tilgjengelig akkurat nå — Kåre bruker reservemodellen. Prøv igjen om litt.]",
            agent="mechanic",
        )

    log.info("[Mechanic] rolle=%s oppgave: %s", req.role, req.task[:120])
    system = _load_personality("mechanic", req.role)
    memory = _load_mechanic_memory()
    if memory:
        system = system + f"\n\n--- DIN HUKOMMELSE ---\n{memory}"
    task_content = f"{req.context}\n\n{req.task}".strip() if req.context else req.task

    messages = [
        {"role": "system", "content": f"/no_think\n{system}"},
        {"role": "user",   "content": task_content},
    ]

    answer = await ask_with_tools(
        messages=messages,
        url=MECHANIC_URL,
        model=MECHANIC_MODEL,
        tools=_tools_for_role(req.role),
    )
    log.info("[Mechanic] svar: %s", answer[:80])
    return AskResponse(answer=answer, agent="mechanic")

# ── Mechanic async jobs ────────────────────────────────────────────────────

async def _run_mechanic_job(job_id: str, task: str, role: str = "standard", context: str = "") -> None:
    """Background task — runs Mechanic and stores result in _jobs."""
    system = _load_personality("mechanic", role)
    memory = _load_mechanic_memory()
    if memory:
        system = system + f"\n\n--- DIN HUKOMMELSE ---\n{memory}"
    task_content = f"{context}\n\n{task}".strip() if context else task
    messages = [
        {"role": "system", "content": f"/no_think\n{system}"},
        {"role": "user",   "content": task_content},
    ]
    job_state = _jobs.get(job_id, {})
    try:
        answer = await ask_with_tools(
            messages=messages,
            url=MECHANIC_URL,
            model=MECHANIC_MODEL,
            job_state=job_state,
            tools=_tools_for_role(role),
        )
        if job_id in _jobs:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = answer
        log.info("[Mechanic job %s] done: %s", job_id[:8], answer[:80])
    except asyncio.CancelledError:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "cancelled"
            _jobs[job_id]["result"] = "[Job cancelled by user]"
        log.info("[Mechanic job %s] cancelled", job_id[:8])
        raise
    except Exception as e:
        if job_id in _jobs:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["result"] = f"[Job failed: {e}]"
        log.error("[Mechanic job %s] error: %s", job_id[:8], e)


@app.post("/jobs/mechanic", response_model=JobResponse)
async def start_mechanic_job(req: TaskRequest):
    """
    Fire-and-forget: start a Mechanic job and return job_id immediately.
    Kåre can monitor with GET /jobs/mechanic/{job_id} while using its own tools.
    """
    if is_fallback_active():
        return JobResponse(
            job_id="",
            status="error",
            result="[Mechanic unavailable — Kåre is in fallback mode]",
        )
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None, "created_at": time.monotonic()}
    task = asyncio.create_task(_run_mechanic_job(job_id, req.task, req.role, req.context))
    _jobs[job_id]["task"] = task  # stored for cancellation via task.cancel()
    log.info("[Mechanic job %s] started: %s", job_id[:8], req.task[:80])
    return JobResponse(job_id=job_id, status="running")


@app.get("/jobs/mechanic/{job_id}", response_model=JobResponse)
async def get_mechanic_job(job_id: str):
    """Poll job status. Returns status=running until done/error/cancelled."""
    job = _jobs.get(job_id)
    if job is None:
        return JobResponse(job_id=job_id, status="error", result="[Job not found — may have expired]")
    return JobResponse(job_id=job_id, status=job["status"], result=job["result"])


@app.delete("/jobs/mechanic/{job_id}", response_model=JobResponse)
async def cancel_mechanic_job(job_id: str):
    """Cancel a running job. Cancels the asyncio Task — closes httpx connection — Ollama stops generating."""
    job = _jobs.get(job_id)
    if job is None:
        return JobResponse(job_id=job_id, status="error", result="[Job not found — may have expired]")
    task = job.get("task")
    if task and not task.done():
        task.cancel()
        log.info("[Mechanic job %s] cancellation requested via DELETE", job_id[:8])
    return JobResponse(job_id=job_id, status=job["status"], result=job.get("result"))


@app.patch("/jobs/mechanic/{job_id}", response_model=JobResponse)
async def inject_mechanic_comment(job_id: str, req: InjectRequest):
    """Inject a user comment into a running job. Mechanic sees it at the next tool round."""
    job = _jobs.get(job_id)
    if job is None:
        return JobResponse(job_id=job_id, status="error", result="[Job not found — may have expired]")
    if job.get("status") != "running":
        return JobResponse(job_id=job_id, status=job["status"], result="[Job is not running — cannot inject comment]")
    job["injected"] = req.comment
    log.info("[Mechanic job %s] comment injected: %s", job_id[:8], req.comment[:60])
    return JobResponse(job_id=job_id, status="running", result=f"[Comment queued: {req.comment[:60]}]")


# ── Heartbeat ─────────────────────────────────────────────────────────────────

@app.get("/")
async def heartbeat():
    running = sum(1 for j in _jobs.values() if j["status"] == "running")
    return {"status": "ok", "agents": ["miss_library", "mechanic"], "active_jobs": running, "total_jobs": len(_jobs)}
