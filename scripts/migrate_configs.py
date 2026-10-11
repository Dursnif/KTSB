#!/usr/bin/env python3
"""
Config migration — thin CLI around kaare_core.config_migrations.run() (P134).

Runs at every container start (scripts/docker_entrypoint.sh) and, on bare metal, before kaare.service
(scripts/systemd/kaare-config-migrate.conf). Safe when many start at once: one migrates, the rest wait on the
migration lock and find nothing to do. A normal start writes nothing.

- Copies missing config files from configs_default/ (never overwrites)
- Keeps an unreadable or empty YAML file in configs/.history/ as `_corrupt` before replacing it with the template
- Runs each numbered migration step once (state in configs/.config_version)
- Adds missing keys from the templates (never overwrites a value; null counts as missing)
- Snapshots the whole configs/ folder to configs/.history/_snapshots/ before the first change

Usage: migrate_configs.py [--dry-run] [--config-dir DIR] [--defaults-dir DIR]
Exit codes: 0 = ok (changed or up to date), 1 = error.
"""

import argparse
import sys
from pathlib import Path

# Run as a plain script (entrypoint, systemd): the repo root must be importable before kaare_core is.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kaare_core import (
    config_migrations,
    config_store,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bring configs/ up to the shipped templates (P134).")
    ap.add_argument(
        "--dry-run", action="store_true", help="report what would change, write nothing"
    )
    ap.add_argument("--config-dir", type=Path, default=config_store.CONFIG_DIR)
    ap.add_argument("--defaults-dir", type=Path, default=config_store.DEFAULT_DIR)
    args = ap.parse_args()
    try:
        rep = config_migrations.run(args.config_dir, args.defaults_dir, dry_run=args.dry_run)
    except config_store.ConfigLockTimeout as e:
        print(f"[migrate] ERROR: another migration is still running ({e})", flush=True)
        return 1
    except Exception as e:
        print(f"[migrate] ERROR: {type(e).__name__}: {e}", flush=True)
        return 1
    for line in rep.lines():
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
