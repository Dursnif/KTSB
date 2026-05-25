#!/usr/bin/env bash
# backup_to_nas.sh — Synology NAS backup of Kåre
#
# Backs up all of /kaare/ except:
#   - venv/              (regenerable: pip install)
#   - frontend/node_modules/ (regenerable: npm install)
#   - state/prism/       (Meilisearch index — rebuilt automatically from logs)
#   - __pycache__/ *.pyc (Python bytecode — regenerable)
#   - *.tmp              (temporary files)
#
# Kåres semantiske minne (state/qdrant/, ~8.6GB) er inkludert.
# Configs inkl. .env-filer er inkludert (tokens, API-nøkler).
#
# Kjøres daglig kl. 03:00 via kaare-backup.timer
# Logg: /kaare/logs/backup_nas.log

set -euo pipefail

NAS_HOST="nas"
NAS_DEST="~/kaare_backup/kaare/"
SOURCE="/kaare/"
LOG="/kaare/logs/backup_nas.log"
LOCK="/tmp/kaare_backup.lock"

mkdir -p /kaare/logs

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"
}

# Hindre overlappende kjøringer
if [ -e "$LOCK" ]; then
    log "SKIP: Backup already running (lock: $LOCK)"
    exit 0
fi
touch "$LOCK"
trap "rm -f '$LOCK'" EXIT

log "=== Backup start ==="

START=$(date +%s)

rsync -az \
    --delete \
    --delete-excluded \
    --exclude='venv/' \
    --exclude='frontend/node_modules/' \
    --exclude='state/prism/' \
    --exclude='.cache/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.tmp' \
    --exclude='.git/' \
    -e "ssh -o ConnectTimeout=10 -o BatchMode=yes" \
    "$SOURCE" \
    "$NAS_HOST:$NAS_DEST" \
    2>&1 | tee -a "$LOG"
RSYNC_EXIT=${PIPESTATUS[0]}

END=$(date +%s)
ELAPSED=$(( END - START ))

if [ "$RSYNC_EXIT" -eq 0 ]; then
    log "=== Backup ferdig på ${ELAPSED}s — alt overført ==="
elif [ "$RSYNC_EXIT" -eq 23 ] || [ "$RSYNC_EXIT" -eq 24 ]; then
    log "=== Backup ferdig på ${ELAPSED}s — noen filer hoppet over (kode $RSYNC_EXIT), sjekk logg ==="
    exit 0
else
    log "=== Backup FEILET på ${ELAPSED}s — rsync kode $RSYNC_EXIT ==="
    exit "$RSYNC_EXIT"
fi
