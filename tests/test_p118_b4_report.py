"""
P118 B4 — 9B evidence verifier, proposals (confirm in chat), Janus end call, S5 morning report.
LLM calls are faked; storage, verification and the report builder are real (temp paths).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p118_b4_report.py -v
"""

import asyncio
import json
import sys

import pytest

sys.path.insert(0, "/kaare")

import kaare_core.dev_slot.steps as steps
import kaare_core.dev_slot.tools as slot_tools
import kaare_core.dev_slot.verifier as verifier
import kaare_core.memory.learning_store as ls
import kaare_core.tools.executor as executor
import kaare_core.tools.shared_tools as shared_tools
import kaare_core.tools.trace_reader as tr
from kaare_core.config import get_tools_for_role
from kaare_core.dev_slot.verify import quote_in, verify_claims

RID = "rid-meet-1791200000000"
EVIDENCE_LINE = "line 46: return json.dumps(data)"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(ls, "DB_PATH", tmp_path / "learning.db")
    log = tmp_path / "tool_calls.log"
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", log)
    monkeypatch.setattr(tr, "_TOOL_LOG", log)

    async def fake_mech(name, args, lang=None):
        return f"MECH {name} → {EVIDENCE_LINE}"

    monkeypatch.setattr(slot_tools.mechanic_tools, "execute_tool", fake_mech)
    return tmp_path


# ── verifier ────────────────────────────────────────────────────────────────


def _fake_9b(monkeypatch, reply):
    async def ask(messages, rid):
        ask.seen = messages
        return reply

    monkeypatch.setattr(verifier, "_ask", ask)
    return ask


@pytest.mark.parametrize(
    "reply,expected",
    [
        (
            {
                "ok": True,
                "text": json.dumps({"supports": "yes", "quote": "return json.dumps(data)"}),
            },
            True,
        ),
        (
            {"ok": True, "text": json.dumps({"supports": "yes", "quote": "None collapse"})},
            False,
        ),  # invented quote
        (
            {
                "ok": True,
                "text": json.dumps({"supports": "partly", "quote": "return json.dumps(data)"}),
            },
            False,
        ),
        ({"ok": True, "text": json.dumps({"supports": "no", "quote": ""})}, False),
        ({"ok": False, "error": "model busy", "text": ""}, True),  # no 9B → code check stands alone
        ({"ok": True, "text": "garbage"}, True),  # unparsed → code check stands alone
    ],
)
def test_verifier_outcomes(monkeypatch, reply, expected):
    ask = _fake_9b(monkeypatch, reply)
    v = asyncio.run(
        verifier.check_claim(
            "Line 46 serializes the state", f"[tool:x#1] {EVIDENCE_LINE}", rid=RID, lang="nb"
        )
    )
    assert verifier.final_verified(True, v) is expected
    assert verifier.final_verified(False, v) is False, "the 9B can never rescue a failed code check"
    if reply.get("ok"):
        assert "Bevistekst" in ask.seen[1]["content"]


# ── S3 with verifier + proposals ────────────────────────────────────────────


def _s3_fakes(monkeypatch, quote_9b):
    async def kare(messages, tools, **kw):
        if messages[-1]["role"] == "user":
            return {
                "ok": True,
                "text": "",
                "message": {"role": "assistant", "content": ""},
                "tool_calls": [
                    {
                        "function": {
                            "name": "utforsk",
                            "arguments": {"action": "les", "sti": "/kaare/x.py"},
                        }
                    }
                ],
            }
        ref = messages[-1]["content"].split("]")[0].lstrip("[")
        return {
            "ok": True,
            "tool_calls": None,
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "text": "Line 46 serializes the state",
                            "evidence_refs": [ref],
                            "quote": EVIDENCE_LINE,
                        },
                        {"text": "Unbacked guess", "evidence_refs": [], "quote": ""},
                    ],
                    "proposal": "Reset the flag on recovery",
                }
            ),
        }

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)
    calls = []

    async def check(claim, evidence, rid="", lang=""):
        calls.append(claim)
        return {
            "status": "ok",
            "supports": "yes",
            "quote": quote_9b,
            "quote_found": quote_9b in evidence,
        }

    monkeypatch.setattr(steps.verifier, "check_claim", check)
    return calls


