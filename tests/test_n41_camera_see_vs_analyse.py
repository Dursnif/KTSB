"""N41 — the camera tool separates SEE (what is stored) from a new ANALYSIS (image model / GPU), per role.

Owner decision 2026-11-16: children may see the picture, earlier conclusions and who/what/where, but not use the GPU. Role flag
`camera_analyse` in tool_permissions.yaml (a row in the Users page's role × tool matrix, like library_online).
- defaults: child/teen see, never analyse; young_adult/adult/admin both; household/stranger neither (no camera);
- a child asking for an analysis gets an honest «not for your role — here is what is stored», and NO model call;
- a child still gets the picture without analysis, a stored event with its stored words, and the stored reads;
- an adult's analysis works as before; the role note (DT04-S05b) names camera_analyse for a child;
- an existing install's roles that already had `camera` keep analysing after the upgrade (migration step).
No HTTP, no model, no screen. Tmp files only. Placeholders only."""

import asyncio
import base64
from pathlib import Path

import pytest
import yaml

from kaare_core import config
from kaare_core.config_migrations import camera_see_and_analyse
from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import store as fstore
from kaare_core.tools import executor_camera
from kaare_core.tools.i18n import t
from kaare_core.users import store as ustore

DEFAULTS = yaml.safe_load(Path("/kaare/configs_default/tool_permissions.yaml").read_text())
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"
B64 = base64.b64encode(JPEG).decode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_TOOL_PERMISSIONS", DEFAULTS)
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    for name, role in (("kid", "child"), ("teen1", "teen"), ("grown", "adult")):
        ustore.create_user(username=name, display_name=name, role=role, pin="482915")
    calls: list = []

    async def vlm(prompt="", images=None, **kw):
        calls.append(prompt)
        return {"ok": True, "text": "A cat on the steps."}

    async def role_call(role, prompt, images=None, rid="", **kw):
        return await vlm(prompt=prompt, images=images)

    async def snap(camera):
        return B64

    async def cams():
        return [{"api_name": "cam_a", "friendly_name": "Camera A"}]

    async def no_rest(event_id):
        raise RuntimeError("no Frigate in tests")

    monkeypatch.setattr(executor_camera, "ask_llm", vlm)
    monkeypatch.setattr(executor_camera, "ask_llm_role", role_call)
    monkeypatch.setattr(executor_camera, "fetch_snapshot_b64", snap)
    monkeypatch.setattr(executor_camera, "get_cameras", cams)
    monkeypatch.setattr(executor_camera, "fetch_event_snapshot", no_rest)
    monkeypatch.setattr(executor_camera, "_save_image", lambda data, uid, kind, ext="jpg": "img1")
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    domain_store.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    (domain_store.SNAPSHOT_DIR / "ev1.jpg").write_bytes(JPEG)
    yield calls
    fstore.reset_for_tests()


def _run(uid, args, name="camera"):
    return asyncio.run(executor_camera.dispatch(name, {"_user_id": uid, **args}))


def test_default_roles():
    roles = DEFAULTS["roles"]
    for role in ("child", "teen", "young_adult", "adult"):
        assert "camera" in roles[role]
    assert "camera_analyse" not in roles["child"] and "camera_analyse" not in roles["teen"]
    assert "camera_analyse" in roles["young_adult"] and "camera_analyse" in roles["adult"]
    for role in ("household", "stranger"):
        assert "camera" not in roles[role] and "camera_analyse" not in roles[role]


def test_role_flag(env):
    assert [config.role_may_analyse_camera(r) for r in ("child", "teen", "young_adult", "adult", "admin", "stranger")] \
        == [False, False, True, True, True, False]


@pytest.mark.parametrize("args", [
    {"action": "snapshot", "camera": "cam_a", "analyse": True},
    {"action": "snapshot", "camera": "cam_a", "query": "who is there?"},
    {"action": "snapshot", "scope": "all"},
    {"action": "show_event", "event_id": "ev1", "analyse": True},
])
def test_a_child_asking_for_analysis_gets_no_model_call(env, args):
    out = _run("kid", args)
    assert env == []  # no GPU work at all
    assert t("cam_analyse_not_for_role", "nb") in out


def test_the_legacy_snapshot_tool_is_gated_too(env):
    out = _run("kid", {"camera": "cam_a"}, name="hent_snapshot")
    assert env == [] and t("cam_analyse_not_for_role", "nb") in out


def test_a_child_still_sees_the_picture_and_the_stored_words(env):
    out = _run("kid", {"action": "snapshot", "camera": "cam_a", "analyse": True})
    assert "/api/image/img1" in out  # the picture without analysis
    out = _run("kid", {"action": "show_event", "event_id": "ev1", "analyse": True})
    assert "/api/image/img1" in out and env == []
    assert _run("kid", {"action": "analyze"}) != t("cam_analyse_not_for_role", "nb")  # stored reads are SEE


def test_an_adult_still_gets_the_analysis(env):
    out = _run("grown", {"action": "snapshot", "camera": "cam_a", "analyse": True})
    assert "A cat on the steps." in out and len(env) == 1


def test_role_note_names_camera_analyse_for_a_child(env):
    assert "camera_analyse" in config.get_tools_withheld_for_role("child")
    assert "camera_analyse" not in config.get_tools_withheld_for_role("adult")


def test_migration_keeps_analysis_for_roles_that_had_the_camera():
    data = {"roles": {"child": ["ha_read"], "teen": ["ha_read"], "young_adult": ["memory"],
                      "adult": ["camera", "memory"], "admin": ["all"], "stranger": ["get_weather"]}}
    changes = camera_see_and_analyse(data, DEFAULTS)
    r = data["roles"]
    assert "camera_analyse" in r["adult"]  # had the camera → keeps analysing
    assert "camera" in r["child"] and "camera_analyse" not in r["child"]
    assert "camera" in r["teen"] and "camera_analyse" not in r["teen"]
    assert "camera" in r["young_adult"] and "camera_analyse" in r["young_adult"]
    assert r["admin"] == ["all"] and "camera" not in r["stranger"]
    assert changes and camera_see_and_analyse(data, DEFAULTS) == []  # runs once


def test_a_picture_to_a_screen_with_analysis_shows_the_still_but_no_model_call(env, monkeypatch):
    shown = []

    async def screens(arguments, img_b64, caption, lang):
        shown.append(caption)
        return "SHOWN"

    monkeypatch.setattr(executor_camera, "_show_on_screens", screens)
    out = _run("kid", {"action": "snapshot", "camera": "cam_a", "screen": "tv_stue", "analyse": True})
    assert env == [] and shown and "SHOWN" in out  # the still went to the screen, the image model never ran
    assert t("cam_analyse_not_for_role", "nb") in out
    out = _run("kid", {"action": "show_event", "event_id": "ev1", "screen": "tv_stue", "analyse": True})
    assert env == [] and len(shown) == 2
