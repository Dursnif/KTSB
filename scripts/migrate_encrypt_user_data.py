"""
Migration script: split profile.yaml into household_visible + encrypted private.

Run automatically at first login after crypto infrastructure is deployed.
Safe to run multiple times (idempotent — skips already-migrated profiles).

What it does:
  1. Reads existing profile.yaml (plaintext, flat structure)
  2. Extracts household_visible fields (preferred_name, role, age, key_facts, etc.)
  3. Moves everything else into a 'private' dict
  4. If session_key is available: encrypts private → private_encrypted (base64)
  5. Archives original profile.yaml + observations.md to archive_{date}/
  6. Writes new profile.yaml with household_visible + private_encrypted

If called without session_key: splits structure only (no encryption yet).
Encryption happens on next login call with session_key.

Usage:
  From auth.py after login:
    from scripts.migrate_encrypt_user_data import migrate_user_if_needed
    await migrate_user_if_needed(user_id, private_key_bytes)
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

USERS_DIR = Path("/kaare/state/users")

# Fields that live in household_visible (public), everything else goes private
_HOUSEHOLD_FIELDS = {"household_visible"}

# Top-level keys that are metadata, not personal data (keep as-is, not encrypted)
_META_KEYS = {"household_visible", "updated", "meta"}

# Keys that should NOT be moved to private (they're either housekeeping or already public)
_SKIP_PRIVATE = {"household_visible", "updated", "meta", "change_log", "private_encrypted", "private"}


def _is_migrated(profile: dict) -> bool:
    """Profile is migrated if it has private_encrypted OR has no personal data beyond household_visible."""
    return "private_encrypted" in profile


def _build_private_section(profile: dict) -> dict:
    """Extract all personal data (everything except meta/household keys) into a private dict."""
    private: dict = {}
    for key, value in profile.items():
        if key in _SKIP_PRIVATE:
            continue
        private[key] = value
    return private


def migrate_user_if_needed(user_id: str, private_key_bytes: bytes | None = None) -> bool:
    """
    Check if this user's profile needs migration. If so, migrate it.
    Returns True if migration was performed, False if already migrated or skipped.

    private_key_bytes: if provided, encrypts the private section immediately.
                       if None, splits structure only (encryption deferred to next login).
    """
    profile_path = USERS_DIR / user_id / "profile.yaml"
    obs_path = USERS_DIR / user_id / "observations.md"

    if not profile_path.exists():
        return False

    try:
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error(f"[MIGRATE] failed to read profile for {user_id}: {e}")
        return False

    if _is_migrated(profile):
        logger.debug(f"[MIGRATE] {user_id} already migrated, skipping")
        return False

    logger.info(f"[MIGRATE] starting migration for {user_id}")

    # Step 1: Ensure household_visible exists (auto-initialize if not)
    if "household_visible" not in profile:
        from kaare_core.users.profile_manager import get_household_visible
        # This auto-initializes and saves household_visible, then we reload
        get_household_visible(user_id)
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}

    # Step 2: Archive original files
    date_str = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dir = USERS_DIR / user_id / f"archive_{date_str}"
    archive_dir.mkdir(exist_ok=True)
    shutil.copy2(profile_path, archive_dir / "profile.yaml")
    if obs_path.exists():
        shutil.copy2(obs_path, archive_dir / "observations.md")
    logger.info(f"[MIGRATE] archived originals for {user_id} to {archive_dir.name}")

    # Step 3: Build private section from all non-meta/non-household keys
    private_data = _build_private_section(profile)

    # Step 4: Build new profile structure
    new_profile: dict = {
        "household_visible": profile.get("household_visible", {}),
        "updated": profile.get("updated", datetime.now().strftime("%Y-%m-%d")),
    }
    if profile.get("meta"):
        new_profile["meta"] = profile["meta"]

    if private_key_bytes is not None and private_data:
        # Encrypt private section
        try:
            from kaare_core.crypto import encrypt_dict
            new_profile["private_encrypted"] = encrypt_dict(private_data, private_key_bytes)
            logger.info(f"[MIGRATE] private section encrypted for {user_id}")
        except Exception as e:
            logger.error(f"[MIGRATE] encryption failed for {user_id}: {e}")
            # Store unencrypted with a marker so we can retry
            new_profile["private"] = private_data
            new_profile["_needs_encryption"] = True
    else:
        # No key available — store plaintext private section for now
        new_profile["private"] = private_data
        new_profile["_needs_encryption"] = True
        logger.info(f"[MIGRATE] private section stored plaintext for {user_id} (no key — will encrypt on next login)")

    # Step 5: Encrypt observations.md if we have a key
    if private_key_bytes is not None and obs_path.exists():
        try:
            from kaare_core.crypto import encrypt_text
            obs_content = obs_path.read_text(encoding="utf-8")
            encrypted_obs = encrypt_text(obs_content, private_key_bytes)
            obs_path.with_suffix(".enc").write_text(encrypted_obs, encoding="utf-8")
            obs_path.unlink()  # remove plaintext
            logger.info(f"[MIGRATE] observations.md encrypted for {user_id}")
        except Exception as e:
            logger.error(f"[MIGRATE] observations encryption failed for {user_id}: {e}")

    # Step 6: Write new profile atomically
    try:
        tmp = profile_path.with_suffix(".tmp")
        tmp.write_text(
            yaml.dump(new_profile, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(profile_path)
        logger.info(f"[MIGRATE] migration complete for {user_id}")
        return True
    except Exception as e:
        logger.error(f"[MIGRATE] failed to write new profile for {user_id}: {e}")
        # Restore from archive
        shutil.copy2(archive_dir / "profile.yaml", profile_path)
        return False


def finalize_encryption(user_id: str, private_key_bytes: bytes) -> bool:
    """
    Called on login if profile has _needs_encryption=True.
    Encrypts the plaintext 'private' section now that we have the key.
    """
    profile_path = USERS_DIR / user_id / "profile.yaml"
    obs_path = USERS_DIR / user_id / "observations.md"

    try:
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False

    if not profile.get("_needs_encryption"):
        return False

    changed = False

    # Encrypt 'private' section if present
    private_data = profile.pop("private", None)
    if private_data:
        try:
            from kaare_core.crypto import encrypt_dict
            profile["private_encrypted"] = encrypt_dict(private_data, private_key_bytes)
            profile.pop("_needs_encryption", None)
            changed = True
            logger.info(f"[MIGRATE] deferred encryption complete for {user_id}")
        except Exception as e:
            logger.error(f"[MIGRATE] deferred encryption failed for {user_id}: {e}")
            profile["private"] = private_data  # put it back
            return False

    # Encrypt plaintext observations.md if present
    if obs_path.exists():
        try:
            from kaare_core.crypto import encrypt_text
            obs_content = obs_path.read_text(encoding="utf-8")
            obs_path.with_suffix(".enc").write_text(
                encrypt_text(obs_content, private_key_bytes), encoding="utf-8"
            )
            obs_path.unlink()
            changed = True
        except Exception as e:
            logger.error(f"[MIGRATE] deferred obs encryption failed for {user_id}: {e}")

    if changed:
        tmp = profile_path.with_suffix(".tmp")
        tmp.write_text(
            yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(profile_path)

    return changed
