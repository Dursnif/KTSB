"""Dev-slot settings (P118 PR2) — read from settings.yaml `dev_slot:`, with working defaults.

The defaults are the lightest tier that works (rule 24); the GUI exposes all of them (B5, B8).
"""

from __future__ import annotations

from kaare_core.config import get_settings

DEFAULTS = {
    "time_budget_minutes": 25,  # whole slot, all steps — the safety ceiling (B8-D4)
    "topics_per_meeting": 3,  # problems S3 develops per night, one per theme first (B8)
    "max_user_traces": 20,  # user traces reviewed per night (S2)
    "round_cap_per_topic": 4,  # Kåre's tool rounds per topic (S3)
}


def get_dev_slot_config() -> dict:
    """Merged view: settings.yaml `dev_slot:` over DEFAULTS; invalid values fall back."""
    return get_dev_slot_config_from(get_settings() or {})


def get_dev_slot_config_from(settings: dict) -> dict:
    """Same, from an already-loaded settings dict (the settings API reads the file fresh)."""
    raw = (settings or {}).get("dev_slot") or {}
    cfg = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        try:
            value = int(raw.get(key, default))
            cfg[key] = value if value >= 0 and (value > 0 or key == "max_user_traces") else default
        except (TypeError, ValueError):
            cfg[key] = default
    return cfg
