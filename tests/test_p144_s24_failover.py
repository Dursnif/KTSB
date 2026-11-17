"""P144-s24 item 1 — the Frigate 9B calls fail over to the other agent-pool instance when their own cannot be reached.

2026-10-30 23:08 → 00:30: `kaare_agents2` (11447) was stopped; every `frigate` call (share_with: miss_kare) failed with
«All connection attempts failed» while `kaare_agents1` (11445) was up and idle — ep 105/106 were concluded without a
look. The lean call (s06c: no lock, no thinking) stays as it is on the normal path; only a *connection* failure tries
the next instance in `model_lock.pool_order()` (config, never a hardcoded port). One instance = no failover (rule 24).
A model that answers badly (HTTP 500, empty text) is not a connection failure and is not retried elsewhere.

No real HTTP (httpx patched), no live logs (`_log_llm_call_bg` patched).
"""

import asyncio

import httpx
import pytest

import adapters.llm_adapter as llm_adapter

OWN = "http://127.0.0.1:11447"
OTHER = "http://127.0.0.1:11445"


class _Resp:
    status_code = 200

    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(self.status_code))

    def json(self):
        return self._data


def _client(calls: list, down: set[str], status: dict | None = None):
    class _C:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append(url)
            base = url.rsplit("/api/", 1)[0]
            if base in down:
                raise httpx.ConnectError("All connection attempts failed")
            return _Resp({"message": {"content": '{"person": true}'}, "done_reason": "stop"},
                         (status or {}).get(base, 200))

    return _C


@pytest.fixture
def env(monkeypatch):
    calls: list[str] = []
    state = {"calls": calls, "down": set(), "status": {}, "pool": [OWN, OTHER]}
    monkeypatch.setattr(llm_adapter.httpx, "AsyncClient", _client(calls, state["down"], state["status"]))
    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(llm_adapter, "log_think", lambda **kw: None)
    monkeypatch.setattr(llm_adapter, "_get_model", lambda r: "nine-b")
    monkeypatch.setattr(llm_adapter, "pool_order", lambda url: [u for u in state["pool"] if u == url.rstrip("/")]
                        + [u for u in state["pool"] if u != url.rstrip("/")] if url.rstrip("/") in state["pool"]
                        else [url.rstrip("/")])
    monkeypatch.setattr(llm_adapter, "CFG", {
        "miss_kare": {"provider": "ollama", "base_url": OWN, "model_role": "miss_kare", "agent_pool": True,
                      "options": {"num_ctx": 32768}, "timeout": 120},
        "fallback": {"provider": "ollama", "base_url": OTHER, "model_role": "miss_kare", "agent_pool": True,
                     "options": {"num_ctx": 32768}, "timeout": 120},
        "frigate": {"enabled": True, "share_with": "miss_kare"},
        "default": {"provider": "vllm", "base_url": "http://127.0.0.1:11440", "model_role": "kare"},
    })
    return state


def _ask(role="frigate"):
    return asyncio.run(llm_adapter.ask_llm_role(role, "look", images=["aGk="], rid="frigate-look-1"))


def test_own_instance_down_call_lands_on_the_other(env):
    env["down"].add(OWN)
    res = _ask()
    assert env["calls"] == [f"{OWN}/api/chat", f"{OTHER}/api/chat"]
    assert res["ok"] and res["text"] == '{"person": true}'
    assert res["meta"]["base_url"] == OTHER and res["meta"]["failover_from"] == OWN


def test_normal_path_is_one_call_on_its_own_instance(env):
    res = _ask()
    assert env["calls"] == [f"{OWN}/api/chat"] and res["ok"]
    assert "failover_from" not in res["meta"]


def test_a_bad_answer_is_not_failed_over(env):
    env["status"][OWN] = 500  # the model is there but answered badly — today's handling (P142), no second instance
    res = _ask()
    assert env["calls"] == [f"{OWN}/api/chat"]
    assert not res["ok"] and not res.get("unreachable")


def test_one_instance_means_no_failover_and_says_unreachable(env):
    env["pool"] = [OWN]
    env["down"].add(OWN)
    res = _ask()
    assert env["calls"] == [f"{OWN}/api/chat"]
    assert not res["ok"] and res["unreachable"] is True


def test_every_instance_down_says_unreachable(env):
    env["down"].update({OWN, OTHER})
    res = _ask()
    assert env["calls"] == [f"{OWN}/api/chat", f"{OTHER}/api/chat"]
    assert res["unreachable"] is True


def test_call_llm_chat_marks_only_connection_failures(env):
    env["down"].add(OWN)
    down = asyncio.run(llm_adapter.call_llm_chat("miss_kare", [{"role": "user", "content": "x"}]))
    assert down["unreachable"] is True
    env["down"].clear()
    env["status"][OWN] = 500
    bad = asyncio.run(llm_adapter.call_llm_chat("miss_kare", [{"role": "user", "content": "x"}]))
    assert not bad["ok"] and not bad.get("unreachable")
