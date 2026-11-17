"""Size limits in MB for Kåre's own files (P150 phase 4) — settings.yaml `storage_limits.*`, set in the GUI under
Settings → Logs/Storage. 0 = off (no limit). A missing or broken value falls back to the light default (rule 24).
Read per call through config_store's mtime cache, so a GUI change is picked up without a restart (rule 25).
"""

from __future__ import annotations

from kaare_core import config, config_store

MB = 1_048_576
MAX_MB = 10_000_000
DEFAULTS_MB: dict[str, int] = {
    "think_cache_mb": 20,  # state/think_cache.jsonl
    "inner_thoughts_mb": 10,  # state/inner_thoughts.txt
    "stm_history_mb": 50,  # state/stm_history/
    "observations_mb": 5,  # per user: observations.md
    "raw_ha_events_mb": 700,  # logs/raw/ha_events/ (P150 phase 5) — whole day files, oldest first
    "raw_frigate_mqtt_mb": 300,  # logs/raw/frigate_mqtt/ — at least the normalcy window (28 days) is always kept
}


def get_mb(key: str) -> int:
    default = DEFAULTS_MB[key]
    try:
        data = config_store.read_yaml_cached(config.CONFIG_DIR / "settings.yaml", copy_result=False) or {}
        raw = (data.get("storage_limits") or {}).get(key, default)
        if isinstance(raw, bool):
            return default
        val = int(raw)
        return val if 0 <= val <= MAX_MB else default
    except Exception:
        return default


def get_bytes(key: str) -> int:
    return get_mb(key) * MB


def save_mb(key: str, mb: int) -> None:
    if key not in DEFAULTS_MB:
        raise ValueError(f"unknown limit {key}")
    if isinstance(mb, bool) or not 0 <= int(mb) <= MAX_MB:
        raise ValueError(f"{key} must be 0 (off) to {MAX_MB}")

    def _apply(data):
        lim = data.get("storage_limits")
        data["storage_limits"] = {**(lim if isinstance(lim, dict) else {}), key: int(mb)}

    config_store.update_yaml(config.CONFIG_DIR / "settings.yaml", _apply, "gui:storage_limits")
