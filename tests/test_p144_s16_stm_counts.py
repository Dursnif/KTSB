"""P144-s16 Part B — the Frigate user's STM (existing STMRegistry, user_id «frigate») and the 27B's count table.

- Every concluded episode (model, code: vehicles / false detection) and every urgent result writes one first-person
  memory line into the Frigate STM — role `observation`, names only as the gate allowed, opted-out names scrubbed,
  nothing on a failed consent lookup; an empty Frigate STM is rebuilt from frigate.db.
- The 27B's prompt gets a small code-built table instead of the recent-episodes text: rows = names (as allowed),
  «unknown person», each ticked label; columns = this episode · today · last 7 days · usual now · last seen before.
  Counters live in RAM (fed by the store on every stored event, rebuilt from frigate.db at start) — the conclusion
  path makes no DB query for them.
- The 9B never sees memory.
Never touches live state: tmp DBs, a registry without autosave, fake LLM.
"""

import asyncio
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from adapters import llm_adapter
from kaare_core import app_state, normalcy
from kaare_core.frigate_user import conclude, counts, frames, route, stm
from kaare_core.frigate_user import store as fstore
from kaare_core.memory.short_term import ShortTermMemory, STMRegistry
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

UTC = ZoneInfo("UTC")
T0 = 1_793_143_388.0  # 2026-10-27 23:23 UTC (the tests run the household clock in UTC)
DAY = 86400
CAMS = {"door": {"display_name": "Inngang", "role": "front_door",
                 "labels": {"person": {"analyze": True}, "deer": {"analyze": True}, "car": {"analyze": False}}},
        "east": {"display_name": "utekamera øst", "role": "driveway", "labels": {"person": {"analyze": True}}}}
GOOD = {"summary": "Jeg så Person A gå inn.", "unusual": {"value": False, "reason": "vanlig"},
        "notify_suggestion": None, "raise_at_dev_meeting": None,
        "confidence": {"level": "medium", "reasons": ["ett kamera"]}}
A_PERSON = {"person": True, "animal": "none", "vehicle": False, "nothing": False}
NOTHING = {"person": False, "animal": "none", "vehicle": False, "nothing": True}
SCENE = {"persons": 1, "people": [], "animals": [], "vehicles": "", "at_door": "", "scene": "En person ved døra."}


