"""P139 reopened (2026-11-16) — camera pictures on a screen with and without analysis, and `query` as a question.

Owner decision 2026-11-16: KTSB can send a camera picture to a TV with and without analysis. Before this the camera tool always ran
the image model first and only then sent the picture, with the analysis as its caption — so «show the doorbell on the
TV» waited for the model, and an instruction put in `query` replaced the whole prompt (BUG-log 2026-10-19: the model
«answered» the instruction and wrote a made-up tool call).

Now:
- `analyse` (bool) — false = picture only. Not given: with `screen` and no `query` → picture only; else analysed.
- With a screen the picture is sent before the analysis starts (the model never holds the picture back).
- `query` is added to the camera's own prompt as the user's question about the picture; it never replaces it.
- scope=all reads `query` too (it read only the old key `spørsmål`).
No HTTP, no model, no screen. Placeholders only."""
import asyncio
import base64

import pytest

from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import store as fstore
from kaare_core.tools import executor_camera
from kaare_core.tools.definitions import _kamera_tool_base
from kaare_core.tools.i18n import t

JPEG = b"\xff\xd8fake-jpeg\xff\xd9"
B64 = base64.b64encode(JPEG).decode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    log: list = []

    async def vlm(prompt="", images=None, **kw):
        log.append(("vlm", prompt))
        await asyncio.sleep(0.05)
        log.append(("vlm_done", prompt))
        return {"ok": True, "text": "A cat on the steps."}

    async def role_call(role, prompt, images=None, rid="", **kw):
        return await vlm(prompt=prompt, images=images)

    async def snap(camera):
        return B64

    async def cams():
        return [{"api_name": "cam_a", "friendly_name": "Camera A"}, {"api_name": "cam_b", "friendly_name": "Camera B"}]

    async def screens(arguments, img_b64, caption, lang):
        log.append(("screen", caption))
        return "SHOWN"

    async def no_rest(event_id):
        raise RuntimeError("no Frigate in tests")

    monkeypatch.setattr(executor_camera, "ask_llm", vlm)
    monkeypatch.setattr(executor_camera, "ask_llm_role", role_call)
    monkeypatch.setattr(executor_camera, "fetch_snapshot_b64", snap)
    monkeypatch.setattr(executor_camera, "get_cameras", cams)
    monkeypatch.setattr(executor_camera, "_show_on_screens", screens)
    monkeypatch.setattr(executor_camera, "fetch_event_snapshot", no_rest)
    monkeypatch.setattr(executor_camera, "_save_image", lambda data, uid, kind, ext="jpg": "img1")
    monkeypatch.setattr(executor_camera, "_may_analyse", lambda uid: True)  # N41: these test the analysis itself
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    yield log
    fstore.reset_for_tests()


def _run(args):
    return asyncio.run(executor_camera._kamera({"_user_id": "user_x", **args}, "en"))


def _kinds(log):
    return [k for k, _ in log]


def test_show_on_a_screen_without_a_question_sends_the_picture_and_skips_the_model(env):
    out = _run({"action": "snapshot", "camera": "cam_a", "screen": "tv_x"})
    assert "vlm" not in _kinds(env)
    assert "screen" in _kinds(env)
    assert "SHOWN" in out
    assert t("cam_not_analysed", "en") in out
    assert "/api/image/img1" in out  # the picture also reaches the chat


def test_analyse_false_skips_the_model_even_with_a_question(env):
    _run({"action": "snapshot", "camera": "cam_a", "query": "Is anyone there?", "analyse": False})
    assert "vlm" not in _kinds(env)


def test_with_analysis_the_picture_goes_to_the_screen_before_the_model_starts(env):
    out = _run({"action": "snapshot", "camera": "cam_a", "screen": "tv_x", "analyse": True})
    kinds = _kinds(env)
    assert kinds.index("screen") < kinds.index("vlm")
    assert "A cat on the steps." in out and "SHOWN" in out


def test_snapshot_without_a_screen_is_analysed_as_before(env):
    out = _run({"action": "snapshot", "camera": "cam_a"})
    assert "vlm" in _kinds(env)
    assert "A cat on the steps." in out


def test_query_is_added_to_the_camera_prompt_as_a_question_not_used_as_the_prompt(env):
    q = "Is the gate open?"
    _run({"action": "snapshot", "camera": "cam_a", "query": q})
    prompt = next(p for k, p in env if k == "vlm")
    assert prompt != q
    assert prompt.startswith(t("cam_prompt_single", "en"))
    assert t("cam_prompt_user_question", "en", query=q) in prompt


def test_scope_all_reads_query(env):
    q = "Are there any cars?"
    _run({"action": "snapshot", "scope": "all", "query": q})
    prompt = next(p for k, p in env if k == "vlm")
    assert t("cam_prompt_user_question", "en", query=q) in prompt
    assert t("cam_prompt_all", "en") in prompt


def test_show_event_without_analysis_shows_the_picture_and_skips_the_model(env):
    (domain_store.SNAPSHOT_DIR / "e9.jpg").write_bytes(JPEG)
    out = _run({"action": "show_event", "event_id": "e9", "screen": "tv_x", "analyse": False})
    assert "vlm" not in _kinds(env)
    assert "SHOWN" in out
    assert "/api/image/img1" in out


def test_show_event_with_analysis_shows_first(env):
    (domain_store.SNAPSHOT_DIR / "e9.jpg").write_bytes(JPEG)
    _run({"action": "show_event", "event_id": "e9", "screen": "tv_x", "analyse": True})
    kinds = _kinds(env)
    assert kinds.index("screen") < kinds.index("vlm")


def test_the_schema_offers_analyse():
    props = _kamera_tool_base("en")["function"]["parameters"]["properties"]
    assert props["analyse"]["type"] == "boolean"
