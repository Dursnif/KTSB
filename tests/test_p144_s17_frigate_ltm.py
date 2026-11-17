"""P144-s17: the Frigate user's concluded camera memory reaches Kåre's long-term memory through the nightjob.

Before this the nightjob compressed `interactions` rows only — the cameras have none, so what they saw was forgotten
once the 40-line Frigate STM rolled over (≈ 1.5 days). Now:
- one LTM episode per finished household day, owner «frigate», saved and indexed like every episode; today waits;
- what counts = the Frigate STM rule (model memory + urgent results, never false detections / vehicle-only lines);
- a big day is split in time order; the numbers are a code-built table, never counted by the model;
- opted-out names never reach the prompt or the episode; a failed consent lookup writes nothing;
- the progress marker only moves with a saved episode, a late conclusion becomes an addition (never skipped);
- one internal per-night bound for the per-user loop and the Frigate step; logs carry ids/counts only.
Never touches live state: tmp DBs, fake LLM, fake index.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import kaare_nightjob as nj
from kaare_core import app_state
from kaare_core.frigate_user import query, stm
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import frigate_ltm, long_term, semantic_memory
from kaare_core.memory.short_term import ShortTermMemory, STMRegistry
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

OSLO = ZoneInfo("Europe/Oslo")  # a test zone that is not UTC — the code reads it from get_local_tz(), never a constant
DAY = 86400
D1 = datetime(2026, 10, 27, 1, 0, tzinfo=OSLO).timestamp()  # 01:00 Oslo = 00:00 UTC
D2 = D1 + DAY  # 2026-10-28
NOW = D1 + 3 * DAY + 10 * 3600  # 2026-10-30 11:00 Oslo — 10-27 and 10-28 and 10-29 finished, 10-30 is today
SECRET = "MEMORY-SECRET-5d1a"


class _NoQdrant:
    def __init__(self, *a, **kw):
        raise RuntimeError("no qdrant in tests")


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "interactions.db"
    monkeypatch.setattr(long_term, "DB_PATH", path)
    monkeypatch.setattr(long_term, "_ltm", None)
    long_term.get_ltm()
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    ustore.init_db()
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    frigate_consent.invalidate()
    monkeypatch.setattr(nj, "DB_PATH", path)
    monkeypatch.setattr(nj, "JING_THOUGHTS_PATH", tmp_path / "no_jing.txt")
    monkeypatch.setattr(nj.argus_size, "run_nightly", lambda *a, **kw: {"status": "test"})  # P150: Argus step, no live Qdrant
    monkeypatch.setattr(nj, "_compute_baseline", lambda: None)
    monkeypatch.setattr(nj, "ensure_collection", lambda *a, **kw: None)
    monkeypatch.setattr(nj, "_get_lang", lambda *a: "nb")
    monkeypatch.setattr(nj, "save_daily_summary", lambda *a, **kw: None)
    monkeypatch.setattr(semantic_memory, "_get_client", lambda write=False: _NoQdrant())

    async def no_heal(*a, **kw):
        return {}

    monkeypatch.setattr(nj, "reindex_missing", no_heal)
    state = {"indexed": [], "prompts": [], "replies": [], "hidden": set(), "now": NOW}

    async def fake_index(**kw):
        state["indexed"].append(kw)
        return True

    async def fake_chat(role, messages, **kw):
        prompt = messages[0]["content"]
        state["prompts"].append(prompt)
        text = state["replies"].pop(0) if state["replies"] else f"{SECRET} — jeg husker dagen. {prompt[-200:]}"
        return {"ok": bool(text), "text": text, "error": "" if text else "down"}

    monkeypatch.setattr(nj, "index_episode", fake_index)
    monkeypatch.setattr(nj, "_llm_chat", fake_chat)
    monkeypatch.setattr(stm, "_hidden_names", lambda: state["hidden"])
    monkeypatch.setattr(query, "_users_by_name", dict)
    for mod in (frigate_ltm, stm):
        monkeypatch.setattr(mod, "get_local_tz", lambda: OSLO)
    real_run = frigate_ltm.run

    async def run_at_now(compress, save, max_batches, lang, now=None):
        return await real_run(compress, save, max_batches, lang, now=state["now"])

    monkeypatch.setattr(frigate_ltm, "run", run_at_now)
    yield {**state, "path": path, "state": state}
    fstore.reset_for_tests()
    frigate_consent.invalidate()


def _ev(event_id, start, *, label="person", ref=None, claim=None, camera="door"):
    claim = claim or ("unverified" if ref else "none")
    return fstore.FrigateUserEvent(event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 10,
                                   path_data=[[[0.2, 0.5], start], [[0.6, 0.5], start + 4]], camera_role="front_door",
                                   person_kind="household" if ref else "unknown", person_ref=ref, claim_status=claim)


def _model(summary, level="medium"):
    return {"status": "done", "by": "model", "summary": summary, "lang": "nb", "confidence": {"level": level}}


def _episode(start, conclusion, *, label="person", ref=None, n=1):
    """One stored, closed episode (n events) with this conclusion (None = not concluded yet)."""
    for i in range(n):
        assert fstore.record_event(_ev(f"e{start}-{i}", start + i, label=label, ref=ref))
    (ep,) = [e for e in fstore.close_episodes(now=start + 10_000) if e.start_ts <= start + n]
    if conclusion is not None:
        assert fstore.conclude_episode(ep.episode_id, conclusion)
    return ep.episode_id


def _night(env):
    asyncio.run(nj.run_nightjob())


def _frigate_eps(path):
    conn = sqlite3.connect(str(path))
    rows = conn.execute("SELECT id, user_id, from_id, to_id, interaction_count, narrative, topics, status "
                        "FROM episodes WHERE user_id = 'frigate' ORDER BY id").fetchall()
    conn.close()
    return rows


# ── the rule (shared with the Frigate STM) ──────────────────────────────────────


def test_what_counts_is_one_rule():
    assert stm.counts_as_memory(_model("Jeg så en person."))
    assert not stm.counts_as_memory({**_model("Trolig feildeteksjon."), "by": "code", "false_detection": {"x": 1}})
    assert not stm.counts_as_memory({**_model("Bare kjøretøy."), "by": "code"})
    assert not stm.counts_as_memory({"status": "failed", "summary": "x"})
    assert not stm.counts_as_memory(None)


def test_rebuild_uses_the_same_rule(env, monkeypatch):
    reg = STMRegistry()
    reg._users["frigate"] = ShortTermMemory(user_id="frigate")
    monkeypatch.setattr(app_state, "STM_REGISTRY", reg)
    _episode(D1, {**_model("Bare kjøretøy forbi."), "by": "code"}, label="car")
    _episode(D1 + 600, _model("Jeg så en person ved døra."))
    assert stm.rebuild("nb", now=D1 + DAY) == 1
    (turn,) = reg.get("frigate")._dialog
    assert "person ved døra" in turn.text and "kjøretøy" not in turn.text


# ── the night step ──────────────────────────────────────────────────────────────


def test_frigate_memory_reaches_ltm_one_episode_per_finished_day(env):
    _episode(D1 + 3600, _model("Jeg så en person ved døra om morgenen."))
    _episode(D1 + 7200, _model("Jeg så et rådyr i hagen."), label="deer")
    _episode(D2 + 3600, _model("Jeg så postbudet."))
    _episode(NOW - 3600, _model("Jeg så noen i dag."))  # today: waits
    _night(env)
    eps = _frigate_eps(env["path"])
    assert len(eps) == 2
    for _id, owner, from_id, to_id, _n, _narr, _topics, status in eps:
        assert (owner, from_id, to_id, status) == ("frigate", 0, 0, "ok")
    assert [e[4] for e in eps] == [2, 1]  # memory lines per day
    assert eps[0][6].startswith("frigate, 2026-10-27") and eps[1][6].startswith("frigate, 2026-10-28")
    assert [i["user_id"] for i in env["indexed"]] == ["frigate", "frigate"]
    assert not any("i dag" in p for p in env["prompts"])
    assert fstore.ltm_marker()["settled_through"] == "2026-10-29"  # 10-29 had nothing — settled too


def test_frigate_episodes_leave_the_person_cursors_alone(env):
    conn = sqlite3.connect(str(env["path"]))
    before = conn.execute("SELECT user_id, last_id FROM ltm_cursors ORDER BY user_id").fetchall()
    conn.close()
    _episode(D1 + 3600, _model("Jeg så en person."))
    _night(env)
    conn = sqlite3.connect(str(env["path"]))
    after = conn.execute("SELECT user_id, last_id FROM ltm_cursors ORDER BY user_id").fetchall()
    conn.close()
    assert before == after and len(_frigate_eps(env["path"])) == 1


def test_false_detections_and_vehicle_lines_stay_out_but_are_counted(env):
    _episode(D1 + 3600, _model("Jeg så en person."))
    _episode(D1 + 7200, {**_model("Trolig feildeteksjon ved hekken."), "by": "code", "false_detection": {"a": 1}})
    _episode(D1 + 9000, {**_model("Bare kjøretøy på veien."), "by": "code"}, label="car")
    _night(env)
    (prompt,) = env["prompts"]
    assert "feildeteksjon ved hekken" not in prompt and "kjøretøy på veien" not in prompt
    assert "| trolige feildeteksjoner (ikke i minnelinjene) | 1 |" in prompt
    assert "| episoder med bare kjøretøy (ikke i minnelinjene) | 1 |" in prompt
    assert "| episoder i minnet | 1 |" in prompt


def test_urgent_results_are_included(env):
    ep_id = _episode(D1 + 3600, _model("Jeg så en person."))
    ep = fstore.episode(ep_id)
    c = {**ep.conclusion, "urgent": [{"status": "done", "summary": "Jeg så røyk ved døra.", "ts": D1 + 3601}]}
    assert fstore.conclude_episode(ep_id, c)
    _night(env)
    (prompt,) = env["prompts"]
    assert "(haster, episode" in prompt and "røyk ved døra" in prompt
    assert "| hasteresultater | 1 |" in prompt


def test_the_numbers_and_probability_words_come_from_code(env):
    _episode(D1 + 3600, _model("Jeg så Person A ved døra."), ref="Person A", n=2)
    _episode(D1 + 7200, _model("Jeg så en ukjent person."))
    _night(env)
    (prompt,) = env["prompts"]
    assert "| Person A (sannsynlig) — episoder | 1 |" in prompt  # unverified claim → «sannsynlig» (s08)
    assert "| ukjent person — episoder | 1 |" in prompt
    assert "tell aldri selv" in prompt and "nevn ikke rader som er 0" in prompt
    assert "denne delen" not in prompt  # one part: one column, no part rule


def test_a_big_day_is_split_in_time_order(env):
    for i in range(45):
        _episode(D1 + 600 * i, _model(f"Jeg så person nummer {i}."))
    _night(env)
    eps = _frigate_eps(env["path"])
    assert [e[4] for e in eps] == [23, 22]
    assert "del 1 av 2" in env["prompts"][0] and "del 2 av 2" in env["prompts"][1]
    assert all("bruk kolonnen «denne delen»" in p for p in env["prompts"])  # dry run 2026-10-30: it used «hele dagen»
    assert "| episoder i minnet | 45 | 23 |" in env["prompts"][0]
    assert "nummer 0." in env["prompts"][0] and "nummer 44." in env["prompts"][1]
    assert "nummer 22." in env["prompts"][0] and "nummer 23." in env["prompts"][1]


def test_household_timezone_decides_the_day(env):
    # 23:30 UTC on 10-27 = 00:30 in Oslo on 10-28 → belongs to 10-28, not 10-27
    _episode(D1 + 23.5 * 3600, _model("Jeg så noen rett over midnatt."))
    _night(env)
    (ep,) = _frigate_eps(env["path"])
    assert ep[6].startswith("frigate, 2026-10-28")


def test_a_failed_reply_moves_nothing_and_is_retried_next_night(env):
    _episode(D1 + 3600, _model("Jeg så en person."))
    env["state"]["replies"] = [""]
    _night(env)
    assert _frigate_eps(env["path"]) == []
    assert fstore.ltm_marker() == {"settled_through": None, "done": {}}
    _night(env)
    assert len(_frigate_eps(env["path"])) == 1


def test_a_late_conclusion_becomes_an_addition_never_skipped(env):
    _episode(D1 + 3600, _model("Jeg så en person om morgenen."))
    late = _episode(D1 + 7200, None)  # closed, not concluded yet (conclude retries it)
    _night(env)
    assert [e[4] for e in _frigate_eps(env["path"])] == [1]
    assert fstore.ltm_marker()["settled_through"] is None  # the day still holds a pending episode
    assert fstore.conclude_episode(late, _model("Jeg så en person om kvelden."))
    _night(env)
    eps = _frigate_eps(env["path"])
    assert [e[4] for e in eps] == [1, 1]
    assert "tillegg" in env["prompts"][-1] and "om kvelden" in env["prompts"][-1]
    assert "om morgenen" not in env["prompts"][-1].split("--- Minnelinjer ---")[1]
    marker = fstore.ltm_marker()
    assert marker["settled_through"] == "2026-10-29" and marker["done"] == {}
    _night(env)
    assert len(_frigate_eps(env["path"])) == 2  # nothing compressed twice


def test_an_episode_that_never_concludes_lets_the_day_settle(env):
    _episode(D1 + 3600, None)
    env["state"]["now"] = D1 + frigate_ltm.SETTLE_S + 3600
    _night(env)
    assert _frigate_eps(env["path"]) == []
    assert fstore.ltm_marker()["settled_through"] is not None


def test_an_opted_out_name_never_reaches_the_prompt_or_the_episode(env):
    _episode(D1 + 3600, _model("Jeg så Person A gå inn."), ref="Person A")
    env["state"]["hidden"] = {"Person A"}
    _night(env)
    (prompt,) = env["prompts"]
    assert "Person A" not in prompt
    ((_, _, _, _, _, narrative, topics, _),) = _frigate_eps(env["path"])
    assert "Person A" not in narrative and "Person A" not in topics  # the fake model echoes the prompt


def test_a_failed_consent_lookup_compresses_nothing(env, monkeypatch):
    _episode(D1 + 3600, _model("Jeg så en person."))
    monkeypatch.setattr(stm, "_hidden_names", lambda: None)
    _night(env)
    assert _frigate_eps(env["path"]) == [] and env["prompts"] == []
    assert fstore.ltm_marker()["done"] == {}


def test_the_bound_stops_the_frigate_step_and_the_rest_waits(env, monkeypatch):
    monkeypatch.setattr(nj, "MAX_BATCHES_PER_NIGHT", 1)
    _episode(D1 + 3600, _model("Jeg så en person dag én."))
    _episode(D2 + 3600, _model("Jeg så en person dag to."))
    _night(env)
    assert len(_frigate_eps(env["path"])) == 1
    _night(env)
    assert len(_frigate_eps(env["path"])) == 2


def test_the_bound_stops_the_per_user_loop_and_the_rest_waits(env, monkeypatch):
    monkeypatch.setattr(nj, "MAX_BATCHES_PER_NIGHT", 1)
    monkeypatch.setattr(nj, "_enc", lambda uid, text, allow_system_key=False: text)
    monkeypatch.setattr(nj, "_dec", lambda uid, text, allow_system_key=False: text)
    conn = sqlite3.connect(str(env["path"]))
    for _ in range(25):
        conn.execute("INSERT INTO interactions (ts, user_id, prompt, response, outcome) VALUES "
                     "('2026-10-29T10:00:00+00:00', 'alice', 'p', 'r', 'success')")
    conn.commit()
    conn.close()
    _night(env)
    conn = sqlite3.connect(str(env["path"]))
    counts = conn.execute("SELECT interaction_count FROM episodes WHERE user_id = 'alice'").fetchall()
    conn.close()
    assert counts == [(20,)]
    _night(env)
    conn = sqlite3.connect(str(env["path"]))
    counts = conn.execute("SELECT interaction_count FROM episodes WHERE user_id = 'alice' ORDER BY id").fetchall()
    conn.close()
    assert counts == [(20,), (5,)]


def test_the_log_has_no_memory_text(env, caplog):
    _episode(D1 + 3600, _model(f"Jeg så {SECRET} ved døra."))
    with caplog.at_level(logging.DEBUG):
        _night(env)
    assert len(_frigate_eps(env["path"])) == 1
    assert SECRET not in caplog.text and "ved døra" not in caplog.text
