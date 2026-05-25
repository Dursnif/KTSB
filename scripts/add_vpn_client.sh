#!/bin/bash
# Add a new WireGuard VPN client for Kåre
# Usage: sudo bash /kaare/scripts/add_vpn_client.sh <client_name>
# Example: sudo bash /kaare/scripts/add_vpn_client.sh stian-telefon
#
# Client configs are saved to /kaare/state/vpn_clients/
# IMPORTANT: these files contain private keys — never commit to git

set -e

CLIENT_NAME="${1:-}"
if [ -z "$CLIENT_NAME" ]; then
    echo "Bruk: sudo bash $0 <klientnavn>"
    echo "Eksempel:"
    echo "  sudo bash $0 stian-telefon"
    echo "  sudo bash $0 kari-ipad"
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Kjør som root: sudo bash $0 ${CLIENT_NAME}"
    exit 1
fi

WIREGUARD_DIR="/etc/wireguard"
CLIENTS_DIR="/kaare/state/vpn_clients"
SERVER_PUBLIC_KEY=$(cat "${WIREGUARD_DIR}/server_public.key")

mkdir -p "${CLIENTS_DIR}"
chmod 700 "${CLIENTS_DIR}"

# Find next available IP (starts at 10.0.0.2)
NEXT_IP=2
for i in $(seq 2 254); do
    if ! grep -q "10.0.0.${i}/32" "${WIREGUARD_DIR}/wg0.conf" 2>/dev/null; then
        NEXT_IP=$i
        break
    fi
done

CLIENT_IP="10.0.0.${NEXT_IP}"
echo "Tildeler IP: ${CLIENT_IP} til klient '${CLIENT_NAME}'"

# Read VPN endpoint from settings.yaml
WG_ENDPOINT=$(python3 -c "
import yaml, sys
d = yaml.safe_load(open('/kaare/configs/settings.yaml'))
vpn = d.get('vpn', {})
host = vpn.get('duckdns_host', '')
port = vpn.get('wg_port', 51820)
if not host:
    print('FEIL: vpn.duckdns_host er ikke satt i configs/settings.yaml', file=sys.stderr)
    sys.exit(1)
print(f'{host}:{port}')
")
if [ $? -ne 0 ]; then
    exit 1
fi

# Generate client keypair
CLIENT_PRIVATE_KEY=$(wg genkey)
CLIENT_PUBLIC_KEY=$(echo "${CLIENT_PRIVATE_KEY}" | wg pubkey)

# Add peer to wg0.conf (persists across reboots)
cat >> "${WIREGUARD_DIR}/wg0.conf" << EOF

[Peer]
# ${CLIENT_NAME}
PublicKey = ${CLIENT_PUBLIC_KEY}
AllowedIPs = ${CLIENT_IP}/32
EOF

# Add peer to running WireGuard (takes effect immediately, no restart)
wg set wg0 peer "${CLIENT_PUBLIC_KEY}" allowed-ips "${CLIENT_IP}/32"

# Create client config file
CLIENT_CONF="${CLIENTS_DIR}/${CLIENT_NAME}.conf"
cat > "${CLIENT_CONF}" << EOF
[Interface]
PrivateKey = ${CLIENT_PRIVATE_KEY}
Address = ${CLIENT_IP}/32

[Peer]
PublicKey = ${SERVER_PUBLIC_KEY}
# Only Kare traffic goes through VPN (split tunnel — not all internet traffic)
AllowedIPs = 10.0.0.1/32
Endpoint = ${WG_ENDPOINT}
PersistentKeepalive = 25
EOF

chmod 600 "${CLIENT_CONF}"
echo "Klientkonfig lagret: ${CLIENT_CONF}"

# Generate QR code for mobile WireGuard app
if command -v qrencode &> /dev/null; then
    echo ""
    echo "=== QR-kode — scan i WireGuard-appen ==="
    qrencode -t ansiutf8 < "${CLIENT_CONF}"
    echo ""
else
    echo ""
    echo "Tips: installer qrencode for QR-kode automatisk:"
    echo "  sudo apt install qrencode"
    echo ""
    echo "Eller generer QR manuelt:"
    echo "  sudo qrencode -t ansiutf8 < ${CLIENT_CONF}"
    echo ""
    echo "=== Konfig for manuell import ==="
    cat "${CLIENT_CONF}"
fi

echo "=== Klient '${CLIENT_NAME}' lagt til ==="
echo "IP: ${CLIENT_IP}"
echo "Konfig: ${CLIENT_CONF}"
