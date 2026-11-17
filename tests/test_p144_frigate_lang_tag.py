"""
P144 phase 2 — the Frigate image analysis must not carry the [LANG:xx] tag.

The main prompt's tag contract (adapters/llm_adapter.py) makes the model end its reply with [LANG:nb]; chat strips it.
The camera pipe strips it with `model_enforcement.strip_language_tag` before it parses or stores the image model's words
(frigate_user/pipe.py L3, conclude.py). P144 phase 6: the old responder this test first guarded is gone; the guard now
covers the strip the pipe uses.

Run with: cd /kaare && PYTHONPATH=/kaare venv/bin/python -m pytest tests/test_p144_frigate_lang_tag.py -v
"""

import inspect

from kaare_core import model_enforcement
from kaare_core.frigate_user import pipe


def test_trailing_lang_tag_is_stripped():
    text = model_enforcement.strip_language_tag("En person står ved døren. Ingenting uvanlig. [LANG:nb]")
    assert "[LANG:" not in text
    assert text.strip() == "En person står ved døren. Ingenting uvanlig."


def test_text_without_tag_is_unchanged():
    assert model_enforcement.strip_language_tag("A car drives past.").strip() == "A car drives past."


def test_the_pipe_strips_before_parsing():
    assert "strip_language_tag(text)" in inspect.getsource(pipe)
