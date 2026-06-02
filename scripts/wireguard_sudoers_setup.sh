#!/bin/bash
# One-time setup: installs privileged WireGuard helper + sudoers entry for kaare user.
# Run once as: sudo bash /kaare/scripts/wireguard_sudoers_setup.sh

set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash $0"
    exit 1
fi

echo "=== Installing Kåre WireGuard privilege helper ==="

# Install the privileged Python helper
cp /kaare/scripts/_kaare_wg_manage.py /usr/local/bin/kaare-wg-manage
chmod 755 /usr/local/bin/kaare-wg-manage
chown root:root /usr/local/bin/kaare-wg-manage
echo "Helper installed: /usr/local/bin/kaare-wg-manage"

# Install sudoers entry (allows kaare user to run the helper without password)
cat > /etc/sudoers.d/kaare-wireguard << 'EOF'
# Allow kaare service user to manage WireGuard peers (no password required)
kaare ALL=(ALL) NOPASSWD: /usr/local/bin/kaare-wg-manage
EOF
chmod 440 /etc/sudoers.d/kaare-wireguard
visudo -c -f /etc/sudoers.d/kaare-wireguard
echo "Sudoers entry installed: /etc/sudoers.d/kaare-wireguard"

# Create state directory for client configs
mkdir -p /kaare/state/vpn_clients
chown kaare:kaare /kaare/state/vpn_clients
chmod 700 /kaare/state/vpn_clients

echo ""
echo "=== Setup complete ==="
echo "The kaare service can now manage WireGuard peers without password prompts."
