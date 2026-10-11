"""
The dev-meeting agenda — `note(list_name='agenda')` (P118 B1, T2-D17/T2-D18) — and its sibling
drawer for observations — `note(list_name='observations')` (P133 C1, D1/D9).

Kåre writes "raise this at the dev meeting" items (faults) to the agenda and "I saw X at T"
to observations, from any path he runs on (chat, voice, the reflection loop, timers,
meetings, Frigate). Rows go to the append-only inbox in learning.db
(kaare_core/memory/learning_store.py). Neither drawer is ever pinned or injected into the
system prompt; Kåre only sees one line saying how many agenda items are waiting. Writing
something already there groups it into the existing topic and says so — nothing is dropped.

P133 C2 — resolved lifecycle: a topic raised with a human and settled gets `resolve` with a
short conclusion, but only from a live user turn by a role the GUI allows (D11, default admin).
A later row that means the same as a resolved conclusion is stored as an echo and the writer is
told the conclusion at once; reopening is a proposal a human confirms.
"""

from __future__ import annotations

import logging

from kaare_core.config import get_memory_prompt_config
from kaare_core.memory import learning_store, relevance
from kaare_core.tools.i18n import t
from kaare_core.tools.trace_reader import infer_source
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

_READ_LIMIT = 20  # newest open rows shown by action='read'
# Sources that are a human talking to Kåre right now (trace_reader vocabulary). Background
# loops (refl / meet / timer / frigate) can never resolve a topic, whatever the GUI says.
_LIVE_SOURCES = ("user", "stt")


def _theme_name(theme: str | None, lang: str) -> str:
    return t(f"agenda_theme_{theme or 'other'}", lang)


def _echo_reply(res: dict, lang: str) -> str:
    info = res["resolved"]
    return t(
        "agenda_echo_resolved",
        lang,
        topic=info["topic_id"],
        date=(info.get("resolved_at") or "")[:10],
        conclusion=info.get("conclusion", ""),
    )


def _echo_of(text: str) -> int | None:
    """A resolved topic this text means the same as (BGE-M3, ≥ echo_min_score), or None."""
    try:
        resolved = learning_store.resolved_topics(limit=200)
        if not resolved:
            return None
        hit = relevance.match_resolved_sync(text, resolved)
        return hit[0] if hit else None
    except Exception as e:
        logger.warning("echo check failed: %s", e)
        return None


def _add(
    kind: str,
    text: str,
    context: str,
    *,
    user_id: str,
    rid: str,
    source: str,
    pointer: str,
    lang: str,
    theme: str,
    title: str,
) -> dict | str:
    text = (text or "").strip()
    if not text:
        return t("list_empty_text", lang)
    try:
        return learning_store.add_inbox_item(
            text,
            source=(source or "").strip() or infer_source(rid or ""),
            user_id=user_id,
            rid=rid,
            pointer=(pointer or "").strip(),
            context=(context or "").strip(),
            theme=(theme or "").strip() or None,
            kind=kind,
            title=title,
            echo_of=_echo_of(f"{title} {text}"),
        )
    except Exception as e:
        logger.warning("%s add failed: %s", kind, e)
        return t("agenda_write_failed", lang, error=e)


def agenda_add(
    text: str,
    context: str = "",
    *,
    user_id: str = "global",
    rid: str = "",
    source: str = "",
    pointer: str = "",
    lang: str = "nb",
    theme: str = "",
    title: str = "",
) -> str:
    """Any path can write here (chat, voice, reflection, timers, meetings, later vision/Argus).
    `source` is the caller's own label when it has one; otherwise it is read from the rid with
    the same vocabulary as the trace reader (refl / meet / timer / stt / frigate / user).
    `pointer` lets a caller link the row to its own record (e.g. a Frigate event id).
    `theme` is the writer's theme choice (P118 B8); empty/unknown → set by code rules.
    `title` is the writer's short title (P133)."""
    res = _add(
        "agenda",
        text,
        context,
        user_id=user_id,
        rid=rid,
        source=source,
        pointer=pointer,
        lang=lang,
        theme=theme,
        title=title,
    )
    if isinstance(res, str):
        return res
    if res.get("resolved"):
        return _echo_reply(res, lang)
    if res["known"]:
        return t(
            "agenda_already_known",
            lang,
            topic=res["topic_id"],
            count=res["count"],
            first_seen=res["first_seen"][:10],
        )
    return t(
        "agenda_added",
        lang,
        topic=res["topic_id"],
        theme=_theme_name(res.get("theme"), lang),
    )


