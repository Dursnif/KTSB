"""
WireGuard VPN client management for Kåre.

Handles key generation, client config creation, and peer lifecycle.
Privileged wg operations are delegated to /usr/local/bin/kaare-wg-manage via sudo.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

CLIENTS_DIR = Path("/kaare/state/vpn_clients")
INDEX_PATH = CLIENTS_DIR / "clients.json"
SERVER_PUBKEY_PATH = Path("/etc/wireguard/server_public.key")
WG_MANAGE = "/usr/local/bin/kaare-wg-manage"
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")


def _vpn_settings() -> dict:
    try:
        data = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        vpn = data.get("vpn", {})
        return {
            "duckdns_host": vpn.get("duckdns_host", ""),
            "wg_port": int(vpn.get("wg_port", 51820)),
        }
    except Exception:
        return {"duckdns_host": "", "wg_port": 51820}


def _ensure_dir() -> None:
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict]:
    _ensure_dir()
    if not INDEX_PATH.exists():
        return []
    try:
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_index(clients: list[dict]) -> None:
    _ensure_dir()
    INDEX_PATH.write_text(json.dumps(clients, indent=2, ensure_ascii=False), encoding="utf-8")


def _next_ip(clients: list[dict]) -> str:
    used = {c["ip"] for c in clients}
    for i in range(2, 255):
        ip = f"10.0.0.{i}"
        if ip not in used:
            return ip
    raise RuntimeError("No available VPN IP addresses (10.0.0.2–254 all used).")


def _server_public_key() -> str:
    if not SERVER_PUBKEY_PATH.exists():
        raise RuntimeError("Server public key not found. Run wireguard_setup.sh first.")
    return SERVER_PUBKEY_PATH.read_text().strip()


def _generate_keypair() -> tuple[str, str]:
    """Returns (private_key, public_key). wg genkey/pubkey do not need root."""
    private = subprocess.run(
        ["wg", "genkey"], capture_output=True, text=True, check=True
    ).stdout.strip()
    public = subprocess.run(
        ["wg", "pubkey"], input=private, capture_output=True, text=True, check=True
    ).stdout.strip()
    return private, public


def create_client(username: str, device_name: str) -> dict:
    """
    Creates a new WireGuard client for a user/device.
    Returns client info including the config text (used for QR rendering).
    Raises RuntimeError on failure.
    """
    clients = _load_index()
    client_name = f"{username}_{device_name}".replace(" ", "_").lower()

    # Prevent duplicate names
    if any(c["name"] == client_name for c in clients):
        raise ValueError(f"Client '{client_name}' already exists. Delete it first.")

    vpn = _vpn_settings()
    if not vpn["duckdns_host"]:
        raise RuntimeError("VPN endpoint not configured. Set vpn.duckdns_host in configs/settings.yaml.")

    ip = _next_ip(clients)
    private_key, public_key = _generate_keypair()
    server_pubkey = _server_public_key()

    config_text = (
        f"[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {ip}/32\n"
        f"\n"
        f"[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"# Only Kåre traffic through VPN (split tunnel)\n"
        f"AllowedIPs = 10.0.0.1/32\n"
        f"Endpoint = {vpn['duckdns_host']}:{vpn['wg_port']}\n"
        f"PersistentKeepalive = 25\n"
    )

    # Save config file (for admin reference)
    _ensure_dir()
    conf_path = CLIENTS_DIR / f"{client_name}.conf"
    conf_path.write_text(config_text, encoding="utf-8")
    conf_path.chmod(0o600)

    # Add peer to running WireGuard (requires sudo)
    result = subprocess.run(
        ["sudo", WG_MANAGE, "add", public_key, ip, client_name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        conf_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to add WireGuard peer: {result.stderr.strip()}")

    # Register in index
    entry = {
        "name": client_name,
        "username": username,
        "device_name": device_name,
        "ip": ip,
        "public_key": public_key,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    clients.append(entry)
    _save_index(clients)

    return {**entry, "config": config_text}


def list_clients(username: Optional[str] = None) -> list[dict]:
    """Returns all clients, optionally filtered by username."""
    clients = _load_index()
    if username:
        clients = [c for c in clients if c["username"] == username]
    return [{k: v for k, v in c.items() if k != "public_key"} for c in clients]


def delete_client(client_name: str) -> None:
    """Removes a WireGuard client by name."""
    clients = _load_index()
    target = next((c for c in clients if c["name"] == client_name), None)
    if not target:
        raise ValueError(f"Client '{client_name}' not found.")

    # Remove peer from running WireGuard
    result = subprocess.run(
        ["sudo", WG_MANAGE, "remove", target["public_key"]],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to remove WireGuard peer: {result.stderr.strip()}")

    # Remove config file
    conf_path = CLIENTS_DIR / f"{client_name}.conf"
    conf_path.unlink(missing_ok=True)

    # Remove from index
    clients = [c for c in clients if c["name"] != client_name]
    _save_index(clients)
