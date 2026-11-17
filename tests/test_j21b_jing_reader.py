"""J21b reader 3 (Jing side) — Jing asks KTSB's camera memory first and never sees a name.

- the context URL sits next to the push URL (or `context_url`); the token is the voice's own or the shared inner_voices one;
- the API's lines (pseudonym ids) go into MENNESKER as they are; unreachable or «available: false» → «(ingen)»
  (P144 phase 6: the old face_events.txt fallback is gone — no file, so never a name either).
No real HTTP (urlopen is faked). Placeholders only."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "inner_voices"))
import common as iv_common
import jing_runner


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake(body):
        def urlopen(req, timeout=0):
            seen.append((req.full_url, req.headers.get("Authorization"), req.get_method()))
            if isinstance(body, Exception):
                raise body
            return _Resp(json.dumps(body).encode())
        monkeypatch.setattr(jing_runner.urllib.request, "urlopen", urlopen)
        return seen

    monkeypatch.setattr(iv_common, "load_service_config", lambda name: {"push_token": "shared"} if name == "inner_voices" else {})
    return fake


CFG = {"push_url": "http://ktsb.local:8000/api/inner-voices/push"}


def test_api_lines_with_shared_token(calls):
    seen = calls({"available": True, "people": ["[10:00] person-ab12cd (husstand) — Inngang"]})
    out = jing_runner._people_from_api(1_800_000_000.0, "nb", CFG)
    assert out == "[10:00] person-ab12cd (husstand) — Inngang"
    url, auth, method = seen[0]
    assert url.startswith("http://ktsb.local:8000/api/inner-voices/context?") and "source=jing" in url
    assert auth == "Bearer shared" and method == "GET"


def test_api_unusable_means_fallback(calls):
    calls(OSError("refused"))
    assert jing_runner._people_from_api(0.0, "nb", CFG) is None
    calls({"available": False, "people": []})
    assert jing_runner._people_from_api(0.0, "nb", CFG) is None
    assert jing_runner._people_from_api(0.0, "nb", {}) is None  # no push URL → no API


def test_build_context_prefers_the_api_and_has_no_file_fallback(monkeypatch):
    monkeypatch.setattr(jing_runner, "_read_digest_category", lambda c, cat, lang: "-")
    monkeypatch.setattr(jing_runner, "_people_from_api", lambda c, lang, cfg: "API-LINJE")
    assert "API-LINJE" in jing_runner.build_context(180, "nb", CFG)
    monkeypatch.setattr(jing_runner, "_people_from_api", lambda c, lang, cfg: None)
    out = jing_runner.build_context(180, "nb", CFG)
    mennesker = out.split("\n\n")[0]
    assert mennesker.splitlines()[1:] == [iv_common.t("inner_none", "nb")]
    assert not hasattr(jing_runner, "_read_face_events_recent") and not hasattr(iv_common, "FACE_EVENTS")


# ── the push uses the token the GUI writes (inner_voices.push_token) — found while building reader 3 ──

class _Provider:
    def generate(self, prompt, max_tokens):
        return "en tanke"


def test_jing_and_jang_push_with_the_shared_token(monkeypatch, tmp_path):
    import jang_runner  # noqa: PLC0415  (needs the inner_voices path set up above)
    sent = []
    monkeypatch.setattr(iv_common, "load_service_config", lambda name: {"push_token": "shared"} if name == "inner_voices" else {})
    for mod in (jing_runner, jang_runner):
        monkeypatch.setattr(mod, "push_thought", lambda url, token, source, content: sent.append((source, token)))
    monkeypatch.setattr(jing_runner, "build_context", lambda *a, **k: "ctx")
    jing_runner.run_once(_Provider(), {}, dict(CFG))
    monkeypatch.setattr(jang_runner, "_trim_jing_buffer", lambda *a, **k: None)
    thoughts = tmp_path / "jing_thoughts.txt"
    thoughts.write_text("\n\n[Jing 10:00]\nen observasjon", encoding="utf-8")
    monkeypatch.setattr(jang_runner, "JING_THOUGHTS", thoughts)
    monkeypatch.setattr(jang_runner, "PERSONALITY", tmp_path / "none.md")
    jang_runner.run_once(_Provider(), {}, dict(CFG))
    assert sent == [("jing", "shared"), ("jang", "shared")]
