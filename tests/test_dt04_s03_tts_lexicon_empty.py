"""DT04 R3b: a lexicon file whose `substitutions:` key is empty (YAML null) must load with 0 entries."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "voice"))

from tts_lexicon import parse_substitutions


def test_empty_substitutions_key_is_zero_entries():
    assert parse_substitutions(yaml.safe_load("substitutions:\n")) == []


def test_empty_file_and_missing_key():
    assert parse_substitutions(None) == []
    assert parse_substitutions({}) == []


def test_entries_become_whole_word_patterns():
    subs = parse_substitutions({"substitutions": {"KTSB": "kå tå ess bå"}})
    assert len(subs) == 1 and subs[0][0].sub(subs[0][1], "a KTSB b") == "a kå tå ess bå b"


def test_shipped_default_lexicons_load():
    root = Path(__file__).resolve().parents[1] / "configs_default"
    for name in ("tts_lexicon.yaml", "tts_lexicon_en.yaml", "tts_lexicon_de.yaml"):
        p = root / name
        if p.exists():
            parse_substitutions(yaml.safe_load(p.read_text()))
