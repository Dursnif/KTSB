"""User dicts that leave the server never carry PIN hashes or key material (P128 finding):
an encrypted private key + its Argon2 salt would let anyone brute-force a 6-digit PIN offline."""

import pytest

from kaare_core.users import auth, store

SECRET_FIELDS = (
    "pin_hash",
    "pin_expires_at",
    "encrypted_private_key",
    "argon2_salt",
    "system_encrypted_private_key",
)


@pytest.fixture
def anna(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    store.create_user(
        username="anna", display_name="Anna", role="adult", pin="482915", must_change_pin=False
    )
    store.store_keypair("anna", "cHVi", "ZW5jcnlwdGVk", "c2FsdA==")
    store.store_system_encrypted_key("anna", "c3lzdGVt")
    return "anna"


def test_get_user_and_list_users_strip_secrets(anna):
    for user in (store.get_user(anna), *store.list_users()):
        for field in SECRET_FIELDS:
            assert field not in user, field


def test_key_material_still_reachable_for_key_handling(anna):
    kp = store.get_keypair_data(anna)
    assert kp["encrypted_private_key"] == "ZW5jcnlwdGVk" and kp["argon2_salt"] == "c2FsdA=="
    assert store.get_system_encrypted_key(anna) == "c3lzdGVt"


def test_login_payload_strips_secrets(anna, monkeypatch):
    monkeypatch.setattr(auth, "touch_last_seen", lambda *_: None)
    result = auth.login(anna, "482915")
    for field in (*SECRET_FIELDS, "public_key"):
        assert field not in result["user"], field
