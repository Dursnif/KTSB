"""P144 phase 6 (2026-11-13: «show_event må flyttes til den nye pipen»): the camera tool's `show_event` uses what
the camera pipe uses —
- the picture from the pipe's snapshot store (`frigate_store.SNAPSHOT_DIR`), else Frigate's REST as before;
- the stored words from frigate.db (L3's description + analysis; an old module-1 still text where one is stored), never
  the old frigate_analysis.log (nothing writes it any more);
- the model call through the `frigate` role (Settings → LLM/Models) like the pipe, with the language tag stripped — not
  Kåre's own ask_llm.
Tmp DB and snapshot folder (conftest), no HTTP, no model. Placeholders only."""
import asyncio
import base64

import pytest

from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import store as fstore
from kaare_core.tools import executor_camera

T0 = 1_800_000_000.0
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    calls = []

    async def role_call(role, prompt, images=None, rid="", **kw):
        calls.append({"role": role, "prompt": prompt, "images": images, "rid": rid})
        return {"ok": True, "text": "Ei katt sitter på trappa. [LANG:nb]"}

    async def no_rest(event_id):
        raise RuntimeError("no Frigate in tests")

    async def no_screens(arguments, img_b64, caption, lang):
        return ""

    async def kare_llm(*a, **k):
        raise AssertionError("show_event must not call Kåre's ask_llm")

    monkeypatch.setattr(executor_camera, "ask_llm_role", role_call, raising=False)
    monkeypatch.setattr(executor_camera, "ask_llm", kare_llm, raising=False)
    monkeypatch.setattr(executor_camera, "fetch_event_snapshot", no_rest)
    monkeypatch.setattr(executor_camera, "_show_on_screens", no_screens)
    monkeypatch.setattr(executor_camera, "_save_image", lambda data, uid, kind, ext="jpg": "img1")
    monkeypatch.setattr(executor_camera, "_may_analyse", lambda uid: True)  # N41: these test the analysis itself
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    yield calls
    fstore.reset_for_tests()


def _show(eid="e1"):
    return asyncio.run(executor_camera._kamera({"action": "show_event", "event_id": eid, "_user_id": "boss"}, "nb"))


def test_show_event_uses_the_pipes_snapshot_words_and_role(env):
    (domain_store.SNAPSHOT_DIR / "e1.jpg").write_bytes(JPEG)
    fstore.record_event(fstore.FrigateUserEvent(event_id="e1", camera="cam_door", label="cat", start_ts=T0,
                                                end_ts=T0 + 10, description="En katt på trappa.",
                                                analysis="Sitter stille."))
    out = _show()
    assert len(env) == 1 and env[0]["role"] == "frigate"
    assert env[0]["images"] == [base64.b64encode(JPEG).decode()]
    assert "En katt på trappa. Sitter stille." in env[0]["prompt"]
    assert env[0]["rid"].startswith("rid-frigate-show-")
    assert out.startswith("Ei katt sitter på trappa.") and "[LANG:" not in out


def test_no_stored_words_means_no_prior_analysis_line(env):
    (domain_store.SNAPSHOT_DIR / "e2.jpg").write_bytes(JPEG)
    _show("e2")
    assert "Den automatiske analysen sa" not in env[0]["prompt"]


def test_no_picture_anywhere_says_so(env):
    out = _show("missing")
    assert env == [] and "missing" in out
