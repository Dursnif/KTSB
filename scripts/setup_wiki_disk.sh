#!/bin/bash
set -e

PARTITION="/dev/sda2"
MOUNTPOINT="/mnt/wiki"
LABEL="wiki"

echo "=== Formaterer $PARTITION som ext4 ==="
mkfs.ext4 -L "$LABEL" "$PARTITION"

echo "=== Oppretter monteringspunkt $MOUNTPOINT ==="
mkdir -p "$MOUNTPOINT"

echo "=== Henter UUID ==="
UUID=$(blkid -s UUID -o value "$PARTITION")
echo "UUID: $UUID"

echo "=== Legger til i /etc/fstab ==="
# Fjern gammel oppføring for denne partisjonen hvis den finnes
sed -i "\|$PARTITION\|d" /etc/fstab
sed -i "\|$MOUNTPOINT\|d" /etc/fstab

echo "UUID=$UUID $MOUNTPOINT ext4 defaults,noatime 0 2" >> /etc/fstab

echo "=== Monterer disken ==="
mount -a

echo "=== Setter eierskap til kaare ==="
chown kaare:kaare "$MOUNTPOINT"
chmod 775 "$MOUNTPOINT"

echo ""
echo "=== Ferdig! ==="
df -h "$MOUNTPOINT"
