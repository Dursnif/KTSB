"""P151 phase 5: rendered cards for the chat (`/api/card/<id>`). Small HTML files under state/cards/; the oldest go
first when the folder passes its size limit (feedback_size_based_retention — size, never age)."""

import re
import secrets
from pathlib import Path

CARD_DIR = Path("/kaare/state/cards")
MAX_BYTES = 5 * 1024 * 1024  # ~1000 recipe cards; a card is a few KB
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def save(html: str, card_dir: Path = CARD_DIR, max_bytes: int = MAX_BYTES) -> str:
    card_dir.mkdir(parents=True, exist_ok=True)
    cid = "card_" + secrets.token_urlsafe(12)
    (card_dir / f"{cid}.html").write_text(html, encoding="utf-8")
    _trim(card_dir, max_bytes)
    return cid


def _trim(card_dir: Path, max_bytes: int) -> None:
    files = sorted(card_dir.glob("*.html"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    while files and total > max_bytes:
        p = files.pop(0)
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def path_of(cid: str, card_dir: Path = CARD_DIR) -> Path | None:
    if not _ID_RE.match(cid or ""):
        return None
    p = card_dir / f"{cid}.html"
    return p if p.is_file() else None
