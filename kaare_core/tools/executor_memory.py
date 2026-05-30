import yaml
from pathlib import Path

from kaare_core.memory.long_term import get_ltm, USER_GLOBAL
from kaare_core.tools.i18n import t, get_lang
from kaare_core.tools.think_cache import read_think_history, format_for_kare

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

MEMORY_TOOLS = {
    "minne",
    "søk_i_minne",
    "bekreft_interaksjoner",
    "hent_ubekreftede",
    "les_møte",
    "les_indre_tanker",
    "hent_gammel_stm",
    "les_tankehistorikk",
}


def _get_stm_history(date: str | None = None, lang: str = "nb") -> str:
    """Return a formatted STM snapshot for the given date, or list available dates."""
    try:
        hist_dir = Path(
            yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
            .get("stm", {})
            .get("history_dir", "/kaare/state/stm_history")
        )
    except Exception:
        hist_dir = Path("/kaare/state/stm_history")

    if not date:
        if not hist_dir.exists():
            return t("mem_no_stm_history", lang)
        files = sorted(hist_dir.glob("*.json"), reverse=True)
        if not files:
            return t("mem_no_stm_snapshots", lang)
        dates = [f.stem for f in files[:14]]
        return t("mem_stm_dates", lang) + "\n" + "\n".join(f"  - {d}" for d in dates)

    snap_path = hist_dir / f"{date}.json"
    if not snap_path.exists():
        available = sorted(hist_dir.glob("*.json"), reverse=True) if hist_dir.exists() else []
        tip = ", ".join(f.stem for f in available[:5]) if available else "ingen"
        return t("mem_no_stm_for_date", lang, date=date, tip=tip)

    try:
        import json as _j
        data = _j.loads(snap_path.read_text(encoding="utf-8"))
    except Exception as e:
        return t("mem_stm_read_error", lang, date=date, error=e)

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
            dates = ", ".join(p.stem for p in available[-5:]) if available else "ingen"
            return t("mem_no_reflection", lang, date=date, dates=dates)
    else:
        path = Path("/kaare/state/memory/reflection_latest.md")
        if not path.exists():
            return t("mem_no_reflection_file", lang)
    try:
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > 6000:
            content = content[:6000] + "\n\n[… resten er kuttet]"
        return content
    except Exception as e:
        return t("mem_reflection_error", lang, error=e)


def _read_dev_meeting(date: str | None = None, lang: str = "nb") -> str:
    if date:
        path = Path(f"/kaare/state/memory/dev_meetings/{date}.md")
        if not path.exists():
            available = sorted(Path("/kaare/state/memory/dev_meetings").glob("*.md"))
            dates = ", ".join(p.stem for p in available[-5:]) if available else "ingen"
            return t("mem_no_dev_meeting", lang, date=date, dates=dates)
    else:
        path = Path("/kaare/state/memory/dev_meeting_latest.md")
        if not path.exists():
            return t("mem_no_dev_meeting_file", lang)
    try:
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > 6000:
            content = content[:6000] + "\n\n[… resten er kuttet]"
        return content
    except Exception as e:
        return t("mem_dev_meeting_error", lang, error=e)


def _search_memory(query: str, lang: str = "nb") -> str:
    if not query.strip():
        return t("mem_empty_query", lang)
    try:
        hits = get_ltm().search_interactions(query, limit=6)
        if not hits:
            return t("mem_no_results", lang, query=query)
        lines = [t("mem_search_results", lang, count=len(hits)) + "\n"]
        for h in hits:
            ts = h["ts"][:16].replace("T", " ")
            prompt_short = h["prompt"][:80].replace("\n", " ")
            resp_short = h["response"][:80].replace("\n", " ")
            entity = f" [{h['entity_id']}]" if h["entity_id"] else ""
            lines.append(
                f"- {ts}{entity}\n"
                f"  Bruker: {prompt_short}\n"
                f"  Kåre: {resp_short}\n"
                f"  Utfall: {h['outcome']} | Tillit: {h['confidence']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Søk i minne feilet: {e}"


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
            "denied":   t("mem_verdict_denied", lang),
            "test":     t("mem_verdict_test", lang),
        }
        return f"{updated} {verdict_labels.get(verdict, verdict)}: {ids_int}"
    except Exception as e:
        return t("mem_mark_error", lang, error=e)


def _get_unverified(user_id: str, limit: int = 10, offset: int = 0, lang: str = "nb") -> str:
    uid = user_id if user_id else USER_GLOBAL
    try:
        rows = get_ltm().get_unverified_interactions(user_id=uid, limit=limit, offset=offset)
        if not rows:
            return t("mem_no_unverified", lang)
        total_note = f" (viser {offset + 1}–{offset + len(rows)})"
        lines = [t("mem_unverified_header", lang, note=total_note)]
        for r in rows:
            ts = r["ts"][:10]
            prompt_short = r["prompt"][:100].replace("\n", " ")
            resp_short = r["response"][:150].replace("\n", " ")
            lines.append(
                f"\n[ID {r['id']} | {ts}]\n"
                f"  Du: {prompt_short}\n"
                f"  Kåre: {resp_short}"
            )
        return "\n".join(lines)
    except Exception as e:
        return t("mem_unverified_error", lang, error=e)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "minne":
        action = arguments.get("action", "")
        if action == "search":
            return _search_memory(arguments.get("query", ""), lang=lang)
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
        if action == "fetch_stm":
            return _get_stm_history(arguments.get("date"), lang=lang)
        if action == "hent_gammel_stm":
            return _get_stm_history(arguments.get("date"), lang=lang)
        return f"Unknown action for minne: '{action}'. Valid: search, fetch_unverified, confirm, fetch_stm."

    if name == "les_møte":
        meeting_type = arguments.get("type", "reflection")
        date = arguments.get("date")
        if meeting_type == "development":
            return _read_dev_meeting(date, lang=lang)
        return _read_reflection(date, lang=lang)

    if name == "les_indre_tanker":
        return _read_inner_thoughts(lang=lang)

    if name == "søk_i_minne":
        return _search_memory(arguments.get("query", ""), lang=lang)

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
        return _get_stm_history(arguments.get("date"), lang=lang)

    if name == "les_tankehistorikk":
        entries = read_think_history(
            n=min(int(arguments.get("count", 10)), 50),
            search=arguments.get("filter") or None,
            recovered_only=bool(arguments.get("only_recovery", False)),
        )
        return format_for_kare(entries)

    return f"[executor_memory] Unknown tool: {name}"
