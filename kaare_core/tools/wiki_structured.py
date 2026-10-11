"""
Structured wiki data — tables and lists extracted by the P110 pipeline (P111 Phase 4).

The prose in Qdrant carries a marker `[se liste: <label> #<ordinal>]` (en: `[see list: …]`)
where a table or list stood; the rows live in one SQLite table `wiki_collections` keyed by
`(article_id, ordinal)`. This module is the rigid, deterministic half of Miss Library:
marker → row lookup → text where EVERY row carries its table's name (Option B, decided by
Stian 2026-09-22), so two adjacent tables can never be read as one (P110 U15).

Always article-first: there is no search over table content on its own (data-model fact).
The database is a static file, opened read-only + immutable (no -wal/-shm next to it).
"""

import json
import re
import sqlite3
import threading

# `[se liste: Medaljer – Sommer-OL 2004 Athen #2]` / `[see list: … #3]`
MARKER_RE = re.compile(r"\[(?:se liste|see list): (.*?) #(\d+)\]")

# Kinds that are content for Kåre. Left out: references, layout, infobox_part (already in the
# prose since P110 U10), list_other (already written into the prose as text lines).
_TABLE_KINDS = {"data", "th_table", "succession", "other"}
_LIST_KINDS = {"list_data", "list_terms", "list_links"}
# list_links is content (e.g. "Deltakerland": 201 countries) EXCEPT under these sections
_NOISE_SECTIONS = {
    "eksterne lenker",
    "referanser",
    "se også",
    "kilder",
    "noter",
    "fotnoter",
    "external links",
    "references",
    "see also",
    "sources",
    "notes",
    "footnotes",
}

_conns: dict[str, sqlite3.Connection] = {}
_conns_lock = threading.Lock()


def _conn(db_path: str) -> sqlite3.Connection:
    with _conns_lock:
        c = _conns.get(db_path)
        if c is None:
            c = sqlite3.connect(
                f"file:{db_path}?mode=ro&immutable=1", uri=True, check_same_thread=False
            )
            _conns[db_path] = c
        return c


def parse_markers(text: str) -> list[tuple[str, int]]:
    """(label, ordinal) for every marker in the prose, in order, without duplicates."""
    seen: set[int] = set()
    out = []
    for label, ordinal in MARKER_RE.findall(text or ""):
        o = int(ordinal)
        if o not in seen:
            seen.add(o)
            out.append((label.strip(), o))
    return out


def strip_markers(text: str) -> str:
    """Replace markers by their bare label in brackets, e.g. «[Rekorder]» — used when the
    table itself is rendered separately, so the model doesn't see the raw marker syntax."""
    return MARKER_RE.sub(lambda m: f"[{m.group(1).strip()}]", text or "")


def _section(row: dict) -> list[str]:
    try:
        return [s for s in json.loads(row.get("section_path") or "[]") if s]
    except Exception:
        return []


def is_content(row: dict) -> bool:
    kind = row.get("kind", "")
    if kind in _TABLE_KINDS or kind in ("list_data", "list_terms"):
        return True
    if kind == "list_links":
        sec = _section(row)
        return not (sec and sec[0].strip().lower() in _NOISE_SECTIONS)
    return False


def fetch(db_path: str, article_id: int, ordinals: list[int] | None = None) -> dict[int, dict]:
    """Rows of one article (all, or only the given ordinals), content kinds only."""
    c = _conn(db_path)
    cols = (
        "ordinal",
        "article_title",
        "kind",
        "section_path",
        "caption",
        "columns",
        "rows",
        "n_rows",
        "n_cols",
    )
    sql = f"SELECT {', '.join(cols)} FROM wiki_collections WHERE article_id = ?"
    params: list = [int(article_id)]
    if ordinals:
        sql += f" AND ordinal IN ({','.join('?' * len(ordinals))})"
        params += [int(o) for o in ordinals]
    out = {}
    for r in c.execute(sql, params):
        row = dict(zip(cols, r, strict=False))
        if is_content(row):
            out[row["ordinal"]] = row
    return out


def label_of(row: dict, marker_label: str = "") -> str:
    return (
        marker_label
        or (row.get("caption") or "").strip()
        or (_section(row)[-1] if _section(row) else "")
        or f"#{row['ordinal']}"
    )


def _cell(v) -> str:
    return " ".join(str(v if v is not None else "").split())


def render_lines(row: dict, label: str) -> list[str]:
    """One line per row/item, each prefixed with the table's name."""
    try:
        columns = json.loads(row.get("columns") or "[]")
        rows = json.loads(row.get("rows") or "[]")
    except Exception:
        return []
    kind = row.get("kind", "")
    lines: list[str] = []
    if kind in ("list_data", "list_links"):
        for item in rows:
            level = item[0] if len(item) > 0 and isinstance(item[0], int) else 0
            text = _cell(item[1] if len(item) > 1 else "")
            if text:
                lines.append(f"{label} — {'  ' * level}{text}")
    elif kind == "list_terms":
        for item in rows:
            term = _cell(item[0] if len(item) > 0 else "")
            definition = _cell(item[1] if len(item) > 1 else "")
            if term or definition:
                lines.append(f"{label} — {term}: {definition}".rstrip(": "))
    else:
        generic = all(str(c).startswith("col_") for c in columns) if columns else True
        for r in rows:
            cells = []
            for i, v in enumerate(r):
                val = _cell(v)
                if not val:
                    continue
                name = "" if generic or i >= len(columns) else _cell(columns[i])
                cells.append(f"{name}: {val}" if name else val)
            if cells:
                lines.append(f"{label} — " + " | ".join(cells))
    return lines


def section_text(row: dict) -> str:
    return " › ".join(_section(row))
