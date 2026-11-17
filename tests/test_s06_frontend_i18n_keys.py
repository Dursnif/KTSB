"""Every literal t("key") in the listed GUI files must exist in nb, en and de.

A key that exists under another parent (e.g. `settings.agentMessages.title` while the code asks for
`agentMessages.title`) makes i18next show the raw key in every language (BUG-log 2026-11-16, DT04-s06 item 2).
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "frontend" / "src"
FILES = ["pages/admin/AgentMessages.tsx", "pages/admin/Users.tsx", "pages/admin/Dashboard.tsx"]
LANGS = ["nb", "en", "de"]
_T_RE = re.compile(r"""\bt\(\s*["']([A-Za-z0-9_.]+)["']""")


def _lookup(tree: dict, key: str):
    node = tree
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@pytest.mark.parametrize("rel", FILES)
def test_literal_keys_exist_in_all_languages(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    keys = sorted(set(_T_RE.findall(src)))
    assert keys, f"no t() keys found in {rel}"
    missing = []
    for lang in LANGS:
        tree = json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
        missing += [f"{lang}:{k}" for k in keys if not isinstance(_lookup(tree, k), str)]
    assert not missing, f"{rel}: keys missing → {missing}"
