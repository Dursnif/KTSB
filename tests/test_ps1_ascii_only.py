"""Windows PowerShell 5.1 reads a .ps1 without BOM as Windows-1252: a UTF-8 «—» becomes a quote character and
the script stops parsing (setup.ps1, found 2026-10-11 on Windows 11). Keep every shipped .ps1 ASCII-only."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"node_modules", "venv", ".git", "backups", "state", "logs"}


def _ps1_files():
    for p in ROOT.rglob("*.ps1"):
        if not SKIP.intersection(p.relative_to(ROOT).parts):
            yield p


def test_there_is_a_ps1_to_check():
    assert (ROOT / "setup.ps1").exists()


def test_ps1_files_are_ascii_only():
    bad = []
    for p in _ps1_files():
        for n, line in enumerate(p.read_bytes().split(b"\n"), 1):
            if any(b > 127 for b in line):
                bad.append(f"{p.relative_to(ROOT)}:{n}")
    assert not bad, "non-ASCII in .ps1 (breaks Windows PowerShell 5.1): " + ", ".join(bad)
