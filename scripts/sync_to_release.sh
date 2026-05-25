#!/bin/bash
# sync_to_release.sh
# Kopierer ren kode fra /kaare/ til /mnt/ai_disk/ktsb-release/
#
# ALDRI push direkte fra /kaare/ til GitHub.
# Kjør alltid denne skriptet først, deretter push fra release-mappa.
#
# Bruk:
#   bash /kaare/scripts/sync_to_release.sh
#   cd /mnt/ai_disk/ktsb-release
#   git status
#   git add -A && git commit -m "..." && git push

set -e

SOURCE="/kaare"
RELEASE_DIR="/mnt/ai_disk/ktsb-release"

echo "=== KTSB Release Sync ==="
echo "Fra:  $SOURCE"
echo "Til:  $RELEASE_DIR"
echo ""

if [ ! -d "$RELEASE_DIR" ]; then
  echo "FEIL: $RELEASE_DIR finnes ikke."
  echo "Opprett den med: sudo mkdir -p $RELEASE_DIR && sudo chown \$(whoami):\$(whoami) $RELEASE_DIR"
  exit 1
fi

rsync -av --delete \
  --exclude='.git/' \
  --exclude='configs/*.env' \
  --exclude='configs/settings.yaml' \
  --exclude='.env' \
  --exclude='state/' \
  --exclude='data/' \
  --exclude='logs/' \
  --exclude='runtime/' \
  --exclude='backups/' \
  --exclude='venv/' \
  --exclude='.venv/' \
  --exclude='memory_embed_server/venv/' \
  --exclude='services/voice/venv/' \
  --exclude='services/embedding/convert_venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='*.pyd' \
  --exclude='*.egg-info/' \
  --exclude='frontend/node_modules/' \
  --exclude='frontend/dist/' \
  --exclude='models/' \
  --exclude='*.onnx' \
  --exclude='*.bin' \
  --exclude='*.safetensors' \
  --exclude='*.pt' \
  --exclude='*.ot' \
  --exclude='*.png' \
  --exclude='*.jpg' \
  --exclude='*.jpeg' \
  --exclude='*.gif' \
  --exclude='.playwright-mcp/' \
  --exclude='.claude/' \
  --exclude='claude_memory' \
  --exclude='CLAUDE.md' \
  --exclude='CURRENT.md' \
  --exclude='vaktmester_report.json' \
  --exclude='vaktmester_state.json' \
  --exclude='vaktmester_delta.json' \
  --exclude='vaktmester_inbox/' \
  --exclude='.npm/' \
  --exclude='volumes/' \
  --exclude='intel/' \
  --exclude='tmp_systemd/' \
  --exclude='ltm.db' \
  --exclude='*.db' \
  --exclude='*.bak' \
  --exclude='*.bak_*' \
  --exclude='*.swp' \
  --exclude='*.tmp' \
  --exclude='.DS_Store' \
  --exclude='.idea/' \
  --exclude='.vscode/' \
  --exclude='intent_server' \
  --exclude='services/gui/' \
  --exclude='www/' \
  --exclude='mini_kaareha/' \
  --exclude='vaktmester_halfyear.py' \
  --exclude='vaktmester_monthly.py' \
  --exclude='lost+found/' \
  --exclude='.ssh/' \
  --exclude='.cache/' \
  --exclude='configs/aliases.yaml' \
  --exclude='configs/nodes.yaml' \
  --exclude='configs/services.yaml' \
  --exclude='capability_map.yaml' \
  --exclude='configs/*.save' \
  --exclude='configs/*.save.*' \
  --exclude='configs/kare_ha_bridgealiases.yaml.txt' \
  --exclude='configs/prism/' \
  --exclude='docs/' \
  "$SOURCE/" "$RELEASE_DIR/"

echo ""
echo "=== Sync ferdig! ==="
echo ""
echo "Neste steg:"
echo "  cd $RELEASE_DIR"
echo "  git status"
echo "  git add -A && git commit -m 'beskrivelse' && git push"
