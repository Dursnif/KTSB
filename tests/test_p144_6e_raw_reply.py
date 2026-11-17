"""P144 6e item (3) (main session 2026-11-13): the image model's raw reply is stored per L3 and per frame, shown in
«Siste deteksjoner» as «Bildemodellens ord (rått)».

- L3: the event keeps the reply exactly as the model gave it (`l3_raw`, sealed like `description`) — also when it could
  not be parsed (that is when the raw words matter most); capped at RAW_CAP_BYTES (~4 KB, UTF-8 safe);
- frames: each frame row of module 4's run keeps its reply (`raw`) inside the episode scene — same retention as the
  frame rows (`frames_state` goes when the episode is slimmed);
- retention: `events.slim` clears `l3_raw` together with description/analysis (the event's detail tier);
- `store.recent_model_reads` carries `raw` on both kinds.
Fakes only (tmp DBs, fake LLM, fake Frigate)."""

import json

from kaare_core.frigate_user import store as fstore
from tests import test_p144_s06a_pipe as s06a
from tests import test_p144_s07_retention as s07
from tests import test_p144_s25_frames_step as s25

env = s06a.env
_s04_db = s07._s04_db  # s07's `db` fixture builds on s04's
db = s07.db
frames_env = s25.env


def test_l3_raw_is_stored_as_the_model_gave_it(env):
    s06a._process(s06a._event(), "Person A", 0.95)
    e = s06a._stored()
    assert e.l3_raw and e.l3_raw.endswith("[LANG:nb]") and '"description": "a person in a blue jacket"' in e.l3_raw
    assert e.description == "a person in a blue jacket"  # the parsed words as before


def test_an_unreadable_reply_is_kept_raw(env, monkeypatch):
    async def broken(role, prompt, images=None, rid=""):
        return {"ok": True, "text": "I see a person", "meta": {}}

    monkeypatch.setattr(s06a.pipe, "ask_llm_role", broken)
    s06a._process(s06a._event(), "Person A", 0.95)
    assert s06a._stored().l3_raw == "I see a person"


def test_the_raw_reply_is_capped_utf8_safe():
    text = "æ" * 5000  # 2 bytes each
    capped = fstore.cap_raw(text)
    assert len(capped.encode("utf-8")) <= fstore.RAW_CAP_BYTES and set(capped) == {"æ"}
    assert fstore.cap_raw("") is None and fstore.cap_raw(None) is None and fstore.cap_raw("kort") == "kort"


def test_slimming_clears_l3_raw(db, monkeypatch):
    s07._set(retention_dry_run=False, frigate_db_max_mb=1)  # P150: slimmed because the store is over its size limit
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 50 * 1_048_576)
    ev = s07._ev("p", start=s07.T0, description="x", analysis="y")
    ev.l3_raw = '{"description": "x"}'
    fstore.record_event(ev)
    assert s07._raw("SELECT l3_raw FROM events")[0]["l3_raw"]
    fstore.run_retention(now=s07.T0 + 8 * s07.DAY)
    assert all(r["l3_raw"] is None for r in s07._raw("SELECT l3_raw FROM events"))


def test_each_frame_row_keeps_its_raw_reply(frames_env):
    frames_env["answers"][2] = "{oops"
    _ep, _events, scene = s25._scene(frames_env)
    rows = {r["frame"]: r for r in scene["frames_state"]["rows"]}
    assert json.loads(rows[1]["raw"]) == s25.FULL
    assert rows[2]["raw"] == "{oops" and rows[2]["kind"] == "unreadable"
    assert "raw" not in rows[3]  # skipped: no call, so no reply


def test_the_reads_view_carries_raw(env):
    s06a._process(s06a._event(), "Person A", 0.95)
    rows, _more = fstore.recent_model_reads()
    assert rows[0]["kind"] == "l3" and rows[0]["raw"].endswith("[LANG:nb]")
