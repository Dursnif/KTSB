"""
Read/write helpers for state/household_state.yaml.
Household-level state (home/away mode). Never per-user.
"""

import os
import yaml
from datetime import datetime, timezone
from pathlib import Path

_STATE_PATH = Path("/kaare/state/household_state.yaml")

_DEFAULTS = {
    "mode": "home",
    "away_since": None,
    "expected_return": None,
    "away_reason": None,
    "members": {},
    "last_updated": None,
}


def read_household_state() -> dict:
    try:
        if _STATE_PATH.exists():
            data = yaml.safe_load(_STATE_PATH.read_text(encoding="utf-8")) or {}
            return {**_DEFAULTS, **data}
    except Exception:
        pass
    return dict(_DEFAULTS)


def write_household_state(data: dict) -> None:
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    os.replace(tmp, _STATE_PATH)


def set_away(reason: str | None = None, expected_return: str | None = None) -> None:
    state = read_household_state()
    state["mode"] = "away"
    state["away_since"] = datetime.now(timezone.utc).isoformat()
    state["away_reason"] = reason or None
    state["expected_return"] = expected_return or None
    write_household_state(state)


def set_home() -> None:
    state = read_household_state()
    state["mode"] = "home"
    state["away_since"] = None
    state["expected_return"] = None
    state["away_reason"] = None
    write_household_state(state)


def is_away() -> bool:
    return read_household_state().get("mode") == "away"


def household_mode_block() -> str:
    """Return a compact system prompt line when household is away. Empty string when home."""
    state = read_household_state()
    if state.get("mode") != "away":
        return ""
    parts = ["**Husstand: bortreise.**"]
    if state.get("away_reason"):
        parts.append(state["away_reason"] + ".")
    if state.get("expected_return"):
        parts.append(f"Forventet hjemkomst: {state['expected_return']}.")
    return " ".join(parts)
