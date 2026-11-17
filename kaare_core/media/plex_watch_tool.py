"""
Kåre's view of a person's Plex watching (P140): «KTSB knows» next to «Plex claims», never merged silently.

Used by the `media` tool actions plex_continue, plex_skipped and plex_mark. The person is the asking KTSB user;
their Plex account comes from the Users page (users.db `plex_account_id`). Plex's own view (On Deck) is only
available for the account that owns the configured token — the server owner, account 1 on every Plex server.
"""

import asyncio
import logging
import time

from adapters import plex_adapter as plex
from kaare_core.config import local_from_ts
from kaare_core.media import plex_listener, watch_store
from kaare_core.tools import executor_ha
from kaare_core.tools.i18n import t
from kaare_core.users.store import get_user

logger = logging.getLogger("plex_watch_tool")

_OTHER_SCREENS_MAX = 4  # «other screens» line: at most this many
_LIVE_S = 120  # a play seen this recently while «playing» is happening now (ticks come every ~10 s)
OWNER_ACCOUNT_ID = 1  # Plex server owner — the account the configured token belongs to


def _account_for(user_id: str) -> int | None:
    try:
        user = get_user(user_id) or {}
    except Exception:
        return None
    acc = user.get("plex_account_id")
    return int(acc) if acc is not None else None


def _label(play: dict) -> str:
    if play.get("item_type") == "episode":
        return f"{plex.fmt_se(play.get('season'), play.get('episode'))} «{play.get('title', '')}»"
    return f"«{play.get('title', '')}»"


def _where(play: dict) -> str:
    return play.get("screen") or play.get("player") or "?"


def _when(ts: float) -> str:
    return local_from_ts(ts).strftime("%d.%m %H:%M")


def _offset(ms: int | None, lang: str) -> str:
    return plex._fmt_duration(ms or 0, lang) if ms else "0"


def _find_show_key(account_id: int, query: str) -> tuple[str, str]:
    """(show_key, show_title) of the best match among the account's own plays, else ("", "")."""
    needle = query.lower().strip()
    best: tuple[float, str, str] = (0.0, "", "")
    with watch_store._lock, watch_store._conn() as conn:
        rows = conn.execute(
            "SELECT show_key, show_title, MAX(last_seen_at) AS seen FROM plays WHERE account_id=? AND show_key<>''"
            " GROUP BY show_key, show_title",
            (account_id,),
        ).fetchall()
    for r in rows:
        if needle and needle in (r["show_title"] or "").lower() and r["seen"] > best[0]:
            best = (r["seen"], r["show_key"], r["show_title"])
    return best[1], best[2]


async def _next_episode(show_key: str, season: int | None, episode: int | None) -> dict | None:
    """The episode after (season, episode) in the library, crossing into the next season."""
    if not show_key or season is None or episode is None:
        return None
    container = await plex.fetch(f"/library/metadata/{show_key}/allLeaves")
    eps = sorted(
        (
            m
            for m in container.get("Metadata") or []
            if m.get("parentIndex") is not None and m.get("index") is not None
        ),
        key=lambda m: (int(m["parentIndex"]), int(m["index"])),
    )
    for m in eps:
        if (int(m["parentIndex"]), int(m["index"])) > (int(season), int(episode)):
            return m
    return None


async def _plex_claim(show_key: str, lang: str) -> tuple[str, str]:
    """(line, rating_key) for what Plex's «continue watching» says — owner account only."""
    try:
        container = await plex.fetch("/library/onDeck")
    except Exception as e:
        return t("plex_claim_failed", lang, error=type(e).__name__), ""
    for m in container.get("Metadata") or []:
        item = plex_listener.item_from_metadata(m)
        if show_key and item["show_key"] != show_key:
            continue
        label = (
            f"{plex.fmt_se(item['season'], item['episode'])} «{item['title']}»"
            if item["type"] == "episode"
            else f"«{item['title']}»"
        )
        show = f"{item['show_title']} — " if item["show_title"] and not show_key else ""
        offset = m.get("viewOffset")
        where = t("plex_claim_offset", lang, offset=_offset(offset, lang)) if offset else ""
        return t(
            "plex_claim_line", lang, label=f"{show}{label}", where=where, key=item["rating_key"]
        ), item["rating_key"]
    return t("plex_claim_none", lang), ""


