"""P144-s29: the pipe reads Double Take's REST rows (patched here, GET only in production) before it decides an event and
before it re-decides on a late vote, so the per-detector votes and the face-box check reach the stored check. Reuses the
s19 pipe fakes: no broker, no Frigate, no LLM, no live users.db / frigate.db / configs."""

import asyncio
import json

import pytest

from kaare_core.frigate_user import face_votes, pipe
from tests.test_p144_s19_pipe_votes import (  # noqa: F401 — s19 fixture + helpers
    _process,
    _stored,
    env,
)

CFG = ("enabled: true\nface_votes: {enabled: true, rest_url: 'http://dt.invalid:3000'}\n"
       "face_sensors: {detectors: {det_if: {label: InsightFace, mode: admin, trust: likely}, "
       "det_cp: {label: CPAI, mode: admin, trust: unlikely}}}\n")  # s29b: words (were weights 1.0 / 0.3)


def _mqtt(event_id, detector, name, filename, conf=75.0, box=(100, 100, 80, 100)):
    top, left, w, h = box
    m = {"name": name, "confidence": conf, "match": True, "type": "snapshot", "detector": detector,
         "filename": filename, "box": {"top": top, "left": left, "width": w, "height": h}}
    return face_votes.on_message(f"double-take/matches/{name}",
                                 json.dumps({"id": event_id, "camera": "inngang", "match": m}).encode())


def _row(filename, response, size=(1000, 1000)):
    return {"id": 1, "file": {"filename": filename, "width": size[0], "height": size[1]},
            "response": json.dumps([{"detector": d, "attempt": 1, "results": rs} for d, rs in response.items()])}


def _res(name, conf=75.0, box=(100, 100, 80, 100)):
    top, left, w, h = box
    return {"name": name, "confidence": conf, "match": True, "box": {"top": top, "left": left, "width": w, "height": h}}


@pytest.fixture
def rest(env, tmp_path, monkeypatch):  # noqa: F811
    (tmp_path / "frigate_user.yaml").write_text(CFG)
    calls, pages = [], []

    async def fake(base, page):
        calls.append(page)
        return pages[page - 1] if page - 1 < len(pages) else []
    monkeypatch.setattr(face_votes, "_fetch_page", fake)
    return {"calls": calls, "pages": pages}


def test_the_pipe_reads_rest_before_it_decides(rest):
    # MQTT shows only CPAI («Person B»); REST shows InsightFace said «Person A» on the same image → A wins by weight.
    _mqtt("e1", "det_cp", "person b", "a.jpg")
    rest["pages"].append([_row("a.jpg", {"det_cp": [_res("person b")], "det_if": [_res("person a", 61.0)]})])
    _process("Person A")
    e = _stored()
    assert rest["calls"] == [1]
    assert e.check["sensors"]["relation"] == "agree" and e.claim_status == "confirmed"
    dets = {d["detector"]: d for d in e.check["sensors"]["detectors"]}
    assert dets["det_if"]["ref"] == "persona" and dets["det_cp"]["ref"] == "personb"


def test_a_torso_box_never_reaches_the_stored_decision(rest):
    torso = (460, 300, 400, 500)
    _mqtt("e1", "det_cp", "person b", "t.jpg", 74.0, torso)
    rest["pages"].append([_row("t.jpg", {"det_cp": [_res("person b", 74.0, torso)], "det_if": []})])
    _process(None)
    e = _stored()
    assert e.person_ref is None
    assert e.check["l2"]["detectors"][0]["discarded"] == 1 or e.check["l2"]["detectors"][1]["discarded"] == 1


def test_a_late_vote_reads_rest_too(rest):
    _process("Person A")
    rest["pages"].append([_row("late.jpg", {"det_cp": [_res("person a")], "det_if": [_res("person a", 70.0)]})])

    async def main():
        pipe.start_face_votes()
        _mqtt("e1", "det_cp", "person a", "late.jpg")
        for _ in range(100):
            await asyncio.sleep(0.02)
            if _stored().claim_status == "confirmed":
                break
    asyncio.run(main())
    e = _stored()
    assert e.claim_status == "confirmed" and 1 in rest["calls"]
    assert {d["detector"] for d in e.check["sensors"]["detectors"]} == {"det_cp", "det_if"}
