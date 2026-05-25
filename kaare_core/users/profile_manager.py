"""
Brukerprofil-manager for Kåre.
Per-bruker YAML-profil + rullerende observasjonslogg (maks 90 dager).
Lagres i /kaare/state/users/{user_id}/

Privacy model:
  household_visible: — small set of hardcoded fields, always accessible to Kåre
  private/private_encrypted: — everything else, encrypted with user's PIN key

Vault system:
  When a user is offline (no active session key), agents write SealedBox-encrypted
  vault files (vault_*.bin) to the user's directory. On next login, process_vault_files()
  decrypts and applies them, then deletes the .bin files.
"""
import base64
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

USERS_DIR    = Path("/kaare/state/users")
OBS_MAX_DAYS = 90

# Accounts that are system-only — never appear in household block
SYSTEM_ACCOUNTS: set[str] = {"admin", "kare"}

# Hardcoded schema for what is globally visible about each household member.
# Only these fields can ever end up in the household block.
HOUSEHOLD_VISIBLE_FIELDS: dict[str, Any] = {
    "preferred_name":  None,   # what to call them
    "role":            None,   # family | admin | guest
    "age":             None,   # integer or null
    "key_facts":       [],     # ["nøttallergi", "vegetarianer"]
    "current_context": None,   # "Studerer til eksamen i mai"
    "recent_updates":  [],     # ["Farget håret rosa 2026-05-10"]
}


def _user_dir(user_id: str) -> Path:
    d = USERS_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


_TEMPLATE_PATH = Path("/kaare/configs/profile_template.yaml")

def _empty_profile(user_id: str) -> dict:
    try:
        profile = yaml.safe_load(_TEMPLATE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        profile = {}
    profile["meta"] = {
        "user_id": user_id,
        "display_name": user_id,
        "role": None,
        "created": datetime.now().strftime("%Y-%m-%d"),
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "kare_summary": None,
    }
    return profile


_PROMPT_TOP_LABELS = {
    "preferred_name":             "Foretrukket navn",
    "language":                   "Språk",
    "personality_summary":        "Hvem er denne personen",
    "communication_style":        "Kommunikasjonsstil",
    "response_length_preference": "Svar-lengde",
    "formality_with_kare":        "Tone med Kåre",
    "proactivity_preference":     "Proaktivitet",
    "key_values":                 "Kjerneverdier",
    "topics_to_avoid":            "Ikke snakk om",
    "topics_to_flag":             "Alltid ta opp",
    "current_context":            "Nåværende kontekst",
    "curiosity":                  "Kåre lurer fortsatt på",
}


def _is_new_user(user_id: str, prompt_top: dict) -> bool:
    """True if Kåre has no meaningful knowledge about this user yet."""
    skip = {"language", "preferred_name"}
    filled = [
        k for k, v in prompt_top.items()
        if k not in skip and v is not None and v != [] and v != ""
    ]
    if filled:
        return False
    obs_path = _user_dir(user_id) / "observations.md"
    return not obs_path.exists() or obs_path.stat().st_size < 100


def get_profile_prompt_top(user_id: str) -> str:
    """Format prompt_top section of profile.yaml for injection into system prompt."""
    profile = load_profile(user_id)
    top = profile.get("prompt_top")
    if not isinstance(top, dict):
        top = {}

    new_user = _is_new_user(user_id, top)

    lines = []
    for key, label in _PROMPT_TOP_LABELS.items():
        val = top.get(key)
        if val is None or val == [] or val == "":
            continue
        if isinstance(val, list):
            lines.append(f"- **{label}:** {', '.join(str(v) for v in val)}")
        else:
            lines.append(f"- **{label}:** {val}")

    name = top.get("preferred_name") or user_id

    if not lines:
        if new_user:
            return (
                f"# Bruker: {name}\n"
                f"Du kjenner ikke denne personen ennå — du vet ingenting om hvem de er.\n"
                f"Møt dem med genuin nysgjerrighet. Det er helt naturlig å spørre hvem de er."
            )
        return ""

    return f"# Brukerprofil: {name}\n" + "\n".join(lines)


def init_profile(user_id: str, display_name: str) -> None:
    """Create profile.yaml on user creation with the admin-set display_name locked in meta."""
    path = _user_dir(user_id) / "profile.yaml"
    if path.exists():
        profile = load_profile(user_id)
    else:
        profile = _empty_profile(user_id)
    profile["meta"]["display_name"] = display_name
    save_profile(user_id, profile)


def get_display_name(user_id: str) -> str:
    """Return preferred_name (if set by user) or admin-set display_name, else user_id."""
    profile = load_profile(user_id)
    preferred = (profile.get("prompt_top") or {}).get("preferred_name")
    if preferred:
        return preferred
    return (profile.get("meta") or {}).get("display_name") or user_id


def load_profile(user_id: str) -> dict:
    path = _user_dir(user_id) / "profile.yaml"
    if not path.exists():
        return _empty_profile(user_id)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else _empty_profile(user_id)
    except Exception:
        return _empty_profile(user_id)


def save_profile(user_id: str, profile: dict) -> None:
    profile["updated"] = datetime.now().strftime("%Y-%m-%d")
    path = _user_dir(user_id) / "profile.yaml"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        yaml.dump(profile, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def update_profile_field(user_id: str, field: str, value: Any, reason: str) -> None:
    """Oppdater ett felt i profilen og logg endringen."""
    profile = load_profile(user_id)
    profile[field] = value
    profile.setdefault("change_log", []).append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "field": field,
        "reason": reason,
    })
    profile["change_log"] = profile["change_log"][-50:]
    save_profile(user_id, profile)


