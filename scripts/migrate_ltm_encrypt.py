#!/usr/bin/env python3
"""
Encrypt existing plaintext LTM rows for one or more users.

Usage (while the Kåre API is running and the user is logged in):
    PYTHONPATH=/kaare venv/bin/python scripts/migrate_ltm_encrypt.py [username ...]

If no usernames are given, processes all users that currently have an active session.
The script uses the in-memory session keys from the running API process — it cannot
decrypt private keys itself. Users must be logged in for migration to succeed.

Idempotent: rows already prefixed with ENC: are skipped.
"""

import sys
sys.path.insert(0, "/kaare")

from kaare_core.memory.long_term import migrate_user_ltm
from kaare_core.session_keys import active_sessions
from kaare_core.users.store import list_users, has_keypair


def main() -> None:
    if len(sys.argv) > 1:
        users = sys.argv[1:]
    else:
        users = active_sessions()
        if not users:
            print("No active sessions found. Start the Kåre API and log in first.")
            sys.exit(1)
        print(f"Found active sessions: {users}")

    total = 0
    for uid in users:
        if not has_keypair(uid):
            print(f"  {uid}: no keypair — skipping")
            continue
        n = migrate_user_ltm(uid)
        print(f"  {uid}: {n} rows encrypted")
        total += n

    print(f"\nDone. Total rows encrypted: {total}")


if __name__ == "__main__":
    main()
