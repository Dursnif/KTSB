"""P144-s14 item 6 + item 3 (GUI side) — the episode list API.

- an episode concluded before s06e (status done, no `by`) is shown as concluded (`by: model`), never «venter på
  konklusjon» — the user's screenshot 2026-10-28: 27.10 20:25 / 19:56 showed «pending»; a copy of frigate.db had 45 of 47
  done episodes without `by`;
- a code conclusion without `by` (role «code») is `by: code`;
- the list carries the store's write status, so Integrations → Frigate can show «Kameraminnet kan ikke skrive: …».
Reuses s11's fixture (tmp DBs, Frigate patched).
"""

from test_p144_s11_episodes_api import (  # noqa: F401 — client is a fixture
    T0,
    _episode,
    _ev,
    _hdr,
    client,
)

from kaare_core.frigate_user import store as fstore


def _items(client):  # noqa: F811 — the fixture
    r = client.get("/api/frigate_user/episodes", headers=_hdr())
    assert r.status_code == 200
    return r.json()


def test_done_episode_without_by_is_shown_as_concluded(client):  # noqa: F811
    eid = _episode(T0, _ev("a1", start=T0))
    fstore.close_episodes(T0 + 10_000, 120)
    assert fstore.conclude_episode(eid, {"status": "done", "summary": "Jeg så en person.", "role": "frigate_conclude",
                                         "unusual": {"value": False, "reason": ""}})
    (item,) = _items(client)["episodes"]
    assert item["conclusion_status"] == "done"
    assert item["by"] == "model"
    assert item["summary"] == "Jeg så en person."


def test_code_conclusion_without_by_is_code(client):  # noqa: F811
    eid = _episode(T0, _ev("a1", start=T0, label="car"))
    fstore.close_episodes(T0 + 10_000, 120)
    assert fstore.conclude_episode(eid, {"status": "done", "summary": "Jeg så bare kjøretøy.", "role": "code"})
    (item,) = _items(client)["episodes"]
    assert item["by"] == "code"


def test_list_carries_the_store_write_status(client, monkeypatch):  # noqa: F811
    assert _items(client)["store"]["ok"] is True
    monkeypatch.setattr(fstore, "write_status", lambda: {"ok": False, "op": "record_event", "cause": "read_only",
                                                         "error": "attempt to write a readonly database",
                                                         "since": T0, "failures": 3})
    st = _items(client)["store"]
    assert st["ok"] is False and st["cause"] == "read_only" and st["failures"] == 3
