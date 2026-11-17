"""
P144-s03 — the `frigate` model role: the user chooses which LLM/VLM analyses Frigate events.

- Role shares `default` (the shipped default) or is missing → since P144-s06c a lean direct call to
  default's URL/model (thinking off, no Kåre system prompt, no Kåre lock) — never through ask_llm().
- Role has its own model → the analysis goes to that role's URL/model, with the image.
- An own Ollama model on an instance another role already uses gets that instance's num_ctx
  (a differing num_ctx forces a full Ollama reload — CLAUDE.md GPU policy).
- configs_default/llm.yaml ships `frigate: {share_with: default}` and the config migration adds it
  to existing installs; the GUI may edit the role.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p144_s03_frigate_role.py -v
"""

import asyncio
import copy
import sys
from pathlib import Path

import yaml

sys.path.insert(0, "/kaare")

import adapters.llm_adapter as llm_adapter
from kaare_core import model_enforcement
from kaare_core.config_migrations import fill_missing_keys
from kaare_core.routers import router_settings

_IMG = "aGVsbG8="


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, data):
        self._data = data

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
            if url.endswith("/v1/chat/completions"):  # vLLM (default in _setup)
                return _FakeResponse({"choices": [{"message": {"content": "Via default. [LANG:nb]"}}]})
            return _FakeResponse({"message": {"content": "En person ved døren. [LANG:nb]"}})

    return _Client


def _setup(monkeypatch, frigate_cfg, extra_cfg=None):
    """CFG with a vLLM default + the given frigate block; ask_llm records instead of calling."""
    cfg = {
        "default": {"base_url": "http://127.0.0.1:11440", "provider": "vllm", "model_role": "kare"},
        **(extra_cfg or {}),
    }
    if frigate_cfg is not None:
        cfg["frigate"] = frigate_cfg
    monkeypatch.setattr(llm_adapter, "CFG", cfg)
    monkeypatch.setattr(llm_adapter, "_get_model", lambda r: {"kare": "kare-27b", "frigate": "vlm-x"}[r])
    default_calls: list = []

    async def fake_ask_llm(prompt, images=None, rid="", **kw):
        default_calls.append({"images": images})
        return {"ok": True, "text": "Via default. [LANG:nb]"}

    monkeypatch.setattr(llm_adapter, "ask_llm", fake_ask_llm)
    http_calls: list = []
    monkeypatch.setattr(llm_adapter.httpx, "AsyncClient", _fake_httpx(http_calls))
    # call_llm_chat logs every call to the live logs/llm_calls.log — keep test calls out of it
    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    # _IMG is not a real image; the vLLM path would try to resize it
    monkeypatch.setattr(llm_adapter, "_resize_image_for_vllm", lambda uri, max_pixels: uri)
    return default_calls, http_calls


def _analyse() -> dict:
    """The call the camera pipe makes for L3 (frigate_user/pipe.py): the `frigate` role with the image, then the language
    tag stripped (P144 phase 6: the old responder that made this call before is gone)."""
    res = asyncio.run(llm_adapter.ask_llm_role("frigate", "Describe.", images=[_IMG], rid="rid-test-s03"))
    return {"analysis": model_enforcement.strip_language_tag(res.get("text") or "")}


def test_own_model_is_used(monkeypatch):
    default_calls, http_calls = _setup(
        monkeypatch,
        {"provider": "ollama", "base_url": "http://10.9.9.9:11434", "model_role": "frigate",
         "options": {"num_ctx": 8192}},
    )
    res = _analyse()
    assert default_calls == [], "the analysis ignored the frigate role and used default"
    assert len(http_calls) == 1
    call = http_calls[0]
    assert call["url"] == "http://10.9.9.9:11434/api/chat"
    assert call["json"]["model"] == "vlm-x"
    assert call["json"]["messages"][-1]["images"] == [_IMG]
    assert res["analysis"] == "En person ved døren."


def test_shared_with_default_is_lean_on_default(monkeypatch):
    default_calls, http_calls = _setup(monkeypatch, {"share_with": "default", "enabled": True})
    res = _analyse()
    assert default_calls == []  # P144-s06c: not through ask_llm (Kåre's lock + system prompt)
    assert len(http_calls) == 1
    assert http_calls[0]["url"] == "http://127.0.0.1:11440/v1/chat/completions"
    assert http_calls[0]["json"]["model"] == "kare-27b"
    assert http_calls[0]["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert res["analysis"] == "Via default."


def test_missing_role_is_lean_on_default(monkeypatch):
    default_calls, http_calls = _setup(monkeypatch, None)
    _analyse()
    assert default_calls == []
    assert http_calls[0]["url"] == "http://127.0.0.1:11440/v1/chat/completions"


def test_shared_ollama_instance_keeps_its_num_ctx(monkeypatch):
    pool_member = {"provider": "ollama", "base_url": "http://127.0.0.1:11447", "model_role": "miss_kare",
                   "options": {"num_ctx": 32768}, "agent_pool": True}
    _, http_calls = _setup(
        monkeypatch,
        {"provider": "ollama", "base_url": "http://127.0.0.1:11447", "model_role": "frigate",
         "options": {"num_ctx": 4096, "temperature": 0.3}},
        {"miss_kare": pool_member},
    )
    _analyse()
    opts = http_calls[0]["json"]["options"]
    assert opts["num_ctx"] == 32768
    assert opts["temperature"] == 0.3


def test_default_template_inherits_default():
    tpl = yaml.safe_load(Path("/kaare/configs_default/llm.yaml").read_text(encoding="utf-8"))
    assert tpl["frigate"] == {"enabled": True, "share_with": "default"}


def test_migration_adds_role_to_existing_install():
    tpl = yaml.safe_load(Path("/kaare/configs_default/llm.yaml").read_text(encoding="utf-8"))
    old = copy.deepcopy(tpl)
    del old["frigate"]
    added = fill_missing_keys(old, tpl)
    assert "frigate" in added
    assert old["frigate"] == {"enabled": True, "share_with": "default"}


def test_gui_may_edit_role():
    assert "frigate" in router_settings._EDITABLE_LLM_ROLES


def test_own_role_saved_by_gui_without_model_role(monkeypatch):
    # The GUI's share→own switch writes provider/base_url but no model_role; the model then
    # lives under models.yaml `frigate` (router: s.get("model_role", role)).
    _, http_calls = _setup(monkeypatch, {"provider": "ollama", "base_url": "http://10.9.9.9:11434"})
    _analyse()
    assert http_calls[0]["json"]["model"] == "vlm-x"
