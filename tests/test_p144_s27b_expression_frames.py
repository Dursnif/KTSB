"""P144-s27b — expression and gesture in the frame run (measured: gestures 1/12 → 11/12, smiles 0 → 11/12 on ep 245/247).
Open text, no example words; kept per frame row only (never in the state the 9B is shown, so nothing to copy forward);
dropped where code sets the person absent; «not seen» under the s20 size limits; only when the house switch is on and no
named household member in the episode has turned it off. Fakes only."""

import asyncio
import json
from zoneinfo import ZoneInfo

import pytest

from kaare_core import model_enforcement as me
from kaare_core.frigate_user import conclude, frames, route
from kaare_core.frigate_user import episode_state as es
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users import frigate_consent

UTC = ZoneInfo("UTC")
T0 = 1_792_998_000.0
CAMS = {"door": {"display_name": "Ringeklokke", "role": "front_door"}}
BIG = [0.3, 0.2, 0.3, 0.7]  # 0.7 × 480 = 336 px
SMALL = [0.5, 0.5, 0.05, 0.15]  # 72 px: gesture yes, face no
TINY = [0.5, 0.5, 0.03, 0.08]  # 38 px


def test_prompts_ask_for_both_in_own_words_and_name_nothing():
    for key in ("frigate_frames_full_ex", "frigate_frames_change_ex"):
        for lang in ("en", "nb", "de"):
            text = _t_i18n(key, lang, camera="c", time="t", state="s", language="l")
            assert '"expression"' in text and '"gesture"' in text, (key, lang)
        en = _t_i18n(key, "en", camera="c", time="t", state="s", language="l").lower()
        for word in ("smil", "wave", "thumb", "happy", "angry", "point", "e.g.", "for example", "such as"):
            assert word not in en, (key, word)


def test_full_answer_keeps_looks_per_row_not_in_the_state():
    st = es.EpisodeState()
    data = {"persons": [{"age": "adult", "gender": "man", "clothing": "dark", "place": "door", "posture": "standing",
                         "carried": "", "expression": "smiling", "gesture": "thumbs up"}]}
    st.apply_full(data, "door", T0, 1, tz=UTC, looks=True)
    assert st.rows[-1]["looks"] == {"P1": {"expression": "smiling", "gesture": "thumbs up"}}
    shown = st.prompt_text("door", "en")
    assert "smiling" not in shown and "thumbs" not in shown


def test_change_answer_keeps_looks_for_persons_in_the_image():
    st = es.EpisodeState()
    st.apply_full({"persons": [{"age": "adult", "clothing": "dark"}]}, "door", T0, 1, tz=UTC, looks=True)
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "expression": "open mouth", "gesture": "arm out"}],
                     "new_persons": []}, "door", T0 + 3, 2, 1, tz=UTC, looks=True)
    assert st.rows[-1]["looks"] == {"P1": {"expression": "open mouth", "gesture": "arm out"}}


def test_without_the_switch_nothing_is_kept():
    st = es.EpisodeState()
    st.apply_full({"persons": [{"age": "adult", "expression": "smiling", "gesture": "wave"}]}, "door", T0, 1, tz=UTC)
    assert "looks" not in st.rows[-1]


def test_absent_by_code_drops_the_looks():
    """247 #7: the 9B copied P1 forward on an empty frame and wrote «neutral»; code sets P1 absent → nothing kept."""
    st = es.EpisodeState()
    st.apply_full({"persons": [{"age": "adult"}]}, "door", T0, 1, tz=UTC, looks=True)
    st.apply_change({"known": [{"id": "P1", "in_image": "yes", "expression": "neutral"}], "new_persons": []},
                    "door", T0 + 30, 2, 1, tz=UTC, looks=True)
    st.set_absent("door", T0 + 30, "frigate_no_track", tz=UTC, ids=["P1"])
    assert not st.rows[-1].get("looks")


def test_placeholders_are_not_kept():
    st = es.EpisodeState()
    st.apply_full({"persons": [{"age": "adult", "expression": "unclear", "gesture": ""}]}, "door", T0, 1, tz=UTC,
                  looks=True)
    assert not st.rows[-1].get("looks")


@pytest.mark.parametrize("px,expression,gesture", [(336, "smiling", "wave"), (72, "", "wave"), (38, "", "")])
def test_size_limits(px, expression, gesture):
    st = es.EpisodeState()
    st.apply_full({"persons": [{"age": "adult", "expression": "smiling", "gesture": "wave"}]}, "door", T0, 1, tz=UTC,
                  looks=True)
    es.limit_looks(st.rows[-1], px)
    got = (st.rows[-1].get("looks") or {}).get("P1") or {}
    assert got.get("expression", "") == expression and got.get("gesture", "") == gesture


def test_timeline_line_carries_them_as_the_image_models_words():
    st = es.EpisodeState()
    st.apply_full({"persons": [{"age": "adult", "posture": "standing", "expression": "smiling", "gesture": "wave"}]},
                  "door", T0, 1, tz=UTC, looks=True)
    line = es.timeline_lines(st, "nb")[0]
    assert "smiling" in line and "wave" in line and "uttrykk" in line and "gest" in line


def test_answer_texts_include_them_for_the_language_check():
    data = {"known": [{"id": "P1", "expression": "smiler", "gesture": "vinker"}]}
    assert {"smiler", "vinker"} <= set(es.answer_texts(data))


