"""Who may shape Kåre through conversation — the shared gate behind the GUI's learning-influence
settings (Settings → Assistant settings: «Who can shape Kåre's self-image?» and the automation
reflex contributors). P120.

Modes per section in settings.yaml: all / selected (allowed_users) / admin_only.
- Kåre's own processes («kare») are always allowed — they are not a person steering him.
- The system identities (global_user, unknown_user) and the shared scope id «global» are
  allowed only when picked explicitly in allowed_users — never through «all» or «admin_only»,
  because an unidentified voice must not shape Kåre unless the admin chose that.
- Any error denies (fail closed).
"""

import logging
from pathlib import Path

import yaml

from kaare_core.users.store import ANONYMOUS_IDS, get_user

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def contributor_allowed(section: str, default_mode: str, user_id: str) -> bool:
    if user_id == "kare":
        return True
    try:
        settings = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        cfg = settings.get(section, {}) or {}
        mode = cfg.get("contributors", default_mode)
        allowed_users = cfg.get("allowed_users", []) or []
        if user_id in ANONYMOUS_IDS:
            return user_id in allowed_users
        if mode == "all":
            return True
        if mode == "admin_only":
            u = get_user(user_id)
            return bool(u and u.get("role") == "admin")
        if mode == "selected":
            return user_id in allowed_users
        return False
    except Exception as e:
        logger.warning("contributor gate %s failed for %s — denied: %s", section, user_id, e)
        return False
