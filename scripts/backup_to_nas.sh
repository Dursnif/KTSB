#!/usr/bin/env bash
# backup_to_nas.sh — Synology NAS backup of Kåre
#
# Backs up all of /kaare/ except:
#   - venv/              (regenerable: pip install)
#   - frontend/node_modules/ (regenerable: npm install)
#   - __pycache__/ *.pyc (Python bytecode — regenerable)
#   - *.tmp              (temporary files)
#
# Kåres semantiske minne (state/qdrant/, ~8.6GB) er inkludert.
# Configs inkl. .env-filer er inkludert (tokens, API-nøkler).
#
# state/system_master.key er EKSKLUDERT (P87) — denne nøkkelen kombinert med en
# brukers system_encrypted_private_key-blob i users.db åpner brukerens data uten
# PIN. Reiser den med backupen, blir en stjålet NAS-kopi en skjelettnøkkel.
#
# Kjøres daglig kl. 03:00 via kaare-backup.timer
# Logg: /kaare/logs/backup_nas.log
#
# Før hver sync roteres 3 snapshots av gårsdagens NAS-kopi (kaare_snapshots/1-3,
# 1=nyest). --delete gjør denne synkroniseringen destruktiv ved uhell (lokal
# sletting speiles til NAS neste natt) — snapshotene er sikkerhetsnettet mot
# akkurat det. Beste-innsats: feiler rotasjonen, fortsetter backupen likevel.

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

log "--- Roterer snapshots (3 rullerende, før sync overskriver) ---"
if ssh -o ConnectTimeout=10 -o BatchMode=yes "$NAS_HOST" '
    set -e
    cd ~/kaare_backup || exit 0
    mkdir -p kaare_snapshots
    cd kaare_snapshots
    rm -rf 3
    [ -d 2 ] && mv 2 3
    [ -d 1 ] && mv 1 2
    [ -d ../kaare ] && cp -al ../kaare 1
    exit 0
' 2>&1 | tee -a "$LOG"; then
    :
else
    log "Snapshot-rotasjon feilet (ikke-kritisk) — fortsetter med vanlig sync"
fi

rsync -az \
    --delete \
    --delete-excluded \
    --exclude='venv/' \
    --exclude='frontend/node_modules/' \
    --exclude='.cache/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.tmp' \
    --exclude='.git/' \
    --exclude='state/system_master.key' \
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
