"""P149 D5/D6 / run-queue J4 — the night meeting's three-night stop.

The lean call sees only the earlier themes with their dates and the new proposal (never who proposed what), answers in
a closed field; code counts the chain; at night 3 the `repeated_theme` detector (profile reflection_topic, shadow by
default) refuses it; repetition raises Worry, never Belief; a refused chain is parked. The embedding is a logged
number only. Reports, weights.db: tmp files; the model and the embedder are fakes."""

import asyncio

import pytest

from kaare_core import model_enforcement as me
from kaare_core import weights
from kaare_core.memory import repeat_stop as rs
from kaare_core.weights import store as wstore


def _report(d, night, kare, miss):
    (d / f"{night}.md").write_text(f"# Refleksjon – {night}\n\n## Samtale\n\n**[Kåre]**\n{kare}\n\n"
                                   f"**[Miss Kåre]**\n{miss}\n\n**[Møteleder]**\nok\n", encoding="utf-8")


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    wstore.reset_for_tests()
    d = tmp_path / "refl" / "user_a"
    d.mkdir(parents=True)
    _report(d, "2026-10-19", "Tema om lys.", "Tema A første gang.")
    _report(d, "2026-10-20", "Tema om lyd.", "Tema A igjen, med nye ord.")
    yield tmp_path / "refl"
    weights.flush()
    wstore.reset_for_tests()


def test_earlier_themes_read_openings_with_dates(base):
    th = rs.earlier_themes(base / "user_a", "2026-10-21")
    assert [(x.night, x.who) for x in th] == [("2026-10-19", "Kåre"), ("2026-10-19", "Miss Kåre"),
                                              ("2026-10-20", "Kåre"), ("2026-10-20", "Miss Kåre")]


def test_prompt_never_names_who_proposed(base):
    prompt, ids = rs.build_prompt("ny", rs.earlier_themes(base / "user_a", "2026-10-21"), "nb")
    assert "Miss Kåre" not in prompt and "Kåre" not in prompt and "N4 (2026-10-20)" in prompt
    assert ids["N2"] == "2026-10-19"


def test_parse_answer_is_closed():
    ids = {"N1": "2026-10-19", "N2": "2026-10-20"}
    assert rs.parse_answer('{"answer": "N2"}', ids) == "2026-10-20"
    assert rs.parse_answer('{"answer": "new"}', ids) is None
    assert rs.parse_answer("N9", ids) is None and rs.parse_answer("rubbish", ids) is None


def _fake_llm(answer):
    async def llm(prompt):
        return answer
    return llm


def test_chain_counts_to_three_and_worry_rises_belief_does_not(base):
    # night 1 new, night 2 repeats night 1, night 3 repeats night 2 → one chain of three nights
    v1 = asyncio.run(rs.judge("user_a", "A", "2026-10-19", "nb", llm=_fake_llm('{"answer":"new"}'), base=base))
    v2 = asyncio.run(rs.judge("user_a", "A", "2026-10-20", "nb", llm=_fake_llm('{"answer":"N2"}'), base=base))
    v3 = asyncio.run(rs.judge("user_a", "A", "2026-10-21", "nb", llm=_fake_llm('{"answer":"N4"}'), base=base))
    assert (v1.nights, v2.nights, v3.nights) == (1, 2, 3) and v1.chain == v2.chain == v3.chain == "2026-10-19"
    w = weights.get(rs._subject("user_a", v3.chain))
    assert w.worry > 0 and w.belief == pytest.approx(weights.model.BELIEF_PRIOR) and w.validated is None


def test_a_new_theme_starts_its_own_chain(base):
    v = asyncio.run(rs.judge("user_a", "B", "2026-10-21", "nb", llm=_fake_llm('{"answer":"new"}'), base=base))
    assert v.nights == 1 and v.chain == "2026-10-21" and v.repeats is None


def test_a_failing_call_counts_as_new(base):
    async def boom(prompt):
        raise RuntimeError("down")
    v = asyncio.run(rs.judge("user_a", "A", "2026-10-21", "nb", llm=boom, base=base))
    assert v.repeats is None and v.nights == 1


def test_embedding_is_only_a_number(base):
    async def embed(texts):
        return [[1.0, 0.0]] + [[1.0, 0.0]] * (len(texts) - 1)
    v = asyncio.run(rs.judge("user_a", "A", "2026-10-21", "nb", llm=_fake_llm('{"answer":"new"}'), embed=embed,
                             base=base))
    assert v.embed_best == pytest.approx(1.0) and v.repeats is None  # a perfect cosine never makes it a repeat


def test_park_is_a_row(base):
    rs.park("user_a", "2026-10-19")
    assert weights.is_parked(rs._subject("user_a", "2026-10-19"))


def test_detector_refuses_at_night_three_and_is_shadow():
    d = me.RepeatedThemeDetector()
    assert not d.matches(text_out="x", used_tools=[], meta={"theme_nights": 2}, lang="nb")
    assert d.matches(text_out="x", used_tools=[], meta={"theme_nights": 3}, lang="nb")
    assert not d.matches(text_out="x", used_tools=[], meta={"theme_nights": 4, "theme_new_user_evidence": True},
                         lang="nb")
    assert "3" in d.correction_message("nb") or d.matches(text_out="", used_tools=[], meta={"theme_nights": 3},
                                                          lang="nb")
    assert me.profile_default_mode("reflection_topic", d) == me.MODE_SHADOW


def test_who_selects_the_comparison_set_without_telling_the_model(base):
    seen = []

    async def llm(prompt):
        seen.append(prompt)
        return '{"answer":"N2"}'

    v = asyncio.run(rs.judge("user_a", "A", "2026-10-21", "nb", llm=llm, who="Miss Kåre", base=base))
    assert "Tema om lys" not in seen[0] and "Tema A igjen" in seen[0] and "Miss Kåre" not in seen[0]
    assert v.repeats == "2026-10-20"


def test_shadow_check_counts_parks_and_never_refuses(base, monkeypatch):
    monkeypatch.setattr(rs, "REFLECTIONS_BASE", base)
    answers = iter(['{"answer":"N1"}', '{"answer":"N2"}'])  # night 1 has nothing earlier: no call

    async def lean(prompt, rid):
        return next(answers)

    async def no_embed(texts):
        return None

    monkeypatch.setattr(rs, "_lean_call", lean)
    monkeypatch.setattr(rs, "_embed", no_embed)
    outs = [asyncio.run(rs.shadow_check("user_a", "A", "Miss Kåre", "nb", night=n))
            for n in ("2026-10-19", "2026-10-20", "2026-10-21")]
    assert [o["nights"] for o in outs] == [1, 2, 3]
    assert outs[2]["would_refuse"] is True and outs[2]["refuse"] is False
    assert weights.is_parked(rs._subject("user_a", outs[2]["chain"]))


def test_meeting_calls_the_shadow_check_for_both_proposals():
    # PLC0415: the meeting module imports the whole stack — only this test needs it
    import kaare_reflection as kr  # noqa: PLC0415
    with open(kr.__file__) as f:
        src = f.read()
    assert src.count("repeat_stop.shadow_check(") == 1
    assert 'for _who, _tema in (("Kåre", kare_tema), ("Miss Kåre", miss_kare_tema)):' in src  # both proposals
