#!/usr/bin/env python3
"""
Config migration: runs on every container startup.

- Copies missing config files from configs_default/
- Restores corrupted YAML files from defaults (invalid YAML → replace)
- Adds missing keys from defaults into existing configs (deep merge, never overwrites)
- Treats null/None values as missing (fills in from defaults)
"""
import shutil
import sys
from pathlib import Path

import yaml

DEFAULTS_DIR = Path("/kaare/configs_default")
CONFIGS_DIR = Path("/kaare/configs")


def deep_merge(default: dict, existing: dict) -> dict:
    """Return existing with any missing keys filled in from default. Never overwrites."""
    if not isinstance(default, dict) or not isinstance(existing, dict):
        return existing
    result = dict(existing)
    for key, default_val in default.items():
        if key not in result or result[key] is None:
            result[key] = default_val
        elif isinstance(default_val, dict) and isinstance(result[key], dict):
            result[key] = deep_merge(default_val, result[key])
    return result


def migrate():
    CONFIGS_DIR.mkdir(exist_ok=True)
    changed = []

    for default_file in sorted(DEFAULTS_DIR.glob("*.yaml")):
        config_file = CONFIGS_DIR / default_file.name

        try:
            default_data = yaml.safe_load(default_file.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[migrate] Warning: default {default_file.name} is invalid YAML: {e}", flush=True)
            continue

        if not config_file.exists():
            shutil.copy(default_file, config_file)
            changed.append(f"  copied {default_file.name} (was missing)")
            continue

        try:
            existing_data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[migrate] {config_file.name} is corrupted ({e}) — restoring from default", flush=True)
            shutil.copy(default_file, config_file)
            changed.append(f"  restored {config_file.name} (corrupted YAML)")
            continue

        merged = deep_merge(default_data, existing_data)
        if merged != existing_data:
            with open(config_file, "w", encoding="utf-8") as f:
                yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            changed.append(f"  updated {config_file.name} (added missing keys)")

    if changed:
        print("[migrate] Config changes applied:", flush=True)
        for line in changed:
            print(line, flush=True)
    else:
        print("[migrate] All configs up to date.", flush=True)


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"[migrate] ERROR: {e}", flush=True)
        sys.exit(1)
