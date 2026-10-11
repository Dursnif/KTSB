import asyncio
import json as _json
from pathlib import Path

import yaml

from kaare_core.config import ADMIN_ONLY_TOOLS
from kaare_core.crypto import unseal
from kaare_core.memory.long_term import USER_GLOBAL, get_ltm
from kaare_core.memory.semantic_memory import search_memory
from kaare_core.session_keys import resolve_key_sync
from kaare_core.tools.i18n import get_lang, t
from kaare_core.tools.think_cache import format_for_kare, read_think_history
from kaare_core.users.store import get_user

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

MEMORY_TOOLS = {
    "memory",
    "read_meeting",
    "inner_thoughts",
    "thought_history",
    "minne",
    "søk_i_minne",
    "bekreft_interaksjoner",
    "hent_ubekreftede",
    "les_møte",
    "les_indre_tanker",
    "hent_gammel_stm",
    "les_tankehistorikk",
}


def _load_stm_snapshot_data(path: Path, user_id: str, allow_system_key: bool = False) -> dict | str:
    """Load STM snapshot data from path (.json or .enc). Returns dict or error string."""
    enc_path = path.with_suffix(".enc")
    try:
        if enc_path.exists():
            session_key = resolve_key_sync(user_id, allow_system_key=allow_system_key)
            if not session_key:
                return f"[snapshot encrypted — not readable without active session for '{user_id}']"
            encrypted_blob = enc_path.read_text(encoding="utf-8")
            return _json.loads(unseal(encrypted_blob, session_key))
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"[read error: {e}]"


def _get_stm_history(
    date: str | None = None,
    lang: str = "nb",
    user_id: str = "global",
    allow_system_key: bool = False,
) -> str:
    """Return a formatted STM snapshot for the given date, or list available dates.

    allow_system_key=True (P87): set by dispatch() when the tool call carries
    arguments["_allow_system_key"] — currently only kaare_dev_meeting.py's Kåre tool
    loop sets that, for its own unattended tool calls during the nightly dev meeting.
    """
    try:
        hist_dir = Path(
            yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
            .get("stm", {})
            .get("history_dir", "/kaare/state/stm_history")
        )
    except Exception:
        hist_dir = Path("/kaare/state/stm_history")

    if not hist_dir.exists():
        return t("mem_no_stm_history", lang)

    if not date:
        # Collect both flat *.json (old format) and YYYY-MM-DD directories (new format)
        flat = [f.stem for f in hist_dir.glob("*.json")]
        dirs = [d.name for d in hist_dir.iterdir() if d.is_dir() and len(d.name) == 10]
        all_dates = sorted(set(flat + dirs), reverse=True)
        if not all_dates:
            return t("mem_no_stm_snapshots", lang)
        return t("mem_stm_dates", lang) + "\n" + "\n".join(f"  - {d}" for d in all_dates[:14])

    data: dict | str | None = None

    # 1. Old flat format: {date}.json
    flat_path = hist_dir / f"{date}.json"
    if flat_path.exists():
        data = _load_stm_snapshot_data(flat_path, user_id, allow_system_key=allow_system_key)
    else:
        # 2. New directory format: {date}/{user_id}.json or .enc
        date_dir = hist_dir / date
        if not date_dir.is_dir():
            flat = [f.stem for f in hist_dir.glob("*.json")]
            dirs = [d.name for d in hist_dir.iterdir() if d.is_dir() and len(d.name) == 10]
            tip = ", ".join(sorted(set(flat + dirs), reverse=True)[:5]) or t("mem_no_dates", lang)
            return t("mem_no_stm_for_date", lang, date=date, tip=tip)

        uid_candidates = [user_id] if user_id not in ("", "global") else []
        uid_candidates.append("global")
        for uid in uid_candidates:
            p = date_dir / f"{uid}.json"
            if p.exists() or (date_dir / f"{uid}.enc").exists():
                data = _load_stm_snapshot_data(p, uid, allow_system_key=allow_system_key)
                break

    if data is None:
        return t("mem_no_stm_for_date", lang, date=date, tip="ingen snapshot funnet")
    if isinstance(data, str):
        return data

    parts = []
    saved_at = data.get("saved_at", t("mem_stm_not_found", lang))[:16].replace("T", " ")
    parts.append(t("mem_stm_header", lang, date=date, saved_at=saved_at))

    if data.get("daily_summary"):
        parts.append(t("mem_daily_summary", lang) + data["daily_summary"][:500])

    dialog = data.get("dialog", [])
    if dialog:
        shown = min(20, len(dialog))
        parts.append(t("mem_dialog_header", lang, turns=len(dialog), shown=shown))
        for turn in dialog[-shown:]:
            role = turn.get("role", "?")
            text = turn.get("text", "")[:200]
            ts = turn.get("ts", "")[:16].replace("T", " ")
            parts.append(f"  [{ts}] {role}: {text}")

    ok_actions = [a for a in data.get("actions", []) if a.get("ok")]
    if ok_actions:
        shown = min(10, len(ok_actions))
        parts.append(t("mem_actions_header", lang, shown=shown, total=len(ok_actions)))
        for a in ok_actions[-shown:]:
            ts = a.get("ts", "")[:16].replace("T", " ")
            parts.append(f"  [{ts}] {a['action']} {a['entity_id']}")

    return "\n".join(parts)