async def continue_text(user_id: str, query: str, lang: str, client: str = "") -> str:
    account = _account_for(user_id)
    if account is None:
        return t("plex_not_linked", lang)
    show_key, show_title = ("", "")
    if query:
        show_key, show_title = await asyncio.to_thread(_find_show_key, account, query)
        if not show_key:
            return t("plex_ktsb_no_show", lang, query=query)
    # P140 bug A: «where was I on that screen» — the position on the named screen, never the person's newest
    # anywhere passed off as that screen's
    lines: list[str] = []
    screen = ""
    if client:
        found = await asyncio.to_thread(executor_ha.resolve_screen_nodes, client)
        if len(found) > 1:
            return t("plex_screen_choices", lang, client=client, choices=executor_ha.choices_text(found))
        if found:
            screen = found[0][0]
        else:
            lines.append(t("plex_screen_unknown", lang, client=client))
    names = await asyncio.to_thread(executor_ha.screen_names)
    cp = None
    if screen:
        cp = await asyncio.to_thread(watch_store.continue_point, account, show_key or None, None, screen, names)
    elsewhere = bool(screen) and cp is None
    if cp is None:
        cp = await asyncio.to_thread(watch_store.continue_point, account, show_key or None)
    if cp is None:
        return "\n".join([*lines, t("plex_ktsb_nothing", lang)])

    play = cp["play"]
    title = show_title or play.get("show_title") or ""
    if screen and not elsewhere:
        lines.append(
            t("plex_continue_header_screen", lang, show=title, screen=screen)
            if title
            else t("plex_continue_header_any_screen", lang, screen=screen)
        )
    else:
        lines.append(
            t("plex_continue_header", lang, show=title) if title else t("plex_continue_header_any", lang)
        )
    if elsewhere:
        lines.append(t("plex_screen_nothing", lang, screen=screen, where=_where(play)))
    seen = t("plex_seen_at", lang, when=_when(play["last_seen_at"]), player=_where(play))
    ktsb_key = ""
    live = (
        play.get("state") in ("playing", "buffering")
        and time.time() - play["last_seen_at"] < _LIVE_S
    )
    if cp["kind"] == "resume" and live:
        lines.append(
            t(
                "plex_ktsb_now",
                lang,
                label=_label(play),
                offset=_offset(play["last_offset_ms"], lang),
                seen=seen,
            )
        )
        ktsb_key = play["rating_key"]
    elif cp["kind"] == "resume":
        lines.append(
            t(
                "plex_ktsb_resume",
                lang,
                label=_label(play),
                offset=_offset(play["last_offset_ms"], lang),
                seen=seen,
            )
        )
        lines.append(
            t(
                "plex_ktsb_resume_hint",
                lang,
                key=play["rating_key"],
                seconds=int(play["last_offset_ms"] / 1000),
            )
        )
        ktsb_key = play["rating_key"]
    elif cp["kind"] == "next":
        how = t(f"plex_status_{cp['status']}", lang)
        lines.append(t("plex_ktsb_last", lang, label=_label(play), how=how, seen=seen))
        try:
            nxt = await _next_episode(play["show_key"], play.get("season"), play.get("episode"))
        except Exception as e:
            nxt = None
            lines.append(t("plex_next_lookup_failed", lang, error=type(e).__name__))
        if nxt:
            ktsb_key = str(nxt.get("ratingKey") or "")
            nlabel = (
                f"{plex.fmt_se(nxt.get('parentIndex'), nxt.get('index'))} «{nxt.get('title', '')}»"
            )
            lines.append(t("plex_ktsb_next", lang, label=nlabel, key=ktsb_key))
        else:
            lines.append(t("plex_ktsb_no_next", lang))
    else:
        lines.append(t("plex_ktsb_movie_done", lang, label=_label(play), seen=seen))

    if play.get("show_key"):
        others = [
            f"{key}: {_label(p)} — {t(f'plex_status_{status}', lang)} ({_when(p['last_seen_at'])})"
            for key, p, status in await asyncio.to_thread(
                watch_store.latest_per_screen, account, play["show_key"], None, names
            )
            if p["id"] != play["id"]
        ]
        if others:
            lines.append(t("plex_other_screens", lang, items="; ".join(others[:_OTHER_SCREENS_MAX])))

    if account == OWNER_ACCOUNT_ID:
        claim_line, claim_key = await _plex_claim(
            play.get("show_key", "") if show_key or title else "", lang
        )
        lines.append(claim_line)
        if claim_key and ktsb_key and claim_key != ktsb_key:
            lines.append(t("plex_disagree_hint", lang))
    else:
        lines.append(t("plex_claim_unavailable", lang))
    return "\n".join(lines)


async def skipped_text(user_id: str, query: str, lang: str) -> str:
    account = _account_for(user_id)
    if account is None:
        return t("plex_not_linked", lang)
    show_key = ""
    if query:
        show_key, _title = await asyncio.to_thread(_find_show_key, account, query)
        if not show_key:
            return t("plex_ktsb_no_show", lang, query=query)
    plays = await asyncio.to_thread(watch_store.skipped, account, show_key or None)
    if not plays:
        return t("plex_skipped_none", lang)
    lines = [t("plex_skipped_header", lang, n=len(plays))]
    lines.extend(
        t(
            "plex_skipped_line",
            lang,
            show=p.get("show_title") or "",
            label=_label(p),
            offset=_offset(p["max_offset_ms"], lang),
            when=_when(p["last_seen_at"]),
            key=p["rating_key"],
        )
        for p in plays
    )
    return "\n".join(lines)


async def mark_text(user_id: str, rating_key: str, status: str, lang: str) -> str:
    account = _account_for(user_id)
    if account is None:
        return t("plex_not_linked", lang)
    if not rating_key:
        return t("media_no_rating_key", lang)
    if status not in (*watch_store.OVERRIDES, "clear"):
        return t("plex_mark_bad_status", lang, status=status)
    await asyncio.to_thread(watch_store.set_override, account, rating_key, status)
    return t(f"plex_marked_{status}", lang, key=rating_key)
