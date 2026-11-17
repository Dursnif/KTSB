"""TV pairing files that KTSB cannot read must give an honest answer, not a crash (DT04 handoff, item 7).

The house's `state/tv_pairing/` folders were made by a pairing run as another user (700/600). The service then gets
PermissionError from `Path.exists()` and from `os.chmod` on a folder it does not own.
"""

import os

import pytest

from kaare_core.voice import tv_control as tc


@pytest.fixture
def pair_dir(tmp_path, monkeypatch):
    d = tmp_path / "pair"
    host = d / "10.0.0.5"
    host.mkdir(parents=True)
    (host / "samsung_token.txt").write_text("tok", encoding="utf-8")
    monkeypatch.setattr(tc, "PAIR_DIR", d)
    monkeypatch.setattr(tc, "ACTION_LOG", tmp_path / "tv.log")
    os.chmod(host, 0)  # like a folder owned by someone else: no access for this process
    yield d
    os.chmod(host, 0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
def test_is_paired_on_unreadable_folder_does_not_crash(pair_dir):
    assert tc.is_paired("10.0.0.5", "samsung_ws") is False
    assert tc.is_paired("10.0.0.5", "atv_remote") is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any folder")
def test_unreadable_pairing_gives_an_honest_reason(pair_dir):
    res = tc.samsung_act("10.0.0.5", "status")
    assert res["outcome"] == tc.NOT_PAIRED
    assert "not readable" in res["error"]


def test_secrets_dir_survives_a_folder_it_does_not_own(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "PAIR_DIR", tmp_path / "pair")

    def no_chmod(path, mode):
        raise PermissionError(1, "Operation not permitted", str(path))

    monkeypatch.setattr(tc.os, "chmod", no_chmod)
    d = tc.secrets_dir("10.0.0.6")
    assert d.is_dir()
