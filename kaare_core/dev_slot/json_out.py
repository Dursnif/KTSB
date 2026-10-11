"""Pull one JSON object out of a model reply (P118). Models wrap JSON in prose or code fences;
this finds the first balanced {...} that parses. Never raises."""

from __future__ import annotations

import json
import re

from kaare_core.think_strip import strip_think_block

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    body, _think, _closed = strip_think_block(text)
    body = body or text
    m = _FENCE_RE.search(body)
    candidates = [m.group(1)] if m else []
    start = body.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(body)):
            ch = body[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(body[start : i + 1])
                    break
        start = body.find("{", start + 1)
        if len(candidates) > 8:
            break
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return None