def test_s3_verified_finding_gives_pending_proposal(monkeypatch, env):
    t1 = ls.add_inbox_item("Fallback flag sticks", source="refl", rid="r1")
    calls = _s3_fakes(monkeypatch, "return json.dumps(data)")
    res = asyncio.run(
        steps.s3_develop(
            t1["topic_id"],
            "Fallback flag sticks",
            rid=RID,
            lang="en",
            run_id="r",
            budget=steps.Budget(5),
            round_cap=2,
            tools=slot_tools.SlotTools(RID, "en"),
        )
    )
    assert calls == ["Line 46 serializes the state"], "9B asked only where the code check passed"
    assert [f["verified"] for f in res["findings"]] == [True, False]
    assert (
        res["proposal_id"]
        and ls.list_proposals("pending")[0]["text"] == "Reset the flag on recovery"
    )


def test_s3_no_verified_finding_no_proposal(monkeypatch, env):
    t1 = ls.add_inbox_item("Fallback flag sticks", source="refl", rid="r1")
    _s3_fakes(monkeypatch, "invented words")
    res = asyncio.run(
        steps.s3_develop(
            t1["topic_id"],
            "x",
            rid=RID,
            lang="en",
            run_id="r",
            budget=steps.Budget(5),
            round_cap=2,
            tools=slot_tools.SlotTools(RID, "en"),
        )
    )
    assert [f["verified"] for f in res["findings"]] == [False, False]
    assert res["findings"][0]["reason"] == "verifier_quote_not_in_evidence"
    assert res["proposal_id"] is None and ls.list_proposals("pending") == []
    assert len(ls.run_findings("r")) == 2, "unverified findings are kept"


# ── proposals tool (chat) ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lang,word", [("nb", "godkjent"), ("en", "confirmed"), ("de", "bestätigt")]
)
def test_dev_proposals_tool(monkeypatch, env, lang, word):
    monkeypatch.setattr(executor, "get_lang", lambda uid: lang)
    t1 = ls.add_inbox_item("Topic", source="refl", rid="r1")
    fid = ls.add_finding("r", "Finding text", ["tool:x#1"], True, topic_id=t1["topic_id"])
    pid = ls.add_proposal(fid, "Do the thing")
    listed = asyncio.run(
        executor.execute_tool("dev_proposals", {"action": "list", "_user_id": "stian"})
    )
    assert f"#{pid}" in listed and "Do the thing" in listed and "Finding text" in listed
    ok = asyncio.run(
        executor.execute_tool(
            "dev_proposals", {"action": "confirm", "proposal_id": pid, "_user_id": "stian"}
        )
    )
    assert word in ok and "stian" in ok
    again = asyncio.run(
        executor.execute_tool(
            "dev_proposals", {"action": "reject", "proposal_id": pid, "_user_id": "stian"}
        )
    )
    assert f"#{pid}" in again and ls.list_proposals("confirmed")[0]["decided_by"] == "stian"
    assert "proposal_id" in asyncio.run(
        executor.execute_tool("dev_proposals", {"action": "confirm", "_user_id": "stian"})
    )


def test_dev_proposals_is_admin_only():
    for role in ("child", "teen", "adult"):
        assert "dev_proposals" not in {x["function"]["name"] for x in get_tools_for_role(role)}
    assert "dev_proposals" in {x["function"]["name"] for x in get_tools_for_role("admin")}


