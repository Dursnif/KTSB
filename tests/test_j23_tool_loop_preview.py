"""J23 — every LLM call of the tool loop (chat, reflection meeting, dev meeting) writes a prompt preview in llm_calls.log.

Measured 05–12.11: 1836 of 2444 rid-refl calls and 454 of 588 rid-meet calls had no `prompt_preview` — `ask_llm_with_tools`
logged only `prompt_meta`, while the meetings write no `generate_in` route line, so their trace showed a step with no prompt
(the J7 finding for the camera, now for the meetings). The line now carries the same preview as the background calls
(`call_log_extra`: length + first characters of the last user message). Source check on the writer — no model call."""

import inspect
import re

from adapters import llm_adapter


def test_tool_loop_log_line_has_the_preview():
    src = inspect.getsource(llm_adapter.ask_llm_with_tools)
    i = src.index('"prompt_meta": _build_prompt_meta(')
    block = src[max(0, i - 1500): i + 200]
    assert re.search(r'"prompt_preview"', block), "the llm_calls line of the tool loop has no prompt_preview"
