"""P144-s06b: the 27B concludes once per closed episode (idle-gated), with an urgent bypass that
sees the image. Never touches live state — users.db and frigate.db point at tmp_path, the LLM is
faked and `_log_llm_call_bg` is patched (s03's leak)."""

import asyncio
import copy
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from adapters import frigate_adapter, llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.config_migrations import fill_missing_keys
from kaare_core.frigate_user import conclude, pipe
from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_settings
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

T0 = time.time() - 3 * 3600
IMG = "aW1hZ2U="  # «image»

GOOD = {
    "summary": "Jeg så en person ved inngangen som gikk videre til garasjen.",
    "unusual": {"value": False, "reason": "vanlig tid for denne kameraet"},
    "notify_suggestion": None,
    "raise_at_dev_meeting": None,
    "confidence": {"level": "medium", "reasons": ["to kameraer", "dagslys"]},
}


def _settings(**conclude_kw):
    c = {
        "idle_gate_s": 30,
        "urgent_classes": ["not_welcome"],
        "urgent_when_away_person": True,
        "urgent_labels": ["fire", "smoke"],
        "max_attempts": 3,
        "poll_s": 30,
    }
    c.update(conclude_kw)
    return {"enabled": True, "episode_gap_s": 120, "conclude": c}


class FakeLlm:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, role, prompt, images=None, rid=""):
        self.calls.append({"role": role, "prompt": prompt, "images": images})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return {"text": reply}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    ustore.create_user(username="persona", display_name="Person A", role="adult", pin="482915")
    ustore.update_user("persona", frigate_faces=["Person A"])
    frigate_consent.invalidate()
    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)

    async def no_recording(camera, ts, height=480):  # P144-s06d: the scene step must never reach the real Frigate
        raise RuntimeError("no recording in tests")

    monkeypatch.setattr(frigate_adapter, "fetch_recording_snapshot", no_recording)
    monkeypatch.setattr(conclude, "_settings", lambda: _settings())
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(normalcy, "_load_baseline", dict)
    monkeypatch.setattr(conclude, "_world_rows", list)  # P144-s06e: never read the live knowledge.db
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    fake = FakeLlm([json.dumps(GOOD)])
    monkeypatch.setattr(conclude, "ask_llm_role", fake)
    yield fake
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _ev(event_id, *, camera="inngang", label="person", start=T0, kind="unknown", claim="none",
        person_ref=None, description=None, **kw):
    return fstore.FrigateUserEvent(
        event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 10,
        person_ref=person_ref, person_kind=kind, claim_status=claim, claim_score=0.8,
        description=description, **kw,
    )


def _record_episode():
    assert fstore.record_event(_ev("e1", camera="inngang", description="mørk jakke"))
    assert fstore.record_event(_ev("e2", camera="garasje", start=T0 + 30, description="bærer en eske"))
    assert fstore.record_event(_ev("e3", camera="inngang", start=T0 + 70, label="car"))


def _episodes():
    return fstore.episodes_between(0, time.time() + 10)


# ── episode → one 27B call ──────────────────────────────────────────────────────


def test_closed_episode_gets_one_call_with_all_events(env):
    _record_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    assert len(env.calls) == 1
    call = env.calls[0]
    assert call["role"] == "frigate_conclude"
    assert not call["images"]  # the episode prompt is text only
    for text in ("inngang", "garasje", "mørk jakke", "bærer en eske", "car"):
        assert text in call["prompt"]
    (ep,) = _episodes()
    assert ep.conclusion["status"] == "done"
    # P144-s24 item 3: no frame here (no recording in tests) = no image check — the stored text says so (code fact)
    assert ep.conclusion["summary"] == f"{GOOD['summary']} (Ikke sjekket med bilder.)"
    assert ep.conclusion["images_checked"] is False
    assert ep.conclusion["unusual"] == GOOD["unusual"]
    assert ep.conclusion["confidence"]["level"] == "medium"


def test_open_episode_is_not_concluded(env):
    _record_episode()
    asyncio.run(conclude.run_once(now=T0 + 90))  # still within the gap
    assert env.calls == []
    assert _episodes()[0].status == "open"


def test_concluded_episode_is_not_called_again(env):
    _record_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    asyncio.run(conclude.run_once(now=T0 + 1100))
    assert len(env.calls) == 1


def test_switch_off_does_nothing(env, monkeypatch):
    monkeypatch.setattr(conclude, "_settings", lambda: {**_settings(), "enabled": False})
    _record_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    assert env.calls == []
    assert _episodes()[0].status == "open"  # not even closed


# ── idle gate ───────────────────────────────────────────────────────────────────


def test_idle_gate_defers_while_user_active_and_never_drops(env, monkeypatch):
    _record_episode()
    monkeypatch.setattr(app_state, "_last_user_prompt_time", time.time())
    asyncio.run(conclude.run_once(now=T0 + 1000))
    assert env.calls == []
    (ep,) = _episodes()
    assert ep.status == "closed" and ep.conclusion is None  # closed, still waiting
    monkeypatch.setattr(app_state, "_last_user_prompt_time", time.time() - 31)
    asyncio.run(conclude.run_once(now=T0 + 1100))
    assert len(env.calls) == 1
    assert _episodes()[0].conclusion["status"] == "done"