def add_observation(user_id: str, text: str) -> None:
    """Legg til en dagsbasert observasjon i observations.md, trim etterpå."""
    path = _user_dir(user_id) / "observations.md"
    date_str = datetime.now().strftime("%Y-%m-%d")
    entry = f"\n## {date_str}\n{text.strip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
    _trim_observations(user_id)


def update_nested_profile_field(user_id: str, section: str, field: str, value: str) -> str:
    """
    Write a specific section.field in profile.yaml.
    If the current field is a list, appends value as a new item.
    If the current field is null/string, replaces it.
    """
    profile = load_profile(user_id)
    sec = profile.get(section)
    if not isinstance(sec, dict):
        return f"Unknown section: '{section}'"
    current = sec.get(field)
    if isinstance(current, list):
        if value.strip():
            sec[field] = current + [value.strip()]
    else:
        sec[field] = value.strip() if value.strip() else None
    profile[section] = sec
    if isinstance(profile.get("meta"), dict):
        profile["meta"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    save_profile(user_id, profile)
    return "OK"


def read_profile_yaml_as_text(user_id: str) -> str:
    """Return profile.yaml content as readable text, skipping null/empty fields."""
    profile = load_profile(user_id)
    lines = []
    for section, content in profile.items():
        if section in ("change_log",):
            continue
        if isinstance(content, dict):
            filled = {k: v for k, v in content.items() if v is not None and v != [] and v != ""}
            if filled:
                lines.append(f"\n[{section}]")
                for k, v in filled.items():
                    lines.append(f"  {k}: {v}")
        elif content is not None and content != "":
            lines.append(f"{section}: {content}")
    return "\n".join(lines) if lines else "Ingen profildata registrert ennå."


def get_recent_observations(user_id: str, days: int = 14) -> str:
    """Returner observasjoner fra siste N dager."""
    path = _user_dir(user_id) / "observations.md"
    if not path.exists():
        return "Ingen observasjoner ennå."
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = path.read_text(encoding="utf-8").splitlines()
    result, include = [], False
    for line in lines:
        if line.startswith("## "):
            include = line[3:].strip()[:10] >= cutoff
        if include:
            result.append(line)
    return "\n".join(result).strip() if result else "Ingen nylige observasjoner."


def _trim_observations(user_id: str) -> None:
    """Fjern observasjoner eldre enn OBS_MAX_DAYS. Kalles automatisk etter add_observation."""
    path = _user_dir(user_id) / "observations.md"
    if not path.exists():
        return
    cutoff = (datetime.now() - timedelta(days=OBS_MAX_DAYS)).strftime("%Y-%m-%d")
    lines = path.read_text(encoding="utf-8").splitlines()
    result, include = [], False
    for line in lines:
        if line.startswith("## "):
            include = line[3:].strip()[:10] >= cutoff
        if include:
            result.append(line)
    path.write_text("\n".join(result) + "\n", encoding="utf-8")


# ── Vault system (agent-writes when user is offline) ─────────────────────────────

def write_vault_entry(user_id: str, payload: dict) -> bool:
    """
    Encrypt and write a vault entry for a user who is currently offline.
    Uses the user's public key (SealedBox) — no session key needed.
    Returns True if written, False if the user has no public key (not yet migrated).
    """
    try:
        from kaare_core.users.store import get_public_key_b64
        from kaare_core.crypto import seal
        pub_key_b64 = get_public_key_b64(user_id)
        if not pub_key_b64:
            return False
        pub_key_bytes = base64.b64decode(pub_key_b64)
        json_text = json.dumps(payload, ensure_ascii=False)
        encrypted = seal(json_text, pub_key_bytes)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        vault_path = _user_dir(user_id) / f"vault_{ts}.bin"
        vault_path.write_text(encrypted, encoding="utf-8")
        logger.info(f"[VAULT] wrote vault entry for {user_id}: type={payload.get('type')}")
        return True
    except Exception as e:
        logger.error(f"[VAULT] failed to write vault entry for {user_id}: {e}")
        return False


def _apply_vault_payload(user_id: str, payload: dict) -> None:
    """Apply a decrypted vault payload to the user's profile."""
    ptype = payload.get("type")
    if ptype == "observation":
        add_observation(user_id, payload["text"])
    elif ptype == "profile_field":
        update_profile_field(
            user_id,
            payload["field"],
            payload["value"],
            payload.get("reason", "Fra vault"),
        )
    else:
        logger.warning(f"[VAULT] unknown payload type '{ptype}' for {user_id}")


def process_vault_files(user_id: str, private_key_bytes: bytes) -> int:
    """
    Decrypt and apply all pending vault files for a user on login.
    Returns the number of vault entries successfully processed.
    Failed files are left in place for retry on next login.
    """
    from kaare_core.crypto import unseal
    user_dir = _user_dir(user_id)
    vault_files = sorted(user_dir.glob("vault_*.bin"))
    if not vault_files:
        return 0
    processed = 0
    for vf in vault_files:
        try:
            encrypted = vf.read_text(encoding="utf-8")
            json_text = unseal(encrypted, private_key_bytes)
            payload = json.loads(json_text)
            _apply_vault_payload(user_id, payload)
            vf.unlink()
            processed += 1
            logger.info(f"[VAULT] applied {vf.name} for {user_id}")
        except Exception as e:
            logger.error(f"[VAULT] failed to process {vf.name} for {user_id}: {e}")
    if processed:
        logger.info(f"[VAULT] processed {processed} vault entries for {user_id}")
    return processed


# ── Household visible (global, no PIN needed) ─────────────────────────────────

def get_household_visible(user_id: str) -> dict:
    """Return household_visible section for a user (no PIN needed).
    Auto-initializes from existing profile data on first call."""
    profile = load_profile(user_id)
    hv = profile.get("household_visible")
    if isinstance(hv, dict):
        result = {}
        for field, default in HOUSEHOLD_VISIBLE_FIELDS.items():
            result[field] = hv.get(field, default)
        if not result["preferred_name"]:
            result["preferred_name"] = user_id
        return result

    # Auto-initialize from existing flat profile
    hv_new: dict[str, Any] = {}
    for field, default in HOUSEHOLD_VISIBLE_FIELDS.items():
        hv_new[field] = default

    # Seed preferred_name from identity.known_name or user_id
    identity = profile.get("identity", {})
    if isinstance(identity, dict):
        hv_new["preferred_name"] = identity.get("known_name") or user_id
    else:
        hv_new["preferred_name"] = user_id

    # Seed role from meta if present
    meta = profile.get("meta", {})
    if isinstance(meta, dict) and meta.get("role"):
        hv_new["role"] = meta["role"]

    # Save the initialized section (best effort — may fail in test/read-only contexts)
    try:
        profile["household_visible"] = hv_new
        save_profile(user_id, profile)
    except Exception:
        pass
    return hv_new


def update_household_visible(user_id: str, field: str, value: Any) -> str:
    """Write a single field to household_visible. Field must be in HOUSEHOLD_VISIBLE_FIELDS."""
    if field not in HOUSEHOLD_VISIBLE_FIELDS:
        valid = list(HOUSEHOLD_VISIBLE_FIELDS.keys())
        return f"Unknown field: '{field}'. Valid: {valid}"
    profile = load_profile(user_id)
    if "household_visible" not in profile:
        profile["household_visible"] = {k: v for k, v in HOUSEHOLD_VISIBLE_FIELDS.items()}
    hv = profile["household_visible"]
    default = HOUSEHOLD_VISIBLE_FIELDS[field]
    if isinstance(default, list):
        if isinstance(value, list):
            hv[field] = value
        elif value and isinstance(value, str):
            existing = hv.get(field) or []
            if value not in existing:
                existing.append(value)
            hv[field] = existing[-10:]  # keep at most 10 recent entries
    else:
        hv[field] = value
    profile["household_visible"] = hv
    save_profile(user_id, profile)
    return f"Hus-profil oppdatert: {field} = {value}"


def get_all_household_visible() -> dict[str, dict]:
    """Read household_visible for ALL personal users. Used for system prompt injection."""
    result: dict[str, dict] = {}
    try:
        for entry in sorted(USERS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            user_id = entry.name
            if user_id in SYSTEM_ACCOUNTS:
                continue
            if not (entry / "profile.yaml").exists():
                continue
            result[user_id] = get_household_visible(user_id)
    except Exception:
        pass
    return result


def format_household_block(all_visible: dict[str, dict]) -> str:
    """Format all household_visible data as a compact system prompt block."""
    if not all_visible:
        return ""
    lines = ["### Husstanden"]
    for user_id, hv in all_visible.items():
        name = hv.get("preferred_name") or user_id
        role = hv.get("role")
        age = hv.get("age")
        key_facts = hv.get("key_facts") or []
        current_context = hv.get("current_context")
        recent_updates = hv.get("recent_updates") or []

        parts = []
        if role:
            parts.append(role)
        if age:
            parts.append(str(age))
        header = f"**{name}**"
        if parts:
            header += f" ({', '.join(parts)})"
        header += ":"

        details = []
        if key_facts:
            details.append(", ".join(key_facts))
        if current_context:
            details.append(current_context)
        for upd in recent_updates[-3:]:
            details.append(upd)

        line = header + (" " + ". ".join(details) + "." if details else "")
        lines.append(f"- {line}")
    return "\n".join(lines)
