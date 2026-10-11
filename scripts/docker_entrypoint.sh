#!/bin/bash
set -e

# Every start, every container: bring configs/ up to the shipped templates (P134). First run included — missing
# files are created from configs_default/ atomically. When several containers start at once, the first one
# migrates and the others wait on the migration lock, then find nothing to do (a normal start writes nothing).
mkdir -p /kaare/configs
python3 /kaare/scripts/migrate_configs.py

# Ensure required runtime directories exist
mkdir -p /kaare/state /kaare/data /kaare/logs /kaare/runtime

exec "$@"
