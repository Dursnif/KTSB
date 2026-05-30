"""
Kåre brukerdatabase — SQLite.

Lagrer brukere lokalt. Ingen sky, ingen tredjepart.
Fil: /kaare/state/users/users.db

Roller: child | teen | young_adult | adult | admin
"""

import sqlite3
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import bcrypt

DB_PATH = Path("/kaare/state/users/users.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT    NOT NULL UNIQUE,
    display_name     TEXT    NOT NULL,
    role             TEXT    NOT NULL DEFAULT 'adult',
    pin_hash         TEXT    NOT NULL,
    avatar           TEXT    NOT NULL DEFAULT '',
    is_active        INTEGER NOT NULL DEFAULT 1,
    must_change_pin  INTEGER NOT NULL DEFAULT 0,
    pin_expires_at   TEXT,
    personality      TEXT    NOT NULL DEFAULT 'standard',
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
"""

TEMP_PIN_TTL_HOURS = 1

VALID_ROLES = {"child", "teen", "young_adult", "adult", "admin"}
VALID_VPN_ACCESS = {"local_only", "ai_only", "full_access"}


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        conn.commit()
        _ensure_admin(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Legg til nye kolonner i eksisterende database uten å miste data."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "must_change_pin" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_pin INTEGER NOT NULL DEFAULT 0")
    if "pin_expires_at" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN pin_expires_at TEXT")
    if "personality" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN personality TEXT NOT NULL DEFAULT 'standard'")
    if "vpn_access" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN vpn_access TEXT NOT NULL DEFAULT 'local_only'")
    # Crypto columns (per-user encryption)
    if "public_key" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN public_key TEXT")
    if "encrypted_private_key" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN encrypted_private_key TEXT")
    if "argon2_salt" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN argon2_salt TEXT")


def _ensure_admin(conn: sqlite3.Connection) -> None:
    """Oppretter standard admin-bruker hvis ingen brukere finnes."""
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        _create_user_conn(conn, username="admin", display_name="Administrator",
                          role="admin", pin="1234", avatar="🛡️", must_change_pin=True)
        import logging
        logging.getLogger(__name__).warning(
            "Opprettet standard admin-bruker (PIN: 1234). Bytt PIN umiddelbart!"
        )


# ── Hashing ────────────────────────────────────────────────────────────────────

def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()


def verify_pin(pin: str, pin_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pin.encode(), pin_hash.encode())
    except Exception:
        return False


# ── CRUD ───────────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    d = dict(row)
    d.pop("pin_hash", None)
    d.pop("pin_expires_at", None)
    d["is_active"] = bool(d["is_active"])
    d["must_change_pin"] = bool(d.get("must_change_pin", 0))
    d.setdefault("personality", "standard")
    d.setdefault("vpn_access", "local_only")
    return d


def _create_user_conn(conn, *, username, display_name, role, pin, avatar="",
                      must_change_pin=False) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"Ugyldig rolle: {role}")
    if len(pin) < 4:
        raise ValueError("PIN må være minst 4 tegn.")
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pin_hash = hash_pin(pin)
    expires = None
    if must_change_pin:
        expires = (datetime.now(timezone.utc) + timedelta(hours=TEMP_PIN_TTL_HOURS)).isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO users
           (username, display_name, role, pin_hash, avatar, must_change_pin, pin_expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, display_name, role, pin_hash, avatar, 1 if must_change_pin else 0, expires, ts),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_dict(row)


def create_user(*, username: str, display_name: str, role: str,
                pin: str, avatar: str = "", must_change_pin: bool = True) -> dict:
    conn = _get_conn()
    try:
        return _create_user_conn(conn, username=username, display_name=display_name,
                                 role=role, pin=pin, avatar=avatar,
                                 must_change_pin=must_change_pin)
    finally:
        conn.close()


def get_user(username: str) -> Optional[dict]:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_user_with_hash(username: str) -> Optional[dict]:
    """Kun for intern auth-bruk — inkluderer pin_hash."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_users() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY role, display_name"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_user(username: str, *, display_name: Optional[str] = None,
                role: Optional[str] = None, avatar: Optional[str] = None,
                is_active: Optional[bool] = None,
                personality: Optional[str] = None,
                vpn_access: Optional[str] = None) -> Optional[dict]:
    if role and role not in VALID_ROLES:
        raise ValueError(f"Ugyldig rolle: {role}")
    if vpn_access and vpn_access not in VALID_VPN_ACCESS:
        raise ValueError(f"Invalid vpn_access value: {vpn_access}")
    fields, params = [], []
    if display_name is not None:
        fields.append("display_name=?"); params.append(display_name)
    if role is not None:
        fields.append("role=?"); params.append(role)
    if avatar is not None:
        fields.append("avatar=?"); params.append(avatar)
    if is_active is not None:
        fields.append("is_active=?"); params.append(1 if is_active else 0)
    if personality is not None:
        fields.append("personality=?"); params.append(personality)
    if vpn_access is not None:
        fields.append("vpn_access=?"); params.append(vpn_access)
    if not fields:
        return get_user(username)
    params.append(username)
    conn = _get_conn()
    try:
        conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE username=?", params)
        conn.commit()
        return get_user(username)
    finally:
        conn.close()


def update_pin(username: str, new_pin: str) -> bool:
    if len(new_pin) < 4:
        raise ValueError("PIN må være minst 4 tegn.")
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET pin_hash=?, must_change_pin=0, pin_expires_at=NULL WHERE username=?",
            (hash_pin(new_pin), username),
        )
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0] > 0
    finally:
        conn.close()


