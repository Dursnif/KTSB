"""
Ollama tool-call rescue — a small translator between Ollama and the model.

Ollama parses a model's tool-call XML itself. When the model slips on the format, Ollama's
strict parser throws the whole answer away and returns HTTP 500 ("XML syntax error ...").
Seen with qwen3.5 9B: `<parameter=action>\\ngit_log>\\n<parameter=antall>` — a lone `>` where
`</parameter>` belongs (P117 C25, upstream ollama#16383 / PR #16398).

When that happens, post_chat() re-runs the *same* round through /api/generate in raw mode:
the prompt is rendered from the model family's official chat template (bundled in
chat_templates/), the raw text comes back unparsed, and the tool calls are parsed here,
tolerantly. The caller gets the same JSON shape as /api/chat, so nothing else changes.

Only used "where it works": the model's Ollama RENDERER must have a bundled template, the
request must carry tools, and no message may carry images (raw mode cannot place them).
Otherwise — and on any error inside the rescue — the original HTTP error is raised as before.
"""

import json
import logging
import re
from pathlib import Path

import httpx
import jinja2
from jinja2.ext import loopcontrols
from jinja2.sandbox import ImmutableSandboxedEnvironment

log = logging.getLogger("ollama_tool_rescue")

_TEMPLATE_DIR = Path(__file__).parent / "chat_templates"
# Ollama RENDERER name → official chat template file (add a family here to cover it)
_TEMPLATE_FILES = {"qwen3.5": "qwen3_5.jinja"}
# Ollama's error body when its tool-call parser rejects the model output
_PARSE_ERROR_RE = re.compile(r"XML syntax error|tool call", re.IGNORECASE)

_renderer_cache: dict[tuple[str, str], str] = {}
_template_cache: dict[str, jinja2.Template] = {}


def _raise_exception(message: str):
    raise jinja2.exceptions.TemplateError(message)


def _tojson(value, ensure_ascii=False, indent=None, separators=None, sort_keys=False) -> str:
    # Same as transformers' chat-template tojson: plain JSON, no HTML escaping
    return json.dumps(
        value, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys
    )


def _env() -> ImmutableSandboxedEnvironment:
    env = ImmutableSandboxedEnvironment(
        trim_blocks=True, lstrip_blocks=True, extensions=[loopcontrols]
    )
    env.filters["tojson"] = _tojson
    env.globals["raise_exception"] = _raise_exception
    return env


def get_template(renderer: str) -> jinja2.Template | None:
    """Bundled chat template for an Ollama renderer, or None if we have none."""
    name = _TEMPLATE_FILES.get(renderer)
    if not name:
        return None
    if renderer not in _template_cache:
        _template_cache[renderer] = _env().from_string(
            (_TEMPLATE_DIR / name).read_text(encoding="utf-8")
        )
    return _template_cache[renderer]


async def _renderer(client: httpx.AsyncClient, base: str, model: str) -> str:
    key = (base, model)
    if key not in _renderer_cache:
        r = await client.post(f"{base}/api/show", json={"model": model})
        r.raise_for_status()
        m = re.search(r"^RENDERER\s+(\S+)", r.json().get("modelfile", ""), re.MULTILINE)
        _renderer_cache[key] = m.group(1) if m else ""
    return _renderer_cache[key]


def _messages_for_template(messages: list[dict]) -> list[dict]:
    """Ollama chat messages → the shape the HF template expects (arguments as a mapping)."""
    out = []
    for m in messages:
        m = dict(m)
        calls = []
        for tc in m.get("tool_calls") or []:
            fn = dict(tc.get("function", tc))
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    fn["arguments"] = json.loads(args) if args else {}
                except ValueError:
                    fn["arguments"] = {}
            calls.append({"function": fn})
        if calls:
            m["tool_calls"] = calls
        out.append(m)
    return out


def render_prompt(
    template: jinja2.Template, messages: list[dict], tools: list[dict], think: bool
) -> str:
    return template.render(
        messages=_messages_for_template(messages),
        tools=tools,
        add_generation_prompt=True,
        enable_thinking=bool(think),
    )


def _param_types(tools: list[dict]) -> dict[str, dict[str, str]]:
    types: dict[str, dict[str, str]] = {}
    for tool in tools or []:
        fn = tool.get("function", tool)
        props = (fn.get("parameters") or {}).get("properties") or {}
        types[fn.get("name", "")] = {k: (v or {}).get("type", "string") for k, v in props.items()}
    return types


