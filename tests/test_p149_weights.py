"""P149 phase 1: the weight store and model, in shadow. Every test uses its own tmp_path ledger; nothing reaches
/kaare/state, /kaare/runtime, a live database, HTTP or MQTT."""

import re
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from kaare_core import weights
from kaare_core.users import frigate_consent
from kaare_core.weights import ltm_import, model
from kaare_core.weights import store as wstore

S = ("sensor", "insightface|cam_a|day")
B = ("belief", "user drinks a soft drink in the daytime", "user_a")


@pytest.fixture
def st(tmp_path, monkeypatch):
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(frigate_consent, "untracked_faces", lambda: frozenset({"opted out"}))
    wstore.reset_for_tests()
    s = wstore.get_store()
    yield s
    s.flush()
    wstore.reset_for_tests()


def _days_ago(n: float) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).isoformat(timespec="seconds")


# ── trust scale (D1) ──

def test_empty_subject_is_unknown_and_uncertain(st):
    w = st.get(S)
    assert w.belief == model.BELIEF_PRIOR and w.validated is None and w.word == "uncertain"
    assert w.source == "none" and w.learned_n == 0 and w.learned_goal == 5


def test_machines_never_pass_half(st):
    for i in range(500):
        st.record(S, source_class="world", source="detector", context=f"ev{i}", polarity=+1)
    w = st.get(S)
    assert w.belief == pytest.approx(model.MACHINE_CAP) and w.validated is None
    assert w.word == "uncertain"  # machines alone never reach «likely»


def test_self_inference_capped_lower(st):
    for i in range(200):
        st.record(B, source_class="self", source="meeting", context=f"night{i}", polarity=+1, pointer=f"m:{i}")
    assert st.get(B).belief == pytest.approx(model.SELF_CAP)


def test_humans_go_above_half_never_above_ceiling(st):
    for i in range(200):
        st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
    w = st.get(S)
    assert 0.5 < w.validated <= model.HUMAN_CEILING and w.word == "very_likely" and w.source == "human"


def test_five_verbatim_yes_is_likely_not_very_likely(st):
    for i in range(5):
        st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
    w = st.get(S)
    assert w.word == "likely" and w.learned_n == 5


def test_one_of_one_differs_from_fifty_of_fifty(st):
    st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context="q0")
    one = st.get(S).validated
    s2 = ("sensor", "other|cam|day")
    for i in range(50):
        st.answer(s2, by_user="user_a", verdict="yes", verbatim=True, context=f"y{i}")
        st.answer(s2, by_user="user_a", verdict="no", verbatim=True, context=f"n{i}")
    assert one > 0.5 and st.get(s2).validated == pytest.approx(0.5)


def test_vague_answers_count_less(st):
    s2 = ("sensor", "vague|cam|day")
    for i in range(5):
        st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
        st.answer(s2, by_user="user_a", verdict="yes", verbatim=False, context=f"q{i}")
    assert st.get(s2).validated < st.get(S).validated


def test_skip_leaves_it_unknown(st):
    st.answer(S, by_user="user_a", verdict="skip", verbatim=True, context="q0")
    assert st.get(S).validated is None


# ── many angles count, repeats once (D2, P13) ──

def test_repeats_in_one_conversation_count_once(st):
    for _ in range(20):
        st.record(B, source_class="user", source="chat", context="conv-1", polarity=+1)
    w = st.get(B)
    assert w.angles == 1 and w.belief == pytest.approx(model.BELIEF_PRIOR + model.BELIEF_STEP["user"])


def test_ten_angles_move_belief_about_a_tenth(st):
    for i in range(10):
        st.record(B, source_class="world", source="obs", context=f"day{i}", polarity=+1)
    assert st.get(B).belief - model.BELIEF_PRIOR == pytest.approx(0.1)


def test_repeated_answer_in_one_question_counts_once(st):
    for _ in range(5):
        st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context="q-same")
    assert st.get(S).learned_n == 1


# ── echo rule (W8) ──

def test_self_without_pointer_moves_nothing_but_is_kept(st):
    for i in range(30):
        st.record(B, source_class="self", source="meeting", context=f"night{i}", polarity=+1, worry=0.3)
    w = st.get(B)
    assert w.belief == model.BELIEF_PRIOR and w.worry == 0.0
    st.flush()
    n = sqlite3.connect(st.path).execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
    assert n == 30


def test_record_never_moves_validated(st):
    for i in range(50):
        st.record(S, source_class="user", source="chat", context=f"c{i}", polarity=+1)
    assert st.get(S).validated is None


# ── contradiction and worry (D4 b, W9) ──

