"""DT04 R0.6b — the camera pipe ships on, with the heavy modules off (P144 J21 decision 3, rule 24 lightest tier).

The template turns the Frigate user on with m4 (frame by frame), m5 (the conclusion) and m6 (the 27B on doubt) «off»:
a stock install has one chat model, and these calls would queue on it. Before R0.6b only m2 acted; now m4–m6 do too:
- m4 off → after the gate's look (m3) no frame-by-frame run and no one-call description; the frames are still kept.
- m5 off → no conclusion call: the episode is closed by code (so it never waits forever), not a memory line; the
  label gate's «likely false detection» still works.
- m6 off → no 27B look on doubt; the conclusion (if m5 is on) runs as before.
Never touches live state (tmp DBs and config, fake LLM).
"""

import json
from pathlib import Path

import pytest
import yaml

from kaare_core.frigate_user import conclude, modules, stm
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from tests.test_p144_s16_blind_gate import A_PERSON, T0, _ev, _kinds, _only_episode, _run
from tests.test_p144_s16_blind_gate import env as _s16_env  # noqa: F401 — fixture

TEMPLATE = Path("/kaare/configs_default/frigate_user.yaml")


@pytest.fixture
def env(_s16_env, tmp_path, monkeypatch):  # noqa: F811
    cfg = tmp_path / "frigate_user.yaml"
    monkeypatch.setattr(fsettings, "CONFIG_PATH", cfg)

    def set_modules(**mods):
        cfg.write_text(yaml.safe_dump({"enabled": True, "modules": dict(mods)}))

    _s16_env["set_modules"] = set_modules
    set_modules()
    return _s16_env


def _person_episode(env):
    env["look"] = json.dumps(A_PERSON)
    assert fstore.record_event(_ev("p1", "door", T0, label="person", role="front_door"))


# ── the switches act ────────────────────────────────────────────────────────────

def test_m4_to_m6_are_switches_that_act():
    assert {4, 5, 6} <= set(modules.ACTING)


def test_the_template_ships_the_pipe_on_with_the_heavy_modules_off():
    tpl = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert tpl["enabled"] is True
    assert tpl["modules"]["m4"] == tpl["modules"]["m5"] == tpl["modules"]["m6"] == "off"
    assert tpl["face_votes"]["enabled"] is False
    res = modules.resolve(tpl, {})
    assert [m for m in range(8) if res[m]["on"]] == [0, 3, 7]


def test_m4_off_skips_frame_by_frame_and_the_description(env):
    env["set_modules"](m4="off", m5="off", m6="off")
    _person_episode(env)
    _run()
    assert _kinds(env) == ["look"]  # the gate's look only — no description, no conclusion
    ep = _only_episode()
    assert ep.scene["status"] == "skipped" and ep.scene["reason"] == "module_off"
    assert ep.scene["frames"]  # the frames are still listed (kept)


def test_m5_off_closes_the_episode_by_code_and_it_is_no_memory_line(env):
    env["set_modules"](m4="off", m5="off", m6="off")
    _person_episode(env)
    assert _run() == 1
    c = _only_episode().conclusion
    assert c["status"] == "done" and c["by"] == "code" and c["reason"] == "module_off"
    assert "frigate_conclude" not in _kinds(env)
    assert stm.counts_as_memory(c) is False
    assert _run() == 0  # never picked up again


def test_m5_on_concludes_as_before(env):
    env["set_modules"](m6="off")
    _person_episode(env)
    _run()
    assert _kinds(env)[-1] == "frigate_conclude"
    assert _only_episode().conclusion["by"] == "model"


def test_m6_off_never_asks_the_27b_on_doubt(env, monkeypatch):
    env["set_modules"](m6="off")

    async def must_not_run(*a, **k):
        raise AssertionError("doubt look ran with m6 off")

    monkeypatch.setattr(conclude, "_doubt_look", must_not_run)
    _person_episode(env)
    _run()
    assert _only_episode().conclusion["by"] == "model"
