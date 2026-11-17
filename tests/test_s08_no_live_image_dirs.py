"""DT04-s08 / N41 «Later» / T36: no test may create folders under the house's state/.

2026-11-16 18:09 and 21:51 a test run left empty folders state/images/u/{input,frigate}: image_store.save_image() does
mkdir() before write_bytes(); the open() guard in conftest stopped the write, the caller swallowed the AssertionError, and
the folder stayed. These tests never create anything in the house, also when red: they only read the path, or call
mkdir on a path whose parent does not exist (unguarded → FileNotFoundError, guarded → AssertionError)."""

import os
from pathlib import Path

import pytest

from kaare_core import image_store

LIVE_STATE = Path("/kaare/state").resolve()
# Parent does not exist and is never created: an unguarded mkdir(parents=False) fails on its own without touching disk.
_PROBE = LIVE_STATE / "__s08_guard_probe_missing_parent__" / "child"


def test_image_root_is_not_the_house_in_tests():
    assert not image_store._IMAGES_ROOT.resolve().is_relative_to(LIVE_STATE)


def test_save_image_lands_in_tmp(tmp_path):
    # Checked BEFORE save_image runs, so a red run can never create a folder in the house.
    root = image_store._IMAGES_ROOT.resolve()
    assert not root.is_relative_to(LIVE_STATE)
    assert root.is_relative_to(Path(tmp_path).resolve())
    image_id = image_store.save_image(b"\x89PNG", "s08_probe_user", "frigate")
    assert (image_store._IMAGES_ROOT / "s08_probe_user" / "frigate" / f"{image_id}.png").exists()


def test_path_mkdir_under_live_state_is_refused():
    assert not _PROBE.parent.exists()
    with pytest.raises(AssertionError):
        _PROBE.mkdir()
    assert not _PROBE.parent.exists()


def test_os_mkdir_under_live_state_is_refused():
    assert not _PROBE.parent.exists()
    with pytest.raises(AssertionError):
        os.mkdir(_PROBE)
    assert not _PROBE.parent.exists()


def test_mkdir_outside_live_state_still_works(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    os.makedirs(tmp_path / "c" / "d", exist_ok=True)
    assert (tmp_path / "a" / "b").is_dir() and (tmp_path / "c" / "d").is_dir()
