"""J21 step 5b (P144 phase 5): module switches in config + dependency checks — no behaviour change.

`frigate_user.yaml: modules: {m1..m6: auto|on|off}`; module 0 (base) and 7 (alerts) are always on. «auto» derives from what
each install already has configured: m1 = gone since P144 phase 6 (always off, «replaced» by L3 + module 4), m2 = Double Take set up
(face_votes.enabled with the Frigate user on), m3–m6 = frigate_user.enabled. The shipped template says «auto» everywhere,
so the config migration (which only adds missing keys, from configs_default) never turns a module off in an existing
house. Proofs: (a) this house's config after migration gives today's modules; (b) a clean configs_default gives 0 + 7
only (decision 3).
DT04 R0.6b (2026-11-14) changed the template: the Frigate user ships ON with m4–m6 «off» (lightest tier, rule 24) and
m4–m6 act. A clean template now gives 0 + 3 + 7. An install that already stores `modules` keeps them (the house:
all auto); one with the pipe on but no `modules` key gets m4–m6 off from the template — said in the PENDING_SYNC row. Dependencies are shown, never enforced here; 1 and 4 both on → «dobbelt arbeid», a warning only."""
import copy
from pathlib import Path

import pytest
import yaml

from kaare_core.config_migrations import fill_missing_keys
from kaare_core.frigate_user import modules
from kaare_core.frigate_user import settings as fsettings

DEFAULT = Path("/kaare/configs_default")