def _read_inner_thoughts(lang: str = "nb") -> str:
    path = Path("/kaare/state/inner_thoughts.txt")
    if not path.exists() or not path.stat().st_size:
        return t("mem_no_inner_thoughts", lang)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        return t("mem_inner_thoughts_error", lang, error=e)
    try:
        path.unlink()
    except Exception:
        pass
    return content if content else t("mem_no_inner_thoughts", lang)


def _read_reflection(date: str | None = None, lang: str = "nb") -> str:
    if date:
        path = Path(f"/kaare/state/memory/reflections/{date}.md")
        if not path.exists():
            available = sorted(Path("/kaare/state/memory/reflections").glob("*.md"))
            dates = (
                ", ".join(p.stem for p in available[-5:]) if available else t("mem_no_dates", lang)
            )
            return t("mem_no_reflection", lang, date=date, dates=dates)
    else:
        path = Path("/kaare/state/memory/reflection_latest.md")
        if not path.exists():
            return t("mem_no_reflection_file", lang)
    try:
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > 6000:
            content = content[:6000] + "\n\n" + t("mem_truncated", lang)
        return content
    except Exception as e:
        return t("mem_reflection_error", lang, error=e)


def _read_dev_meeting(date: str | None = None, lang: str = "nb") -> str:
    if date:
        path = Path(f"/kaare/state/memory/dev_meetings/{date}.md")
        if not path.exists():
            available = sorted(Path("/kaare/state/memory/dev_meetings").glob("*.md"))
            dates = (
                ", ".join(p.stem for p in available[-5:]) if available else t("mem_no_dates", lang)
            )
            return t("mem_no_dev_meeting", lang, date=date, dates=dates)
    else:
        path = Path("/kaare/state/memory/dev_meeting_latest.md")
        if not path.exists():
            return t("mem_no_dev_meeting_file", lang)
    try:
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > 6000:
            content = content[:6000] + "\n\n" + t("mem_truncated", lang)
        return content
    except Exception as e:
        return t("mem_dev_meeting_error", lang, error=e)


def _clip(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


async def _search_memory(
    query: str, user_id: str = "", allow_system_key: bool = False, lang: str = "nb"
) -> str:
    if not query.strip():
        return t("mem_empty_query", lang)
    uid = user_id if user_id else USER_GLOBAL
    try:
        result = await asyncio.to_thread(
            get_ltm().search_interactions_ranked, query, 6, uid, allow_system_key
        )
        episodes = [
            e
            for e in await search_memory(query, limit=3, user_id=uid)
            if e.get("narrative") and e["narrative"] != "[kryptert]"
        ]
        hits = result["hits"]
        if not hits and not episodes:
            return t("mem_no_results", lang, query=query)
        lines = []
        if hits:
            lines.append(
                t("mem_search_results", lang, count=len(hits), total=result["total"]) + "\n"
            )
            for h in hits:
                ts = h["ts"][:16].replace("T", " ")
                entity = f" [{h['entity_id']}]" if h["entity_id"] else ""
                lines.append(
                    t(
                        "mem_search_row",
                        lang,
                        id=h["id"],
                        ts=ts,
                        entity=entity,
                        prompt=_clip(h["prompt"], 200),
                        response=_clip(h["response"], 300),
                        outcome=h["outcome"],
                        confidence=h["confidence"],
                    )
                )
        if episodes:
            lines.append("\n" + t("mem_episode_header", lang, count=len(episodes)))
            lines.extend(f"- [{(e.get('ts') or '?')[:10]}] {e['narrative']}" for e in episodes)
        return "\n".join(lines)
    except Exception as e:
        return t("mem_search_error", lang, error=e)


def _verify_interactions(ids, verdict: str, user_id: str, lang: str = "nb") -> str:
    if not ids:
        return t("mem_no_ids", lang)
    uid = user_id if user_id else USER_GLOBAL
    try:
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.strip("[] ").split(",") if x.strip()]
        ids_int = [int(i) for i in ids]
        updated = get_ltm().mark_interactions(ids=ids_int, verdict=verdict, user_id=uid)
        verdict_labels = {
            "verified": t("mem_verdict_verified", lang),
            "denied": t("mem_verdict_denied", lang),
            "test": t("mem_verdict_test", lang),
        }
        return f"{updated} {verdict_labels.get(verdict, verdict)}: {ids_int}"
    except Exception as e:
        return t("mem_mark_error", lang, error=e)


