"""J21b reader 1, repointed in P144 phase 6 (List B item 9): the camera tool's `analyze` shows the image model's single-image
reads that already exist — L3 per event and module 4's frames (`store.recent_model_reads`, the same rows as «Siste
deteksjoner») — newest first, household time, the camera's display name, the step, the model's own words and the
event/episode id. Never a new model call; the old frigate_analysis.log and module 1's `check.still` are gone.
Tmp DB; placeholders."""
import asyncio
from zoneinfo import ZoneInfo

import pytest

from kaare_core.frigate_user import store as fstore
from kaare_core.tools import executor_camera

T0 = 1_800_000_000.0  # 2027-01-15 08:00 UTC = 09:00 in Oslo
OSLO = ZoneInfo("Europe/Oslo")

L3 = {"kind": "l3", "ts": T0 + 60, "camera": "cam_door", "label": "person", "event_id": "e1", "episode_id": 7,
      "words": {"description": "En person ved døra.", "analysis": "Står stille."}}
FRAME = {"kind": "frame", "ts": T0 + 120, "camera": "cam_door", "label": "", "episode_id": 7, "frame": 3, "index": 2,
         "words": {"persons": {"P1": {"place": "trappa", "posture": "står", "carried": ""}}}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(executor_camera, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(executor_camera, "_camera_display", lambda cam: {"cam_door": "Inngang"}.get(cam, cam))
    fstore.reset_for_tests()
    yield tmp_path
    fstore.reset_for_tests()


def _analyze(count=10):
    return asyncio.run(executor_camera._kamera({"action": "analyze", "count": count, "_user_id": "boss"}, "nb"))


def test_reads_are_shown_newest_first_with_the_models_own_words(env, monkeypatch):
    monkeypatch.setattr(fstore, "recent_model_reads", lambda limit=40, before=None: ([FRAME, L3], False))
    blocks = _analyze().split("\n\n---\n\n")
    assert len(blocks) == 2
    assert blocks[0].startswith("[2027-01-15 09:02:00] Inngang — bilde 3 i episode #7")
    assert "P1 place: trappa, posture: står" in blocks[0] and "carried" not in blocks[0]  # empty fields left out
    assert blocks[0].endswith("episode_id: 7")
    assert blocks[1].startswith("[2027-01-15 09:01:00] Inngang — L3 (enkeltbilde, person)")
    assert "Bildemodellens ord: En person ved døra. · Står stille." in blocks[1] and blocks[1].endswith("event_id: e1")


def test_count_is_passed_on_and_capped(env, monkeypatch):
    seen = []
    monkeypatch.setattr(fstore, "recent_model_reads", lambda limit=40, before=None: (seen.append(limit), ([], False))[1])
    _analyze(count=4)
    _analyze(count=500)
    assert seen == [4, 50]


def test_the_step_text_follows_the_language(env, monkeypatch):
    monkeypatch.setattr(fstore, "recent_model_reads", lambda limit=40, before=None: ([L3], False))
    out = asyncio.run(executor_camera._kamera({"action": "analyze", "_user_id": "boss"}, "en"))
    assert "L3 (single image, person)" in out and "The image model's words:" in out


def test_a_real_l3_row_from_the_db(env):
    fstore.record_event(fstore.FrigateUserEvent(event_id="e9", camera="cam_door", label="person", start_ts=T0,
                                                end_ts=T0 + 12, description="Ei katt på trappa."))
    out = _analyze()
    assert "Ei katt på trappa." in out and "event_id: e9" in out


def test_nothing_says_so(env):
    out = _analyze()
    assert out and "---" not in out  # the existing «no analysis» text, no crash
