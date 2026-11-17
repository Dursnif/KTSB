"""P144-s24 follow-up (2026-10-31 ~02:40):

1. «si det»: when an episode was analysed again after its day went into Kåre's LTM, the night's addition says it is a
   correction of episode N (a later memory can clear something up or change the conclusion) — not just «concluded
   later».
4. the «Analyser» rate is a GUI setting (Integrations → Frigate, Expert): `conclude.analyse_per_10min`, default 2 in
   code and in the template, clamped; the endpoint reads it at request time (no restart).
Never touches live state (tmp config / DBs).
"""

import asyncio
import time
from pathlib import Path

import pytest
import yaml

from kaare_core.frigate_user import conclude
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import frigate_ltm
from kaare_core.routers import router_frigate_user as rfu
from tests.test_p144_s24_reanalyse import _concluded_episode, _hdr, env  # noqa: F401 — fixture

TEMPLATE = Path("/kaare/configs_default/frigate_user.yaml")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "frigate_user.yaml"
    path.write_text("enabled: true\n")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", path)
    return path


def test_rate_setting_default_clamp_and_update(cfg):
    assert fsettings.get_settings()["conclude"]["analyse_per_10min"] == 2
    cfg.write_text("enabled: true\nconclude:\n  analyse_per_10min: 999\n")
    assert fsettings.get_settings()["conclude"]["analyse_per_10min"] == 60
    out = fsettings.update({"conclude": {"analyse_per_10min": 10}})
    assert out["conclude"]["analyse_per_10min"] == 10
    with pytest.raises(ValueError):
        fsettings.update({"conclude": {"analyse_per_10min": 0}})


def test_template_ships_the_generic_default():
    assert yaml.safe_load(TEMPLATE.read_text())["conclude"]["analyse_per_10min"] == 2


def test_endpoint_reads_the_rate_setting(env, monkeypatch):  # noqa: F811
    ep_id = _concluded_episode(env)
    c = env["client"]
    rate = {"n": 2}
    monkeypatch.setattr(rfu, "_rate_max", lambda: rate["n"])
    now = time.time()
    rfu._STARTS.extend([now, now])
    assert c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr()).status_code == 429
    rate["n"] = 10  # the GUI changed it — no restart
    assert c.post(f"/api/frigate_user/episodes/{ep_id}/analyse", headers=_hdr()).status_code == 202


def test_rate_max_follows_the_frigate_user_settings(cfg):
    cfg.write_text("enabled: true\nconclude:\n  analyse_per_10min: 7\n")
    assert rfu._rate_max() == 7


def test_ltm_addition_names_the_corrected_episode(env):  # noqa: F811
    ep_id = _concluded_episode(env)
    day = frigate_ltm._day_of(fstore.episode(ep_id).start_ts)
    fstore.set_ltm_marker({"settled_through": None, "done": {day: [ep_id, 4242]}})
    assert asyncio.run(conclude.reanalyse(ep_id))["ok"] is True
    assert frigate_ltm.reopen_episode(ep_id) == "reopened"
    (batch,), _ = frigate_ltm.day_batches(day, fstore.ltm_marker(), time.time() + 30 * 86400, "nb", set())
    assert batch.corrected == [ep_id]
    prompt = frigate_ltm.build_prompt(batch, "nb")
    assert frigate_ltm._t("correction", "nb", ids=str(ep_id)) in prompt
    # a plain late conclusion stays a plain addition
    batch.corrected = []
    assert frigate_ltm._t("correction", "nb", ids=str(ep_id)) not in frigate_ltm.build_prompt(batch, "nb")
