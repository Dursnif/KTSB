"""Tests for persistent conversation memory."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from server.memory import ConversationMemory


class TestConversationMemory:
    def test_store_and_retrieve_summary(self, tmp_path):
        mem = ConversationMemory(db_path=str(tmp_path / "test.db"))
        mem.store_summary(
            speaker="mikalv",
            summary="Snakket om varmepumpa og strompris.",
            topics=["varmepumpe", "strom"],
            satellite_id="rpi-stue",
        )
        results = mem.get_recent_context("mikalv", days=1)
        assert "varmepumpa" in results

    def test_search_conversations(self, tmp_path):
        mem = ConversationMemory(db_path=str(tmp_path / "test.db"))
        mem.store_summary("mikalv", "Diskuterte Sonos-oppsett i garasjen.", ["sonos", "garasje"])
        mem.store_summary("mikalv", "Spurte om vaermeldingen.", ["vaer"])
        results = mem.search("Sonos")
        assert len(results) >= 1
        assert "Sonos" in results[0]["summary"]

    def test_per_speaker_isolation(self, tmp_path):
        mem = ConversationMemory(db_path=str(tmp_path / "test.db"))
        mem.store_summary("mikalv", "Admin stuff.", ["admin"])
        mem.store_summary("barn1", "Barnegreier.", ["lek"])
        mikalv_ctx = mem.get_recent_context("mikalv", days=30)
        assert "Admin" in mikalv_ctx
        assert "Barnegreier" not in mikalv_ctx
