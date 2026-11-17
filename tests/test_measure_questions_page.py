"""Measurement, not a check (BUG-log 2026-11-16 «Frigate questions page in the GUI takes 5–7 s»): how long each
question endpoint takes on a frigate.db the size of the house's (≈18 000 sealed events over 14 days), step by step.

Runs only on request: `KTSB_MEASURE=1 pytest -s tests/test_measure_questions_page.py`. Everything is in tmp_path —
frigate.db, weights.db, receipts, the question cards and the sealing key come from conftest's autouse redirects (and are
set again here explicitly); nothing reads or opens the house's state/. Synthetic placeholder data only."""

import os
import random
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from kaare_core import receipts
from kaare_core.frigate_user import sealing
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.memory import chat_questions
from kaare_core.routers import router_frigate_user as rfu
from kaare_core.storage import budget
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from kaare_core.weights import store as wstore

pytestmark = pytest.mark.skipif(not os.environ.get("KTSB_MEASURE"), reason="measurement — run with KTSB_MEASURE=1")

N_EVENTS = int(os.environ.get("KTSB_MEASURE_EVENTS", "18000"))
DAYS = 14
CAMS = [f"cam_{i}" for i in range(8)]
LABELS = ["person"] * 4 + ["car"] * 2 + ["bicycle", "dog", "cat", "bird"]
TEXT = "Placeholder description of what the image model saw: a figure near the door, light jacket, moving left. " * 3


@pytest.fixture
def house(tmp_path, monkeypatch):
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(wstore, "DB_PATH", tmp_path / "weights.db")
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(receipts, "RECEIPTS_LOG", str(tmp_path / "receipts.log"))
    monkeypatch.setattr(chat_questions, "CARDS_PATH", tmp_path / "cards.json")
    monkeypatch.setattr(budget, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(sealing, "_master", lambda: bytes(range(32)))
    monkeypatch.setattr(rfu, "_camera_cards", lambda: {c: {"display_name": c} for c in CAMS})
    sealing.reset_keys()
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nretention_dry_run: true\n")
    fstore.reset_for_tests()
    wstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    for p in (fstore.DB_PATH, wstore.DB_PATH, ustore.DB_PATH):
        assert "/kaare/state" not in str(p)
    return tmp_path


def _fill(now: float) -> float:
    """Write N_EVENTS sealed events through the store (as the pipe does). Returns seconds taken."""
    rnd = random.Random(7)
    t0 = time.perf_counter()
    for i in range(N_EVENTS):
        start = now - rnd.uniform(0, DAYS * 86400)
        label = rnd.choice(LABELS)
        stationary = label in ("car", "bicycle") and rnd.random() < 0.6
        ev = fstore.FrigateUserEvent(
            event_id=f"{start:.6f}-{i:06d}", camera=rnd.choice(CAMS), label=label, start_ts=start, end_ts=start + 30,
            person_ref=None, person_kind="unknown", claim_status="none",
            check={"decision": "none", "ir": rnd.random() < 0.3,
                   "sensors": {"relation": "none", "detectors": [{"detector": "det_x", "ref": None}]}},
            attributes={"colour": "grey", "items": ["bag"], "note": TEXT[:80]}, description=TEXT, analysis=TEXT * 2,
            stationary=stationary,
            boxes=[[start + k, 0.1 * (i % 9), 0.1 * (k % 9), 0.05, 0.1, 0.5, 0, int(stationary)] for k in range(6)],
            path_data=None if stationary else [[[0.1 * k, 0.2], start + k] for k in range(5)])
        fstore.record_event(ev)
    return time.perf_counter() - t0


def _time(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def test_measure_question_endpoints(house):
    now = time.time()
    fill_s = _fill(now)
    with sqlite3.connect(fstore.DB_PATH) as c:
        rows = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    admin = {"sub": "boss", "role": "admin"}
    steps = {
        "events_between 7 d": lambda: fstore.events_between(now - 7 * 86400, now),
        "events_between 14 d": lambda: fstore.events_between(now - 14 * 86400, now),
        "things": lambda: rfu.api_thing_questions(_u=admin),
        "things/answered": lambda: rfu.api_thing_history(_u=admin),
        "what": lambda: rfu.api_what_questions(_u=admin),
        "places": lambda: rfu.api_place_questions(_u=admin),
        "mine": lambda: rfu.api_my_questions(payload={"sub": "boss"}),
        "who": lambda: rfu.api_admin_who_questions(u=admin),
    }
    one = {k: _time(f) for k, f in steps.items()}
    unseal_calls = {"n": 0}
    real_unseal = fstore._unseal

    def counting(v):
        unseal_calls["n"] += 1
        return real_unseal(v)

    fstore._unseal = counting
    try:
        _time(steps["events_between 14 d"])
    finally:
        fstore._unseal = real_unseal
    page = [k for k in steps if not k.startswith("events_between")]
    if hasattr(fstore, "_drop_events_cache"):  # N40: measure the tab cold, as when it is opened
        fstore._drop_events_cache()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(page)) as pool:  # the GUI opens the tab: all six at once
        futs = {k: pool.submit(_time, steps[k]) for k in page}
        together = {k: f.result() for k, f in futs.items()}
    wall = time.perf_counter() - t0
    print(f"\n[measure] {rows} events in frigate.db (filled in {fill_s:.1f} s)")
    for k, v in one.items():
        print(f"[measure] alone    {k:22s} {v:6.2f} s")
    print(f"[measure] unseal calls for one 14-day load: {unseal_calls['n']}")
    for k, v in together.items():
        print(f"[measure] together {k:22s} {v:6.2f} s")
    print(f"[measure] whole tab (6 at once) wall {wall:.2f} s")
    assert rows > 0
