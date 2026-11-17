"""Log messages are English (CLAUDE.md rule 8; DT04-S05a, DT04-R2j). Scans the first string argument of every
logger/print call in the listed files for Norwegian words."""

import ast
import logging
import re
from pathlib import Path

import pytest

import kaare_ha_gateway as gw

ROOT = Path(__file__).resolve().parent.parent
FILES = ["kaare_argus.py", "kaare_core/voice/wyoming_server.py", "kaare_core/users/store.py"]
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
_NORWEGIAN = re.compile(
    r"[æøåÆØÅ]|\b(feil|opprettet|klar|aktiv|startet|overvåker|rapport|indeksert|droppet|aktive|sendt|fra|"
    r"opptaksmodus|lagret|runde|svarte|ikke|dreper|bytt|umiddelbart|til)\b",
    re.IGNORECASE,
)


def _log_messages(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        f = node.func
        is_log = isinstance(f, ast.Attribute) and f.attr in _LOG_METHODS
        is_print = isinstance(f, ast.Name) and f.id == "print"
        if not (is_log or is_print):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            yield node.lineno, first.value
        elif isinstance(first, ast.JoinedStr):
            yield node.lineno, "".join(v.value for v in first.values if isinstance(v, ast.Constant))


@pytest.mark.parametrize("rel", FILES)
def test_log_messages_are_english(rel):
    bad = [f"{rel}:{ln}: {msg!r}" for ln, msg in _log_messages(ROOT / rel) if _NORWEGIAN.search(msg)]
    assert not bad, "Norwegian log lines:\n" + "\n".join(bad)


def test_gateway_formatter_stamps_text_lines_and_keeps_json_lines():
    fmt = gw._GatewayLogFormatter()
    text = logging.LogRecord("x", logging.INFO, __file__, 1, "Frigate MQTT event: %s", ("a",), None)
    out = fmt.format(text)
    assert re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}.* INFO Frigate MQTT event: a$", out), out
    js = logging.LogRecord("x", logging.INFO, __file__, 1, '{"ts": "t", "event": "e"}', None, None)
    assert fmt.format(js) == '{"ts": "t", "event": "e"}'