def observation_add(
    text: str,
    context: str = "",
    *,
    user_id: str = "global",
    rid: str = "",
    source: str = "",
    pointer: str = "",
    lang: str = "nb",
    theme: str = "",
    title: str = "",
) -> str:
    """«I saw X at T» — kept, grouped, counted into patterns by the night consolidation, never
    developed by the dev slot and never injected into the prompt (P133 D9)."""
    res = _add(
        "observation",
        text,
        context,
        user_id=user_id,
        rid=rid,
        source=source,
        pointer=pointer,
        lang=lang,
        theme=theme,
        title=title,
    )
    if isinstance(res, str):
        return res
    if res.get("resolved"):
        return _echo_reply(res, lang)
    return t(
        "observation_added",
        lang,
        topic=res["topic_id"],
        count=res["count"],
        theme=_theme_name(res.get("theme"), lang),
    )


def _read(kind: str, title_key: str, empty_key: str, lang: str) -> str:
    try:
        rows = learning_store.list_inbox(status="new", limit=_READ_LIMIT, kind=kind)
        total = learning_store.count_open_items(kind)
        topics = learning_store.count_open_topics(kind)
    except Exception as e:
        logger.warning("%s read failed: %s", kind, e)
        return t("agenda_write_failed", lang, error=e)
    if not rows:
        return t(empty_key, lang)
    lines = [t(title_key, lang, shown=len(rows), total=total, topics=topics)]
    for r in rows:
        ctx = f" → {r['context']}" if r.get("context") else ""
        theme = f" · {r['theme']}" if r.get("theme") else ""
        title = f"{r['title']}: " if r.get("title") else ""
        lines.append(
            f"  [#{r['topic_id']}{theme}] {r['ts'][:10]} ({r['source']}) — {title}{r['text']}{ctx}"
        )
    return "\n".join(lines)


def agenda_read(lang: str = "nb") -> str:
    return _read("agenda", "agenda_read_title", "agenda_empty", lang)


def observation_read(lang: str = "nb") -> str:
    return _read("observation", "observation_read_title", "observation_empty", lang)


def resolved_read(lang: str = "nb") -> str:
    """Resolved topics with their conclusions — what Kåre already settled with a human."""
    try:
        rows = learning_store.resolved_topics(limit=_READ_LIMIT)
    except Exception as e:
        return t("agenda_write_failed", lang, error=e)
    if not rows:
        return t("resolved_empty", lang)
    lines = [t("resolved_read_title", lang, count=len(rows))]
    lines.extend(
        f"  [#{r['id']}] {str(r['resolved_at'])[:10]} — {r['label'] or ''}: {r['conclusion']}"
        f" ({t('resolved_echoes', lang, n=r['echoes'])})"
        for r in rows
    )
    return "\n".join(lines)


def _may_resolve(user_id: str, rid: str) -> str | None:
    """None when allowed; otherwise the i18n key of the refusal. Fail closed."""
    if infer_source(rid or "") not in _LIVE_SOURCES or not rid:
        return "resolve_refused_background"
    if not user_id or user_id in ("global", "kare"):
        return "resolve_refused_background"
    try:
        role = (user_store.get_user(user_id) or {}).get("role", "")
    except Exception:
        role = ""
    allowed = get_memory_prompt_config()["resolve_roles"]
    if not role or role == "stranger" or role not in allowed:
        return "resolve_refused_role"
    return None


def resolve(topic_id, conclusion: str, *, user_id: str, rid: str, lang: str = "nb") -> str:
    refusal = _may_resolve(user_id, rid)
    if refusal:
        return t(refusal, lang)
    try:
        tid = int(str(topic_id).lstrip("#"))
    except (TypeError, ValueError):
        return t("resolve_missing_topic", lang)
    if not (conclusion or "").strip():
        return t("resolve_missing_conclusion", lang)
    info = learning_store.resolve_topic(tid, conclusion, rid=rid, user_id=user_id)
    if not info:
        return t("resolve_not_found", lang, topic=tid)
    return t("resolve_done", lang, topic=info["topic_id"], conclusion=info["conclusion"])


def reopen(topic_id, reason: str, *, lang: str = "nb") -> str:
    """Ask to reopen a resolved topic — becomes a proposal a human confirms in chat."""
    try:
        tid = int(str(topic_id).lstrip("#"))
    except (TypeError, ValueError):
        return t("resolve_missing_topic", lang)
    pid = learning_store.add_reopen_proposal(tid, reason or "")
    if pid is None:
        return t("reopen_not_resolved", lang, topic=tid)
    return t("reopen_proposed", lang, topic=tid, id=pid)


def agenda_prompt_line(lang: str = "nb") -> str:
    """One system-prompt line: how many agenda items wait for tonight. Empty when none."""
    try:
        n = learning_store.count_open_items()
    except Exception:
        return ""
    if not n:
        return ""
    return t("agenda_prompt_line", lang, count=n)
