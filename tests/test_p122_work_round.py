"""
P122 — web search as a work round: Kåre sends a slow search off (`background: true`), answers
at once, and the finished result goes to KÅRE (internal turn), whose answer reaches the chat.
STM keeps only the marker line. Shared job runner with Mechanic. No network, no LLM.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p122_work_round.py -v
"""

import asyncio
import sys

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.agents.mechanic.job_store as js
import kaare_core.tools.executor_library as lib
import kaare_core.tools.timer_service as ts
import kaare_core.tools.work_round as wr
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import t

# ── Generic runner ──────────────────────────────────────────────────────────


def test_runner_records_result_and_calls_on_done():
    seen = {}

    async def scenario():
        async def work(job):
            return "result text"

        async def on_done(job_id, job):
            seen["status"], seen["result"] = job["status"], job["result"]

        job_id = js.start_background(work, kind="test", user_id="u", on_done=on_done)
        await js._jobs[job_id]["task"]
        return job_id

    job_id = asyncio.run(scenario())
    assert seen == {"status": "done", "result": "result text"}
    assert js.get_job(job_id)["status"] == "done"


def test_runner_marks_errors_and_still_delivers():
    seen = {}

    async def scenario():
        async def work(job):
            raise RuntimeError("boom")

        async def on_done(job_id, job):
            seen["status"] = job["status"]

        job_id = js.start_background(work, kind="test", on_done=on_done)
        await js._jobs[job_id]["task"]

    asyncio.run(scenario())
    assert seen["status"] == "error"


def test_mechanic_still_delivers_pending_and_chat(monkeypatch):
    calls = {}

    async def fake_work(job, task, role, context, user_id, rid):
        return "mechanic answer"

    monkeypatch.setattr(js, "_mechanic_work", fake_work)
    monkeypatch.setattr(
        js, "_write_pending_result", lambda u, j, s: calls.setdefault("pending", (u, s))
    )

    monkeypatch.setattr(
        ts,
        "queue_chat_notification",
        lambda u, m, rid="", source_id="": calls.setdefault("chat", (u, m)),
    )
    monkeypatch.setattr(  # patched where used (job_store imports it at module level)
        js,
        "queue_chat_notification",
        lambda u, m, rid="", source_id="": calls.setdefault("chat", (u, m)),
    )

    async def scenario():
        job_id = await js.start_job("check disk", user_id="stian", rid="r")
        await js._jobs[job_id]["task"]

    asyncio.run(scenario())
    assert (
        calls["pending"] == ("stian", "mechanic answer") and "mechanic answer" in calls["chat"][1]
    )


# ── Choosing a work round ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("web_search", {"background": True}, True),
        ("web_search", {"background": "true"}, True),
        ("web_search", {"background": False}, False),
        ("web_search", {}, False),
        ("library", {"action": "law", "background": True}, True),
        ("library", {"action": "fetch_table", "background": True}, False),
        ("web_search", {"background": True, "_source": "work_round"}, False),  # no chained rounds
        ("note", {"background": True}, False),
    ],
)
def test_wants_background(name, args, expected):
    assert wr.wants_background(name, args) is expected


def test_stm_keeps_only_the_marker_line():
    prompt = "[Arbeidsrunde ferdig — spørsmålet var: «x».]\n\n- «raw quote» [Straffeloven § 351]"
    assert wr.stm_text(prompt, "work_round") == "[Arbeidsrunde ferdig — spørsmålet var: «x».]"
    assert wr.stm_text(prompt, "gui") == prompt


# ── Start + delivery through Kåre ───────────────────────────────────────────


class _Resp:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    def json(self):
        return {"text": self._text}


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_result_goes_to_kare_and_his_answer_to_chat(monkeypatch, lang):
    posted, chat = {}, {}
    monkeypatch.setattr(wr, "get_lang", lambda uid: lang)
    monkeypatch.setattr(wr.web_fetch, "setting", lambda key: 5.0)
    monkeypatch.setattr(wr, "service_headers", lambda: {"X-Kaare-Service-Token": "tok"})
    monkeypatch.setattr(wr, "get_service", lambda sec, key: "http://api.test")

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            posted.update(url=url, json=json, headers=headers)
            return _Resp("Kåre's interpreted answer")

    monkeypatch.setattr(wr.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        wr, "queue_chat_notification", lambda u, m, rid="", source_id="": chat.update(user=u, msg=m)
    )

    async def fake_run(name, args):
        assert "background" not in args and args["_deadline"] == 5.0
        return "- «raw quote» [source]"

    async def scenario():
        out = wr.start(
            "web_search",
            {"query": "Göta kanal", "background": True, "_user_id": "stian", "_rid": "r"},
            fake_run,
        )
        job = next(
            j for j in js._jobs.values() if j["kind"] == "work_round" and j["status"] == "running"
        )
        await job["task"]
        return out

    out = asyncio.run(scenario())
    assert out.startswith(t("work_round_started", lang, job="")[:20])
    assert posted["url"] == "http://api.test/api/generate"
    assert posted["json"]["source"] == "work_round" and posted["json"]["user_id"] == "stian"
    assert posted["json"]["prompt"].startswith(
        t("work_round_prompt_head", lang, question="Göta kanal")
    )
    assert "«raw quote»" in posted["json"]["prompt"]
    assert chat == {"user": "stian", "msg": "Kåre's interpreted answer"}  # never the raw result


def test_executor_dispatch_starts_work_round(monkeypatch):
    started = {}
    monkeypatch.setattr(
        lib.work_round,
        "start",
        lambda name, args, run: started.update(name=name, run=run) or "STARTED",
    )
    out = asyncio.run(
        lib.dispatch(
            "web_search", {"query": "x", "near_home": False, "stakes": "normal", "background": True}
        )
    )
    assert out == "STARTED" and started["name"] == "web_search" and started["run"] is lib._dispatch


def test_work_round_source_is_internal_only():
    # PLC0415: importing kaare_api runs the API's startup (DB init, service token) — only in this test
    import kaare_api  # noqa: PLC0415

    assert "work_round" in kaare_api._INTERNAL_SOURCES


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_background_param_in_schemas(lang):
    tools = {tl["function"]["name"]: tl for tl in get_tools(lang)}
    for name in ("web_search", "library"):
        props = tools[name]["function"]["parameters"]["properties"]
        assert props["background"]["type"] == "boolean" and props["background"]["description"]