def _ev(event_id, camera, start, *, label="person", ref=None, kind="unknown", claim=None, **kw):
    claim = claim or ("confirmed" if ref else "none")  # the storage gate keeps a name only on a named claim
    path = [[[0.2, 0.5], start], [[0.6, 0.5], start + 4]]
    return fstore.FrigateUserEvent(event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 10,
                                   path_data=path, camera_role=CAMS.get(camera, {}).get("role"), person_kind=kind,
                                   person_ref=ref, claim_status=claim, **kw)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    ustore.init_db()
    frigate_consent.invalidate()
    counts.reset_for_tests()
    reg = STMRegistry()
    reg._users["frigate"] = ShortTermMemory(user_id="frigate")  # no autosave: never the live state/stm_users
    monkeypatch.setattr(app_state, "STM_REGISTRY", reg)
    state = {"look": json.dumps(A_PERSON), "calls": [], "reg": reg, "hidden": set()}

    async def llm(role, prompt, images=None, rid=""):
        kind = "look" if (":look" in rid) else "scene" if (":scene" in rid) else role
        state["calls"].append({"kind": kind, "prompt": prompt})
        if kind == "look":
            return {"text": state["look"]}
        return {"text": json.dumps(SCENE if kind == "scene" else GOOD)}

    async def one_frame(picked):
        return [frames.Frame(ts=T0, camera="door", jpeg=b"\xff\xd8a")]

    async def no_weather(ep):
        return None

    monkeypatch.setattr(llm_adapter, "_log_llm_call_bg", lambda **kw: None)
    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    monkeypatch.setattr(frames, "fetch_frames", one_frame)
    monkeypatch.setattr(fstore, "save_episode_frames", lambda *a, **k: 0)
    monkeypatch.setattr(conclude, "_episode_weather", no_weather)
    monkeypatch.setattr(conclude, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(conclude, "_is_away", lambda: False)
    monkeypatch.setattr(conclude, "get_lang", lambda *a, **k: "nb")
    monkeypatch.setattr(conclude, "_world_rows", list)
    monkeypatch.setattr(conclude, "_untracked_names", lambda: state["hidden"])
    monkeypatch.setattr(stm, "_hidden_names", lambda: state["hidden"])
    monkeypatch.setattr(normalcy, "_load_baseline", lambda: {"days_of_data": 28, "buckets": {}})
    monkeypatch.setattr(app_state, "_last_user_prompt_time", 0.0)
    # counts prunes with its own clock when the store calls it without `now`: freeze it at the test day, or the
    # events fall out of the 8-day window once the real date has moved on (failed from 2026-11-04).
    monkeypatch.setattr(counts, "time", SimpleNamespace(time=lambda: T0))
    for mod in (route, conclude, stm):
        monkeypatch.setattr(mod, "get_local_tz", lambda: UTC)
    yield state
    counts.reset_for_tests()
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _lines(reg):
    return [(t.role, t.text) for t in reg.get("frigate")._dialog]


def _run(now):
    return asyncio.run(conclude.run_once(now=now))


def _episodes():
    return fstore.episodes_between(0, T0 * 2)


# ── STM ─────────────────────────────────────────────────────────────────────────


def test_a_concluded_episode_writes_one_observation_line(env):
    assert fstore.record_event(_ev("p1", "door", T0, ref="Person A", kind="household", claim="unverified"))
    assert _run(T0 + 1000) == 1
    (line,) = _lines(env["reg"])
    assert line[0] == "observation"
    assert "Jeg så Person A gå inn." in line[1] and "episode " in line[1] and "2026-10-27 23:23" in line[1]


def test_code_conclusions_write_no_lines(env):
    # P144-s20 item 6 (changed, said out loud): was `test_code_conclusions_write_lines_too`, expecting 2 lines (false
    # detection + vehicles). The live writer now uses stm.counts_as_memory() like rebuild() and the LTM step → 0 lines;
    # both episodes are still concluded and stored in frigate.db.
    env["look"] = json.dumps(NOTHING)
    assert fstore.record_event(_ev("d1", "east", T0, label="deer"))
    assert fstore.record_event(_ev("c1", "door", T0 + 3 * DAY, label="car"))
    _run(T0 + 3 * DAY + 1000)
    assert _lines(env["reg"]) == []
    kinds = sorted(stm.kind(e.conclusion) for e in _episodes())
    assert kinds == ["false_detection", "vehicles_only"]


def test_opted_out_names_are_scrubbed_from_the_line(env):
    env["hidden"] = {"Person A"}
    assert fstore.record_event(_ev("p1", "door", T0))
    _run(T0 + 1000)
    (line,) = _lines(env["reg"])
    assert "Person A" not in line[1]


def test_failed_consent_lookup_writes_nothing(env, monkeypatch):
    monkeypatch.setattr(stm, "_hidden_names", lambda: None)
    assert fstore.record_event(_ev("p1", "door", T0))
    _run(T0 + 1000)
    assert _lines(env["reg"]) == []
    assert _episodes()[0].conclusion["status"] == "done"  # the conclusion itself is not blocked


def test_urgent_result_is_written_at_once(env, monkeypatch):
    monkeypatch.setattr(conclude, "_settings", lambda: {"enabled": True, "episode_gap_s": 120, "conclude": {
        **conclude.DEFAULT_CONCLUDE, "urgent_labels": ["smoke"]}})

    async def llm(role, prompt, images=None, rid=""):
        return {"text": json.dumps({**GOOD, "summary": "Jeg så røyk ved døra.",
                                    "claim_verdict": {"verdict": "unsure", "why": "x"}})}

    monkeypatch.setattr(conclude, "ask_llm_role", llm)
    ev = _ev("s1", "door", T0, label="smoke")
    assert fstore.record_event(ev)
    assert asyncio.run(conclude.conclude_urgent(ev, None)) is not None
    (line,) = _lines(env["reg"])
    assert "haster" in line[1] and "røyk" in line[1]


def test_empty_stm_is_rebuilt_from_frigate_db_and_a_full_one_is_left_alone(env):
    assert fstore.record_event(_ev("p1", "door", T0))
    _run(T0 + 1000)
    env["reg"]._users["frigate"] = ShortTermMemory(user_id="frigate")
    assert stm.rebuild("nb", now=T0 + 2000) == 1
    assert "Jeg så Person A gå inn." in _lines(env["reg"])[0][1]
    assert stm.rebuild("nb", now=T0 + 2000) == 0


def test_the_9b_never_sees_memory(env):
    assert fstore.record_event(_ev("p0", "door", T0 - DAY, ref="Person A", kind="household"))
    _run(T0 - DAY + 1000)
    assert fstore.record_event(_ev("p1", "door", T0, ref="Person A", kind="household"))
    _run(T0 + 1000)
    for c in env["calls"]:
        if c["kind"] in ("look", "scene"):
            assert "Jeg så" not in c["prompt"] and "Person A" not in c["prompt"]
            assert "episoder" not in c["prompt"]


# ── counters in RAM ─────────────────────────────────────────────────────────────


def test_counters_follow_the_store_and_count_episodes(env):
    counts.rebuild(now=T0)
    for i in range(3):  # one person on three cameras in one episode = 1 episode
        assert fstore.record_event(_ev(f"a{i}", "door", T0 + 10 * i, ref="Person A", kind="household"))
    c = counts.count([counts.name_key("Person A")], T0 + DAY, T0 - 3600, exclude_episode=None)
    assert c.week == 1 and c.today == 1 and c.last.camera == "door"


def test_rebuild_reads_frigate_db_once_and_the_table_needs_no_query(env, monkeypatch):
    assert fstore.record_event(_ev("p0", "door", T0 - 2 * DAY, ref="Person A", kind="household"))
    counts.reset_for_tests()
    assert counts.rebuild(now=T0) == 1
    calls = []
    monkeypatch.setattr(fstore, "events_between", lambda *a, **k: calls.append(a) or [])
    monkeypatch.setattr(fstore, "episodes_between", lambda *a, **k: calls.append(a) or [])
    monkeypatch.setattr(fstore, "visits_between", lambda *a, **k: calls.append(a) or [])
    ep = fstore.Episode(episode_id=99, start_ts=T0, end_ts=T0 + 10, cameras=["door"], labels=["person"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    table = conclude.count_table(ep, [_ev("x", "door", T0)], "nb", CAMS, set())
    assert calls == []
    row = next(ln for ln in table if ln.startswith("| Person A |"))
    assert "| 0 | 0 | 1 |" in row and "25.10 23:23, Inngang" in row


def test_table_rows_columns_and_labels(env):
    counts.rebuild(now=T0)
    assert fstore.record_event(_ev("p0", "door", T0 - 3 * DAY, ref="Person A", kind="household"))
    assert fstore.record_event(_ev("u0", "east", T0 - 2 * 3600))  # unknown person today
    assert fstore.record_event(_ev("d0", "east", T0 - 5 * DAY, label="deer"))
    ep = fstore.Episode(episode_id=99, start_ts=T0 + 3600, end_ts=T0 + 3610, cameras=["door"], labels=["person"],
                        person_refs=["Person A"], event_count=2, status="closed", conclusion=None, concluded_ts=None)
    events = [_ev("x1", "door", T0 + 3600, ref="Person A", kind="household"),
              _ev("x2", "door", T0 + 3605, ref="Person A", kind="household")]
    table = conclude.count_table(ep, events, "nb", CAMS, set())
    text = "\n".join(table)
    assert table[0].startswith("| | denne episoden (hendelser) | i dag før denne (episoder) | siste 7 dager")
    assert "| Person A | 2 | 0 | 1 |" in text
    assert "| ukjent person | 0 | 0 | 1 |" in text  # 22:23 the day before: in the 7 days, not «today»
    assert "| person (Frigate-etikett) | 2 |" in text and "| hjort (Frigate-etikett) | 0 | 0 | 1 |" in text
    assert "car" not in text and "bil" not in text  # not ticked, not in the episode
    assert "aldri (Inngang)" in text  # usual now: never seen this weekday-hour


def test_opted_out_name_is_counted_as_unknown(env):
    counts.rebuild(now=T0)
    assert fstore.record_event(_ev("p0", "door", T0 - DAY, ref="Person B", kind="household"))
    ep = fstore.Episode(episode_id=99, start_ts=T0, end_ts=T0 + 10, cameras=["door"], labels=["person"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    text = "\n".join(conclude.count_table(ep, [_ev("x", "door", T0)], "nb", CAMS, {"Person B"}))
    assert "Person B" not in text and "| ukjent person | 1 | 0 | 1 |" in text
    closed = "\n".join(conclude.count_table(ep, [_ev("x", "door", T0)], "nb", CAMS, None))  # lookup failed
    assert "Person B" not in closed


def test_the_27b_prompt_has_the_table_not_the_recent_text(env):
    assert fstore.record_event(_ev("p0", "door", T0 - DAY, ref="Person A", kind="household"))
    _run(T0 - DAY + 1000)
    assert fstore.record_event(_ev("p1", "door", T0, ref="Person A", kind="household"))
    _run(T0 + 1000)
    concl = [c["prompt"] for c in env["calls"] if c["kind"] == "frigate_conclude"][-1]
    assert "## Tall fra minnet mitt (laget av koden)" in concl
    assert "| Person A | 1 | 0 | 1 |" in concl
    assert "Nylige episoder" not in concl and "Tidligere besøk" not in concl
    assert "Jeg så Person A gå inn." not in concl.split("## Tall fra minnet mitt")[1]  # numbers, not old summaries


@pytest.mark.parametrize("lang, head", [("en", "| | this episode (events) |"), ("de", "| | diese Episode (Ereignisse) |")])
def test_table_in_english_and_german(env, lang, head):
    ep = fstore.Episode(episode_id=99, start_ts=T0, end_ts=T0 + 10, cameras=["door"], labels=["person"],
                        person_refs=[], event_count=1, status="closed", conclusion=None, concluded_ts=None)
    assert conclude.count_table(ep, [_ev("x", "door", T0)], lang, CAMS, set())[0].startswith(head)
