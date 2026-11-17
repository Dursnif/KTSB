"""P134 guard: kaare_core/config_store.py is the only code that writes into configs/ (or configs_default/).

Static (AST) check over every shipped Python file: a write-type call (open for writing, write_text/_bytes, unlink,
rename/replace, touch, shutil copy/move/rmtree, yaml.dump to a stream) whose target refers to a config path —
a literal containing «configs», or a name assigned from one in the same module/function — fails the test.
The allowlist is empty on purpose: route the write through config_store instead of adding to it.

Known blind spot: a helper that writes to a path it receives as a parameter (`def _write(path, …)`) cannot be
resolved statically. Every such helper that targeted configs/ before P134 now calls config_store; keep it so.
"""

import ast
import re
from pathlib import Path

ROOT = Path("/kaare")
SKIP_PARTS = {
    "venv",
    "node_modules",
    "backups",
    "tests",
    "__pycache__",
    ".cache",
    "dist",
    "firmware",
}
STORE = ROOT / "kaare_core" / "config_store.py"
# "path/to/file.py:line" — keep empty. One exception (DT04-S05r, v1.11.1): the updater runs in its own stdlib-only
# container (no config_store, no yaml) and, on a rollback, puts back the byte copy of configs/ it took before the update.
# Keyed (file, function) — a line number broke as soon as the file grew (2026-11-17).
ALLOWLIST: set[tuple[str, str]] = {
    ("services/updater/updater.py", "_restore_configs"),
}

WRITE_ATTRS = {"write_text", "write_bytes", "unlink", "rename", "replace", "touch", "rmdir"}
SHUTIL = {"copy", "copy2", "copyfile", "move", "copytree", "rmtree"}
OS_FN = {"replace", "rename", "remove", "unlink"}
CONFIG_MARKERS = (
    "/kaare/configs",
    "configs/",
    "CONFIG_DIR",
    "_CONFIGS_DIR",
    "configs_dir",
    "DEFAULT_DIR",
)


def _files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if SKIP_PARTS & set(rel.parts) or rel.parts[0].startswith(".") or p == STORE:
            continue
        if rel.parts[:2] == ("core", "kaare"):
            continue
        yield p


def _mentions_config(text: str) -> bool:
    return any(m in text for m in CONFIG_MARKERS)


def _assigns(nodes) -> list[tuple[str, ast.AST]]:
    nodes = list(nodes)
    out = [
        (t.id, n.value)
        for n in nodes
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(t, ast.Name)
    ]
    out += [  # with open(<config>, "w") as f: yaml.dump(d, f) — write-mode opens only
        (n.optional_vars.id, n.context_expr)
        for n in nodes
        if isinstance(n, ast.withitem)
        and isinstance(n.optional_vars, ast.Name)
        and isinstance(n.context_expr, ast.Call)
        and _is_write_open(n.context_expr)
    ]
    return out


def _is_write_open(call: ast.Call) -> bool:
    if not (isinstance(call.func, ast.Name) and call.func.id == "open" and call.args):
        return False
    mode = (
        call.args[1]
        if len(call.args) > 1
        else next((k.value for k in call.keywords if k.arg == "mode"), None)
    )
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and bool(set(mode.value) & set("wax+"))
    )


def _grow(names: set[str], assigns: list[tuple[str, ast.AST]], src: str) -> set[str]:
    """Names assigned from an expression that mentions a config path or an already-known config name (iterated, so
    `B = A / "x"` after `A = CONFIG_DIR` counts too)."""
    names = set(names)
    texts = [(n, ast.get_source_segment(src, v) or "") for n, v in assigns]
    changed = True
    while changed:
        changed = False
        for name, value in texts:
            if name not in names and (
                _mentions_config(value) or any(_word_in(n, value) for n in names)
            ):
                names.add(name)
                changed = True
    return names


