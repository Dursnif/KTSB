#!/bin/bash
# WireGuard server setup for Kåre AI-PC (192.168.0.232)
# Run once as: sudo bash /kaare/scripts/wireguard_setup.sh

set -e

echo "=== Kåre WireGuard Server Setup ==="

if [ "$(id -u)" -ne 0 ]; then
    echo "Kjør som root: sudo bash $0"
    exit 1
fi

mkdir -p /etc/wireguard
chmod 700 /etc/wireguard

# Generate server keypair if not already done
if [ ! -f /etc/wireguard/server_private.key ]; then
    wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key
    chmod 600 /etc/wireguard/server_private.key
    echo "Server-nøkler generert."
else
    echo "Server-nøkler finnes allerede — hopper over generering."
fi

SERVER_PRIVATE_KEY=$(cat /etc/wireguard/server_private.key)
SERVER_PUBLIC_KEY=$(cat /etc/wireguard/server_public.key)

# Create wg0.conf (only if it doesn't exist)
if [ ! -f /etc/wireguard/wg0.conf ]; then
    cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = ${SERVER_PRIVATE_KEY}
Address = 10.0.0.1/24
ListenPort = 51820
SaveConfig = false

# VPN clients are added by /kaare/scripts/add_vpn_client.sh
EOF
    chmod 600 /etc/wireguard/wg0.conf
    echo "WireGuard-konfig opprettet: /etc/wireguard/wg0.conf"
else
    echo "wg0.conf finnes allerede — hopper over."
fi

# Enable and start
systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0 || systemctl restart wg-quick@wg0

echo ""
echo "=== WireGuard-server er oppe ==="
echo "Server public key: ${SERVER_PUBLIC_KEY}"
echo ""
wg show
echo ""
echo "Neste steg:"
echo "  sudo bash /kaare/scripts/add_vpn_client.sh stian-telefon"
