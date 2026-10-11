"""No undefined names in the service entry points and routers (P120: removing /api/intent_to_ha left
a reference in kaare_api.py that broke every chat request — the module is never imported by the
other tests, so only a static check catches it). Skipped when pyflakes is not installed."""

from pathlib import Path

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes import reporter as _reporter  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    "kaare_api.py",
    "kaare_ha_gateway.py",
    "services/voice/voice_bridge.py",
    "services/voice/voice_manager.py",
    "kaare_core/service_auth.py",
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kaare_core/routers").glob("*.py")),
    *sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kaare_core/users").glob("*.py")),
]


class _Collect(_reporter.Reporter):
    def __init__(self):
        self.messages = []

    def flake(self, message):
        if "undefined name" in str(message):
            self.messages.append(str(message))

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.messages.append(f"{filename}:{lineno}: syntax error: {msg}")

    def unexpectedError(self, filename, msg):
        self.messages.append(f"{filename}: {msg}")


@pytest.mark.parametrize("rel", FILES)
def test_no_undefined_names(rel):
    rep = _Collect()
    pyflakes_api.checkPath(str(ROOT / rel), rep)
    assert not rep.messages, "\n".join(rep.messages)
