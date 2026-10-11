"""P117 C25 — rescue tool calls when Ollama's qwen3.5 parser rejects the model's XML (HTTP 500)."""

import asyncio
import json

import httpx
import pytest

from kaare_core import ollama_tool_rescue as rescue

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "inspiser",
            "description": "Logger, tjenester, ressurser, git — «les» bare",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["logg", "tjenester", "git_log", "ressurser"],
                    },
                    "antall": {"type": "integer"},
                    "tjeneste": {"type": "string"},
                    "logglinjer": {"type": "integer"},
                    "fil": {"type": "string"},
                    "linjer": {"type": "integer"},
                },
                "required": ["action"],
            },
        },
    }
]

# Real broken blocks from the production model (raw_xml_probe.py, 2026-10-02)
BROKEN_GIT_LOG = "<tool_call>\n<function=inspiser>\n<parameter=action>\ngit_log>\n<parameter=antall>\n10\n</parameter>\n</function>\n</tool_call>"
BROKEN_TJENESTER = (
    "<tool_call>\n<function=inspiser>\n<parameter=action>\ntjenester>\n<parameter=tjeneste>\nkaare-argus\n"
    "</parameter>\n<parameter=logglinjer>\n25\n</parameter>\n</function>\n</tool_call>"
)
BROKEN_LOGG = (
    "<tool_call>\n<function=inspiser>\n<parameter=action>\nlogg>\n<parameter=fil>\nkaare_ha_gateway.log</parameter>\n"
    "<parameter=linjer>\n30\n</parameter>\n</function>\n</tool_call>"
)
WELL_FORMED = "<tool_call>\n<function=inspiser>\n<parameter=action>\nressurser\n</parameter>\n</function>\n</tool_call>"


def _args(text):
    return rescue.parse_tool_calls(text, TOOLS)[1][0]["function"]["arguments"]


def test_broken_action_is_repaired_and_typed():
    assert _args(BROKEN_GIT_LOG) == {"action": "git_log", "antall": 10}
    assert _args(BROKEN_TJENESTER) == {
        "action": "tjenester",
        "tjeneste": "kaare-argus",
        "logglinjer": 25,
    }
    assert _args(BROKEN_LOGG) == {"action": "logg", "fil": "kaare_ha_gateway.log", "linjer": 30}


def test_well_formed_and_content_before_call():
    content, calls = rescue.parse_tool_calls("Jeg sjekker ressursene.\n\n" + WELL_FORMED, TOOLS)
    assert content == "Jeg sjekker ressursene."
    assert calls == [
        {"function": {"index": 0, "name": "inspiser", "arguments": {"action": "ressurser"}}}
    ]


def test_multiple_calls_and_think_block_dropped():
    text = "<think>\nhmm\n</think>\n\n" + WELL_FORMED + "\n" + BROKEN_GIT_LOG
    content, calls = rescue.parse_tool_calls(text, TOOLS)
    assert content == ""
    assert [c["function"]["arguments"]["action"] for c in calls] == ["ressurser", "git_log"]


def test_plain_answer_has_no_calls():
    assert rescue.parse_tool_calls("Alt ser fint ut.", TOOLS) == ("Alt ser fint ut.", [])


def test_render_matches_official_template_shape():
    tpl = rescue.get_template("qwen3.5")
    messages = [
        {"role": "system", "content": "Du er Mechanic."},
        {"role": "user", "content": "Sjekk git."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "inspiser", "arguments": {"action": "git_log"}}}],
        },
        {"role": "tool", "content": "abc123 fix", "name": "inspiser"},
    ]
    prompt = rescue.render_prompt(tpl, messages, TOOLS, think=False)
    assert prompt.startswith("<|im_start|>system\n# Tools\n")
    assert (
        "«les»" in prompt and "\\u00ab" not in prompt and "&lt;" not in prompt
    )  # plain JSON, no escaping
    assert "<parameter=action>\ngit_log\n</parameter>" in prompt
    assert "<tool_response>\nabc123 fix\n</tool_response>" in prompt
    assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert rescue.get_template("llama3") is None


# ── post_chat with a fake Ollama ────────────────────────────────────────────


def _fake_ollama(
    renderer="qwen3.5",
    generate_text=BROKEN_GIT_LOG,
    chat_status=500,
    chat_body='{"error":"XML syntax error on line 7: element <parameter> closed by </function>"}',
):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/chat":
            return httpx.Response(chat_status, text=chat_body)
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json={"modelfile": f"FROM x\nRENDERER {renderer}\nPARSER {renderer}\n"}
            )
        if request.url.path == "/api/generate":
            assert json.loads(request.content)["raw"] is True
            return httpx.Response(200, json={"response": generate_text, "done_reason": "stop"})
        return httpx.Response(404)

    return handler, seen


def _run(handler, payload):
    rescue._renderer_cache.clear()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await rescue.post_chat(
                client, "http://ollama:11434/api/chat", payload, source="test"
            )

    return asyncio.run(go())


PAYLOAD = {
    "model": "m",
    "messages": [{"role": "user", "content": "sjekk git"}],
    "tools": TOOLS,
    "options": {"num_ctx": 32768},
    "think": False,
}


def test_500_parse_error_is_rescued():
    handler, seen = _fake_ollama()
    data = _run(handler, PAYLOAD)
    assert data["rescued"] is True
    assert data["message"]["tool_calls"][0]["function"]["arguments"] == {
        "action": "git_log",
        "antall": 10,
    }
    assert seen == ["/api/chat", "/api/show", "/api/generate"]


def test_unknown_renderer_raises_original_error():
    handler, _ = _fake_ollama(renderer="gemma4")
    with pytest.raises(httpx.HTTPStatusError):
        _run(handler, PAYLOAD)


def test_images_are_not_rescued():
    handler, seen = _fake_ollama()
    payload = {**PAYLOAD, "messages": [{"role": "user", "content": "se", "images": ["aGVp"]}]}
    with pytest.raises(httpx.HTTPStatusError):
        _run(handler, payload)
    assert seen == ["/api/chat"]


def test_other_500_is_not_rescued():
    handler, seen = _fake_ollama(chat_body='{"error":"model runner has unexpectedly stopped"}')
    with pytest.raises(httpx.HTTPStatusError):
        _run(handler, PAYLOAD)
    assert seen == ["/api/chat"]


def test_normal_response_passes_through():
    handler, seen = _fake_ollama(
        chat_status=200, chat_body='{"message": {"role": "assistant", "content": "ok"}}'
    )
    assert _run(handler, PAYLOAD)["message"]["content"] == "ok"
    assert seen == ["/api/chat"]
