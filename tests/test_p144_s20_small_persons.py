"""P144-s20 item 1 — a size rule for small persons (Bug «L3 invents attributes on small persons», s18 ep 86).

Measured (s20 log, m1): the 9B never says «unknown» for glasses/headwear/age when the person is small — at 25 px high
it still filled glasses 16/24, headwear 24/24, age 24/24 (glasses «yes» 18/24 even at full size = a prior, not
sight); only its beard answer dropped to unknown (24/24 at 25 px). So code decides from Frigate's own box: below
FACE_ATTRS_MIN_PX the face fields (beard, glasses) are unknown, below HEAD_ATTRS_MIN_PX also headwear and age band and
the appearance sentence; the 9B is not told why (priming rule). The 27B gets a code fact «person far away (N px)».
Pure functions + the pipe with fakes — no live state.
"""

import asyncio
import json
from io import BytesIO

import pytest
from PIL import Image

from kaare_core.frigate_user import checks, conclude, pipe, settings, store

FULL = {"persons": 1, "beard": "no", "glasses": "yes", "age_band": "adult", "headwear": "yes"}


def _data(h_rel: float) -> dict:
    return {"box": [0.4, 0.3, 0.05, h_rel]}


def test_person_px_is_box_height_times_frame_height():
    assert checks.person_px(_data(0.0667), (640, 360)) == 24  # ep 86: a 10×24 px person on the footpath
    assert checks.person_px(_data(0.5), (853, 480)) == 240  # a recording frame (480 p) scales with it
    assert checks.person_px({}, (640, 360)) is None
    assert checks.person_px({"box": [0.1, 0.2]}, (640, 360)) is None
    assert checks.person_px({"box": "x"}, (640, 360)) is None


def test_tiny_person_gets_no_attributes():
    attrs, limited = checks.limit_by_size(FULL, 24)
    assert attrs == {"persons": 1, "beard": "unknown", "glasses": "unknown", "age_band": "unknown",
                     "headwear": "unknown"}
    assert sorted(limited) == ["age_band", "beard", "glasses", "headwear"]


def test_mid_size_person_keeps_head_but_not_face():
    attrs, limited = checks.limit_by_size(FULL, checks.HEAD_ATTRS_MIN_PX + 10)
    assert attrs["glasses"] == "unknown" and attrs["beard"] == "unknown"
    assert attrs["headwear"] == "yes" and attrs["age_band"] == "adult"
    assert sorted(limited) == ["beard", "glasses"]


def test_big_or_unmeasured_person_is_untouched():
    assert checks.limit_by_size(FULL, checks.FACE_ATTRS_MIN_PX) == (FULL, [])
    assert checks.limit_by_size(FULL, None) == (FULL, [])  # no box (REST 404): the 9B's own answer stands


def test_unknown_never_contradicts_after_the_limit():
    attrs, _ = checks.limit_by_size(FULL, 24)
    vote = checks.l3_compare(attrs, {"beard": "full", "glasses": "no", "age_band": "senior", "headwear": "no"})
    assert not (vote.verdict == "no" and vote.strong)


# ── the pipe: the limit is applied before storing and before the claim check ──


@pytest.fixture
def piped(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    store.reset_for_tests()
    monkeypatch.setattr(settings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "get_settings", lambda: {**settings.DEFAULTS, "enabled": True})
    buf = BytesIO()
    Image.new("RGB", (640, 360), (90, 120, 60)).save(buf, "JPEG")
    jpeg = buf.getvalue()
    state = {"box_h": 0.0667, "prompts": []}

    async def get_event(eid):
        return {"start_time": 1_793_300_000.0, "end_time": 1_793_300_010.0,
                "data": {"box": [0.4, 0.3, 0.02, state["box_h"]], "top_score": 0.8}}

    async def snap(eid, height=None):  # P144-s25: the pipe asks for a capped height
        return jpeg

    async def llm(role, prompt, images=None, rid=""):
        state["prompts"].append(prompt)
        return {"text": json.dumps({**FULL, "description": "Mørk jakke, briller og lue.",
                                    "analysis": "En person går langs veien."})}

    monkeypatch.setattr(pipe.frigate_adapter, "get_event", get_event)
    monkeypatch.setattr(pipe.frigate_adapter, "fetch_event_snapshot", snap)
    monkeypatch.setattr(pipe.frigate_store, "SNAPSHOT_DIR", tmp_path / "snaps")
    monkeypatch.setattr(pipe, "ask_llm_role", llm)
    monkeypatch.setattr(pipe, "_camera_role", lambda cam: "driveway")
    monkeypatch.setattr(pipe, "_camera_episodes", lambda cam: True)
    monkeypatch.setattr(pipe, "_drive", lambda *a, **kw: ("normal", True, "selected"))
    monkeypatch.setattr(pipe, "_place_of", lambda cam: None)
    return state


def _run(eid="e1"):
    ev = {"event_id": eid, "camera": "vest", "label": "person", "top_score": 0.8, "duration": 10,
          "has_snapshot": True}
    asyncio.run(pipe.process(ev, None, None))
    return store.event_by_id(eid)


def test_pipe_stores_unknown_attributes_for_a_far_away_person(piped):
    kept = _run()
    assert kept.attributes["glasses"] == "unknown" and kept.attributes["headwear"] == "unknown"
    assert kept.attributes["persons"] == 1  # the count stays — the gate reads it
    assert kept.check["person_px"] == 24 and kept.check["frame_h"] == 360
    assert sorted(kept.check["size_limited"]) == ["age_band", "beard", "glasses", "headwear"]
    assert kept.description is None  # the appearance sentence on a 24 px person is not sight
    assert kept.analysis == "En person går langs veien."
    assert all("px" not in p and "liten" not in p for p in piped["prompts"])  # the 9B is never told why


def test_pipe_leaves_a_big_person_alone(piped):
    piped["box_h"] = 0.6
    kept = _run("e2")
    assert kept.attributes["glasses"] == "yes" and "size_limited" not in kept.check
    assert kept.check["person_px"] == 216
    assert kept.description == "Mørk jakke, briller og lue."


# ── the 27B gets the size as a code fact ──


def _ev(check: dict) -> store.FrigateUserEvent:
    return store.FrigateUserEvent(event_id="e1", camera="vest", label="person", start_ts=1_793_300_000.0,
                                  check=check, attributes={"persons": 1, "glasses": "unknown"})


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_event_line_tells_the_27b_the_person_is_far_away(lang):
    line = conclude._event_line(_ev({"person_px": 24, "frame_h": 360, "size_limited": ["glasses"]}), lang)
    assert "24 px" in line and "360" in line


def test_event_line_without_size_fact_for_a_big_person():
    assert "px" not in conclude._event_line(_ev({"person_px": 216, "frame_h": 360}), "nb").split("påstandssjekk")[0]
