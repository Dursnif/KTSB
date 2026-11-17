"""Save feedback says whether a setting is in use or needs a restart (DT04-S05d part 2, rule 25).

The frontend has no JS test runner, so this checks the source and the locale files: the texts exist in nb/en/de,
the shared SaveFeedback shows `applies` and links to Maintenance, every card whose PUT answers `applies` passes it
on, and the Forklaringer tab explains it.
"""

import json
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
KEYS = ["common.saved_in_use", "common.saved_needs_restart", "common.open_maintenance", "forklaringer.vedlikehold.item_8"]
CARDS = {
    "pages/admin/integrations/TabHomeAssistant.tsx": "apiPutHa(",
    "pages/admin/integrations/TabMqtt.tsx": "apiPutMqtt(",
    "pages/admin/integrations/FrigateCard.tsx": "apiPutFrigate(",
    "pages/admin/integrations/PlexCard.tsx": "apiPutPlex(",
    "pages/admin/settings/llm/EmbeddingCard.tsx": "apiPutEmbeddingBackend(",
    "pages/admin/settings/llm/WhisperCard.tsx": "apiPutVoiceBackend(",
}


def _get(tree, key):
    for part in key.split("."):
        tree = tree.get(part) if isinstance(tree, dict) else None
    return tree


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_texts_exist(lang):
    tree = json.loads((SRC / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
    missing = [k for k in KEYS if not isinstance(_get(tree, k), str)]
    assert not missing, missing
    assert "{{service}}" in _get(tree, "common.saved_needs_restart")


def test_save_feedback_shows_applies_and_links_to_maintenance():
    shared = (SRC / "pages/admin/settings/shared.tsx").read_text(encoding="utf-8")
    hook = (SRC / "pages/admin/settings/useSaveState.ts").read_text(encoding="utf-8")
    assert "applies" in hook and "saved = (applies" in hook
    assert "common.saved_needs_restart" in shared and "/admin/system" in shared
    assert "system.services.labels." in shared  # names the real restart button


@pytest.mark.parametrize("rel,call", sorted(CARDS.items()))
def test_cards_pass_applies(rel, call):
    src = (SRC / rel).read_text(encoding="utf-8")
    assert call in src, f"{rel} no longer calls {call}"
    assert re.search(r"\.saved\(\s*\w+\??\.applies", src), f"{rel}: save result's `applies` not passed to saved()"
    assert re.search(r"<SaveFeedback state=\{\w+\.state\} applies=\{\w+\.applies\}", src), rel


def test_forklaringer_lists_item_8():
    src = (SRC / "pages/admin/settings/TabForklaringer.tsx").read_text(encoding="utf-8")
    assert 'items("vedlikehold", 8)' in src
