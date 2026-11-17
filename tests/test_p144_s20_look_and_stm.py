"""P144-s20 items 5 and 6 — the closed look's `nothing` beside a being, and one «what counts» rule for the Frigate STM.

- Item 5 (s18 rec. 4, ep 80/91): «choose all that apply» over 9 frames — `nothing=true` together with a person or an
  animal means «some frames are empty» (ep 91: the person had gone in on frame 9), not an unreadable answer. Code
  stores `nothing=False` + `some_empty=True`; the gate keeps reading person/animal.
- Item 6 (P144 Bug from s17): the live STM writer (`conclude._remember`) uses `stm.counts_as_memory()` — the same rule as
  `stm.rebuild()` and the nightly LTM step — so false-detection and vehicle-only code lines no longer reach the STM.
  Urgent results still go in at once (they are memory lines in `stm.memory_lines` too).
Pure functions and a recording stub for `stm.remember` — no DB, no live state.
"""

import json

import pytest

from kaare_core.frigate_user import conclude, stm, store

# ── item 5 ──────────────────────────────────────────────────────────────────────

EP91 = '{"person": true, "animal": "none", "vehicle": false, "nothing": true}'  # one of 9 frames empty
EP80 = '```json\n{"person": "true", "animal": null, "vehicle": "false", "nothing": "true"}\n```'


@pytest.mark.parametrize("text", [EP91, EP80])
def test_nothing_beside_a_person_means_some_frames_empty(text):
    parsed, why = conclude.parse_look(text)
    assert why == ""
    assert parsed["person"] is True
    assert parsed["nothing"] is False
    assert parsed["some_empty"] is True


def test_nothing_beside_an_animal_means_some_frames_empty():
    parsed, _ = conclude.parse_look('{"person": false, "animal": "rådyr", "vehicle": false, "nothing": true}')
    assert parsed["animal"] == "rådyr" and parsed["nothing"] is False and parsed["some_empty"] is True


def test_nothing_alone_stays_nothing():
    parsed, _ = conclude.parse_look('{"person": false, "animal": "none", "vehicle": false, "nothing": true}')
    assert parsed["nothing"] is True
    assert "some_empty" not in parsed


def test_gate_keeps_reading_the_person_when_some_frames_are_empty():
    parsed, _ = conclude.parse_look(EP91)
    ep = store.Episode(episode_id=91, start_ts=0.0, end_ts=100.0, cameras=["door"], labels=["person"], person_refs=[],
                       event_count=1, status="closed", conclusion=None, concluded_ts=None)
    ev = store.FrigateUserEvent(event_id="e1", camera="door", label="person", start_ts=0.0, end_ts=10.0)
    assert conclude.label_gate(ep, [ev], {"look": {"status": "done", **parsed}}, away=False) is None


# ── item 6 ──────────────────────────────────────────────────────────────────────

MODEL = {"status": "done", "by": "model", "summary": "Jeg så en person ved døra."}
FALSE = {"status": "done", "by": "code", "summary": "Trolig feildeteksjon: Frigate sa «hjort» …",
         "false_detection": {"frigate_labels": ["deer"]}}
VEHICLES = {"status": "done", "by": "code", "summary": "Jeg så bare kjøretøy kl. 10:00–10:01 …"}
URGENT = {"event_id": "s1", "reason": "label:smoke", "status": "done", "summary": "Jeg så røyk ved døra.",
          "claim_verdict": {"verdict": "unsure", "why": "x"}}


@pytest.fixture
def written(monkeypatch):
    calls = []
    monkeypatch.setattr(stm, "remember", lambda *a, **kw: calls.append((a, kw)) or True)
    return calls


def test_model_memory_is_written(written):
    conclude._remember(1, 0.0, MODEL, "nb")
    assert len(written) == 1 and written[0][0][2] == MODEL["summary"]


@pytest.mark.parametrize("update", [FALSE, VEHICLES], ids=["false_detection", "vehicles_only"])
def test_code_lines_do_not_reach_the_stm(written, update):
    conclude._remember(1, 0.0, update, "nb")
    assert written == []
    assert not stm.counts_as_memory(update)  # the same rule rebuild() and the LTM step use


def test_urgent_result_still_written_at_once(written):
    conclude._remember(1, 0.0, URGENT, "nb", urgent=True)
    assert len(written) == 1 and written[0][1].get("urgent") is True


def test_failed_or_empty_conclusions_write_nothing(written):
    conclude._remember(1, 0.0, {"status": "failed", "reason": "json"}, "nb")
    conclude._remember(1, 0.0, {"status": "done", "by": "model", "summary": " "}, "nb")
    assert written == []


def test_json_shapes_are_plain(written):  # guard: nothing in the update is mutated by the check
    before = json.dumps(FALSE, sort_keys=True)
    conclude._remember(1, 0.0, FALSE, "nb")
    assert json.dumps(FALSE, sort_keys=True) == before
