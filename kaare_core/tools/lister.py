"""
Kåre's list system — three separate lists with different semantics:
  handle   : the household's shared shopping list (state/lister/handleliste.json)
  huske    : per-user reminder list (state/lister/brukere/{user_id}_huske.json)
  kare     : Kåre's own follow-up points (state/lister/kare_huske.json)

All lists are JSON arrays, written atomically (tmp file + rename). The on-disk keys stay
Norwegian ("tekst", "dato", "kjøpt", "påminn_ved_login", "kontekst" …) — they are the stored
data format; the code around them is English (P116 B27).
"""

import difflib
import json
import os
import re
import uuid
from pathlib import Path

from kaare_core.config import get_settings as _get_settings
from kaare_core.config import today_local
from kaare_core.tools.i18n import t

_LISTER_DIR = Path("/kaare/state/lister")
_SHOPPING_PATH = _LISTER_DIR / "handleliste.json"
_KARE_PATH = _LISTER_DIR / "kare_huske.json"

_TOOL_SYNTAX_LEAK_RE = re.compile(r"</?[a-zA-Z_][\w:.=/-]*>")
# «2026-09-01 — » repeated at the start: the list renders `[id] dato — tekst` and Kåre copies
# that line into edit/write, so without this the stamp grew once per edit (P133 C0).
_LEADING_DATE_STAMP_RE = re.compile(r"^(?:\s*\d{4}-\d{2}-\d{2}\s*[—–-]\s*)+")
_KARE_NOTE_PROMPT_CHARS_DEFAULT = 300  # per-note cap in the prompt block (P133 C0)
_KARE_WRITE_MAX_CHARS_DEFAULT = 600  # a follow-up, not a diary (P133 D13)
_KARE_TITLE_CHARS = 80


def _strip_tool_syntax_leak(text: str) -> str:
    """Strip stray tool-call tag fragments (e.g. `</p>`, `<parameter=context>`) that
    occasionally leak into free-text tool arguments during long tool-call chains
    (see BUG-log 2026-09-05) — notes are plain natural-language text and never
    legitimately contain literal tags, so this is safe to strip unconditionally."""
    if "<" not in text:
        return text
    cleaned = _TOOL_SYNTAX_LEAK_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _reminder_path(user_id: str) -> Path:
    return _LISTER_DIR / "brukere" / f"{user_id}_huske.json"


