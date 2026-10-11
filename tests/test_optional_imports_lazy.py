"""Optional dependencies must stay lazy in service entry points (DT03 2026-10-09).

N20 moved imports to the top of files; four optional ones went with them and every Docker
container that lacks the package crashed at start (API: private `local/`; embedding: openvino;
voice: sounddevice/PortAudio and wespeakerruntime/torch). These packages are absent in at least
one shipped image, so a module-level import of them is a crash for strangers (rule 24).

Checked on the source (AST), not by importing: importing the entry modules starts config/user
setup and could touch the live install (tests can touch live state).
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# file → top-level packages that are absent in at least one image that runs this file
OPTIONAL_BY_FILE = {
    "kaare_api.py": {"local"},
    "services/embedding/server.py": {"openvino", "torch", "FlagEmbedding"},
    "services/voice/voice_bridge.py": {"sounddevice", "torch", "torchaudio", "openvino_genai", "wespeakerruntime"},
    "services/voice/speaker_recognition.py": {"wespeakerruntime", "torch", "torchaudio"},
}


def _module_level_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Top-level package names imported at module level, outside any try block."""
    found = []
    for node in tree.body:  # direct children only: functions, classes and try blocks are skipped
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name.split(".")[0]) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module.split(".")[0]))
    return found


@pytest.mark.parametrize("rel", sorted(OPTIONAL_BY_FILE))
def test_optional_imports_not_at_module_level(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    bad = [(ln, pkg) for ln, pkg in _module_level_imports(tree) if pkg in OPTIONAL_BY_FILE[rel]]
    assert not bad, f"{rel}: optional dependency imported at module level {bad} — keep it lazy (# noqa: PLC0415)"


def test_private_local_package_never_imported_at_module_level():
    """`local/` is an install's own extension package and is never shipped."""
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("local/", "tests/", "frontend/", "backups/")) or "venv" in rel or "node_modules" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        offenders += [f"{rel}:{ln}" for ln, pkg in _module_level_imports(tree) if pkg == "local"]
    assert not offenders, offenders
