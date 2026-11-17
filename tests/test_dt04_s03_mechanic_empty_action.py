"""DT04 2.7: mechanic called without an action is guided, not answered with «Unknown action ''»."""

import asyncio

import pytest

from kaare_core.tools import executor_agents


@pytest.mark.parametrize("args", [{}, {"action": ""}, {"action": "  "}])
@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_missing_action_is_guided(monkeypatch, args, lang):
    monkeypatch.setattr(executor_agents, "get_lang", lambda *_: lang)
    out = asyncio.run(executor_agents.dispatch("mechanic", dict(args)))
    for option in ("search", "delegate", "result", "cancel", "comment"):
        assert option in out
    assert "''" not in out and "Ukjent action" not in out and "Unknown action" not in out
