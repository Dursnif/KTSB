"""J27 — Jing and Jang as KTSB processes; their models are the model roles `jing` / `jang` (Settings → LLM/Models).

- one rid per thought (`rid-jing-<ms>` / `rid-jang-<ms>`), traced as source «inner»;
- Jing's context: people from the camera memory as pseudonym ids, never names; the thought lands in jing_thoughts.txt;
- Jang: the last Jing blocks → one thought in inner_thoughts.txt, the Jing buffer trimmed;
- a role with a base_url is called through KTSB's model layer with its API key as Bearer — the key never in llm_calls.log;
- a role without a base_url uses the in-process model (one machine, rule 24);
- restart buttons: «jing» / «jang» are restartable units; the shipped templates are disabled with an empty address.
No live state: state files in tmp_path, no HTTP (the client is faked), no model. Placeholders only."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from adapters import llm_adapter
from kaare_core import inner_voice_runner as ivr
from kaare_core.routers import router_system
from kaare_core.tools import trace_reader

ROOT = Path(__file__).resolve().parent.parent
KEY = "test-key-9f8e7d"


@pytest.fixture
def state(tmp_path, monkeypatch):
    for name in ("DIGEST", "JING_THOUGHTS", "INNER_THOUGHTS", "PERSONALITY"):
        monkeypatch.setattr(ivr, name, tmp_path / f"{name.lower()}.txt")
    monkeypatch.setattr(ivr, "get_lang", lambda uid: "nb")
    monkeypatch.setattr(ivr.query, "available", lambda: True)
    monkeypatch.setattr(ivr.inner_context, "people", lambda since, lang, now=None: ["[10:00] person-ab12cd (husstand) — Inngang"])
    monkeypatch.setattr(ivr, "_settings", lambda v: {"interval_seconds": 180, "max_tokens": 50, "turns_back": 3,
                                                     "inner_thoughts_retention_hours": 24})
    monkeypatch.setattr(ivr, "uses_model_role", lambda role: True)
    calls = []

    async def fake_chat(role, messages, **kw):
        calls.append((role, messages, kw))
        return {"ok": True, "text": f"<think>x</think>tanke fra {role}"}

    monkeypatch.setattr(ivr, "call_llm_chat", fake_chat)
    return tmp_path, calls


def test_jing_thought_one_rid_no_names(state):
    _tmp, calls = state
    res = asyncio.run(ivr.jing_once())
    assert res["rid"].startswith("rid-jing-") and res["written"] and res["text"] == "tanke fra jing"
    role, messages, kw = calls[0]
    assert role == "jing" and kw["rid"] == res["rid"] and kw["disable_thinking"] is True
    assert "person-ab12cd" in messages[1]["content"]
    assert "[Jing " in ivr.JING_THOUGHTS.read_text() and "tanke fra jing" in ivr.JING_THOUGHTS.read_text()
    assert trace_reader.infer_source(res["rid"]) == "inner"


def test_jang_distils_and_trims(state):
    _tmp, calls = state
    ivr.JING_THOUGHTS.write_text("".join(f"\n\n[Jing 10:0{i}]\nblokk {i}" for i in range(5)))
    res = asyncio.run(ivr.jang_once())
    assert res["rid"].startswith("rid-jang-") and calls[0][0] == "jang"
    assert "blokk 4" in calls[0][1][1]["content"] and "blokk 1" not in calls[0][1][1]["content"]  # turns_back 3
    assert "tanke fra jang" in ivr.INNER_THOUGHTS.read_text()
    assert len(ivr.jing_blocks(ivr.JING_THOUGHTS.read_text())) == 3


def test_no_base_url_uses_the_in_process_model(state, monkeypatch):
    monkeypatch.setattr(ivr, "uses_model_role", lambda role: False)
    monkeypatch.setattr(ivr, "_local_generate", lambda role, s, u, n: "lokal tanke")
    monkeypatch.setattr(ivr, "call_llm_chat", lambda *a, **k: pytest.fail("no model role → no HTTP"))
    assert asyncio.run(ivr.jing_once(write=False))["text"] == "lokal tanke"


class _Resp:
    status_code = 200
    text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}}


def test_key_is_sent_as_bearer_and_never_logged(monkeypatch, tmp_path):
    keys = tmp_path / "llm_keys.env"
    keys.write_text(f"JING_API_KEY={KEY}\n")
    monkeypatch.setattr(llm_adapter, "_LLM_KEYS_FILE", str(keys))
    monkeypatch.setitem(llm_adapter.CFG, "jing", {"provider": "vllm", "base_url": "http://192.0.2.5:8095",
                                                   "model_role": "jing", "api_key_env": "JING_API_KEY", "enabled": True})
    monkeypatch.setattr(llm_adapter, "_get_model", lambda role: role)
    seen, logged = [], []

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            seen.append((url, headers))
            return _Resp()

    monkeypatch.setattr(llm_adapter.httpx, "AsyncClient", Client)
    monkeypatch.setattr(llm_adapter.log_buffer, "write", lambda path, line: logged.append(line))
    res = asyncio.run(llm_adapter.call_llm_chat("jing", [{"role": "user", "content": "hei"}], rid="rid-jing-1"))
    assert res["ok"] and seen[0][0] == "http://192.0.2.5:8095/v1/chat/completions"
    assert seen[0][1]["Authorization"] == f"Bearer {KEY}"
    assert KEY not in json.dumps(logged)


def test_restart_buttons_and_shipped_templates():
    assert router_system._RESTARTABLE_SERVICES["jing"] == "kaare-jing.service"
    assert router_system._RESTARTABLE_SERVICES["jang"] == "kaare-jang.service"
    llm = yaml.safe_load((ROOT / "configs_default" / "llm.yaml").read_text())
    for v in ("jing", "jang"):
        assert llm[v]["base_url"] == "" and llm[v]["enabled"] is False and llm[v]["api_key_env"] == f"{v.upper()}_API_KEY"
        unit = (ROOT / "scripts" / "systemd" / f"kaare-{v}.service").read_text()
        assert f"kaare_inner_voices.py --voice {v}" in unit
