"""
Shared utilities for Jing and Jang runners:
path constants, config loaders, think-block stripper, STM reader.
"""
import json
import re
import time
from datetime import datetime
from pathlib import Path

import yaml

KAARE_BASE = Path("/kaare")

SETTINGS_PATH = KAARE_BASE / "configs" / "settings.yaml"
SERVICES_PATH = KAARE_BASE / "configs" / "services.yaml"
JING_THOUGHTS = KAARE_BASE / "state" / "jing_thoughts.txt"
INNER_THOUGHTS = KAARE_BASE / "state" / "inner_thoughts.txt"
FACE_EVENTS = KAARE_BASE / "state" / "argus" / "face_events.txt"
DIGEST = KAARE_BASE / "state" / "argus" / "digest.txt"
STM_USERS_DIR = KAARE_BASE / "state" / "stm_users"
PERSONALITY = KAARE_BASE / "state" / "personality_self.md"
MODEL_CACHE_DIR = KAARE_BASE / "services" / "inner_voices" / "ov_cache"


def strip_think(text: str) -> str:
    """Remove <think>...</think> blocks, including unclosed ones at token limit."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
    return text.strip()


def load_behavior_config(name: str) -> dict:
    """Load jing or jang behavioral settings from settings.yaml."""
    try:
        return yaml.safe_load(SETTINGS_PATH.read_text()).get(name, {})
    except Exception:
        return {}


def load_service_config(name: str) -> dict:
    """Load jing or jang infrastructure config from services.yaml."""
    try:
        return yaml.safe_load(SERVICES_PATH.read_text()).get(name, {})
    except Exception:
        return {}


def read_stm_recent(cutoff: float) -> str:
    """Read recent dialog entries from all per-user STM snapshots."""
    if not STM_USERS_DIR.exists():
        return "(ingen)"
    recent: list[str] = []
    for snap_file in sorted(STM_USERS_DIR.glob("*.json")):
        try:
            data = json.loads(snap_file.read_text(errors="replace"))
        except Exception:
            continue
        for entry in data.get("dialog", []):
            ts_str = entry.get("ts", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
            except Exception:
                continue
            if ts >= cutoff:
                role = entry.get("role", "")
                text = (entry.get("text") or "").strip()
                if role in ("user", "assistant") and text:
                    recent.append(f"[{role}] {text[:200]}")
    return "\n".join(recent) if recent else "(ingen)"