# ── malformed output ───────────────────────────────────────────────────────────


def test_malformed_json_is_stored_failed_and_retried_at_most_n(env):
    env.replies = ["[LANG:nb] Jeg tror det var en katt."]
    _record_episode()
    for i in range(5):
        asyncio.run(conclude.run_once(now=T0 + 1000 + i))
    assert len(env.calls) == 3  # max_attempts
    c = _episodes()[0].conclusion
    assert c["status"] == "failed"
    assert c["attempts"] == 3
    assert c["reason"]


def test_failed_then_good_is_done(env):
    env.replies = ["ikke json", json.dumps(GOOD)]
    _record_episode()
    asyncio.run(conclude.run_once(now=T0 + 1000))
    asyncio.run(conclude.run_once(now=T0 + 1001))
    c = _episodes()[0].conclusion
    assert c["status"] == "done" and c["attempts"] == 2


def test_parse_strips_lang_tag_and_fences():
    text = "[LANG:nb]\n```json\n" + json.dumps(GOOD) + "\n```"
    out, reason = conclude.parse_conclusion(text)
    assert reason == "" and out["summary"] == GOOD["summary"]


def test_parse_rejects_missing_fields():
    out, reason = conclude.parse_conclusion(json.dumps({"summary": "x"}))
    assert out is None and reason
    bad_conf = {**GOOD, "confidence": {"level": "sure", "reasons": []}}
    assert conclude.parse_conclusion(json.dumps(bad_conf))[0] is None


def test_parse_urgent_needs_claim_verdict():
    assert conclude.parse_conclusion(json.dumps(GOOD), urgent=True)[0] is None
    ok = {**GOOD, "claim_verdict": {"verdict": "unsure", "why": "ansiktet er lite"}}
    assert conclude.parse_conclusion(json.dumps(ok), urgent=True)[0]["claim_verdict"]["verdict"] == "unsure"


# ── urgent bypass ───────────────────────────────────────────────────────────────

URGENT = {**GOOD, "unusual": {"value": True, "reason": "ikke velkommen"},
          "claim_verdict": {"verdict": "unsure", "why": "lite ansikt"}}


def test_urgent_not_welcome_bypasses_idle_gate_and_sends_image(env, monkeypatch):
    env.replies = [json.dumps(URGENT)]
    monkeypatch.setattr(app_state, "_last_user_prompt_time", time.time())  # user active
    ev = _ev("u1", kind="not_welcome", claim="unverified", person_ref="Person B")
    assert fstore.record_event(ev)
    res = asyncio.run(conclude.conclude_urgent(ev, IMG))
    assert len(env.calls) == 1
    assert env.calls[0]["images"] == [IMG]
    assert env.calls[0]["role"] == "frigate_conclude"
    assert res["claim_verdict"]["verdict"] == "unsure"
    (ep,) = _episodes()
    assert ep.status == "open"  # not batched: the episode is still running
    assert ep.conclusion["urgent"][0]["event_id"] == "u1"
    assert ep.conclusion["urgent"][0]["claim_verdict"]["verdict"] == "unsure"
    assert "status" not in ep.conclusion  # the episode itself still waits for its conclusion


def test_episode_conclusion_keeps_earlier_urgent_result(env):
    env.replies = [json.dumps(URGENT), json.dumps(GOOD)]
    ev = _ev("u1", label="fire")
    assert fstore.record_event(ev)
    asyncio.run(conclude.conclude_urgent(ev, IMG))
    asyncio.run(conclude.run_once(now=T0 + 1000))
    c = _episodes()[0].conclusion
    assert c["status"] == "done"
    assert c["urgent"][0]["event_id"] == "u1"
    assert "u1" in env.calls[1]["prompt"] or "fire" in env.calls[1]["prompt"]


def test_not_urgent_event_makes_no_call(env):
    ev = _ev("n1")
    assert fstore.record_event(ev)
    assert asyncio.run(conclude.conclude_urgent(ev, IMG)) is None
    assert env.calls == []


def test_urgent_off_when_switch_off(env, monkeypatch):
    monkeypatch.setattr(conclude, "_settings", lambda: {**_settings(), "enabled": False})
    ev = _ev("u1", kind="not_welcome")
    assert asyncio.run(conclude.conclude_urgent(ev, IMG)) is None
    assert env.calls == []


def test_is_urgent_rules(env, monkeypatch):
    assert conclude.is_urgent(_ev("a", kind="not_welcome"))
    assert conclude.is_urgent(_ev("b", label="smoke"))
    assert not conclude.is_urgent(_ev("c"))
    monkeypatch.setattr(conclude, "_is_away", lambda: True)
    assert conclude.is_urgent(_ev("d"))  # person while away
    assert not conclude.is_urgent(_ev("e", label="car"))
    monkeypatch.setattr(conclude, "_settings", lambda: _settings(urgent_when_away_person=False))
    assert not conclude.is_urgent(_ev("f"))


