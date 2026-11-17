"""T32b job 2 — keep household names out of web queries that unattended jobs send to the internet.

The 2026-11-12 reflection meeting sent «<name> Kåre refleksjonsmøte lukkepunkt brytende tidslinje» to the web-search
provider. A night job has no user who chose to share a name, so names of household members, known guests and the
assistant's own name are removed before the query leaves the house. Names come from the user store, the face config and
settings (CLAUDE.md rule 13 — never hardcoded). A query that is empty or pointless after scrubbing is not sent at all.
Only the rid and the number of removed names are logged — never the names.
"""

import logging
import re

from kaare_core.config import get_settings
from kaare_core.frigate_user import faces as _faces
from kaare_core.users import store as _store

logger = logging.getLogger(__name__)

MIN_TOKEN_LEN = 2
MIN_KEPT_WORDS = 2  # fewer meaningful words than this after scrubbing → the query is not sent
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_SPLIT_RE = re.compile(r"[\s_\-.]+")
NIGHT_RID_PREFIXES = ("rid-meet-", "rid-refl-")


def _tokens(name: str) -> set[str]:
    return {p for p in _SPLIT_RE.split(str(name or "").strip()) if len(p) >= MIN_TOKEN_LEN}


def household_names() -> set[str]:
    """Every name token KTSB knows for people in this house (users, their face names, configured faces incl. guests)
    and the assistant's own name. Each source is optional — a failing source never blocks the scrub of the others."""
    names: set[str] = set()
    try:
        for u in _store.list_users():
            for value in (u.get("username"), u.get("display_name"), *(u.get("frigate_faces") or [])):
                names |= _tokens(value)
    except Exception as e:
        logger.warning("[query_scrub] user store unavailable: %s", e)
    try:
        for face in _faces.configured_faces():
            names |= _tokens(face)
    except Exception as e:
        logger.warning("[query_scrub] face config unavailable: %s", e)
    try:
        names |= _tokens((get_settings() or {}).get("assistant_name") or "")
    except Exception as e:
        logger.warning("[query_scrub] settings unavailable: %s", e)
    return names


def scrub(query: str, names: set[str]) -> tuple[str, int]:
    """Remove every name token (whole word, any case, also genitive «Stians»/«Kåre's»). Returns (query, removed)."""
    out, removed = str(query or ""), 0
    for name in sorted(names, key=len, reverse=True):
        rx = re.compile(r"(?<!\w)" + re.escape(name) + r"(?:'?s)?(?!\w)", re.IGNORECASE | re.UNICODE)
        out, n = rx.subn(" ", out)
        removed += n
    return re.sub(r"\s+", " ", out).strip(), removed


def outgoing_query(query: str, rid: str = "") -> str | None:
    """The query to send, or None when nothing meaningful is left after removing household names."""
    cleaned, removed = scrub(query, household_names())
    if removed:
        logger.info("[query_scrub] rid=%s removed %d name(s) from an outgoing web query", rid or "-", removed)
    if len(_WORD_RE.findall(cleaned)) < MIN_KEPT_WORDS:
        if removed:
            logger.info("[query_scrub] rid=%s query not sent — nothing meaningful left", rid or "-")
        return None
    return cleaned


def is_night_rid(rid: str) -> bool:
    return str(rid or "").startswith(NIGHT_RID_PREFIXES)
