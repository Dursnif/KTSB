"""Receipts for write_reflex (confirm / reject), dev_proposals (confirm / reject), timer (set) and kare_image (a made
image): one receipt from code with the stored id. Refusals have none. Stores are faked or in tmp_path; no real timer
fires, no image model is called. Placeholders only."""

import asyncio

import pytest

from kaare_core import receipts
from kaare_core.tools import executor, executor_reflexes, timer_service


@pytest.fixture
def reflexes(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_reflexes, "_PROPOSALS_PATH", tmp_path / "reflex_proposals.json")
    monkeypatch.setattr(executor_reflexes, "_RULES_PATH", tmp_path / "fastpath_rules.yaml")
    monkeypatch.setattr(executor_reflexes, "_is_allowed_reflex_contributor", lambda uid: True)
    monkeypatch.setattr(executor_reflexes._fp, "invalidate_cache", lambda: None)
    executor_reflexes._save_proposals([
        {"id": "p1", "phrase": "lys i gangen", "status": "pending", "provider": "ha", "action": "turn_on",
         "entity_id": "light.x"},
        {"id": "p2", "phrase": "musikk på", "status": "pending", "provider": "ha"}])


def _run(name, args):
    return asyncio.run(executor.execute_tool(name, {"_user_id": "user_x", **args}))


def test_reflex_confirm_and_reject(reflexes):
    out = _run("write_reflex", {"action": "confirm", "proposal_id": "p1"})
    assert [r["kind"] for r in receipts.parse(out)] == ["reflex"] and receipts.parse(out)[0]["ref"].startswith("reflex:rf_")
    out = _run("write_reflex", {"action": "reject", "proposal_id": "p2"})
    assert receipts.parse(out) == [{"kind": "reflex_rejected", "ref": "reflex_proposal:p2"}]
    assert receipts.parse(_run("write_reflex", {"action": "confirm", "proposal_id": "nope"})) == []


def test_dev_proposal_decision(monkeypatch):
    monkeypatch.setattr(executor.learning_store, "decide_proposal", lambda pid, status, user: {"id": pid} if pid == 7 else None)
    out = _run("dev_proposals", {"action": "confirm", "proposal_id": 7})
    assert receipts.parse(out) == [{"kind": "dev_proposal", "ref": "proposal:7"}]
    assert receipts.parse(_run("dev_proposals", {"action": "reject", "proposal_id": 8})) == []


def test_timer_set(monkeypatch):
    monkeypatch.setattr(timer_service, "_persist_timers", lambda: None)
    monkeypatch.setattr(timer_service, "_timers", {})
    out = _run("timer", {"action": "set", "prompt": "ta ut av ovnen", "in_seconds": 600})
    r = receipts.parse(out)
    assert len(r) == 1 and r[0]["kind"] == "timer" and r[0]["ref"] == "timer:" + next(iter(timer_service._timers))
    assert receipts.parse(_run("timer", {"action": "set", "prompt": "", "in_seconds": 600})) == []


def test_image_made(monkeypatch):
    async def fake(prompt, negative, user_id):
        return {"ok": True, "image_id": "img123"}

    monkeypatch.setattr(executor, "generate_image", fake)
    monkeypatch.setattr(executor, "_llm_cfg", lambda role: {"enabled": True})
    out = _run("kare_image", {"prompt": "en katt i solnedgang"})
    assert receipts.parse(out) == [{"kind": "image", "ref": "image:img123"}]