def _scopes(tree: ast.Module, src: str):
    """(calls, config names) per scope: module level, then each function with the module's names + its own."""
    funcs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]
    in_func = {id(x) for f in funcs for x in ast.walk(f) if x is not f}
    module_nodes = [n for n in ast.walk(tree) if id(n) not in in_func]
    imported = {  # constants imported from the config modules, e.g. CAPABILITY_MAP_PATH as _CAPABILITY_MAP_PATH
        a.asname or a.name
        for n in module_nodes
        if isinstance(n, ast.ImportFrom)
        and n.module in ("kaare_core.config", "kaare_core.config_store")
        for a in n.names
        if a.name.lstrip("_").isupper()
    }
    module_names = _grow(imported, _assigns(module_nodes), src)
    yield [n for n in module_nodes if isinstance(n, ast.Call)], module_names
    for f in funcs:
        nested = {
            id(x)
            for g in ast.walk(f)
            if g is not f and isinstance(g, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            for x in ast.walk(g)
            if x is not g
        }
        own = [n for n in ast.walk(f) if n is not f and id(n) not in nested]
        yield [n for n in own if isinstance(n, ast.Call)], _grow(module_names, _assigns(own), src)


def _word_in(word: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(word)}(?!\w)", text) is not None


def _targets(call: ast.Call, src: str) -> list[str]:
    f = call.func
    seg = lambda n: ast.get_source_segment(src, n) or ""  # noqa: E731
    if isinstance(f, ast.Name) and f.id == "open" and call.args:
        mode = (
            call.args[1]
            if len(call.args) > 1
            else next((k.value for k in call.keywords if k.arg == "mode"), None)
        )
        if (
            isinstance(mode, ast.Constant)
            and isinstance(mode.value, str)
            and set(mode.value) & set("wax+")
        ):
            return [seg(call.args[0])]
        return []
    if not isinstance(f, ast.Attribute):
        return []
    base = f.value.id if isinstance(f.value, ast.Name) else None
    if base == "shutil" and f.attr in SHUTIL:
        return [seg(a) for a in call.args[:2]][-1:] if f.attr != "rmtree" else [seg(call.args[0])]
    if base == "os" and f.attr in OS_FN:
        return [seg(a) for a in call.args[:2]]
    if base == "yaml" and f.attr in ("dump", "safe_dump") and len(call.args) > 1:
        return [seg(call.args[1])]
    if f.attr in WRITE_ATTRS and base not in ("os", "shutil", "str", "config_store"):
        if f.attr == "replace" and (len(call.args) != 1 or isinstance(call.args[0], ast.Constant)):
            return []  # str.replace(a, b)
        return [seg(f.value)]
    return []


def _hits(tree: ast.Module, src: str) -> list[tuple[int, str]]:
    return [
        (node.lineno, tgt)
        for calls, names in _scopes(tree, src)
        for node in calls
        for tgt in _targets(node, src)
        if _mentions_config(tgt) or any(_word_in(n, tgt) for n in names)
    ]


def _function_at(tree: ast.Module, line: int) -> str:
    """Name of the innermost function whose body holds `line` ("" at module level)."""
    best, size = "", None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno <= line <= (node.end_lineno or 0):
            span = (node.end_lineno or 0) - node.lineno
            if size is None or span < size:
                best, size = node.name, span
    return best


def _violations(rel: str, src: str) -> list[str]:
    tree = ast.parse(src)
    out = []
    for line, tgt in _hits(tree, src):
        fn = _function_at(tree, line)
        if (rel, fn) not in ALLOWLIST:
            out.append(f"{rel}:{line} ({fn or 'module'})  {tgt}")
    return out


def test_no_module_but_config_store_writes_configs():
    violations = []
    for p in _files():
        violations += _violations(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8"))
    assert not violations, "write into configs/ outside config_store:\n" + "\n".join(violations)


_UPDATER_LIKE = (
    "import os, shutil\nfrom pathlib import Path\nCONFIG_DIR = Path('/x/configs')\n"
    + "\n" * 40  # the allowed function far from any fixed line number
    + "def _restore_configs():\n    tmp = CONFIG_DIR / 'a.tmp'\n    shutil.copy2('s', tmp)\n    os.replace(tmp, CONFIG_DIR / 'a')\n"
    + "def other():\n    (CONFIG_DIR / 'b.yaml').write_text('x')\n"
)


def test_updater_exception_is_bound_to_the_function_not_the_line():
    """The one exception is (file, function): a write in the updater's _restore_configs passes wherever it sits in the
    file; the same write in any other function of that file is still caught."""
    found = _violations("services/updater/updater.py", _UPDATER_LIKE)
    assert [f for f in found if "_restore_configs" in f] == []
    assert any("other" in f for f in found), found


def test_guard_catches_the_old_patterns(tmp_path):
    """The checker itself must flag what P134 removed."""
    src = (
        'from pathlib import Path\nimport yaml, shutil\n_S = Path("/kaare/configs/settings.yaml")\n'
        "D = _S.parent\n"
        "def a(d):\n    _S.write_text(yaml.dump(d))\n"
        "def b(d):\n    with open(_S, 'w') as f:\n        yaml.dump(d, f)\n"
        "def c():\n    shutil.copy('x', D / 'y.yaml')\n"
        "from kaare_core.config import CAPABILITY_MAP_PATH as _CAP\n"
        "def e(d):\n    _CAP.write_text(d)\n"
    )
    hits = _hits(ast.parse(src), src)
    assert len(hits) == 5, hits
