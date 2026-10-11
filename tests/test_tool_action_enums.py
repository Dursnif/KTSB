"""
Every `action` enum value a tool offers the model must be handled somewhere in the code.

Static check (no tool is executed — several act on the live house): each enum value from
get_tools() must appear as a string literal in kaare_core/ or adapters/ outside definitions.py.
Found 2026-10-11: world offered «set_var» while executor_world only accepted «sett_var» (N25).

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_tool_action_enums.py -v
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, "/kaare")

import pytest

from kaare_core.tools.definitions import get_tools

_ROOT = Path("/kaare")
# Values passed through unchanged to another service that owns their meaning.
_PASSTHROUGH = {
    ("ha_control", "toggle"),  # executor_ha → HA gateway /api/ha_apply → HA service «toggle»
}


def _code_sources() -> str:
    files = list((_ROOT / "kaare_core").rglob("*.py")) + list((_ROOT / "adapters").glob("*.py"))
    return "\n".join(
        f.read_text(encoding="utf-8") for f in files if f.name != "definitions.py"
    )


def _action_enums() -> list[tuple[str, str]]:
    pairs = []
    for tool in get_tools("en"):
        fn = tool.get("function", tool)
        enum = fn.get("parameters", {}).get("properties", {}).get("action", {}).get("enum") or []
        pairs += [(fn["name"], a) for a in enum]
    return pairs


_SOURCES = _code_sources()


@pytest.mark.parametrize(("tool", "action"), _action_enums())
def test_action_enum_value_is_handled_in_code(tool, action):
    if (tool, action) in _PASSTHROUGH:
        pytest.skip("passed through to another service")
    assert re.search(r"[\"']" + re.escape(action) + r"[\"']", _SOURCES), (
        f"{tool}: action '{action}' is offered to the model but never appears in the code"
    )
