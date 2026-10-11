"""
How Kåre's memory drawers reach the system prompt (P133 C3, D2/D3/D6/D7).

- **Index, not prose** (D2): the kare list and the world facts that are not core are shown as
  one title line each; the full text is one tool call away — or comes by itself through
  «Relevant nå» when it matters for this message (D6).
- **Prose, always** (D3/D7): lessons (rules Kåre follows), the top-ranked world facts (core)
  and resolved topics' conclusions — things that must steer behaviour without being asked for.
- **Relevant nå** (D6/D12): the 2–3 entries closest to the current message, in full text, via
  kaare_core/memory/relevance.py. Cached per query so every tool round of one turn gets the
  same block (same prompt → prefix cache stays warm, no extra embedding calls).

All caps come from `get_memory_prompt_config()` (GUI, rule 25). Every function fails soft —
an empty string, never an exception into prompt building.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

from kaare_core.background import spawn
from kaare_core.config import get_memory_prompt_config
from kaare_core.memory import knowledge_store as ks
from kaare_core.memory import learning_store, relevance
from kaare_core.tools import lister
from kaare_core.tools.i18n import t
from kaare_core.tools.lessons import lessons_for_prompt

logger = logging.getLogger(__name__)

_TITLE_CHARS = 70
_WORLD_ROWS = 200
_QUERY_CACHE_SIZE = 32
_query_cache: OrderedDict[tuple, str] = OrderedDict()


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _capped(header: str, lines: list[str], max_chars: int, lang: str) -> str:
    """Header + as many whole lines as fit; a «… and N more» line when cut."""
    if not lines:
        return ""
    out, used = [header], len(header)
    for i, line in enumerate(lines):
        if used + len(line) + 1 > max_chars and len(out) > 1:
            out.append(t("memory_index_more", lang, n=len(lines) - i))
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


# ── Kare list: index ────────────────────────────────────────────────────────


def kare_index(max_chars: int, lang: str = "nb") -> str:
    try:
        items = lister._sort_pinned_first(lister._read(lister._KARE_PATH))
    except Exception as e:
        logger.warning("memory_prompt: kare list unreadable: %s", e)
        return ""
    lines = [
        f"- [{i['id']}] {_clip(lister.note_title(i), _TITLE_CHARS)}{' 📌' if i.get('pinned') else ''}"
        for i in items
    ]
    return _capped(t("memory_index_kare_title", lang), lines, max_chars, lang)


# ── World: core facts as prose, the rest as an index ────────────────────────


def _world_rows() -> list[dict]:
    try:
        return ks.get_top_facts("world", limit=_WORLD_ROWS, exclude_section_prefixes=["var::"])
    except Exception as e:
        logger.warning("memory_prompt: world facts unreadable: %s", e)
        return []


def _split_world(rows: list[dict], core_chars: int) -> tuple[list[dict], list[dict]]:
    """Core = the top-ranked rows (importance, then newest) that fit in `core_chars`."""
    core, rest, used = [], [], 0
    for r in rows:
        size = len(r["content"]) + 2
        if not rest and used + size <= core_chars:
            core.append(r)
            used += size
        else:
            rest.append(r)
    return core, rest


def _render_rows(rows: list[dict]) -> str:
    """Same section-grouped rendering as the old world block (llm_adapter._render_world_ctx)."""
    lines, last_cat = [], None
    for row in rows:
        section = row.get("section") or "Notes"
        cat = section.split("::", 1)[0]
        if cat != last_cat:
            lines.append(f"\n## {cat}")
            last_cat = cat
        if "::" in section:
            lines.append(f"- {section.split('::', 1)[1]}: {row['content']}")
        else:
            lines.append(row["content"])
    return "\n".join(lines).strip()


def world_prompt(lang: str = "nb") -> str:
    cfg = get_memory_prompt_config()
    core, rest = _split_world(_world_rows(), cfg["core_facts_max_chars"])
    parts = [_render_rows(core)] if core else []
    index_lines = [
        f"- [{r['id']}] {(r.get('section') or '').split('::', 1)[0]}: {_clip(r['content'], _TITLE_CHARS)}"
        for r in rest
    ]
    index = _capped(t("memory_index_world_title", lang), index_lines, cfg["index_max_chars"], lang)
    if index:
        parts.append(index)
    return "\n\n".join(parts)


# ── Lessons + resolved: always prose ────────────────────────────────────────


def lessons_prompt(lang: str = "nb") -> str:
    return lessons_for_prompt(get_memory_prompt_config()["lessons_max_chars"], lang)


def resolved_prompt(lang: str = "nb") -> str:
    try:
        rows = learning_store.resolved_topics(limit=50)
    except Exception as e:
        logger.warning("memory_prompt: resolved topics unreadable: %s", e)
        return ""
    lines = [
        f"- #{r['id']} ({str(r['resolved_at'])[:10]}) "
        f"{(r['label'] + ': ') if r.get('label') else ''}{r['conclusion']}"
        for r in rows
        if r.get("conclusion")
    ]
    return _capped(
        t("resolved_prompt_title", lang),
        lines,
        get_memory_prompt_config()["core_facts_max_chars"],
        lang,
    )


# ── Relevant nå ─────────────────────────────────────────────────────────────


def corpus(include_world: bool) -> list[dict]:
    """What «Relevant nå» searches: kare notes + world facts outside the core prose. Lessons and
    resolved conclusions are already in the prompt as prose; observations never are."""
    docs: list[dict] = []
    try:
        for i in lister._read(lister._KARE_PATH):
            body = lister._strip_date_stamp(i.get("tekst", ""))
            title = (i.get("tittel") or "").strip()
            docs.append(
                {
                    "key": f"kare:{i['id']}",
                    "show": f"[{i['id']}] {(title + ': ') if title else ''}{body}",
                    "text": f"{title}: {body}" if title else body,  # what is embedded
                }
            )
    except Exception as e:
        logger.warning("memory_prompt: kare corpus failed: %s", e)
    if include_world:
        _core, rest = _split_world(
            _world_rows(), get_memory_prompt_config()["core_facts_max_chars"]
        )
        for r in rest:
            section = (r.get("section") or "").split("::", 1)[0]
            docs.append(
                {
                    "key": f"world:{r['id']}",
                    "show": f"[{r['id']}] {(section + ': ') if section else ''}{r['content']}",
                    "text": f"{section}: {r['content']}" if section else r["content"],
                }
            )
    return docs


async def relevant_block(query: str, lang: str = "nb", *, include_world: bool = True) -> str:
    """«Relevant nå» for one message. Starts a background re-embed when vectors are stale (the
    current turn uses what is there). Cached per (query, corpus) so the tool rounds of one
    turn get an identical block."""
    query = (query or "").strip()
    if not query or relevance.provider() is None:
        return ""
    try:
        docs = corpus(include_world)
        if relevance.has_stale(docs):
            spawn(relevance.sync_vectors(docs), name="relevance-sync")
        key = (query[:2000], lang, include_world, hash(tuple(d["key"] + d["text"] for d in docs)))
        if key in _query_cache:
            _query_cache.move_to_end(key)
            return _query_cache[key]
        hits = await relevance.relevant(query, docs)
        block = ""
        if hits:
            lines = [t("memory_relevant_title", lang)]
            lines += [f"- {h['show']}" for h in hits]
            block = "\n".join(lines)
        _query_cache[key] = block
        while len(_query_cache) > _QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
        return block
    except Exception as e:
        logger.warning("memory_prompt: relevant block failed: %s", e)
        return ""
