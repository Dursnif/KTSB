import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import kaare_core.app_state as app_state
from adapters.llm_adapter import ask_llm_with_tools
from kaare_core.config import get_local_tz as _get_local_tz
from kaare_core.model_enforcement import ModelEnforcer, ToolPromiseDetector, tool_usage_tags
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.executor import execute_tool
from kaare_core.tools.i18n import get_lang, t

_log = logging.getLogger("kaare_api")

_JANG_LAST_READ_PATH = Path("/kaare/state/jang_last_read.txt")
_JANG_TS_RE = re.compile(r"^\[Jang (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")
_MEETING_LOCK = Path("/kaare/state/meeting_active.lock")
_INNER_THOUGHTS_PATH = Path("/kaare/state/inner_thoughts.txt")

_JANG_TOOLS: dict[str, list] = {}  # cached per pipe language (nb/en/de)


def _get_jang_tools(lang: str) -> list:
    """Tool schemas for the reflection loop, described in the active pipe language."""
    if lang not in _JANG_TOOLS:
        allowed = {
            "self_image",
            "note",
            "selvbilde",
            "notat",
        }
        _JANG_TOOLS[lang] = [
            tool for tool in get_tools(lang) if tool.get("function", {}).get("name") in allowed
        ]
    return _JANG_TOOLS[lang]


def _in_meeting_blackout() -> bool:
    if not _MEETING_LOCK.exists():
        return False
    age_seconds = time.time() - _MEETING_LOCK.stat().st_mtime
    if age_seconds > 10800:
        try:
            _MEETING_LOCK.unlink(missing_ok=True)
            _log.warning(
                "meeting_active.lock was %.0f hours old — removed as stale", age_seconds / 3600
            )
        except Exception:
            pass
        return False
    return True


def _read_jang_last_read() -> datetime | None:
    try:
        return datetime.fromisoformat(_JANG_LAST_READ_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _write_jang_last_read() -> None:
    try:
        _JANG_LAST_READ_PATH.write_text(
            datetime.now(tz=_get_local_tz()).isoformat(timespec="seconds"), encoding="utf-8"
        )
    except Exception as e:
        _log.warning("jang_reflection: could not write last_read: %s", e)


def _filter_new_thoughts(text: str, since: datetime | None) -> str:
    if since is None:
        return text

    local_tz = _get_local_tz()

    entries: list[str] = []
    current_lines: list[str] = []
    current_ts: datetime | None = None

    for line in text.splitlines():
        m = _JANG_TS_RE.match(line)
        if m:
            if current_lines and current_ts and current_ts > since:
                entries.append("\n".join(current_lines))
            try:
                current_ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(
                    tzinfo=local_tz
                )
            except ValueError:
                current_ts = None
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines and current_ts and current_ts > since:
        entries.append("\n".join(current_lines))

    return "\n\n".join(entries)


async def _run_jang_reflection() -> None:
    thoughts_path = _INNER_THOUGHTS_PATH
    if not thoughts_path.exists() or not thoughts_path.stat().st_size:
        return

    try:
        raw_text = thoughts_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        _log.warning("jang_reflection: could not read inner_thoughts: %s", e)
        return

    if not raw_text:
        return

    since = _read_jang_last_read()
    thoughts = _filter_new_thoughts(raw_text, since)

    if not thoughts.strip():
        # Genuinely nothing new — this is a real "ran, found nothing" tick, safe to stamp.
        app_state._last_jang_run_time = time.time()
        return

    print("[KÅRE REFLECTION] Starting internal reflection cycle")

    # No user is present: the reflection speaks the household's global pipe language.
    lang = get_lang("global")
    micro_prompt = t("refl_loop_prompt", lang, thoughts=thoughts)

    messages = [{"role": "user", "content": micro_prompt}]
    tools = _get_jang_tools(lang)

    # `deferred` stays True only if the very first round comes back busy — i.e. the
    # model was never actually reached, so these thoughts must not be marked read and
    # _last_jang_run_time must not advance (next 60s tick should retry with the same
    # thoughts, not wait a full _JANG_INTERVAL_S). Any round that does reach the model
    # — success, tool-call, or a genuine exception — counts as "attempted": a mid-cycle
    # busy-abort on round 2/3 after that point is a clean early stop, not a lost cycle.
    deferred = False
    attempted_model = False

    # One rid per reflection cycle (P118 B0): every LLM round and every tool call of this
    # cycle shares it, so get_trace(rid) returns the whole cycle as one trace.
    rid = f"rid-refl-{int(time.time() * 1000)}"
    # Same promise check as chat (P118 B1): «I'll note it / raise it at the next dev meeting»
    # without the tool call gets one forced correction round. Only the tool-promise detector —
    # the reflection prompt has no [LANG:xx] tag contract, so the language detector would misfire.
    enforcer = ModelEnforcer(lang, rid=rid, detectors=[ToolPromiseDetector()])
    used_tools: list[str] = []
    max_rounds = 3

    for _round in range(max_rounds):
        try:
            result = await ask_llm_with_tools(
                messages=messages,
                tools=tools,
                rid=rid,
                user_id="kare",
                source="jang",
                relevance_query=thoughts,  # P133 C3: «Relevant nå» from Jang's thoughts
            )
        except Exception as e:
            _log.warning("jang_reflection: ask_llm_with_tools failed: %s", e)
            attempted_model = True
            break

        if result.get("meta", {}).get("busy"):
            if attempted_model:
                print(
                    "[KÅRE REFLECTION] Kåre became busy mid-cycle — stopping early, already-attempted rounds stand."
                )
            else:
                deferred = True
                print(
                    "[KÅRE REFLECTION] Kåre busy — deferring reflection cycle to next tick, thoughts kept unread."
                )
            break

        attempted_model = True

        tool_calls = result.get("tool_calls")
        if not tool_calls:
            text_out = (result.get("text") or "").strip()
            if _round < max_rounds - 1:
                action = enforcer.check(
                    text_out=text_out, used_tools=used_tools, meta=result.get("meta", {})
                )
                if action:
                    messages.append({"role": "assistant", "content": text_out})
                    messages.append({"role": "user", "content": action.correction_message})
                    continue
            print(f"[KÅRE REFLECTION] Reflection done. response={text_out[:160]!r}")
            break

        messages.append(
            {
                "role": "assistant",
                "content": result.get("text") or "",
                "tool_calls": tool_calls,
            }
        )

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            args["_rid"] = rid
            used_tools.extend(tool_usage_tags(name, args))
            print(f"[KÅRE REFLECTION] Tool call: {name}  args={str(args)[:120]}")
            try:
                tool_result = await execute_tool(name, args)
            except Exception as e:
                tool_result = t("refl_loop_tool_error", lang, error=e)
            messages.append({"role": "tool", "name": name, "content": str(tool_result)})

    if not deferred:
        app_state._last_jang_run_time = time.time()
        _write_jang_last_read()

    print("[KÅRE REFLECTION] Reflection cycle complete")


async def start_reflection_loop() -> None:
    await asyncio.sleep(120)  # let startup settle

    while True:
        await asyncio.sleep(60)
        try:
            if not app_state._REFLECTION_ENABLED:
                continue
            now = time.time()
            if now - app_state._last_jang_run_time < app_state._JANG_INTERVAL_S:
                continue
            if now - app_state._last_user_prompt_time < app_state._JANG_INTERVAL_S - 1:
                continue
            if _in_meeting_blackout():
                continue
            await _run_jang_reflection()
        except Exception as e:
            _log.warning("jang_injection_loop: unhandled error: %s", e)
