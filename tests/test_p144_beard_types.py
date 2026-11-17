"""
P144 — beard is more than yes/no (2026-10-26: moustache, circle beard, full beard).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p144_beard_types.py -v
"""

import sys

import pytest
import yaml

sys.path.insert(0, "/kaare")

from kaare_core.frigate_user import faces


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    path = tmp_path / "frigate_faces.yaml"
    monkeypatch.setattr(faces, "CONFIG_PATH", path, raising=False)
    return path


def test_beard_types_allowed():
    for v in ("no", "moustache", "circle_beard", "full", "unknown"):
        assert v in faces.ATTRIBUTES["beard"]


def test_legacy_yes_still_reads(cfg):
    cfg.write_text(yaml.safe_dump({"faces": {"Person A": {"attributes": {"beard": "yes"}}}}))
    assert faces.attribute_profile("Person A")["beard"] == "yes"


@pytest.mark.parametrize("beard", ["moustache", "circle_beard", "full"])
def test_beard_type_round_trip(cfg, beard):
    cfg.write_text(yaml.safe_dump({"faces": {"Person A": {"attributes": {"beard": beard}}}}))
    assert faces.attribute_profile("Person A")["beard"] == beard