def _read(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write(path: Path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _today() -> str:
    return today_local()


# ─────────────────────────────────────────────
# HANDLELISTE
# ─────────────────────────────────────────────


def shopping_add(
    text: str, amount: str = "", unit: str = "", added_by: str = "", lang: str = "nb"
) -> str:
    if not text.strip():
        return t("list_empty_text", lang)
    text = _strip_tool_syntax_leak(text.strip())
    items = _read(_SHOPPING_PATH)
    item = {
        "id": _new_id(),
        "tekst": text,
        "dato": _today(),
        "kjøpt": False,
    }
    if amount.strip():
        item["mengde"] = amount.strip()
    if unit.strip():
        item["enhet"] = unit.strip()
    if added_by.strip():
        item["lagt_til_av"] = added_by.strip()
    items.append(item)
    _write(_SHOPPING_PATH, items)
    amount_str = f" ({amount} {unit})".rstrip() if amount else ""
    return t("list_added", lang, text=text, amount=amount_str, id=item["id"])


def shopping_read(only_unbought: bool = True, lang: str = "nb") -> str:
    items = _read(_SHOPPING_PATH)
    if not items:
        return t("list_shopping_empty", lang)
    shown = [i for i in items if not i.get("kjøpt")] if only_unbought else items
    if not shown:
        return t("list_all_bought", lang)
    lines = [t("list_shopping_title", lang, count=len(shown)) + "\n"]
    for i in shown:
        amount = f" — {i['mengde']} {i.get('enhet', '')}".rstrip() if i.get("mengde") else ""
        by = t("list_added_by", lang, name=i["lagt_til_av"]) if i.get("lagt_til_av") else ""
        lines.append(f"  [{i['id']}] {i['tekst']}{amount}{by}")
    return "\n".join(lines)


def shopping_mark_bought(item_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    items = _read(_SHOPPING_PATH)
    for item in items:
        if item.get("id") == item_id.strip():
            item["kjøpt"] = True
            _write(_SHOPPING_PATH, items)
            return t("list_marked_bought", lang, text=item["tekst"])
    return t("list_item_not_found", lang, id=item_id)


def shopping_delete(item_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    items = _read(_SHOPPING_PATH)
    kept = [i for i in items if i.get("id") != item_id.strip()]
    if len(kept) == len(items):
        return t("list_item_not_found", lang, id=item_id)
    _write(_SHOPPING_PATH, kept)
    return t("list_item_deleted", lang)


def shopping_clear_bought(lang: str = "nb") -> str:
    items = _read(_SHOPPING_PATH)
    kept = [i for i in items if not i.get("kjøpt")]
    count = len(items) - len(kept)
    if count == 0:
        return t("list_no_bought_to_clear", lang)
    _write(_SHOPPING_PATH, kept)
    return t("list_cleared_bought", lang, count=count)


def shopping_clear(lang: str = "nb") -> str:
    count = len(_read(_SHOPPING_PATH))
    _write(_SHOPPING_PATH, [])
    return t("list_cleared_all", lang, count=count)


# ─────────────────────────────────────────────
# BRUKER-HUSKELISTE
# ─────────────────────────────────────────────


def reminder_add(text: str, user_id: str, remind_on_login: bool = False, lang: str = "nb") -> str:
    if not text.strip():
        return t("list_empty_text", lang)
    if not user_id or user_id == "global":
        return t("list_no_user", lang)
    text = _strip_tool_syntax_leak(text.strip())
    path = _reminder_path(user_id)
    items = _read(path)
    item = {
        "id": _new_id(),
        "tekst": text,
        "dato": _today(),
        "ferdig": False,
        "påminn_ved_login": remind_on_login,
    }
    items.append(item)
    _write(path, items)
    remind_str = t("list_remind_on_login", lang) if remind_on_login else ""
    return t("list_reminder_added", lang, text=text, remind=remind_str, id=item["id"])


def reminder_read(user_id: str, only_active: bool = True, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("list_no_user", lang)
    items = _read(_reminder_path(user_id))
    if not items:
        return t("list_reminder_empty", lang)
    shown = [i for i in items if not i.get("ferdig")] if only_active else items
    if not shown:
        return t("list_reminder_all_done", lang)
    lines = [t("list_reminder_title", lang, count=len(shown)) + "\n"]
    for i in shown:
        remind = " ★" if i.get("påminn_ved_login") else ""
        lines.append(f"  [{i['id']}] {i['dato']} — {i['tekst']}{remind}")
    return "\n".join(lines)


def reminder_done(item_id: str, user_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    if not user_id or user_id == "global":
        return t("list_no_user", lang)
    path = _reminder_path(user_id)
    items = _read(path)
    for item in items:
        if item.get("id") == item_id.strip():
            item["ferdig"] = True
            item["påminn_ved_login"] = False
            _write(path, items)
            return t("list_reminder_done", lang, text=item["tekst"])
    return t("list_reminder_not_found", lang, id=item_id)


def reminder_delete(item_id: str, user_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    if not user_id or user_id == "global":
        return t("list_no_user", lang)
    path = _reminder_path(user_id)
    items = _read(path)
    kept = [i for i in items if i.get("id") != item_id.strip()]
    if len(kept) == len(items):
        return t("list_reminder_not_found", lang, id=item_id)
    _write(path, kept)
    return t("list_reminder_deleted", lang)


def reminder_clear(user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("list_no_user", lang)
    path = _reminder_path(user_id)
    count = len(_read(path))
    _write(path, [])
    return t("list_reminder_cleared", lang, count=count)


def reminder_login_reminders(user_id: str) -> list[str]:
    """Texts of every entry flagged påminn_ved_login=True — used by the login flow."""
    if not user_id or user_id == "global":
        return []
    items = _read(_reminder_path(user_id))
    return [i["tekst"] for i in items if i.get("påminn_ved_login") and not i.get("ferdig")]


# ─────────────────────────────────────────────
# KÅRE'S OWN REMINDER LIST
# ─────────────────────────────────────────────

# Dedup-on-write for Kåre's list: how many of the most recent entries (write order, not the
# pin-sorted view) are checked for a near-duplicate, and the similarity threshold (difflib
# SequenceMatcher ratio) for counting as one. Tuned to catch Jang's near-identical repeated
# observations without blocking related-but-different notes.
_KARE_DEDUP_WINDOW = 10
_KARE_DEDUP_THRESHOLD = 0.75


def _kare_settings() -> dict:
    return (_get_settings() or {}).get("kare_reminders", {}) or {}


def _strip_date_stamp(text: str) -> str:
    return _LEADING_DATE_STAMP_RE.sub("", text or "").strip()


def _note_prompt_chars() -> int:
    try:
        n = int(_kare_settings().get("note_prompt_chars", _KARE_NOTE_PROMPT_CHARS_DEFAULT))
        return n if n > 0 else _KARE_NOTE_PROMPT_CHARS_DEFAULT
    except (TypeError, ValueError):
        return _KARE_NOTE_PROMPT_CHARS_DEFAULT


def _write_max_chars() -> int:
    try:
        n = int(_kare_settings().get("write_max_chars", _KARE_WRITE_MAX_CHARS_DEFAULT))
        return n if n > 0 else _KARE_WRITE_MAX_CHARS_DEFAULT
    except (TypeError, ValueError):
        return _KARE_WRITE_MAX_CHARS_DEFAULT


def _too_long(text: str, lang: str) -> str | None:
    """Refusal text when a kare note is longer than the cap — points to observations."""
    limit = _write_max_chars()
    if len(text) > limit:
        return t("list_kare_too_long", lang, max=limit, n=len(text))
    return None


def note_title(item: dict) -> str:
    """The note's title, or its first words when it has none (older notes)."""
    title = (item.get("tittel") or "").strip()
    if title:
        return title
    return _clip(_strip_date_stamp(item.get("tekst", "")), 60)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _find_near_duplicate(items: list, text: str) -> dict | None:
    """Look for a near-identical entry among the _KARE_DEDUP_WINDOW most recently written
    (physical array order). Returns the dict itself (same reference as in items) so the caller
    can mutate it in place, or None."""
    text_l = text.lower()
    for item in items[-_KARE_DEDUP_WINDOW:]:
        ratio = difflib.SequenceMatcher(None, text_l, item.get("tekst", "").lower()).ratio()
        if ratio >= _KARE_DEDUP_THRESHOLD:
            return item
    return None


def _evict_to_cap(items: list, max_entries: int) -> list:
    """FIFO-evict the oldest **unpinned** entries first until the list is within the cap.
    Pinned entries are never evicted automatically."""
    if len(items) <= max_entries:
        return items
    pinned_items = [i for i in items if i.get("pinned")]
    unpinned_items = [i for i in items if not i.get("pinned")]
    room_for_unpinned = max(0, max_entries - len(pinned_items))
    # Keep the newest unpinned entries (cut from the start = oldest first, chronological write order)
    unpinned_items = unpinned_items[-room_for_unpinned:] if room_for_unpinned else []
    # Restore the original relative order (pinned-first sorting happens on read anyway)
    keep_ids = {i["id"] for i in pinned_items} | {i["id"] for i in unpinned_items}
    return [i for i in items if i["id"] in keep_ids]


def _sort_pinned_first(items: list) -> list:
    """Pinned entries first (in their existing relative order), then the rest chronologically.
    Stable sort — nothing is moved on disk."""
    return sorted(items, key=lambda i: not i.get("pinned", False))


def kare_note_add(text: str, context: str = "", lang: str = "nb", title: str = "") -> str:
    if not text.strip():
        return t("list_empty_text", lang)
    text = _strip_date_stamp(_strip_tool_syntax_leak(text.strip()))
    if not text:
        return t("list_empty_text", lang)
    refusal = _too_long(text, lang)
    if refusal:
        return refusal
    title = _strip_tool_syntax_leak((title or "").strip())[:_KARE_TITLE_CHARS]
    context = _strip_tool_syntax_leak(context.strip()) if context.strip() else context
    items = _read(_KARE_PATH)

    duplicate = _find_near_duplicate(items, text)
    if duplicate is not None:
        duplicate["dato"] = _today()
        if context.strip():
            duplicate["kontekst"] = context.strip()
        _write(_KARE_PATH, items)
        return t("list_kare_refreshed", lang, text=duplicate["tekst"], id=duplicate["id"])

    item = {
        "id": _new_id(),
        "tekst": text,
        "dato": _today(),
        "pinned": False,
    }
    if title:
        item["tittel"] = title
    if context.strip():
        item["kontekst"] = context.strip()
    items.append(item)

    max_entries = int(_kare_settings().get("max_entries", 0) or 0)
    if max_entries:
        items = _evict_to_cap(items, max_entries)

    _write(_KARE_PATH, items)
    context_str = t("list_kare_context_suffix", lang, context=context) if context else ""
    return t("list_kare_added", lang, text=text, context=context_str, id=item["id"])


_READ_FULL_MAX = 15  # full-text entries per read (P133 C5 support)


def kare_note_read(
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    lang: str = "nb",
    note_ids: list[str] | None = None,
) -> str:
    """Read Kåre's list. Without a filter: a compact **index** of every entry (id, date, pin,
    title) — the whole list fits, so related notes can be seen together before acting
    (P133 C5: reading 100 notes in full text drowned the context). With search, a date range
    or note_ids: full text of the matches, at most _READ_FULL_MAX per read.
    search: case-insensitive substring match on text + context + title.
    date_from/date_to: YYYY-MM-DD, inclusive bounds."""
    items = _sort_pinned_first(_read(_KARE_PATH))
    total = len(items)
    wanted = {str(n).strip() for n in (note_ids or []) if str(n).strip()}
    filtered = bool(search.strip() or date_from.strip() or date_to.strip() or wanted)
    if not filtered:
        if not items:
            return t("list_kare_empty", lang)
        lines = [t("list_kare_index_title", lang, count=total)]
        for i in items:
            pin = " 📌" if i.get("pinned") else ""
            lines.append(f"  [{i['id']}]{pin} {i.get('dato', '')} — {_clip(note_title(i), 80)}")
        return "\n".join(lines)
    if wanted:
        items = [i for i in items if i.get("id") in wanted]

    if search.strip():
        q = search.strip().lower()
        items = [
            i
            for i in items
            if q in i.get("tekst", "").lower()
            or q in i.get("kontekst", "").lower()
            or q in i.get("tittel", "").lower()
        ]
    if date_from.strip():
        items = [i for i in items if i.get("dato", "") >= date_from.strip()]
    if date_to.strip():
        items = [i for i in items if i.get("dato", "") <= date_to.strip()]

    if not items:
        return t("list_kare_search_none" if filtered else "list_kare_empty", lang)
    title = (
        t("list_kare_search_title", lang, shown=len(items), total=total)
        if filtered
        else t("list_kare_title", lang, count=len(items))
    )
    shown = items[:_READ_FULL_MAX]
    lines = [f"{title}:\n"]
    for i in shown:
        context = f" → {i['kontekst']}" if i.get("kontekst") else ""
        pin = " 📌" if i.get("pinned") else ""
        head = f"{i['tittel']}: " if i.get("tittel") else ""
        lines.append(f"  [{i['id']}] {i['dato']} — {head}{_strip_date_stamp(i['tekst'])}{context}{pin}")
    if len(items) > len(shown):
        lines.append(t("list_kare_read_more", lang, n=len(items) - len(shown)))
    return "\n".join(lines)


def kare_note_get(item_id: str) -> dict | None:
    """One entry by id (for move), or None."""
    return next((i for i in _read(_KARE_PATH) if i.get("id") == (item_id or "").strip()), None)


def kare_note_pin(item_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    items = _read(_KARE_PATH)
    for item in items:
        if item.get("id") == item_id.strip():
            item["pinned"] = True
            _write(_KARE_PATH, items)
            return t("list_kare_pinned", lang, text=item["tekst"])
    return t("list_item_not_found", lang, id=item_id)


def kare_note_unpin(item_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    items = _read(_KARE_PATH)
    for item in items:
        if item.get("id") == item_id.strip():
            item["pinned"] = False
            _write(_KARE_PATH, items)
            return t("list_kare_unpinned", lang, text=item["tekst"])
    return t("list_item_not_found", lang, id=item_id)


def kare_note_done(item_id: str, lang: str = "nb") -> str:
    if not item_id.strip():
        return t("list_missing_id", lang)
    items = _read(_KARE_PATH)
    kept = [i for i in items if i.get("id") != item_id.strip()]
    if len(kept) == len(items):
        return t("list_item_not_found", lang, id=item_id)
    _write(_KARE_PATH, kept)
    return t("list_kare_removed", lang)


def kare_note_delete(item_id: str, lang: str = "nb") -> str:
    return kare_note_done(item_id, lang=lang)


def kare_note_edit(item_id: str, text: str, lang: str = "nb", title: str = "") -> str:
    """Replace the text of one entry in place — keeps its id, date and pin. Without this,
    Kåre tried a non-existent 'edit' action and leaked raw call fragments into notes."""
    if not item_id.strip():
        return t("list_missing_id", lang)
    text = _strip_date_stamp(_strip_tool_syntax_leak(text.strip()))
    if not text:
        return t("list_empty_text", lang)
    refusal = _too_long(text, lang)
    if refusal:
        return refusal
    title = _strip_tool_syntax_leak((title or "").strip())[:_KARE_TITLE_CHARS]
    items = _read(_KARE_PATH)
    for item in items:
        if item.get("id") == item_id.strip():
            if title:
                item["tittel"] = title
            item["tekst"] = text
            item["endret"] = _today()
            _write(_KARE_PATH, items)
            return t("list_kare_edited", lang, text=text, id=item["id"])
    return t("list_item_not_found", lang, id=item_id)


def kare_note_clear(lang: str = "nb") -> str:
    count = len(_read(_KARE_PATH))
    _write(_KARE_PATH, [])
    return t("list_kare_cleared", lang, count=count)


def kare_notes_for_prompt(max_chars: int | None = None, lang: str = "nb") -> str:
    """Compact text for the system prompt; empty string when the list is empty.
    Pinned entries first (see _sort_pinned_first). When max_chars is set and the list exceeds
    it, it is cut at an entry boundary (never mid-line) and a marker line with the real
    shown/total count goes right under the title, so Kåre knows there is more than shown.
    Each entry is clipped to `note_prompt_chars` (P133 C0) so one long note can never take the
    whole block; the full text is one `note read` away."""
    items = _sort_pinned_first(_read(_KARE_PATH))
    if not items:
        return ""
    total = len(items)
    per_note = _note_prompt_chars()
    entry_lines = []
    for i in items:
        context = f" → {i['kontekst']}" if i.get("kontekst") else ""
        pin = " 📌" if i.get("pinned") else ""
        body = _clip(f"{_strip_date_stamp(i.get('tekst', ''))}{context}", per_note)
        entry_lines.append(f"- [{i['id']}] {i['dato']} — {body}{pin}")

    if max_chars is None:
        shown_lines = entry_lines
    else:
        shown_lines = []
        used = len(t("list_kare_prompt_title", lang))
        for line in entry_lines:
            used += len(line) + 1
            if used > max_chars and shown_lines:
                break
            shown_lines.append(line)

    header = [t("list_kare_prompt_title", lang)]
    if len(shown_lines) < total:
        header.append(t("list_kare_injection_capped", lang, shown=len(shown_lines), total=total))
    return "\n".join(header + shown_lines)