def test_contradiction_raises_worry_at_once(st):
    for i in range(3):
        st.answer(B, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
    assert st.get(B).worry == 0.0
    st.record(B, source_class="user", source="chat", context="conv-9", polarity=-1)
    w = st.get(B)
    assert w.worry == pytest.approx(model.WORRY_STEP_BIG, abs=0.01) and "contradiction:user" in w.reasons


def test_agreeing_evidence_raises_no_worry(st):
    for i in range(3):
        st.answer(B, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
    st.record(B, source_class="user", source="chat", context="conv-9", polarity=+1)
    assert st.get(B).worry == 0.0


def test_worry_decays_and_answer_resets(st):
    st.record(B, source_class="world", source="det", context="x", worry=0.3, ts=_days_ago(6))
    assert st.get(B).worry == pytest.approx(0.3 * 0.25, abs=0.01)  # two half-lives
    st.record(B, source_class="world", source="det", context="y", worry=0.3)
    st.answer(B, by_user="user_a", verdict="yes", verbatim=True, context="q")
    assert st.get(B).worry == 0.0


# ── time on facts (D4 a) ──

def test_rows_outside_their_period_do_not_count_now_but_do_then(st):
    then = datetime.now(UTC) - timedelta(days=100)
    for i in range(5):
        st.answer(B, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}", ts=_days_ago(200),
                  valid_from=_days_ago(300), valid_to=_days_ago(50))
    assert st.get(B).validated is None  # the period ended — a change is a new period, not a refutation
    assert st.get(B, at=then).validated > 0.5


# ── nothing deleted, park is a row (W11) ──

def test_park_unpark_are_appended_rows(st):
    st.park(B, "round 3")
    assert st.is_parked(B) and st.get(B).parked
    st.unpark(B, "answered")
    assert not st.is_parked(B)
    st.flush()
    rows = sqlite3.connect(st.path).execute("SELECT action FROM parked ORDER BY id").fetchall()
    assert rows == [("park",), ("unpark",)]


def test_store_has_no_delete_or_update_path():
    with open(wstore.__file__) as f:
        src = f.read()
    assert not re.search(r"DELETE\s+FROM|UPDATE\s+\w+\s+SET|DROP\s+TABLE", src, re.IGNORECASE)


def test_ram_survives_reload(st, tmp_path):
    st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context="q0")
    st.record(S, source_class="world", source="d", context="e1", polarity=+1)
    st.flush()
    again = wstore.WeightStore(tmp_path / "weights.db")
    assert again.get(S).as_dict() | {"maturity": 0} == st.get(S).as_dict() | {"maturity": 0}


# ── consent (W22) ──

def test_opted_out_person_is_never_stored(st):
    assert st.record(B, source_class="world", context="e", polarity=+1, about="Opted Out") is False
    assert st.answer(B, by_user="user_a", verdict="yes", verbatim=True, about=["opted out"]) is False
    assert st.pending() == 0 and st.get(B).angles == 0


# ── maturity and outputs ──

def test_maturity_grows_with_days_and_confirmations(st):
    st.record(S, source_class="world", source="d", context="e0", polarity=+1, ts=_days_ago(7))
    m0 = st.get(S).maturity
    st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context="q0")
    assert 0 < m0 < st.get(S).maturity <= 1.0


def test_action_cautious_while_young(st):
    for i in range(5):
        st.answer(B, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
    w = st.get(B)  # a household belief matures over ~a year; 5 answers keep it young
    assert w.maturity < 0.2 and w.action < w.trust


def test_sensor_matures_at_its_goal(st):
    for i in range(5):
        st.answer(S, by_user="user_a", verdict="yes", verbatim=True, context=f"q{i}")
    assert st.get(S).maturity == pytest.approx(1.0)


def test_bad_input_is_refused_not_raised(st):
    assert st.record(("nonsense", "k"), source_class="world") is False
    assert st.record(S, source_class="llm") is False
    assert st.answer(S, by_user="", verdict="yes", verbatim=True) is False
    assert st.answer(S, by_user="user_a", verdict="maybe", verbatim=True) is False


def test_module_interface_uses_patched_path(st):
    assert weights.record(S, source_class="world", context="e", polarity=+1)
    assert weights.get(S).angles == 1 and wstore.get_store() is st


# ── LTM verifications as vague evidence (D12 note) ──

def _ltm_copy(tmp_path):
    p = tmp_path / "interactions.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE verifications (id INTEGER PRIMARY KEY, ts_asked TEXT, ts_answered TEXT, user_id TEXT, "
              "from_id INT, to_id INT, unverified_count INT, verdict TEXT, user_text TEXT)")
    c.executemany("INSERT INTO verifications (ts_asked, ts_answered, user_id, from_id, to_id, verdict) "
                  "VALUES (?, ?, ?, 1, 2, ?)",
                  [(_days_ago(3), _days_ago(3), "user_a", "confirmed"), (_days_ago(2), _days_ago(2), "user_a", "denied"),
                   (_days_ago(1), None, "user_a", "pending"), (_days_ago(1), _days_ago(1), "user_b", "confirmed")])
    c.commit()
    c.close()
    return p


def test_ltm_verifications_imported_once_as_vague(st, tmp_path):
    p = _ltm_copy(tmp_path)
    assert ltm_import.import_ltm_verifications(p, st) == 3
    assert ltm_import.import_ltm_verifications(p, st) == 0  # idempotent
    a = st.get(("chat_answers", "user_a", "user_a"))
    assert a.yes == model.VAGUE_WEIGHT and a.no == model.VAGUE_WEIGHT and a.learned_n == 2
    b = st.get(("chat_answers", "user_b", "user_b"))
    assert 0.5 < b.validated < 0.6  # one vague yes = a small step


def test_ltm_import_never_writes_the_source(st, tmp_path):
    p = _ltm_copy(tmp_path)
    before = p.read_bytes()
    ltm_import.import_ltm_verifications(p, st)
    assert p.read_bytes() == before


def test_ltm_import_missing_file_is_zero(st, tmp_path):
    assert ltm_import.import_ltm_verifications(tmp_path / "nope.db", st) == 0

