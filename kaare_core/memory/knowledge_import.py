"""
One-time import of the pre-knowledge.db state files into knowledge_store (P116 B22).

Installs upgrading from v1.9.0 still have world.md / world_vars.json / personality_self.md /
notater.json, but since P105 Phase 3 Kåre only reads knowledge.db. Without this import an
upgraded install looks like Kåre forgot his world model, self-image and notes.

Mechanical, no LLM (Stian 2026-10-01): a domain is imported only when it has no rows at all
(active or retired), the legacy files are never modified, and a marker file stops it from
running twice. Duplicates in world.md are left for the weekly knowledge_reflection job, which
already re-validates and retires rows.
"""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from kaare_core.memory import knowledge_store as ks

logger = logging.getLogger(__name__)

_STATE = Path("/kaare/state")
_MARKER = _STATE / "memory" / "knowledge_import_done.json"
_SOURCE = "migration"
_DATED = re.compile(r"^\[?(\d{4}-\d{2}-\d{2})\]?")


def _domain_is_empty(domain: str) -> bool:
    ks._ensure_init()
    conn = ks._get_conn()
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM state_facts WHERE domain=?", (domain,)).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def _world_rows(text: str) -> list[dict]:
    """One row per line under each '## Section'; indented sub-bullets join the line above.
    Text before the first section (title, intro) is skipped."""
    rows: list[dict] = []
    section = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip()
            continue
        if not section or not stripped or stripped == "---" or stripped.startswith("#"):
            continue
        content = stripped[2:].strip() if stripped.startswith(("- ", "* ")) else stripped
        if raw[:1] in (" ", "\t") and rows and rows[-1]["section"] == section:
            rows[-1]["content"] += f"; {content}"
            continue
        rows.append({"domain": "world", "section": section, "content": content})
    return [r for r in rows if r["content"]]


def _world_var_rows(data: dict) -> list[dict]:
    rows = []
    for key, entry in (data or {}).items():
        entry = entry if isinstance(entry, dict) else {"verdi": entry}
        value = str(entry.get("verdi") or entry.get("value") or "").strip()
        if not key or not value:
            continue
        rows.append(
            {
                "domain": "world",
                "section": f"var::{key}",
                "content": json.dumps(
                    {
                        "value": value,
                        "description": str(
                            entry.get("beskrivelse") or entry.get("description") or ""
                        ),
                    }
                ),
                "valid_from": entry.get("satt") or None,
            }
        )
    return rows


def _personality_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- ["):
            continue
        content = re.sub(r"^(\[\d{4}-\d{2}-\d{2}\]) \1 ", r"\1 ", line[2:].strip())
        m = _DATED.match(content)
        rows.append(
            {
                "domain": "personality",
                "section": "",
                "content": content,
                "valid_from": m.group(1) if m else None,
            }
        )
    return rows


def _notes_rows(data: list) -> list[dict]:
    return [
        {
            "domain": "notes",
            "section": (n.get("kategori") or "diverse").strip().lower(),
            "content": n.get("tekst", ""),
            "valid_from": n.get("dato") or None,
        }
        for n in (data or [])
        if isinstance(n, dict) and n.get("tekst")
    ]


def _read(path: Path) -> str | None:
    if path.exists():
        return path.read_text(encoding="utf-8")
    if path.with_name(path.name + ".enc").exists():
        logger.warning(
            "knowledge_import: only an encrypted %s.enc exists — not imported", path.name
        )
    return None


def _collect() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"world": [], "personality": [], "notes": []}
    world = _read(_STATE / "world.md")
    if world:
        out["world"] += _world_rows(world)
    world_vars = _read(_STATE / "world_vars.json")
    if world_vars:
        out["world"] += _world_var_rows(json.loads(world_vars))
    personality = _read(_STATE / "personality_self.md")
    if personality:
        out["personality"] = _personality_rows(personality)
    notes = _read(_STATE / "notater.json")
    if notes:
        out["notes"] = _notes_rows(json.loads(notes))
    return out


def import_legacy_state() -> dict:
    """Run the import once. Returns {domain: rows_imported}; never raises."""
    result: dict[str, int] = {}
    try:
        if _MARKER.exists():
            return result
        rows_by_domain = _collect()
        for domain, rows in rows_by_domain.items():
            if not rows or not _domain_is_empty(domain):
                continue
            n = 0
            for r in rows:
                if (
                    ks.add_fact(
                        domain=domain,
                        section=r.get("section", ""),
                        content=r["content"],
                        source=_SOURCE,
                        importance=0.5,
                        valid_from=r.get("valid_from"),
                    )
                    is not None
                ):
                    n += 1
            result[domain] = n
        _MARKER.parent.mkdir(parents=True, exist_ok=True)
        _MARKER.write_text(json.dumps({"done": datetime.now(UTC).isoformat(), "imported": result}))
        if result:
            logger.info("knowledge_import: imported legacy state into knowledge.db: %s", result)
    except Exception as e:
        logger.warning("knowledge_import: failed, will retry on next start: %s", e)
    return result
