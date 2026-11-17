"""Kåre's reflection waits for the camera's work on the main model (P144, 2026-11-05).

«KÅRE REFLECTION går samtidig som analyse … den er sist i køen og skal alltid vente hvis opptatt, og låsen/køen må vite om
27B er helt ferdig.» The camera's model calls are lock-free toward users (s06c), so the reflection's busy check never saw
them. Order: user > camera analysis > reflection. The camera still never makes a user wait.
"""

import asyncio

import pytest

from adapters import llm_adapter
from kaare_core import llm_fallback
from kaare_core.frigate_user import conclude


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Never touch the house: the 9B-fallback flag goes to tmp, and no real HTTP call can leave the test.
    (A first draft of this file reached the live 9B and left `runtime/kare_9b_fallback.json` on, 2026-11-05 21:10.)"""
    monkeypatch.setattr(llm_fallback, "_FLAG_PATH", tmp_path / "kare_9b_fallback.json")

    class _NoNetwork:
        def __init__(self, *a, **k):
            raise AssertionError("network call in a test")

    monkeypatch.setattr(llm_adapter.httpx, "AsyncClient", _NoNetwork)


def _no_side_calls(monkeypatch, reached: list):
    async def fake_vllm(**kw):
        reached.append("vllm")
        return {"ok": True, "text": "ok", "tool_calls": None, "think_content": "", "think_closed": True,
                "message": {"role": "assistant", "content": "ok"}, "usage": {}}

    async def no_relevant(*a, **k):
        return ""

    monkeypatch.setattr(llm_adapter, "_call_vllm_chat", fake_vllm)
    monkeypatch.setattr(llm_adapter, "_kare_is_busy", lambda: False)
    monkeypatch.setattr(llm_adapter, "is_fallback_active", lambda: False)
    monkeypatch.setattr(llm_adapter._memory_prompt, "relevant_block", no_relevant)
    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setitem(llm_adapter.CFG["default"], "provider", "vllm")


async def _reflection_call():
    return await llm_adapter.ask_llm_with_tools(
        messages=[{"role": "user", "content": "x"}], tools=[], rid="rid-test-refl", user_id="kare", source="jang"
    )


def test_the_reflection_is_busy_while_an_episode_is_analysed(monkeypatch):
    reached: list = []
    _no_side_calls(monkeypatch, reached)

    async def run():
        async with conclude._analysing():
            return await _reflection_call()

    result = asyncio.run(run())
    assert result.get("meta", {}).get("busy") is True
    assert reached == []  # the main model was never called


def test_the_reflection_runs_when_the_camera_is_idle(monkeypatch):
    reached: list = []
    _no_side_calls(monkeypatch, reached)
    try:
        result = asyncio.run(_reflection_call())
    except Exception:  # the fake answer may not satisfy every later step; what counts is that the model was reached
        result = {}
    assert not result.get("meta", {}).get("busy")
    assert reached == ["vllm"]


def test_a_camera_call_on_the_main_model_counts_as_busy_and_one_on_another_model_does_not(monkeypatch):
    seen: list = []

    async def fake_chat(target, messages, **kw):
        seen.append((target, llm_adapter.main_model_background_busy()))
        return {"ok": True, "text": "{}"}

    monkeypatch.setattr(llm_adapter, "call_llm_chat", fake_chat)
    main_url = llm_adapter.CFG["default"].get("base_url")
    monkeypatch.setitem(llm_adapter.CFG, "role_on_main", {"share_with": "default"})
    monkeypatch.setitem(llm_adapter.CFG, "role_elsewhere", {"provider": "ollama", "base_url": "http://elsewhere:1", "model_role": "x"})
    asyncio.run(llm_adapter.ask_llm_role("role_on_main", "p"))
    asyncio.run(llm_adapter.ask_llm_role("role_elsewhere", "p"))
    assert main_url  # the house config has a main model URL
    assert [busy for _, busy in seen] == [True, False]
    assert llm_adapter.main_model_background_busy() is False  # released after the call