# ── report ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "lang,verified_h,susp_h",
    [
        ("nb", "## Bekreftede funn", "## Mistanker"),
        ("en", "## Verified findings", "## Suspicions"),
        ("de", "## Bestätigte Befunde", "## Verdachtsmomente"),
    ],
)
def test_run_b4_writes_report_from_rows(monkeypatch, env, tmp_path, lang, verified_h, susp_h):
    t1 = ls.add_inbox_item("Fallback flag sticks", source="refl", rid="r1")
    ls.apply_topic_verdict(t1["topic_id"], "problem", label="Fallback flag")
    f_ok = ls.add_finding(
        "run-r",
        "Line 46 serializes the state",
        [f"tool:{RID}#1"],
        True,
        topic_id=t1["topic_id"],
        verifier={"status": "ok", "supports": "yes"},
    )
    ls.add_finding(
        "run-r", "Invented None collapse", [f"tool:{RID}#1"], False, topic_id=t1["topic_id"]
    )
    pid = ls.add_proposal(f_ok, "Reset the flag on recovery")
    b3 = {
        "s1": [{"topic_id": t1["topic_id"], "kind": "problem", "same_as": None}],
        "s2": [],
        "skipped_for_time": ["topic:9"],
        "s3": [{"topic_id": t1["topic_id"], "proposal_id": pid, "findings": []}],
    }
    seen = {}

    async def janus(role, messages, **kw):
        assert kw["disable_thinking"] is True
        return {
            "ok": True,
            "text": json.dumps(
                {"notes": [{"topic_id": t1["topic_id"], "note": "Known pattern, still open"}]}
            ),
        }

    async def kare(messages, tools, **kw):
        seen["payload"] = messages[0]["content"]
        assert tools == [] and kw["disable_thinking"] is True
        return {
            "ok": True,
            "text": "I looked at the fallback flag tonight. One finding is verified.",
        }

    monkeypatch.setattr(steps, "call_llm_chat", janus)
    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)

    path = asyncio.run(
        steps.run_b4(
            b3,
            run_id="run-r",
            rid=RID,
            lang=lang,
            budget=steps.Budget(5),
            report_dir=tmp_path / "reports",
            date="2026-10-04",
        )
    )
    text = path.read_text()
    assert path.name == "2026-10-04.md"
    assert "Invented None collapse" not in seen["payload"], "S5 sees only verified findings"
    v_part = text.split(verified_h)[1].split("##")[0]
    s_part = text.split(susp_h)[1].split("##")[0]
    assert "Line 46 serializes the state" in v_part and "Invented None collapse" not in v_part
    assert "Invented None collapse" in s_part
    assert f"**#{pid}** Reset the flag on recovery" in text
    assert "Known pattern, still open" in text and "I looked at the fallback flag tonight" in text


def test_report_survives_failed_summary(monkeypatch, env, tmp_path):
    async def kare(messages, tools, **kw):
        return {"ok": False, "text": ""}

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)
    path = asyncio.run(
        steps.run_b4(
            {"s1": [], "s2": [], "s3": []},
            run_id="empty",
            rid=RID,
            lang="nb",
            budget=steps.Budget(5),
            report_dir=tmp_path,
            date="2026-10-04",
        )
    )
    text = path.read_text()
    assert "kunne ikke skrives" in text and "_Ingen._" in text


def test_quotes_are_checked_against_the_full_result_kare_saw(env, monkeypatch):
    long_result = "header\n" + "x" * 500 + "\n[2026-10-03 00:09] HA service: light.turn_off\nmore"

    async def fake_mech(name, args, lang=None):
        return long_result

    monkeypatch.setattr(slot_tools.mechanic_tools, "execute_tool", fake_mech)
    tools = slot_tools.SlotTools(RID, "en")
    shown = asyncio.run(tools.execute("inspiser", {"action": "logg", "fil": "ha_events.log"}))
    ref = shown.split("]")[0].lstrip("[")
    claim = [
        {
            "text": "light.turn_off at 00:09",
            "evidence_refs": [ref],
            "quote": "[2026-10-03 00:09] HA service: light.turn_off",
        }
    ]

    assert verify_claims(claim, {RID})[0]["verified"] is False, (
        "the 120-char log preview cannot hold it"
    )
    full = verify_claims(claim, {RID}, tool_texts=tools.results)[0]
    assert full["verified"] is True and "light.turn_off" in full["evidence"]


def test_summary_strips_language_tag(monkeypatch):
    async def kare(messages, tools, **kw):
        return {"ok": True, "text": "Jeg så på kameraene.\n\n[LANG:nb]"}

    monkeypatch.setattr(steps, "ask_llm_with_tools", kare)
    assert (
        asyncio.run(steps.s5_summary({}, rid=RID, lang="nb", budget=steps.Budget(5)))
        == "Jeg så på kameraene."
    )


@pytest.mark.parametrize(
    "quote,expected",
    [
        ('"car på utekamera nedside øst (90%)"', True),  # wrapped in quotes
        (
            "[2026-10-02 15:52] Frigate: car på utekamera ... [2026-10-02 15:57] Frigate: car",
            True,
        ),  # joined pieces
        (
            "«car på utekamera nedside øst» … «person på utekamera nedside vest»",
            False,
        ),  # second piece not there
        ("car på utekamera nedside vest (90%)", False),  # altered words
        ("", False),
        ('"..."', False),
    ],
)
def test_quote_in_tolerates_wrapping_but_not_invention(quote, expected):
    ev = (
        "[2026-10-02 15:52] Frigate: car på utekamera nedside øst (90%)\n"
        "[2026-10-02 15:57] Frigate: car på utekamera nedside øst (90%)"
    )
    assert quote_in(quote, ev) is expected
