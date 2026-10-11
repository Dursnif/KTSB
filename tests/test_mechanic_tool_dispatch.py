"""Mechanic tool dispatch (P119 Phase 2): only offered tools run (B7), stray characters in an
action value are cleaned (B8), resources are ready-computed and labelled (B9).
No model, no network: the Ollama call and the agent-pool lock are replaced with fakes."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from kaare_core.agents.mechanic import tools as mech
from kaare_core.tools import shared_tools
from kaare_core.tools.i18n import t

# ── B8: action normalisation ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tool,raw,expected",
    [
        ("utforsk", "søk}", "søk"),
        ("utforsk", " les ", "les"),
        ("utforsk", '"liste"', "liste"),
        ("utforsk", "LES", "les"),
        ("inspiser", "logg>", "logg"),
        ("inspiser", "git_log>", "git_log"),
        ("inspiser", "ressurser]", "ressurser"),
        ("hukommelse", "skriv'", "skriv"),
        ("utforsk", "tull", "tull"),  # no match: cleaned value, tool reports unknown
        ("nettsøk", "x}", "x"),  # tool without an action enum: only cleaned
    ],
)
def test_normalize_action(tool, raw, expected):
    assert mech.normalize_action(tool, raw) == expected


def test_enums_come_from_the_schemas():
    for tool in mech.MECHANIC_TOOLS:
        action = tool["function"].get("parameters", {}).get("properties", {}).get("action", {})
        if "enum" in action:
            assert mech._ACTION_ENUMS[tool["function"]["name"]] == action["enum"]


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_unknown_action_lists_valid_values(lang):
    out = asyncio.run(mech.execute_tool("utforsk", {"action": "tull"}, lang))
    for option in ("les", "liste", "søk"):
        assert option in out
    assert "tull" in out


def test_stray_character_action_runs():
    out = asyncio.run(
        mech.execute_tool("utforsk", {"action": "liste}", "path": "/kaare/tests"}, "en")
    )
    assert "Unknown action" not in out


# ── B7: only offered tools are executed ──────────────────────────────────────


def test_not_offered_tool_is_refused(monkeypatch):
    replies = iter(
        [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "hukommelse",
                                "arguments": {"action": "skriv", "tekst": "x"},
                            }
                        },
                        {
                            "function": {
                                "name": "utforsk",
                                "arguments": {"action": "liste", "path": "/kaare/tests"},
                            }
                        },
                    ],
                }
            },
            {"message": {"content": "done", "tool_calls": []}},
        ]
    )
    sent_payloads, executed, logged = [], [], []

    async def fake_post_chat(client, url, payload, **kwargs):
        sent_payloads.append(payload)
        return next(replies)

    @asynccontextmanager
    async def fake_lock(*args, **kwargs):
        yield "http://127.0.0.1:11447"

    async def fake_execute(name, args, lang=None):
        executed.append(name)
        return "ok"

    monkeypatch.setattr(mech, "post_chat", fake_post_chat)
    monkeypatch.setattr(mech, "lock_agent_pool", fake_lock)
    monkeypatch.setattr(mech, "execute_tool", fake_execute)
    monkeypatch.setattr(mech, "_log_tool_call", lambda *a, **k: logged.append(a[0]))

    offered = mech._tools_by_name("utforsk")
    out = asyncio.run(
        mech.ask_with_tools([{"role": "user", "content": "x"}], tools=offered, lang="en")
    )
    assert out == "done"
    assert executed == ["utforsk"]  # hukommelse never ran
    assert logged == ["hukommelse", "utforsk"]  # refusal and real call both in tool_calls.log
    tool_msgs = [m for m in sent_payloads[1]["messages"] if m["role"] == "tool"]
    refusal = t("mech_tool_not_offered", "en", tool="hukommelse", available="utforsk")
    assert tool_msgs[0]["content"] == refusal


# ── B9: resources ready-computed ─────────────────────────────────────────────


class _FakeRun:
    returncode = 0
    stdout = "0, Card A, 22976, 24467, 3\n1, Card B, 14605, 16380, 0\n"


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_resources_precomputed(monkeypatch, lang):
    monkeypatch.setattr(shared_tools._sp, "run", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(shared_tools, "get_vllm_gpu_ids", lambda: {0})
    out = shared_tools.check_resources({}, lang)
    assert (
        t(
            "sys_res_gpu",
            lang,
            index="0",
            name="Card A",
            used=22976,
            total=24467,
            free=1491,
            pct_free=6,
            util="3",
        )
        in out
    )
    assert (
        t(
            "sys_res_gpu",
            lang,
            index="1",
            name="Card B",
            used=14605,
            total=16380,
            free=1775,
            pct_free=11,
            util="0",
        )
        in out
    )
    assert out.count(t("sys_res_gpu_vllm", lang)) == 1  # only under the vLLM card
    assert out.index(t("sys_res_gpu_vllm", lang)) > out.index("Card A")
    assert out.index(t("sys_res_gpu_vllm", lang)) < out.index("Card B")


def test_resources_without_vllm_has_no_note(monkeypatch):
    monkeypatch.setattr(shared_tools._sp, "run", lambda *a, **k: _FakeRun())
    monkeypatch.setattr(shared_tools, "get_vllm_gpu_ids", lambda: set())
    assert t("sys_res_gpu_vllm", "en") not in shared_tools.check_resources({}, "en")


def test_resources_without_nvidia_smi(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(shared_tools._sp, "run", boom)
    assert t("sys_gpu_unavailable", "de") in shared_tools.check_resources({}, "de")
