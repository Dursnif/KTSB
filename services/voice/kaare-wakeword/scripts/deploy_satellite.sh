#!/usr/bin/env bash
# Deploy v2 satellite package to RPi.
#
# Usage: bash scripts/deploy_satellite.sh
#
# Syncs satellite/ package + models + pyproject to RPi,
# updates systemd service, and restarts.

set -euo pipefail

RPI_HOST="root@192.168.87.199"
RPI_DIR="/opt/kaare-satellite"

echo "=== Deploying Kåre v2 satellite to ${RPI_HOST}:${RPI_DIR} ==="

# 1. Sync satellite package
echo "--- Syncing satellite/ package ---"
rsync -avz --delete \
    --exclude='__pycache__' \
    satellite/ \
    "${RPI_HOST}:${RPI_DIR}/satellite/"

# 2. Sync pyproject.toml (RPi-specific)
echo "--- Syncing pyproject.toml ---"
rsync -avz \
    deploy/rpi-satellite/pyproject.toml \
    "${RPI_HOST}:${RPI_DIR}/pyproject.toml"

# 3. Sync wake word model
echo "--- Syncing wake word model ---"
rsync -avz \
    models/wakeword.tflite \
    "${RPI_HOST}:${RPI_DIR}/models/wakeword.tflite"

# 4. Install/update dependencies via uv
echo "--- Installing dependencies ---"
ssh "${RPI_HOST}" "cd ${RPI_DIR} && uv sync --extra rpi 2>&1 | tail -10"

# 5. Write systemd service (uses uv run)
echo "--- Writing systemd service ---"
ssh "${RPI_HOST}" "cat > /etc/systemd/system/kaare-satellite.service" <<'UNIT'
[Unit]
Description=Kåre Voice Satellite v2
After=network-online.target sound.target bluetooth.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/kaare-satellite
ExecStartPre=/usr/bin/bluetoothctl connect D8:BC:38:E6:E6:DE
ExecStart=/root/.local/bin/uv run -m satellite.pipeline \
    --server ws://192.168.87.242:8765 \
    --device 1 \
    --output-device 4 \
    --model models/wakeword.tflite \
    --satellite-id rpi-respeaker \
    --room stue \
    --volume-boost 1.0 \
    --vad-backend webrtc
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT

# 6. Reload and restart
echo "--- Restarting service ---"
ssh "${RPI_HOST}" "systemctl daemon-reload && systemctl enable kaare-satellite && systemctl restart kaare-satellite"

# 7. Show status
echo "--- Service status ---"
ssh "${RPI_HOST}" "systemctl status kaare-satellite --no-pager" || true

echo ""
echo "=== Deploy complete ==="
echo "Follow logs: ssh ${RPI_HOST} 'journalctl -u kaare-satellite -f'"
