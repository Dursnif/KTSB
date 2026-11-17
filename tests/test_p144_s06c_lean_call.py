"""
P144-s06c — lean model call for the Frigate roles (`frigate`, `frigate_conclude`).

Also when the role shares Kåre's model (`share_with: default`, the shipped default):
- no thinking (vLLM `chat_template_kwargs.enable_thinking=false` / Ollama `think: false`),
- no Kåre system prompt (an optional `system:` on the role itself is honoured),
- no Kåre lock and not through ask_llm(),
- the shared role's URL/model/options (Ollama: the same num_ctx as the instance).
Kåre's own ask_llm() is unchanged: system prompt, lock, thinking as configured.

No real HTTP, no live logs (`_log_llm_call_bg` patched).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p144_s06c_lean_call.py -v
"""

import asyncio
import base64
import io
import sys

import pytest
from PIL import Image

sys.path.insert(0, "/kaare")

import adapters.llm_adapter as llm_adapter


def _png_b64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 80, 40)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


_IMG = _png_b64()
_KARE_SYSTEM = "KÅRES SYSTEMPROMPT med husstandsnavn"


class _FakeResponse:
    status_code = 200

    def __init__(self, data):
        self._data = data
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _fake_httpx(calls: list):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append({"url": url, "json": json})
            if url.endswith("/v1/chat/completions"):
                return _FakeResponse({"choices": [{"message": {"content": '{"persons": 1}'}}]})
            return _FakeResponse({"message": {"content": '{"persons": 1}'}})

    return _Client


class _Lock:
    """Stands in for Kåre's lock: records every acquire."""

    def __init__(self, used: list):
        self._used = used

    async def acquire(self):
        self._used.append("acquire")

    def release(self):
        pass


@pytest.fixture
def env(monkeypatch):
    state = {"http": [], "lock": [], "ask_llm": []}
    monkeypatch.setattr(llm_adapter.httpx, "AsyncClient", _fake_httpx(state["http"]))
    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(llm_adapter, "log_think", lambda **kw: None)
    monkeypatch.setattr(llm_adapter, "_get_kare_lock", lambda: _Lock(state["lock"]))
    monkeypatch.setattr(llm_adapter, "_build_system", lambda *a, **kw: _KARE_SYSTEM)
    monkeypatch.setattr(llm_adapter, "is_fallback_active", lambda: False)  # never touch runtime/ fallback state
    monkeypatch.setattr(llm_adapter, "_get_model", lambda r: {"kare": "kare-27b", "frigate": "vlm-x"}.get(r, r))

    def set_cfg(cfg):
        monkeypatch.setattr(llm_adapter, "CFG", cfg)

    state["set_cfg"] = set_cfg
    return state


_VLLM_DEFAULT = {
    "base_url": "http://127.0.0.1:11440",
    "provider": "vllm",
    "model_role": "kare",
    "system": "kare_system_key",
    "think": True,
    "options": {"num_predict": 2048, "temperature": 0.7},
    "timeout": 90,
}
_OLLAMA_DEFAULT = {
    "base_url": "http://127.0.0.1:11445",
    "provider": "ollama",
    "model_role": "kare",
    "system": "kare_system_key",
    "think": True,
    "options": {"num_ctx": 32768, "num_predict": 2048},
}


def _call(role="frigate", images=(_IMG,)):
    return asyncio.run(llm_adapter.ask_llm_role(role, "Beskriv bildet som JSON.", images=list(images), rid="rid-t"))


@pytest.mark.parametrize("role", ["frigate", "frigate_conclude"])
def test_shared_vllm_role_is_lean(env, role):
    env["set_cfg"]({"default": dict(_VLLM_DEFAULT), role: {"enabled": True, "share_with": "default"}})
    res = _call(role)
    assert res["ok"] and res["text"] == '{"persons": 1}'
    assert len(env["http"]) == 1
    call = env["http"][0]
    assert call["url"] == "http://127.0.0.1:11440/v1/chat/completions"
    body = call["json"]
    assert body["model"] == "kare-27b"
    assert body.get("chat_template_kwargs") == {"enable_thinking": False}, "thinking must be off"
    assert [m["role"] for m in body["messages"]] == ["user"], "no Kåre system prompt"
    assert _KARE_SYSTEM not in str(body)
    assert body.get("max_tokens") == 2048  # the shared role's options
    assert env["lock"] == [], "Kåre's lock must not be taken"


def test_shared_role_does_not_go_through_ask_llm(env, monkeypatch):
    env["set_cfg"]({"default": dict(_VLLM_DEFAULT), "frigate": {"enabled": True, "share_with": "default"}})

    async def boom(*a, **kw):
        raise AssertionError("ask_llm used for a Frigate role")

    monkeypatch.setattr(llm_adapter, "ask_llm", boom)
    assert _call()["ok"]


def test_missing_role_is_lean_on_default(env):
    env["set_cfg"]({"default": dict(_VLLM_DEFAULT)})
    _call("frigate")
    body = env["http"][0]["json"]
    assert body.get("chat_template_kwargs") == {"enable_thinking": False}
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert env["lock"] == []


def test_shared_ollama_role_thinking_off_same_num_ctx(env):
    env["set_cfg"]({"default": dict(_OLLAMA_DEFAULT), "frigate": {"enabled": True, "share_with": "default"}})
    _call()
    call = env["http"][0]
    assert call["url"] == "http://127.0.0.1:11445/api/chat"
    body = call["json"]
    assert body["think"] is False, "Ollama thinking must be off even when default thinks"
    assert body["options"]["num_ctx"] == 32768  # identical num_ctx per instance (GPU policy)
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["messages"][0]["images"] == [_IMG]  # Ollama native image field
    assert env["lock"] == []


def test_role_system_is_honoured(env):
    env["set_cfg"]({
        "default": dict(_VLLM_DEFAULT),
        "frigate": {"enabled": True, "share_with": "default", "system": "Du er et kamera."},
    })
    _call()
    msgs = env["http"][0]["json"]["messages"]
    assert msgs[0] == {"role": "system", "content": "Du er et kamera."}
    assert _KARE_SYSTEM not in str(msgs)


def test_own_vllm_model_has_thinking_off(env):
    env["set_cfg"]({
        "default": dict(_VLLM_DEFAULT),
        "frigate": {"provider": "vllm", "base_url": "http://10.9.9.9:8000", "model_role": "frigate"},
    })
    _call()
    body = env["http"][0]["json"]
    assert env["http"][0]["url"] == "http://10.9.9.9:8000/v1/chat/completions"
    assert body["model"] == "vlm-x"
    assert body.get("chat_template_kwargs") == {"enable_thinking": False}


def test_kare_chat_path_unchanged(env):
    """ask_llm() — Kåre's own call — still takes the lock, sends Kåre's system prompt and leaves thinking alone."""
    env["set_cfg"]({"default": dict(_VLLM_DEFAULT)})
    res = asyncio.run(llm_adapter.ask_llm("hei", rid="rid-t"))
    assert res["ok"]
    body = env["http"][0]["json"]
    assert body["messages"][0] == {"role": "system", "content": _KARE_SYSTEM}
    assert "chat_template_kwargs" not in body
    assert env["lock"] == ["acquire"]
