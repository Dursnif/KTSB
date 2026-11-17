# /kaare/kaare_core/tools/think_cache.py
"""
Rolling think-block cache.

Every LLM call that produces a <think> block is logged here.
Kåre can read the cache via the les_tankehistorikk tool to inspect
his own reasoning history and detect patterns or past uncertainty.
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml as _yaml

from kaare_core import log_buffer
from kaare_core.config import get_think_response_max_chars
from kaare_core.storage import limits, rotation


def _load_settings() -> dict:
    try:
        with open("/kaare/configs/settings.yaml", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:
        return {}


_settings = _load_settings()
_tc = _settings.get("think_cache", {})
_CACHE_PATH = Path(_tc.get("path", "/kaare/state/think_cache.jsonl"))
# P150: kept by size (Settings → Logs/Storage, storage_limits.think_cache_mb), not by a line count or days.
_READ_SCAN_MAX_BYTES = 32 * 1_048_576  # a history search scans at most this much from the end of the file (IO bound)

_write_count = 0
_trimmed_at = 0


def _extract_summary(think_text: str) -> str:
    """Return the last substantive paragraph as a short summary."""
    if not think_text:
        return ""
    paragraphs = [p.strip() for p in think_text.split("\n\n") if p.strip()]
    if not paragraphs:
        lines = [ln.strip() for ln in think_text.splitlines() if ln.strip()]
        return lines[-1][:200] if lines else ""
    last = paragraphs[-1]
    if len(last) < 60 and len(paragraphs) > 1:
        last = paragraphs[-2] + " " + last
    # Return up to first sentence break or truncate
    for sep in (". ", "! ", "? "):
        idx = last.find(sep)
        if idx > 40:
            return last[: idx + 1]
    return last[:200]


def extract_conclusion(think_text: str) -> str:
    """
    Extract the most conclusion-like part from a think block.
    Used as a fallback when the model produces only a think block and no response.
    Returns the last paragraph (or two if the last is very short), capped at 500 chars.
    """
    if not think_text:
        return ""
    paragraphs = [p.strip() for p in think_text.split("\n\n") if p.strip()]
    if not paragraphs:
        lines = [ln.strip() for ln in think_text.splitlines() if ln.strip()]
        return lines[-1][:500] if lines else think_text[:500]
    last = paragraphs[-1]
    if len(last) < 60 and len(paragraphs) > 1:
        last = paragraphs[-2] + "\n\n" + last
    if len(last) > 500:
        last = last[:500] + "…"
    return last


def log_think(
    *,
    think_text: str,
    response: str,
    role: str = "kare",
    model: str = "",
    prompt_preview: str = "",
    latency_ms: int = 0,
    recovered: bool = False,
    rid: str = "",
) -> None:
    """
    Append one think-block entry to the rolling cache.
    Fire-and-forget — never raises.
    """
    global _write_count
    if not think_text:
        return
    try:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "ts": datetime.now(UTC).isoformat(),
            "rid": rid,
            "role": role,
            "model": model,
            "prompt_preview": prompt_preview[:200],
            "think_text": think_text,
            "think_summary": _extract_summary(think_text),
            # Long replies are kept for measuring the model guard (P142); GUI-set length.
            "response": response[: get_think_response_max_chars()],
            "recovered": recovered,
            "latency_ms": latency_ms,
        }
        log_buffer.write(_CACHE_PATH, entry)
        _write_count += 1
    except Exception:
        pass


def _trim_after_flush() -> None:
    """Runs inside log_buffer's flush of this file: check the size once per 20 new entries (as before)."""
    global _trimmed_at
    if _write_count - _trimmed_at >= 20:
        _trimmed_at = _write_count
        _trim_cache()


def _limit_bytes() -> int:
    return limits.get_bytes("think_cache_mb")


def _trim_cache() -> None:
    """Over its size limit the oldest entries go first (0 = off). A stat when under it — no read."""
    try:
        rotation.trim_lines_to_bytes(_CACHE_PATH, _limit_bytes())
    except Exception:
        pass


def read_think_history(
    n: int = 10,
    search: str | None = None,
    role: str | None = None,
    recovered_only: bool = False,
) -> list[dict]:
    """
    Return recent think-block entries, newest first.

    n             – max results (capped at 50)
    search        – substring match against think_text + think_summary + prompt_preview
    role          – filter by role ("kare", "mechanic", etc.)
    recovered_only – only return entries where model produced no response (recovered=True)
    """
    n = min(n, 50)
    try:
        log_buffer.flush(_CACHE_PATH)  # include entries still in RAM (P131)
        if not _CACHE_PATH.exists():
            return []
        entries: list[dict] = []
        # Read from the end (newest first) — never the whole file (P150: it may hold years now)
        for line in rotation.iter_lines_reverse(_CACHE_PATH, max_bytes=_READ_SCAN_MAX_BYTES):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if role and entry.get("role") != role:
                continue
            if recovered_only and not entry.get("recovered"):
                continue
            if search:
                haystack = " ".join(
                    [
                        entry.get("think_text", ""),
                        entry.get("think_summary", ""),
                        entry.get("prompt_preview", ""),
                    ]
                ).lower()
                if search.lower() not in haystack:
                    continue
            entries.append(entry)
            if len(entries) >= n:
                break
        return entries
    except Exception:
        return []


def format_for_kare(entries: list[dict]) -> str:
    """Format think history entries as readable text for Kåre."""
    if not entries:
        return "Ingen think-historikk funnet."
    lines = [f"Fant {len(entries)} think-blokk(er) — nyeste først:\n"]
    for e in entries:
        ts = e.get("ts", "")[:16].replace("T", " ")
        summary = e.get("think_summary", "") or e.get("think_text", "")[:100]
        recovered = " ⚠️ [tomt svar]" if e.get("recovered") else ""
        latency = e.get("latency_ms", 0)
        lines.append(f"[{ts}] ({latency}ms){recovered}\n  {summary}")
    return "\n\n".join(lines)


log_buffer.on_flush(_CACHE_PATH, _trim_after_flush)
