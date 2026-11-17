"""P144-s25 item 0 — the L3 snapshot is capped in size (stopgap until s22's closest-to-original crops).

Since H02 (Frigate detect on the main stream) the event snapshot is 2304–2560 px wide; the 9B's L3 call went from
~450 to ~3 100–3 800 prompt tokens and 11.8 s (s25 log, item 0: `?height=720` → 1 112 tokens, 4.1 s, persons unchanged).
The pipe asks Frigate for `?height=L3_SNAPSHOT_HEIGHT`; a cached full-size copy (the old responder saves one) is scaled
down in code; a snapshot already within the cap is used byte for byte. Fakes only — no live state, no Frigate.
"""

import asyncio
import base64
import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from adapters import frigate_adapter
from kaare_core.frigate_user import pipe, settings, store

FULL = {"persons": 1, "beard": "no", "glasses": "no", "age_band": "adult", "headwear": "no"}


def _jpeg(w: int, h: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (90, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def piped(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    store.reset_for_tests()
    monkeypatch.setattr(settings, "enabled", lambda: True)
    monkeypatch.setattr(settings, "get_settings", lambda: {**settings.DEFAULTS, "enabled": True})
    state = {"heights": [], "images": [], "frigate_jpeg": _jpeg(1280, 720)}

    async def get_event(eid):
        return {"start_time": 1_793_300_000.0, "end_time": 1_793_300_010.0,
                "data": {"box": [0.4, 0.3, 0.1, 0.5], "top_score": 0.8}}

    async def snap(eid, height=None):
        state["heights"].append(height)
        return state["frigate_jpeg"]

    async def llm(role, prompt, images=None, rid=""):
        state["images"] += images or []
        return {"text": json.dumps({**FULL, "description": "Mørk jakke.", "analysis": "En person står."})}

    snaps = tmp_path / "snaps"
    snaps.mkdir()
    state["snaps"] = snaps
    monkeypatch.setattr(pipe.frigate_adapter, "get_event", get_event)
    monkeypatch.setattr(pipe.frigate_adapter, "fetch_event_snapshot", snap)
    monkeypatch.setattr(pipe.frigate_store, "SNAPSHOT_DIR", snaps)
    monkeypatch.setattr(pipe, "ask_llm_role", llm)
    monkeypatch.setattr(pipe, "_camera_role", lambda cam: "driveway")
    monkeypatch.setattr(pipe, "_camera_episodes", lambda cam: True)
    monkeypatch.setattr(pipe, "_drive", lambda *a, **kw: ("normal", True, "selected"))
    monkeypatch.setattr(pipe, "_place_of", lambda cam: None)
    return state


def _run(eid):
    ev = {"event_id": eid, "camera": "vest", "label": "person", "top_score": 0.8, "duration": 10, "has_snapshot": True}
    asyncio.run(pipe.process(ev, None, None))
    return store.event_by_id(eid)


def _sent_size(state) -> tuple[int, int]:
    return Image.open(BytesIO(base64.b64decode(state["images"][-1]))).size


def test_frigate_is_asked_for_a_capped_snapshot(piped):
    kept = _run("e1")
    assert piped["heights"] == [pipe.L3_SNAPSHOT_HEIGHT]
    assert _sent_size(piped) == (1280, 720)
    assert kept.check["frame_h"] == 720  # s20's size rule measures the image the 9B saw


def test_a_cached_full_size_snapshot_is_scaled_down_in_code(piped):
    (piped["snaps"] / "e2.jpg").write_bytes(_jpeg(2560, 1440))
    _run("e2")
    assert piped["heights"] == []  # the cache answered, no Frigate fetch
    assert _sent_size(piped) == (1280, 720)


def test_a_small_cached_snapshot_is_sent_unchanged(piped):
    small = _jpeg(640, 360)
    (piped["snaps"] / "e3.jpg").write_bytes(small)
    _run("e3")
    assert base64.b64decode(piped["images"][-1]) == small


def test_the_cap_is_720_px():
    assert pipe.L3_SNAPSHOT_HEIGHT == 720  # measured in s25 item 0 (4.1 s / 1 112 tokens vs 11.8 s / 3 832 at full)


def test_adapter_passes_the_height_to_frigate(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"jpeg")

    real = httpx.AsyncClient
    monkeypatch.setattr(frigate_adapter.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(frigate_adapter, "_frigate_url", lambda: "http://frigate.test")
    assert asyncio.run(frigate_adapter.fetch_event_snapshot("abc", height=720)) == b"jpeg"
    assert asyncio.run(frigate_adapter.fetch_event_snapshot("abc")) == b"jpeg"
    assert seen == ["http://frigate.test/api/events/abc/snapshot.jpg?height=720",
                    "http://frigate.test/api/events/abc/snapshot.jpg"]
