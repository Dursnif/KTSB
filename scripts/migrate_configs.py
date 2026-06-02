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


def _migrate_nodes(config_file: Path, changed: list) -> None:
    """Migrate is_tv: true → has_display: true + has_audio: true."""
    if not config_file.exists():
        return
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    nodes = data.get("nodes", {})
    if not isinstance(nodes, dict):
        return
    migrated_count = 0
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue
        if "is_tv" in node:
            node.setdefault("has_display", True)
            node.setdefault("has_audio", True)
            del node["is_tv"]
            migrated_count += 1
    if migrated_count:
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        changed.append(f"  migrated nodes.yaml: {migrated_count} node(s) is_tv → has_display/has_audio")


def _migrate_network_config(config_file: Path, changed: list) -> None:
    """Migrate network.local_subnet (string) to network.local_subnets (list)."""
    if not config_file.exists():
        return
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except Exception:
        return
    net = data.get("network", {})
    if not isinstance(net, dict):
        return
    if "local_subnets" in net or "local_subnet" not in net:
        return
    # Convert string to list
    data["network"]["local_subnets"] = [net["local_subnet"]]
    del data["network"]["local_subnet"]
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    changed.append(f"  migrated {config_file.name}: network.local_subnet → network.local_subnets")


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

    _migrate_network_config(CONFIGS_DIR / "settings.yaml", changed)
    _migrate_nodes(CONFIGS_DIR / "nodes.yaml", changed)

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
