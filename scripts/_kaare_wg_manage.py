#!/usr/bin/env python3
"""
Privileged WireGuard peer manager for Kåre.
Run only via sudo — see /etc/sudoers.d/kaare-wireguard.

Usage:
  sudo kaare-wg-manage add <pubkey> <ip> <client_name>
  sudo kaare-wg-manage remove <pubkey>
"""

import sys
import re
import base64
import subprocess
from pathlib import Path

CONF_PATH = Path("/etc/wireguard/wg0.conf")


def validate_pubkey(key: str) -> bool:
    if len(key) != 44:
        return False
    try:
        base64.b64decode(key)
        return True
    except Exception:
        return False


def validate_ip(ip: str) -> bool:
    m = re.fullmatch(r"10\.0\.0\.(\d{1,3})", ip)
    if not m:
        return False
    return 2 <= int(m.group(1)) <= 254


def remove_peer_from_conf(conf_text: str, pubkey: str) -> str:
    """Returns conf_text with the [Peer] block containing pubkey removed."""
    lines = conf_text.splitlines(keepends=True)
    result = []
    block: list[str] = []
    in_peer = False
    skip = False

    for line in lines:
        if re.match(r"^\s*\[Peer\]", line):
            if in_peer:
                if not skip:
                    result.extend(block)
            in_peer = True
            skip = False
            block = [line]
        elif in_peer:
            block.append(line)
            if line.strip() == f"PublicKey = {pubkey}":
                skip = True
        else:
            result.append(line)

    if in_peer and not skip:
        result.extend(block)

    return "".join(result)


def cmd_add(pubkey: str, ip: str, name: str) -> None:
    assert validate_pubkey(pubkey), f"Invalid public key: {pubkey!r}"
    assert validate_ip(ip), f"Invalid IP (must be 10.0.0.2–254): {ip!r}"

    # Add to running WireGuard interface
    subprocess.run(
        ["wg", "set", "wg0", "peer", pubkey, "allowed-ips", f"{ip}/32"],
        check=True,
    )

    # Persist to wg0.conf
    with open(CONF_PATH, "a") as f:
        f.write(f"\n[Peer]\n# {name}\nPublicKey = {pubkey}\nAllowedIPs = {ip}/32\n")

    print("OK")


def cmd_remove(pubkey: str) -> None:
    assert validate_pubkey(pubkey), f"Invalid public key: {pubkey!r}"

    # Remove from running WireGuard
    subprocess.run(["wg", "set", "wg0", "peer", pubkey, "remove"], check=True)

    # Remove from wg0.conf
    conf = CONF_PATH.read_text(encoding="utf-8")
    new_conf = remove_peer_from_conf(conf, pubkey)
    CONF_PATH.write_text(new_conf, encoding="utf-8")

    print("OK")


if __name__ == "__main__":
    op = sys.argv[1] if len(sys.argv) > 1 else ""

    try:
        if op == "add":
            if len(sys.argv) < 5:
                print("Usage: kaare-wg-manage add <pubkey> <ip> <name>", file=sys.stderr)
                sys.exit(1)
            cmd_add(sys.argv[2], sys.argv[3], sys.argv[4])

        elif op == "remove":
            if len(sys.argv) < 3:
                print("Usage: kaare-wg-manage remove <pubkey>", file=sys.stderr)
                sys.exit(1)
            cmd_remove(sys.argv[2])

        else:
            print(f"Unknown operation: {op!r}. Use 'add' or 'remove'.", file=sys.stderr)
            sys.exit(1)

    except AssertionError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"wg command failed: {e}", file=sys.stderr)
        sys.exit(1)