def _get_unverified(user_id: str, limit: int = 10, offset: int = 0, lang: str = "nb") -> str:
    uid = user_id if user_id else USER_GLOBAL
    try:
        ltm = get_ltm()
        rows = ltm.get_unverified_interactions(
            user_id=uid, limit=limit, offset=offset, newest_first=True
        )
        if not rows:
            return t("mem_no_unverified", lang)
        stats = ltm.count_unverified(uid)
        lines = [
            t(
                "mem_unverified_header",
                lang,
                start=offset + 1,
                end=offset + len(rows),
                total=stats["total"],
                first=stats["first_ts"][:10],
                last=stats["last_ts"][:10],
            )
        ]
        for r in rows:
            ts = r["ts"][:10]
            lines.append(
                f"\n[ID {r['id']} | {ts}]\n"
                f"  {t('mem_unverified_user', lang)}: {_clip(r['prompt'], 100)}\n"
                f"  Kåre: {_clip(r['response'], 150)}"
            )
        return "\n".join(lines)
    except Exception as e:
        return t("mem_unverified_error", lang, error=e)


def _is_admin_caller(user_id: str) -> bool:
    """True only for a real user whose role is admin. Missing, unknown or system ids → False."""
    if not user_id or user_id == "global":
        return False
    try:
        rec = get_user(user_id)
    except Exception:
        return False
    return bool(rec) and rec.get("role") == "admin"


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    # Shared logs with other users' text — admin only, fail closed (see config.ADMIN_ONLY_TOOLS).
    if name in ADMIN_ONLY_TOOLS and not _is_admin_caller(arguments.get("_user_id", "")):
        return t("mem_admin_only_tool", lang)

    if name in ("memory", "minne"):
        action = arguments.get("action", "")
        if action == "search":
            return await _search_memory(
                arguments.get("query", ""),
                user_id=arguments.get("_user_id", ""),
                allow_system_key=bool(arguments.get("_allow_system_key", False)),
                lang=lang,
            )
        if action == "fetch_unverified":
            return _get_unverified(
                user_id=arguments.get("_user_id", ""),
                limit=min(int(arguments.get("count", 10)), 20),
                offset=int(arguments.get("skip", 0)),
                lang=lang,
            )
        if action == "confirm":
            return _verify_interactions(
                ids=arguments.get("ids", []),
                verdict=arguments.get("dom", "verified"),
                user_id=arguments.get("_user_id", ""),
                lang=lang,
            )
        if action in ("fetch_stm", "hent_gammel_stm"):
            return _get_stm_history(
                arguments.get("date"),
                lang=lang,
                user_id=arguments.get("_user_id", "global"),
                allow_system_key=bool(arguments.get("_allow_system_key", False)),
            )
        return f"Unknown action for memory: '{action}'. Valid: search, fetch_unverified, confirm, fetch_stm."

    if name in ("read_meeting", "les_møte"):
        meeting_type = arguments.get("type", "reflection")
        date = arguments.get("date")
        if meeting_type == "development":
            return _read_dev_meeting(date, lang=lang)
        return _read_reflection(date, lang=lang)

    if name in ("inner_thoughts", "les_indre_tanker"):
        return _read_inner_thoughts(lang=lang)

    if name == "søk_i_minne":
        return await _search_memory(
            arguments.get("query", ""),
            user_id=arguments.get("_user_id", ""),
            allow_system_key=bool(arguments.get("_allow_system_key", False)),
            lang=lang,
        )

    if name == "bekreft_interaksjoner":
        return _verify_interactions(
            ids=arguments.get("ids", []),
            verdict=arguments.get("dom", "verified"),
            user_id=arguments.get("_user_id", ""),
            lang=lang,
        )

    if name == "hent_ubekreftede":
        return _get_unverified(
            user_id=arguments.get("_user_id", ""),
            limit=min(int(arguments.get("count", 10)), 20),
            offset=int(arguments.get("skip", 0)),
            lang=lang,
        )

    if name == "hent_gammel_stm":
        return _get_stm_history(
            arguments.get("date"),
            lang=lang,
            user_id=arguments.get("_user_id", "global"),
            allow_system_key=bool(arguments.get("_allow_system_key", False)),
        )

    if name in ("thought_history", "les_tankehistorikk"):
        entries = read_think_history(
            n=min(int(arguments.get("count", 10)), 50),
            search=arguments.get("filter") or None,
            recovered_only=bool(arguments.get("only_recovery", False)),
        )
        return format_for_kare(entries)

    return f"[executor_memory] Unknown tool: {name}"
