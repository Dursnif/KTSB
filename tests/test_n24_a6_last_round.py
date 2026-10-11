"""N24 A6 — the enforcer also checks the answer in the last tool round.

Before, `enforcer.check()` sat inside `if round_num < _MAX_TOOL_ROUNDS - 1`, so an answer in round 6 — long answers
after many tool calls, exactly where a false «done» hides (Stian's stress test 2026-10-10) — was never checked.
Now a hit there gets one honest answer without tools (`gen_correction_no_tools_left`). The tool loop itself is not
driven here: `handle_generate` pulls in live memory and users.
"""

import ast
from pathlib import Path

import pytest

from kaare_core.tools.i18n import _T

ROUTER = Path(__file__).resolve().parents[1] / "kaare_core" / "routers" / "router_generate.py"


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_correction_text_exists_and_asks_for_honesty_not_a_call(lang):
    text = _T["gen_correction_no_tools_left"][lang]
    assert text.startswith("[SYSTEM:") and text.endswith("]")
    assert "{" not in text


def _guards_of_check_calls(tree: ast.AST) -> list[list[str]]:
    """For every `enforcer.check(...)` call: the source of each enclosing `if` test."""
    out = []

    def visit(node, guards):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If):
                visit_if(child, guards)
            else:
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "check"
                    and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "enforcer"
                ):
                    out.append(list(guards))
                visit(child, guards)

    def visit_if(node, guards):
        test_src = ast.unparse(node.test)
        visit(ast.Module(body=[ast.Expr(node.test)], type_ignores=[]), guards)
        for stmt in node.body:
            visit(ast.Module(body=[stmt], type_ignores=[]), [*guards, test_src])
        for stmt in node.orelse:
            visit(ast.Module(body=[stmt], type_ignores=[]), [*guards, f"not ({test_src})"])

    visit(tree, [])
    return out


def test_final_answer_check_is_not_limited_to_earlier_rounds():
    guards = _guards_of_check_calls(ast.parse(ROUTER.read_text(encoding="utf-8")))
    assert guards, "enforcer.check() call not found in router_generate.py"
    for g in guards:
        assert not any("_MAX_TOOL_ROUNDS" in cond for cond in g), (
            f"enforcer.check() is guarded by a round limit again: {g}"
        )
