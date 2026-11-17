"""Owner decision 2026-11-16: what Kåre reads aloud on a speaker goes into the user's STM — only THAT the job was done,
plus a small reference for KTSB, never the content itself (a long web/wiki text must not enter memory).

After `announce say` has an outcome (the bridge's wait receipt): one short system line in the user's STM, receipt style —
what (the first words only) · where (the nodes that played) · when · outcome · ref (the turn's RID, and the source
id/URL when the text came from web search / library / wiki). Nothing when nothing played (Kåre's own reply says it).
No HTTP, no bridge. Placeholders only."""

import asyncio

import pytest

from kaare_core.tools import executor

LONG = " ".join(f"ord{i}" for i in range(400))  # a long wiki text read aloud


class FakeSTM:
    def __init__(self):
        self.lines = []

    def add_dialog(self, role, text, user_id=None, **kw):
        self.lines.append((role, text))

    def add_pending_system_note(self, text, user_id=None):
        self.lines.append(("pending", text))


@pytest.fixture
def stm(monkeypatch):
    fake = FakeSTM()
    monkeypatch.setattr(executor, "get_stm", lambda uid: fake)
    return fake


def _bridge(monkeypatch, results, status="ok"):
    class Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": status, "results": results}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return Resp()

    monkeypatch.setattr(executor.httpx, "AsyncClient", Client)


def _say(args):
    return asyncio.run(executor._dispatch("announce", {"action": "say", "_user_id": "user_a", "_rid": "rid-123", **args}))


def test_a_played_announcement_leaves_one_short_line_with_ref(stm, monkeypatch):
    _bridge(monkeypatch, {"kitchen": "played"})
    _say({"text": LONG, "target": "kitchen", "source": "https://example.org/wiki/Placeholder"})
    assert len(stm.lines) == 1
    role, line = stm.lines[0]
    assert role == "system" and "rid-123" in line and "https://example.org/wiki/Placeholder" in line and "kitchen" in line
    assert "ord0" in line and "ord20" not in line and len(line) < 400  # the first words only, never the text


def test_nothing_played_leaves_nothing(stm, monkeypatch):
    _bridge(monkeypatch, {"kitchen": "busy:other_app"}, status="failed")
    _say({"text": "Middagen er klar", "target": "kitchen"})
    assert stm.lines == []


def test_partial_names_only_where_it_played(stm, monkeypatch):
    _bridge(monkeypatch, {"kitchen": "played", "hall": "error"})
    _say({"text": "Middagen er klar", "target": "all"})
    [(_role, line)] = stm.lines
    assert "kitchen" in line and "hall" not in line
