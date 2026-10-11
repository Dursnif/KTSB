"""DT03 R7 — a brand-new user's encrypted STM stopped being saved after the first turn.

save_snapshot() refuses to overwrite an existing .enc unless load_snapshot() decrypted it this
session (protects rich history from a thin post-restart session). The flag was only set by
load_snapshot(), so for a new user the first save created the .enc and every later save was
dropped silently until the next restart + login. Found on Verksted-pc: T1.enc held one turn an
hour after T1's conversation.
"""

import base64
import json

import nacl.public
import pytest

from kaare_core.crypto import unseal
from kaare_core.memory import short_term as st


@pytest.fixture
def keypair(monkeypatch):
    sk = nacl.public.PrivateKey.generate()
    pub_b64 = base64.b64encode(bytes(sk.public_key)).decode()
    monkeypatch.setattr(st, "get_public_key_b64", lambda uid: pub_b64)
    return bytes(sk)


def _turns(path, private_key) -> list[str]:
    data = json.loads(unseal(path.read_text(encoding="utf-8"), private_key))
    return [t["text"] for t in data["dialog"]]


def test_new_user_keeps_saving_after_the_first_snapshot(tmp_path, keypair):
    stm = st.ShortTermMemory(user_id="newbie")
    path = tmp_path / "newbie.json"
    stm.add_dialog("user", "first question", user_id="newbie")
    assert stm.save_snapshot(str(path))
    stm.add_dialog("assistant", "first answer", user_id="newbie")
    stm.add_dialog("user", "second question", user_id="newbie")
    assert stm.save_snapshot(str(path))
    assert _turns(path.with_suffix(".enc"), keypair) == [
        "first question",
        "first answer",
        "second question",
    ]


def test_thin_session_after_restart_still_cannot_overwrite_rich_history(tmp_path, keypair):
    path = tmp_path / "u.json"
    rich = st.ShortTermMemory(user_id="u")
    for i in range(5):
        rich.add_dialog("user", f"old turn {i}", user_id="u")
    rich.save_snapshot(str(path))

    after_restart = st.ShortTermMemory(user_id="u")  # not logged in yet → .enc not loaded
    after_restart.add_dialog("user", "one turn before login", user_id="u")
    after_restart.save_snapshot(str(path))
    assert _turns(path.with_suffix(".enc"), keypair) == [f"old turn {i}" for i in range(5)]