def _y(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _on(res: dict) -> list[int]:
    return [m for m in range(8) if res[m]["on"]]


def test_a_clean_template_gives_modules_0_3_and_7():
    res = modules.resolve(_y(DEFAULT / "frigate_user.yaml"), _y(DEFAULT / "frigate_cameras.yaml"))
    assert _on(res) == [0, 3, 7]  # DT04 R0.6b: pipe on, heavy modules off; was [0, 7] (pipe off)
    assert res[1]["setting"] == "auto" and res[1]["why"] == "replaced"


def test_the_shipped_template_has_the_pipe_on_and_the_heavy_modules_off():
    tpl = _y(DEFAULT / "frigate_user.yaml")
    # DT04 R0.6b: was «auto everywhere, both masters off»
    assert tpl["modules"] == {"m1": "auto", "m2": "auto", "m3": "auto", "m4": "off", "m5": "off", "m6": "off"}
    assert tpl["enabled"] is True and tpl["face_votes"]["enabled"] is False
    assert _y(DEFAULT / "frigate_cameras.yaml")["enabled"] is False


# this house today (configs/ values, shape only) — it stores its modules (since J21 5b), so the template never decides them
HOUSE_USER = {"enabled": True, "face_votes": {"enabled": True},
              "modules": {"m1": "off", "m2": "auto", "m3": "auto", "m4": "auto", "m5": "auto", "m6": "auto"}}
HOUSE_CAMS = {"enabled": True}


def test_this_house_after_migration_keeps_todays_modules():
    user = copy.deepcopy(HOUSE_USER)
    fill_missing_keys(user, _y(DEFAULT / "frigate_user.yaml"))  # what migrate_configs does at start
    assert user["modules"] == HOUSE_USER["modules"]  # stored values are never overwritten by the migration
    res = modules.resolve(user, HOUSE_CAMS)
    assert _on(res) == [0, 2, 3, 4, 5, 6, 7]  # P144 phase 6: m1 is gone; Double Take votes, frames + 27B
    assert res["warnings"] == []


def test_the_real_house_config_files_resolve_like_today():
    """Read-only look at this install's live config (the house's own values): the same modules as today."""
    live_user, live_cams = Path("/kaare/configs/frigate_user.yaml"), Path("/kaare/configs/frigate_cameras.yaml")
    if not live_user.exists() or not live_cams.exists():
        pytest.skip("not this house")
    user = _y(live_user)
    fill_missing_keys(user, _y(DEFAULT / "frigate_user.yaml"))
    res = modules.resolve(user, _y(live_cams))
    assert res[0]["on"] and res[7]["on"]
    assert res[1]["on"] is False and res[1]["why"] == "replaced"  # P144 phase 6: m1 is gone, whatever is stored
    assert all(res[m]["on"] == bool(user.get("enabled")) for m in (3, 4, 5, 6))


def test_explicit_on_off_wins_over_auto():
    user = {"enabled": True, "face_votes": {"enabled": True}, "modules": {"m1": "off", "m2": "off"}}
    res = modules.resolve(user, {"enabled": True})
    assert not res[1]["on"] and res[1]["why"] == "replaced" and not res[2]["on"]  # m1 no longer switchable
    assert res["warnings"] == []


def test_m1_stays_off_even_when_set_on():
    """P144 phase 6: module 1 (the old still-image call) is gone — a stored «on» cannot bring it back."""
    res = modules.resolve({"enabled": True, "modules": {"m1": "on"}}, {"enabled": True})
    assert res[1]["on"] is False and res[1]["setting"] == "auto" and res[1]["why"] == "replaced"


def test_a_module_whose_needs_are_off_is_greyed_with_the_reason(monkeypatch):
    """The dependency logic for when m3–m6 become switchable (today only m2 acts — J21 5e, P144 phase 6)."""
    monkeypatch.setattr(modules, "ACTING", (1, 2, 3, 4, 5, 6))
    user = {"enabled": True, "modules": {"m3": "off"}}
    res = modules.resolve(user, {"enabled": False})
    assert res[4]["on"] is False and res[4]["why"] == "needs" and res[4]["needs_missing"] == [3]
    res = modules.resolve({"enabled": True, "modules": {"m4": "off"}}, {"enabled": False})
    assert res[5]["on"] is False and res[5]["needs_missing"] == ["4|1"]  # the conclusion needs frames or the still image
    assert res[7]["on"] is True  # alerts never depend on anything


def test_modules_0_and_7_cannot_be_switched_off():
    res = modules.resolve({"enabled": False, "modules": {"m0": "off", "m7": "off"}}, {"enabled": False})
    assert res[0]["on"] and res[7]["on"]


def test_the_setting_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\n")
    assert fsettings.get_settings()["modules"] == {f"m{i}": "auto" for i in range(1, 7)}
    fsettings.update({"modules": {"m1": "off"}})
    assert fsettings.get_settings()["modules"]["m1"] == "off" and fsettings.get_settings()["modules"]["m2"] == "auto"
    for bad in ({"m1": "maybe"}, {"m7": "off"}, {"m0": "on"}, "off"):
        with pytest.raises(ValueError):
            fsettings.update({"modules": bad})


def test_auto_double_take_needs_its_own_switch():
    res = modules.resolve({"enabled": True, "face_votes": {"enabled": False}}, {"enabled": True})
    assert res[2]["on"] is False and res[2]["why"] == "auto_double_take_off" and res[3]["on"] is True


def test_on_cannot_switch_on_what_is_not_set_up():
    res = modules.resolve({"enabled": True, "face_votes": {"enabled": False}, "modules": {"m2": "on"}}, {"enabled": False})
    assert res[2]["on"] is False and res[2]["why"] == "not_set_up"


def test_m3_ignores_a_stored_switch_and_m4_to_m6_act():
    # DT04 R0.6b: m4–m6 act now (was: «m3–m6 ignore a stored switch until they act»)
    res = modules.resolve({"enabled": True, "modules": {"m3": "off", "m4": "off", "m6": "off"}}, {"enabled": True})
    assert res[3]["on"] and res[3]["setting"] == "auto"
    assert not res[4]["on"] and not res[6]["on"] and res[4]["setting"] == "off"
    assert not res[5]["on"] and res[5]["why"] == "needs"  # 5 needs 4 (or the replaced 1)


def test_an_install_on_without_stored_modules_gets_the_heavy_ones_off():
    user = {"enabled": True}
    fill_missing_keys(user, _y(DEFAULT / "frigate_user.yaml"))
    assert _on(modules.resolve(user, {"enabled": False})) == [0, 3, 7]