def check_pin_expired(username: str) -> bool:
    """Returner True hvis midlertidig PIN har utløpt."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT must_change_pin, pin_expires_at FROM users WHERE username=?", (username,)
        ).fetchone()
        if not row or not row["must_change_pin"]:
            return False
        if not row["pin_expires_at"]:
            return False
        expires = datetime.fromisoformat(row["pin_expires_at"])
        return datetime.now(timezone.utc) > expires
    finally:
        conn.close()


# ── Keypair (per-user encryption) ─────────────────────────────────────────────

def store_keypair(username: str, public_key_b64: str,
                  encrypted_private_key: str, argon2_salt_b64: str) -> bool:
    """Store or overwrite the user's crypto keypair fields."""
    conn = _get_conn()
    try:
        conn.execute(
            """UPDATE users SET public_key=?, encrypted_private_key=?, argon2_salt=?
               WHERE username=?""",
            (public_key_b64, encrypted_private_key, argon2_salt_b64, username),
        )
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0] > 0
    finally:
        conn.close()


def get_keypair_data(username: str) -> Optional[dict]:
    """Return crypto fields for a user: {public_key, encrypted_private_key, argon2_salt}.
    Returns None if user has no keypair (system accounts)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT public_key, encrypted_private_key, argon2_salt FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if not row or not row["public_key"]:
            return None
        return {
            "public_key": row["public_key"],
            "encrypted_private_key": row["encrypted_private_key"],
            "argon2_salt": row["argon2_salt"],
        }
    finally:
        conn.close()


def get_public_key_b64(username: str) -> Optional[str]:
    """Return only the base64-encoded public key (for vault sealing, no PIN needed)."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT public_key FROM users WHERE username=?", (username,)
        ).fetchone()
        return row["public_key"] if row else None
    finally:
        conn.close()


def has_keypair(username: str) -> bool:
    """True if the user has a crypto keypair stored."""
    return get_keypair_data(username) is not None


def delete_user(username: str) -> bool:
    """Sletter bruker. Admin-brukeren kan ikke slettes hvis det er siste admin."""
    conn = _get_conn()
    try:
        user = conn.execute(
            "SELECT role FROM users WHERE username=?", (username,)
        ).fetchone()
        if not user:
            return False
        if user["role"] == "admin":
            admin_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1"
            ).fetchone()[0]
            if admin_count <= 1:
                raise ValueError("Kan ikke slette siste admin-bruker.")
        conn.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()
        return True
    finally:
        conn.close()
