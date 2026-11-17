"""Receipts for world (update_field, add, set_var) and household (set_away, set_home): one receipt from code with the stored
id / the state actually written. A refused write and a read have no receipt. Stores are faked or in tmp_path."""

import asyncio

import pytest

import kaare_core.memory.knowledge_store as ks
from kaare_core import receipts
from kaare_core.tools import executor_household as eh
from kaare_core.tools import executor_world as ew


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(ks, "_initialized", False)


def _w(args):
    return asyncio.run(ew.dispatch("world", {"_user_id": "user_x", **args}))


@pytest.mark.parametrize("args", [
    {"action": "update_field", "category": "Hus", "field": "byggeår", "value": "1978"},
    {"action": "add", "category": "Notater", "text": "Det er fiber i huset"},
    {"action": "set_var", "key": "sommer_temp", "value": "21"},
])
def test_world_saves_have_a_receipt(world, args):
    r = receipts.parse(_w(args))
    assert len(r) == 1 and r[0]["kind"] == "world" and r[0]["ref"].startswith("world:")


def test_world_refused_and_read_have_none(world):
    assert receipts.parse(_w({"action": "add", "text": ""})) == []
    assert receipts.parse(_w({"action": "read"})) == []


@pytest.fixture
def house(monkeypatch):
    state = {"mode": "home", "last_updated": "2026-11-12T22:00"}

    def away(reason=None, expected_return=None):
        state.update(mode="away", expected_return=expected_return, last_updated="2026-11-12T22:01")

    def home():
        state.update(mode="home", last_updated="2026-11-12T22:02")

    monkeypatch.setattr(eh, "_set_away", away)
    monkeypatch.setattr(eh, "_set_home", home)
    monkeypatch.setattr(eh, "read_household_state", lambda: dict(state))
    monkeypatch.setattr(eh, "_write_context_entry_for_admins", lambda **k: None)
    return state


def test_household_set_away_and_home_receipts(house):
    out = asyncio.run(eh.dispatch("household", {"action": "set_away", "expected_return": "2026-11-20"}, "user_x"))
    assert [r["kind"] for r in receipts.parse(out)] == ["household"] and "2026-11-20" in out
    out = asyncio.run(eh.dispatch("household", {"action": "set_home"}, "user_x"))
    assert receipts.parse(out)[0]["ref"].startswith("household:home")
    assert receipts.parse(asyncio.run(eh.dispatch("household", {"action": "set_home"}, "user_x"))) == []  # already home
    assert receipts.parse(asyncio.run(eh.dispatch("household", {"action": "get_status"}, "user_x"))) == []