def _convert(value: str, typ: str):
    """String from the XML → the schema type, as Ollama's own parser would do."""
    try:
        if typ == "integer":
            return int(value.strip())
        if typ == "number":
            return float(value.strip())
        if typ == "boolean":
            return value.strip().lower() in ("true", "1", "yes")
        if typ in ("object", "array"):
            return json.loads(value)
    except ValueError:
        pass
    return value


_FUNC_RE = re.compile(r"<function=([^>\s]+)>")
_PARAM_RE = re.compile(r"<parameter=([^>\s]+)>")
_END_RE = re.compile(r"</parameter>|<parameter=|</function>|</tool_call>")


def parse_tool_calls(text: str, tools: list[dict]) -> tuple[str, list[dict]]:
    """Tolerant parse of Qwen-style tool calls. Returns (content before the calls, tool_calls).

    A parameter value ends at `</parameter>`, or — when the model forgot it — at the next
    `<parameter=`/`</function>`; in that case a lone trailing `>` (the broken closing tag) is
    dropped."""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    start = text.find("<tool_call>")
    if start < 0:
        return text.strip(), []
    content = text[:start].strip()
    types = _param_types(tools)
    calls: list[dict] = []
    for block in text[start:].split("<tool_call>")[1:]:
        fm = _FUNC_RE.search(block)
        if not fm:
            continue
        name = fm.group(1)
        body = block[fm.end() :]
        args: dict = {}
        pos = 0
        while True:
            pm = _PARAM_RE.search(body, pos)
            if not pm:
                break
            end = _END_RE.search(body, pm.end())
            stop = end.start() if end else len(body)
            value = body[pm.end() : stop].strip("\n")
            closed = bool(end) and end.group(0) == "</parameter>"
            if not closed and value.endswith(">"):
                value = value[:-1].rstrip("\n")
            args[pm.group(1)] = _convert(value, types.get(name, {}).get(pm.group(1), "string"))
            pos = end.end() if closed else stop
        calls.append({"function": {"index": len(calls), "name": name, "arguments": args}})
    return content, calls


async def _rescue(
    client: httpx.AsyncClient, url: str, payload: dict, rid: str, source: str
) -> dict | None:
    model = payload.get("model", "")
    messages = payload.get("messages") or []
    if any(m.get("images") for m in messages):
        log.warning(
            "[TOOL_XML_RESCUE] skipped rid=%s source=%s model=%s — messages carry images",
            rid,
            source,
            model,
        )
        return None
    base = url.split("/api/")[0]
    template = get_template(await _renderer(client, base, model))
    if template is None:
        return None
    prompt = render_prompt(template, messages, payload["tools"], payload.get("think", False))
    gen = {
        "model": model,
        "prompt": prompt,
        "raw": True,
        "stream": False,
        "options": payload.get("options") or {},
    }
    if "keep_alive" in payload:
        gen["keep_alive"] = payload["keep_alive"]
    r = await client.post(f"{base}/api/generate", json=gen)
    r.raise_for_status()
    data = r.json()
    content, calls = parse_tool_calls(data.get("response", ""), payload["tools"])
    log.warning(
        "[TOOL_XML_RESCUE] rescued rid=%s source=%s model=%s tools=%s",
        rid,
        source,
        model,
        [c["function"]["name"] for c in calls],
    )
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {
        "model": model,
        "message": message,
        "done": True,
        "done_reason": data.get("done_reason"),
        "rescued": True,
    }


async def post_chat(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    *,
    rid: str = "",
    source: str = "",
    **post_kwargs,
) -> dict:
    """POST to Ollama /api/chat and return the JSON. If Ollama rejects the model's tool-call
    XML (500), try the raw-mode rescue; if that is not possible, raise the original error."""
    r = await client.post(url, json=payload, **post_kwargs)
    if r.status_code >= 500 and payload.get("tools") and _PARSE_ERROR_RE.search(r.text):
        log.warning(
            "[OLLAMA_5XX] rid=%s source=%s status=%s body=%s",
            rid,
            source,
            r.status_code,
            r.text[:300],
        )
        try:
            rescued = await _rescue(client, url, payload, rid, source)
            if rescued is not None:
                return rescued
        except Exception as e:
            log.warning("[TOOL_XML_RESCUE] failed rid=%s source=%s: %s", rid, source, e)
    r.raise_for_status()
    return r.json()
