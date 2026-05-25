#!/bin/bash
set -e

# Copy default configs on first run (when no configs volume is present)
if [ ! -f /kaare/configs/settings.yaml ]; then
    echo "[kaare] First run — initializing configs from defaults..."
    mkdir -p /kaare/configs
    cp /kaare/configs_default/*.yaml /kaare/configs/ 2>/dev/null || true
    echo "[kaare] Default configs copied. Edit /kaare/configs/ to customize."
fi

# Ensure required runtime directories exist
mkdir -p /kaare/state /kaare/data /kaare/logs /kaare/runtime

exec "$@"