# ── prompt content ─────────────────────────────────────────────────────────────


def _ep(**kw):
    base = {"episode_id": 7, "start_ts": T0, "end_ts": T0 + 80, "cameras": ["inngang"],
            "labels": ["person"], "person_refs": [], "event_count": 1, "status": "closed",
            "conclusion": None, "concluded_ts": None}
    base.update(kw)
    return fstore.Episode(**base)


def test_prompt_labels_unverified_names_and_hides_unnamed():
    events = [
        _ev("a", person_ref="Person B", kind="safe_guest", claim="unverified"),
        _ev("b", person_ref="persona", kind="household", claim="confirmed", start=T0 + 5),
        _ev("c", start=T0 + 9),
    ]
    p = conclude.build_episode_prompt(_ep(), events, "nb", away=False)
    line_a = next(ln for ln in p.splitlines() if "Person B" in ln)
    assert "Frigate-påstand, ubekreftet" in line_a
    line_b = next(ln for ln in p.splitlines() if "persona" in ln or "Person A" in ln)
    assert "bekreftet av KTSB" in line_b


def test_prompt_has_baseline_numbers(monkeypatch):
    dt = datetime.fromtimestamp(T0, UTC)
    key = f"inngang|person|{dt.hour}|{dt.weekday()}"
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {
        "confidence": 0.9, "days_of_data": 28,
        "buckets": {key: {"p_normal": 0.25, "days_with_event": 1, "days_observed": 4}},
    })
    p = conclude.build_episode_prompt(_ep(), [_ev("a")], "nb", away=False)
    assert "1/4" in p and "25 %" in p


def test_prompt_says_when_no_baseline(monkeypatch):
    monkeypatch.setattr(normalcy, "_load_baseline", dict)
    p = conclude.build_episode_prompt(_ep(), [_ev("a")], "en", away=False)
    assert "no baseline" in p.lower()


def test_prompt_language_and_first_person():
    p_en = conclude.build_episode_prompt(_ep(), [_ev("a")], "en", away=True)
    assert "first person" in p_en and "English" in p_en
    assert "away" in p_en.lower()
    p_de = conclude.build_episode_prompt(_ep(), [_ev("a")], "de", away=False)
    assert "Deutsch" in p_de


def test_prompt_compares_with_the_count_table():
    # s16 (2026-10-28): numbers made by code replace the recent-episodes text (test_p144_s16_stm_counts.py)
    p = conclude.build_episode_prompt(_ep(), [_ev("a")], "nb", away=False)
    assert "## Tall fra minnet mitt (laget av koden)" in p and "| ukjent person | 1 |" in p
    assert "Nylige episoder" not in p


# ── the role ───────────────────────────────────────────────────────────────────


def test_role_is_editable_and_shipped_sharing_default():
    assert "frigate_conclude" in router_settings._EDITABLE_LLM_ROLES
    tmpl = yaml.safe_load(Path("/kaare/configs_default/llm.yaml").read_text(encoding="utf-8"))
    assert tmpl["frigate_conclude"] == {"enabled": True, "share_with": "default"}


def test_role_unset_goes_to_default(monkeypatch):
    # P144-s06c: a missing role is a lean call on default's endpoint (call_llm_chat("default", …,
    # disable_thinking=True)), no longer ask_llm().
    seen = {}

    async def fake_chat(role, messages, disable_thinking=False, **kw):
        seen.update(role=role, images=messages[-1].get("images"), disable_thinking=disable_thinking)
        return {"ok": True, "text": "ok"}

    cfg = {k: v for k, v in llm_adapter.CFG.items() if k != "frigate_conclude"}
    monkeypatch.setattr(llm_adapter, "CFG", cfg)
    monkeypatch.setattr(llm_adapter, "call_llm_chat", fake_chat)
    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    res = asyncio.run(llm_adapter.ask_llm_role("frigate_conclude", "p", images=[IMG]))
    assert res["text"] == "ok" and seen == {"role": "default", "images": [IMG], "disable_thinking": True}


def test_migration_adds_role_to_existing_install():
    tpl = yaml.safe_load(Path("/kaare/configs_default/llm.yaml").read_text(encoding="utf-8"))
    old = copy.deepcopy(tpl)
    del old["frigate_conclude"]
    assert "frigate_conclude" in fill_missing_keys(old, tpl)
    assert old["frigate_conclude"] == {"enabled": True, "share_with": "default"}


def test_loop_registers_urgent_handler_with_the_pipe(monkeypatch):
    class Stop(Exception):
        pass

    async def stop(*_a, **_k):
        raise Stop

    monkeypatch.setattr(pipe, "_urgent_handler", None)
    monkeypatch.setattr(conclude.asyncio, "sleep", stop)
    with pytest.raises(Stop):
        asyncio.run(conclude.start_conclude_loop())
    assert pipe._urgent_handler is conclude.conclude_urgent
