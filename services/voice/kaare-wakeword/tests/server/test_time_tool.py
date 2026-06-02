"""Tests for time tool."""
from __future__ import annotations

from unittest.mock import patch
from datetime import datetime

from server.tools.time_tool import TimeTool


class TestTimeTool:
    def test_current_time(self):
        with patch("server.tools.time_tool.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 26, 14, 30, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            tool = TimeTool()
            result = tool.handle({"query": "current_time"})
            assert "14:30" in result

    def test_current_date(self):
        with patch("server.tools.time_tool.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 26, 14, 30, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            tool = TimeTool()
            result = tool.handle({"query": "current_date"})
            assert "26" in result
            assert "februar" in result.lower()

    def test_default_is_time(self):
        tool = TimeTool()
        result = tool.handle({"query": "something_else"})
        assert ":" in result

    def test_no_query_returns_time(self):
        tool = TimeTool()
        result = tool.handle({})
        assert "Klokken" in result
