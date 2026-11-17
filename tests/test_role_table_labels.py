"""Users → tool permissions per role: every row of the role table has its label in nb, en and de.

Found 2026-11-16 by kaare-cb (S05e work): the table looks up `users.tools.labels.<row key>` with the tool's real
(English) name — camera, note, memory, ha_control … — while the locales still held the old Norwegian keys (kamera,
notat, minne, styr_enhet …). Ten rows showed the raw key instead of a label, among them the new camera text."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT / "frontend/src/pages/admin/Users.tsx"


def _row_keys() -> list[str]:
    src = USERS.read_text(encoding="utf-8")
    block = src[src.index("TOOL_ROWS"):]
    block = block[: block.index("];")]
    return re.findall(r'key:\s*"([a-z_]+)"', block)


def test_the_role_table_has_rows():
    assert len(_row_keys()) > 10


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_every_row_has_a_label(lang):
    labels = json.loads((ROOT / f"frontend/src/locales/{lang}.json").read_text(encoding="utf-8"))["users"]["tools"][
        "labels"]
    missing = [k for k in _row_keys() if not labels.get(k)]
    assert missing == []
