"""Cloud replies with empty content carry an error that names the reason (P119 B6).
No network: httpx gets a MockTransport, the API key and cloud config are faked."""

import asyncio

import httpx
import pytest

from adapters import llm_adapter

_EMPTY = {
    "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "length"}],
    "usage": {"completion_tokens": 300, "completion_tokens_details": {"reasoning_tokens": 300}},
}
_OK = {"choices": [{"message": {"role": "assistant", "content": "Hei"}, "finish_reason": "stop"}]}


@pytest.fixture
def fake_cloud(monkeypatch):
    def install(reply: dict):
        real_client = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
        monkeypatch.setattr(
            llm_adapter.httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport)
        )
        monkeypatch.setattr(llm_adapter, "_load_env_file", lambda path: {"CLOUD_API_KEY": "test"})
        monkeypatch.setitem(
            llm_adapter.CFG, "cloud", {"base_url": "http://cloud.invalid/v1", "model_role": "cloud"}
        )
        monkeypatch.setattr(llm_adapter, "_get_model", lambda role: "test-model")

    return install


def _hf():
    return asyncio.run(
        llm_adapter._call_huggingface_chat(
            role="cloud",
            base_url="http://cloud.invalid/v1",
            model="test-model",
            messages=[{"role": "user", "content": "x"}],
        )
    )


def test_hf_empty_reply_names_reason(fake_cloud):
    fake_cloud(_EMPTY)
    res = _hf()
    assert res["ok"] is False
    assert res["error"] == "empty_content (finish_reason=length, reasoning_tokens=300)"


def test_hf_normal_reply_unchanged(fake_cloud):
    fake_cloud(_OK)
    res = _hf()
    assert res["ok"] is True and res["text"] == "Hei" and "error" not in res


def test_ask_llm_cloud_empty_reply_names_reason(fake_cloud):
    fake_cloud({"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    res = asyncio.run(llm_adapter.ask_llm_cloud("x"))
    assert res["ok"] is False
    assert res["error"] == "empty_content (finish_reason=length)"


def test_ask_llm_cloud_normal_reply_unchanged(fake_cloud):
    fake_cloud(_OK)
    res = asyncio.run(llm_adapter.ask_llm_cloud("x"))
    assert res == {"ok": True, "text": "Hei", "meta": {"role": "cloud_llm", "model": "test-model"}}
