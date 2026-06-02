#!/usr/bin/env bash
# setup_permissions.sh — P50: Permanent file ownership fix for /kaare
# Run once as root: sudo bash /kaare/scripts/setup_permissions.sh
# Safe to re-run (idempotent).
set -euo pipefail

echo "=== P50: KTSB ownership fix ==="

# ── Phase 1: UMask=0002 in all kaare service files ────────────────────────────
echo ""
echo "Phase 1: Adding UMask=0002 to kaare service files..."

SERVICES=(
  kaare.service
  kaare-semantic-embed.service
  kaare-agents.service
  kaare-voice-bridge.service
  kaare-embedding.service
  kaare-argus.service
  kaare-ha-log-bridge.service
  kaare-frontend.service
  kaare_ha_gateway.service
  kaare-warmup.service
  kaare-night-sequence.service
  kaare_intent_server.service
  kaare-vaktmester.service
)

for svc in "${SERVICES[@]}"; do
  f="/etc/systemd/system/$svc"
  if [ ! -f "$f" ]; then
    echo "  SKIP (not found): $svc"
    continue
  fi
  if grep -q "^UMask=" "$f"; then
    echo "  SKIP (already set): $svc"
    continue
  fi
  sed -i '/^\[Service\]/a UMask=0002' "$f"
  echo "  Updated: $svc"
done

systemctl daemon-reload
echo "  daemon-reload done"

# ── Phase 2: System-wide umask 002 ────────────────────────────────────────────
echo ""
echo "Phase 2: System-wide umask 002..."

cat > /etc/profile.d/kaare_umask.sh << 'EOF'
# KTSB: all users on this machine collaborate in /kaare — group-write default
umask 002
EOF
chmod 644 /etc/profile.d/kaare_umask.sh
echo "  Created /etc/profile.d/kaare_umask.sh"

# ── Phase 3: setgid on all /kaare directories ─────────────────────────────────
echo ""
echo "Phase 3: setgid on /kaare directories..."

find /kaare -type d \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" \
  -not -path "*/venv/*" \
  -exec chmod g+s {} +

echo "  setgid applied"

# ── Phase 4: One-time ownership fix (chown kaare:kaare) ───────────────────────
echo ""
echo "Phase 4: chown -R kaare:kaare on /kaare (excl. symlinks, node_modules, .git, venv)..."

find /kaare \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" \
  -not -path "*/venv/*" \
  -not -type l \
  -exec chown kaare:kaare {} +

echo "  chown done"

# ── Phase 5: One-time permissions fix ─────────────────────────────────────────
echo ""
echo "Phase 5: chmod — dirs 775, files 664, scripts 775, bin dirs 775..."

# Directories
find /kaare -type d \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" \
  -not -path "*/venv/*" \
  -exec chmod 775 {} +

# Regular files
find /kaare -type f \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" \
  -not -path "*/venv/*" \
  -exec chmod 664 {} +

# Shell scripts
find /kaare -name "*.sh" \
  -not -path "*/node_modules/*" \
  -exec chmod 775 {} +

# Python scripts directly in scripts/
find /kaare/scripts -name "*.py" -exec chmod 775 {} +

# Restore execute bits in venv bin dirs
for bindir in \
  /kaare/venv/bin \
  /kaare/semantic_embed/venv/bin \
  /kaare/services/voice/venv/bin; do
  [ -d "$bindir" ] && find "$bindir" -type f -exec chmod 775 {} + && echo "  Fixed: $bindir"
done

# Restore execute bits in node_modules/.bin (symlinks stay as-is, only regular files)
for bindir in \
  /kaare/frontend/node_modules/.bin; do
  [ -d "$bindir" ] && find "$bindir" -type f -exec chmod 775 {} + 2>/dev/null && echo "  Fixed: $bindir" || true
done

echo "  chmod done"

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
echo "=== Verification ==="

STALE=$(find /kaare \
  -not -type l \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*" \
  -not -path "*/venv/*" \
  -not -user kaare 2>/dev/null | wc -l)

echo "Files not owned by kaare (should be 0): $STALE"
echo "UMask in kaare.service: $(grep UMask /etc/systemd/system/kaare.service || echo 'NOT SET')"
echo "umask profile: $(cat /etc/profile.d/kaare_umask.sh | grep umask)"
echo ""
echo "=== P50 done ==="
echo "Restart services: sudo systemctl restart kaare.service kaare_ha_gateway.service"
