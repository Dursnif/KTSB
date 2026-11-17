"""Receipts for the user_profile tool: «set_field» (a fact the user just said) and «update» (an observation) come back as
one receipt built by code — the field and value actually stored, in whose profile — which Kåre repeats. A refused or failed
write has no receipt. No live state: the profile store is faked. Placeholders only."""

import asyncio

import pytest

from kaare_core import receipts
from kaare_core.tools import executor_personality as ep


@pytest.fixture
def store(monkeypatch):
    saved = []
    monkeypatch.setattr(ep, "update_nested_profile_field",
                        lambda uid, sec, field, value: saved.append((uid, sec, field, value)) or
                        ("OK" if sec != "nope" else "Unknown section: 'nope'"))
    monkeypatch.setattr(ep, "add_observation", lambda uid, text: saved.append((uid, text)))
    return saved


def _tool(args):
    return asyncio.run(ep.dispatch("user_profile", {"_user_id": "user_x", **args}))


def test_set_field_receipt(store):
    out = _tool({"action": "set_field", "section": "food_drink_health", "field": "favorite_meal", "value": "taco"})
    r = receipts.parse(out)
    assert r == [{"kind": "profile_field", "ref": "profile:user_x:food_drink_health.favorite_meal"}]
    assert "favorite_meal = taco" in out and receipts.instruction("nb") in out


def test_observation_receipt(store):
    out = _tool({"action": "update", "observation": "Liker å gå tur om morgenen"})
    assert [x["kind"] for x in receipts.parse(out)] == ["observation"]


def test_refused_write_has_no_receipt(store):
    assert receipts.parse(_tool({"action": "set_field", "section": "nope", "field": "x", "value": "y"})) == []
    assert receipts.parse(_tool({"action": "update", "observation": ""})) == []
