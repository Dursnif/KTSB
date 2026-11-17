"""Miss Kåre judges the whole exchange, not a cut-off fragment (owner decision 2026-11-16, option A; KTSB-visjon thread 4).

rid-1794846135454: Kåre had asked a camera question («Var det deg på utekameraet …?»); the user answered «nei ikke meg».
Miss Kåre saw only that turn — «nei ikke meg» + Kåre's reply — and read it as «a little apology rather than an answer».
Now she gets Kåre's previous turn (the user's message and Kåre's reply, which carried the question) before this one.
No model, no network: the LLM call and the pool lock are fakes. Placeholders only."""

import asyncio
import contextlib

import pytest

from kaare_core.agents.miss_kare import evaluator as ev
from kaare_core.tools.i18n import t

QUESTION = "Var det deg på utekameraet i går kl. 15:55?"


@pytest.fixture
def seen(monkeypatch):
    sent: list = []

    async def llm(messages, url):
        sent.append(messages)
        return "[STILLE]"

    @contextlib.asynccontextmanager
    async def lock(*a, **k):
        yield "http://example.invalid"

    monkeypatch.setattr(ev, "_llm_call", llm)
    monkeypatch.setattr(ev, "lock_agent_pool", lock)
    monkeypatch.setattr(ev, "is_fallback_active", lambda: False)
    monkeypatch.setattr(ev, "get_display_name", lambda uid: None)
    monkeypatch.setattr(ev, "_load_portrait", lambda uid: "")
    monkeypatch.setattr(ev, "get_lang", lambda uid: "nb")
    return sent


def _user_content(sent):
    return next(m["content"] for m in sent[0] if m["role"] == "user")


def test_she_sees_the_question_kare_asked_before_the_short_answer(seen):
    asyncio.run(ev.evaluate("nei ikke meg", "Takk, da noterer jeg det.", "user_x",
                            previous=("hva skjedde i går?", f"Litt bevegelse ute. {QUESTION}")))
    content = _user_content(seen)
    assert QUESTION in content
    assert content.index(QUESTION) < content.index("nei ikke meg")
    assert t("mk_eval_previous_turn", "nb", user_msg="§", kare_response="§").split("§")[0] in content


def test_without_a_previous_turn_the_prompt_is_as_before(seen):
    asyncio.run(ev.evaluate("hei", "Hei!", "user_x"))
    assert _user_content(seen) == t("mk_eval_user_content", "nb", user_msg="hei", kare_response="Hei!")


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_previous_turn_text_in_three_languages(lang):
    out = t("mk_eval_previous_turn", lang, user_msg="U1", kare_response="K1")
    assert "U1" in out and "K1" in out