# ── the gate: house switch + every named household member ─────────────────────


def _ev(ref=None):
    return fstore.FrigateUserEvent(event_id="e1", camera="door", label="person", start_ts=T0, end_ts=T0 + 10,
                                   person_ref=ref)


def test_gate_house_switch_off(monkeypatch):
    monkeypatch.setattr(fsettings, "expressions", lambda: False)
    monkeypatch.setattr(frigate_consent, "expressions_off", lambda: frozenset())
    assert conclude.expressions_allowed([_ev()]) is False


def test_gate_a_named_member_who_said_no_blocks_the_whole_episode(monkeypatch):
    monkeypatch.setattr(fsettings, "expressions", lambda: True)
    monkeypatch.setattr(frigate_consent, "expressions_off", lambda: frozenset({"mum"}))
    assert conclude.expressions_allowed([_ev(), _ev("mum")]) is False
    assert conclude.expressions_allowed([_ev("dad")]) is True


def test_gate_fails_closed_when_the_list_cannot_be_read(monkeypatch):
    monkeypatch.setattr(fsettings, "expressions", lambda: True)
    monkeypatch.setattr(frigate_consent, "expressions_off", lambda: None)
    assert conclude.expressions_allowed([_ev()]) is False


def test_house_switch_default_on_and_settable():
    assert fsettings.DEFAULTS["expressions"] is True
    assert fsettings._validated({"expressions": False}) == {"expressions": False}
    with pytest.raises(ValueError):
        fsettings._validated({"expressions": "yes"})


# ── the frame run end to end ──────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fstore, "episode_frames_dir", lambda: tmp_path / "frames")
    fstore.reset_for_tests()
    state = {"prompts": [], "allowed": True, "box": BIG}

    async def llm(role, prompt, images=None, rid=""):
        state["prompts"].append((rid, prompt))
        if rid.endswith(":frames:1"):
            return {"text": json.dumps({"persons": [{"age": "adult", "gender": "man", "clothing": "dark",
                                                     "posture": "standing", "expression": "smiling",
                                                     "gesture": "thumbs up"}], "doors": []})}
        return {"text": json.dumps({"known": [{"id": "P1", "in_image": "yes", "expression": "neutral",
                                               "gesture": ""}], "new_persons": [], "doors": []})}

    async def timeline(event_id):
        return [{"timestamp": T0, "class_type": "visible", "data": {"box": state["box"]}},
                {"timestamp": T0 + 4, "class_type": "active", "data": {"box": state["box"]}},
                {"timestamp": T0 + 9, "class_type": "gone", "data": {"box": state["box"]}}]

    async def nochange(*a, **k):
        return {"regions": [], "guards": {}, "crops": []}

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(conclude.frigate_adapter, "fetch_timeline", timeline)
    monkeypatch.setattr(conclude, "_changes_step", nochange)
    monkeypatch.setattr(conclude, "_before_lines", lambda *a, **k: [])
    monkeypatch.setattr(conclude, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(route, "get_local_tz", lambda: UTC)
    monkeypatch.setattr(me, "_log_enforcement", lambda *a, **k: None)
    monkeypatch.setattr(me, "get_enforcer_modes", dict)
    monkeypatch.setattr(conclude, "expressions_allowed", lambda events: state["allowed"])
    yield state
    fstore.reset_for_tests()


def _run():
    ev = fstore.FrigateUserEvent(event_id="e1", camera="door", label="person", start_ts=T0, end_ts=T0 + 10,
                                 path_data=[[[0.5, 0.5], T0], [[0.5, 0.6], T0 + 9]], camera_role="front_door")
    assert fstore.record_event(ev)
    (ep,) = fstore.episodes_between(0, T0 * 2)
    events = fstore.episode_events(ep.episode_id)
    picked = [frames.Frame(ts=T0 + 1, camera="door", jpeg=b"x"), frames.Frame(ts=T0 + 5, camera="door", jpeg=b"x")]
    return asyncio.run(conclude._frames_step(ep, events, picked, ["a", "b"], "nb", CAMS))


def test_run_asks_and_keeps_them_when_allowed(env):
    run = _run()
    assert '"expression"' in env["prompts"][0][1]
    rows = run["frames_state"]["rows"]
    assert rows[0]["looks"]["P1"] == {"expression": "smiling", "gesture": "thumbs up"}
    assert any("smiling" in line for line in run["timeline"])


def test_run_does_not_ask_when_not_allowed(env):
    env["allowed"] = False
    run = _run()
    assert all('"expression"' not in p for _r, p in env["prompts"])
    assert not any(r.get("looks") for r in run["frames_state"]["rows"])


def test_run_small_person_keeps_the_gesture_only(env):
    env["box"] = SMALL
    run = _run()
    assert run["frames_state"]["rows"][0]["looks"]["P1"] == {"gesture": "thumbs up"}


def test_27b_gets_the_rule_only_when_there_are_looks(env):
    run = _run()
    ep = fstore.episode(1)
    ep.scene = run
    prompt = conclude.build_episode_prompt(ep, fstore.episode_events(1), "nb", away=False, cameras=CAMS, hidden=set())
    assert conclude._t("rule_looks", "nb") in prompt
