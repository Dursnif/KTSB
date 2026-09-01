"""
Shared <think>-block parser for Ollama-path LLM responses.

Root cause this fixes: if generation hits its token cap before the model closes
its own </think> tag, naive "find </think>, take what's after" parsing finds
nothing and returns the raw, unclosed text (including the literal <think> tag)
as if it were the finished answer. Reference pattern for the unclosed case:
services/inner_voices/common.py::strip_think() (Jing/Jang), which already
handles it correctly — this module generalizes that pattern for callers that
also need the extracted think text itself (for think_cache logging), not just
the cleaned answer.
"""
import re

_THINK_START_RE = re.compile(r"<think>", re.IGNORECASE)
_THINK_END_RE = re.compile(r"</think>", re.IGNORECASE)


def strip_think_block(text: str) -> tuple[str, str, bool]:
    """
    Splits raw model output into (content, think_content, was_closed).

    - No <think> tag at all: content is the text unchanged, think_content empty,
      was_closed True (trivially — there's nothing to close).
    - Well-formed <think>...</think>: content is whatever follows the closing
      tag, think_content is the block's inner text, was_closed True.
    - Unclosed <think> (generation truncated before the model closed its own
      reasoning): everything from <think> onward is treated as think_content,
      NEVER as an answer. content is only whatever text came before the tag
      (normally empty). was_closed False — callers must not treat
      think_content as a finished conclusion in this case, only log it.
    """
    start_m = _THINK_START_RE.search(text)
    if not start_m:
        return text.strip(), "", True

    end_m = _THINK_END_RE.search(text, start_m.end())
    if end_m:
        think_content = text[start_m.end():end_m.start()].strip()
        content = text[end_m.end():].strip()
        return content, think_content, True

    think_content = text[start_m.end():].strip()
    content = text[:start_m.start()].strip()
    return content, think_content, False
