"""
Kåre brukerdatabase — SQLite.

Lagrer brukere lokalt. Ingen sky, ingen tredjepart.
Fil: /kaare/state/users/users.db

Roller: child | teen | young_adult | adult | admin
"""

import base64
import logging
import sqlite3
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import bcrypt
from kaare_core.config import get_settings as _get_settings

logger = logging.getLogger(__name__)

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
ADMIN_TEMP_PIN_TTL_MINUTES = 10

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
        conn.execute("ALTER TABLE users ADD COLUMN vpn_access TEXT NOT NULL DEFAULT 'full_access'")
    # Crypto columns (per-user encryption)
    if "public_key" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN public_key TEXT")
    if "encrypted_private_key" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN encrypted_private_key TEXT")
    if "argon2_salt" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN argon2_salt TEXT")
    # Parent system (P64)
    if "is_parent" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN is_parent INTEGER NOT NULL DEFAULT 0")
    if "pin_required" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN pin_required INTEGER NOT NULL DEFAULT 0")
    if "managed_children" not in existing:
        conn.execute("ALTER TABLE users ADD COLUMN managed_children TEXT DEFAULT NULL")


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
    d.setdefault("vpn_access", "full_access")
    d["is_parent"] = bool(d.get("is_parent", 0))
    d["pin_required"] = bool(d.get("pin_required", 0))
    d.setdefault("managed_children", None)
    return d


def validate_pin_strength(pin: str) -> tuple[bool, str]:
    """Return (True, "") if PIN meets strength requirements, else (False, error_message).

    Minimum length from settings.yaml rate_limit section (default 6).
    Blocks trivial patterns: all-same digits, ascending/descending sequences.
    Existing users with shorter PINs are not affected until they change their PIN.
    """
    min_len = int(_get_settings().get("min_pin_length", 6))
    if len(pin) < min_len:
        return False, f"PIN must be at least {min_len} digits."
    if not pin.isdigit():
        return False, "PIN must contain only digits."
    # All same digits: 0000, 1111, ...
    if len(set(pin)) == 1:
        return False, "PIN must not be all the same digit."
    # Ascending sequential (e.g. 1234, 01234)
    if all(int(pin[i + 1]) - int(pin[i]) == 1 for i in range(len(pin) - 1)):
        return False, "PIN must not be a simple ascending sequence."
    # Descending sequential (e.g. 9876, 43210)
    if all(int(pin[i]) - int(pin[i + 1]) == 1 for i in range(len(pin) - 1)):
        return False, "PIN must not be a simple descending sequence."
    return True, ""


def _create_user_conn(conn, *, username, display_name, role, pin, avatar="",
                      must_change_pin=False) -> dict:
    if role not in VALID_ROLES:
        raise ValueError(f"Ugyldig rolle: {role}")
    if len(pin) < 4:
        raise ValueError("PIN must be at least 4 digits.")
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
    ok, msg = validate_pin_strength(pin)
    if not ok:
        raise ValueError(msg)
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
                vpn_access: Optional[str] = None,
                is_parent: Optional[bool] = None,
                pin_required: Optional[bool] = None,
                managed_children: Optional[str] = None) -> Optional[dict]:
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
    if is_parent is not None:
        fields.append("is_parent=?"); params.append(1 if is_parent else 0)
    if pin_required is not None:
        fields.append("pin_required=?"); params.append(1 if pin_required else 0)
    if managed_children is not None:
        fields.append("managed_children=?"); params.append(managed_children)
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


def generate_temp_pin(username: str) -> str:
    """Generate a 6-digit temp PIN for admin-assisted recovery. Requires allow_admin_pin_reset.

    Returns the plaintext PIN (shown once to admin). Stores only the bcrypt hash.
    Sets must_change_pin=1 and pin_expires_at = now + ADMIN_TEMP_PIN_TTL_MINUTES.
    """
    pin = str(secrets.randbelow(900000) + 100000)  # 100000–999999
    pin_hash = hash_pin(pin)
    expires = (
        datetime.now(timezone.utc) + timedelta(minutes=ADMIN_TEMP_PIN_TTL_MINUTES)
    ).isoformat(timespec="seconds")
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET pin_hash=?, must_change_pin=1, pin_expires_at=? WHERE username=?",
            (pin_hash, expires, username),
        )
        conn.commit()
    finally:
        conn.close()
    return pin


def update_pin(username: str, new_pin: str) -> bool:
    ok, msg = validate_pin_strength(new_pin)
    if not ok:
        raise ValueError(msg)
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


def reencrypt_private_key(username: str, new_pin: str, current_private_key: bytes) -> bool:
    """Re-encrypt the stored private key blob with a new PIN-derived key.
    Called immediately after update_pin() when a user changes their own PIN.
    The public key and in-RAM session key are unchanged — only the encrypted-at-rest blob rotates."""
    kp = get_keypair_data(username)
    if not kp:
        return False
    try:
        from kaare_core.crypto import generate_salt, derive_key_from_pin, encrypt_private_key
        new_salt = generate_salt()
        new_derived = derive_key_from_pin(new_pin, new_salt)
        new_encrypted = encrypt_private_key(current_private_key, new_derived)
        return store_keypair(username, kp["public_key"], new_encrypted, base64.b64encode(new_salt).decode())
    except Exception as e:
        logger.error(f"[STORE] reencrypt_private_key failed for {username}: {e}")
        return False


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
