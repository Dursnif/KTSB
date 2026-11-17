"""P144 6e — «Siste deteksjoner» shows the image model's own single-image reads that already exist (no own still
analysis): L3 per event (`description` + `analysis`) and each frame of module 4 (`frames_state.rows`: persons + looks — not
`changes`, which mixes in code facts). Newest first, paged by `before`, each with camera display name and its episode.
No model call; frigate.db in tmp_path."""

import json

import pytest

from kaare_core.frigate_user import store as fstore
from kaare_core.routers import router_frigate_user as rfu

T0 = 1_800_000_000.0


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    fstore.reset_for_tests()
    monkeypatch.setattr(rfu, "_camera_cards", lambda: {"cam_door": {"display_name": "Inngang"}})
    ev = fstore.FrigateUserEvent(event_id="e1", camera="cam_door", label="person", start_ts=T0, end_ts=T0 + 10,
                                 description="En person i grønn jakke.", analysis="Går mot døra.")
    assert fstore.record_event(ev)
    scene = {"frames_state": {"rows": [
        {"frame": 1, "camera": "cam_door", "ts": T0 + 2, "persons": {"P1": {"place": "porch", "posture": "standing"}},
         "looks": {"P1": {"expression": "focused"}}, "changes": [{"what": "merged", "id": "P1"}]},
        {"frame": 2, "camera": "cam_door", "ts": T0 + 8, "persons": {"P1": {"place": "door"}}}]}}

    def put(conn):
        conn.execute("INSERT INTO episodes (start_ts, end_ts, scene) VALUES (?, ?, ?)", (T0, T0 + 20, json.dumps(scene)))
        conn.commit()
    fstore._with_conn(put)
    yield
    fstore.reset_for_tests()


def test_reads_mix_l3_and_frames_newest_first(db):
    out = rfu.api_frigate_model_reads(_u={"role": "admin"})
    kinds = [(r["kind"], r.get("frame")) for r in out["reads"]]
    assert kinds == [("frame", 2), ("frame", 1), ("l3", None)]
    f1 = out["reads"][1]
    assert f1["index"] == 0 and f1["camera_name"] == "Inngang" and f1["episode_id"] is not None
    assert "changes" not in f1["words"] and f1["words"]["looks"]["P1"]["expression"] == "focused"
    assert out["reads"][2]["words"] == {"description": "En person i grønn jakke.", "analysis": "Går mot døra."}


def test_paging(db):
    first = rfu.api_frigate_model_reads(limit=1, _u={"role": "admin"})
    assert len(first["reads"]) == 1 and first["has_more"]
    rest = rfu.api_frigate_model_reads(before=first["reads"][0]["ts"], _u={"role": "admin"})
    assert [r["kind"] for r in rest["reads"]] == ["frame", "l3"]


def test_module_panel_shows_m1_as_replaced():
    rows = {r["n"]: r for r in rfu.api_frigate_modules(_u={"role": "admin"})["modules"]}
    assert rows[1]["replaced"] is True and rows[1]["switchable"] is False
    assert not rows[2].get("replaced")
