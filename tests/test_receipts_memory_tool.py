"""Receipts for memory action='confirm': marking conversations verified/denied comes back as one receipt from code (how many
rows were actually marked, which ids, in whose memory). Nothing marked → no receipt. The LTM is faked. Placeholders only."""

import asyncio

from kaare_core import receipts
from kaare_core.tools import executor_memory as em


class _Ltm:
    def __init__(self, n):
        self.n = n

    def mark_interactions(self, ids, verdict, user_id):
        return self.n


def _confirm(monkeypatch, n, ids):
    monkeypatch.setattr(em, "get_ltm", lambda: _Ltm(n))
    return asyncio.run(em.dispatch("memory", {"_user_id": "user_x", "action": "confirm", "ids": ids, "dom": "verified"}))


def test_confirm_receipt(monkeypatch):
    out = _confirm(monkeypatch, 2, [11, 12])
    assert receipts.parse(out) == [{"kind": "memory_verdict", "ref": "interactions:11,12"}]


def test_nothing_marked_no_receipt(monkeypatch):
    assert receipts.parse(_confirm(monkeypatch, 0, [99])) == []
