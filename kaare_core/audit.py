"""
Append-only audit log for sensitive operations.

Writes JSONL entries to logs/audit.log. Non-blocking — never crashes the caller.
Admin-readable via GET /api/audit/recent.

Logged events:
  camera_access     — camera snapshot or event list accessed
  admin_user_action — user created, deleted, or PIN reset by admin
  config_change     — sensitive config PUT (LLM config, tool permissions)
  developer_tools   — shell command executed via developer_tools
  rate_limited      — request blocked by rate limiter
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_LOG_PATH = Path("/kaare/logs/audit.log")
_log = logging.getLogger(__name__)


def audit_log(
    event_type: str,
    username: str,
    detail: str,
    request_ip: str = "",
) -> None:
    """Append a single audit event to logs/audit.log (JSONL format).

    Silently swallows all errors — audit logging must never crash the caller.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event_type,
        "user": username,
        "detail": detail,
        "ip": request_ip,
    }
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.warning("audit_log write failed: %s", exc)


def read_recent(limit: int = 200) -> list[dict]:
    """Return the last `limit` audit entries, newest-first."""
    try:
        lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except Exception as exc:
        _log.warning("audit_log read failed: %s", exc)
        return []
    entries = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break
    return entries
