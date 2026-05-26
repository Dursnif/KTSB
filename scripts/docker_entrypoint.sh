#!/bin/bash
set -e

# First run: copy all default configs if settings.yaml doesn't exist yet
if [ ! -f /kaare/configs/settings.yaml ]; then
    echo "[kaare] First run — initializing configs from defaults..."
    mkdir -p /kaare/configs
    cp /kaare/configs_default/*.yaml /kaare/configs/ 2>/dev/null || true
    echo "[kaare] Default configs copied. Edit /kaare/configs/ to customize."
fi

# Every run: migrate configs (fix corrupted YAML, add missing keys from defaults)
python3 /kaare/scripts/migrate_configs.py

# Ensure required runtime directories exist
mkdir -p /kaare/state /kaare/data /kaare/logs /kaare/runtime

exec "$@"
