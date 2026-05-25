"""Tests for web search module."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.websearch import BraveSearcher, SearchResult


class TestSearchResult:
    def test_fields(self):
        r = SearchResult(title="Test", snippet="A snippet", url="https://example.com")
        assert r.title == "Test"
        assert r.url == "https://example.com"


class TestBraveSearcher:
    def test_search_returns_results(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "web": {
                "results": [
                    {"title": "Strømpris", "description": "45 øre/kWh", "url": "https://example.com/1"},
                    {"title": "Energi", "description": "Info om strøm", "url": "https://example.com/2"},
                ]
            }
        }
        with patch("server.websearch.requests.get", return_value=mock_response):
            searcher = BraveSearcher(api_key="test-key")
            results = searcher.search("strømpris norge")
            assert len(results) == 2
            assert results[0].title == "Strømpris"
            assert results[0].snippet == "45 øre/kWh"

    def test_search_handles_timeout(self):
        import requests as req
        with patch("server.websearch.requests.get", side_effect=req.Timeout):
            searcher = BraveSearcher(api_key="test-key")
            results = searcher.search("test")
            assert results == []

    def test_search_no_api_key(self):
        searcher = BraveSearcher(api_key="")
        results = searcher.search("test")
        assert results == []

    def test_format_context(self):
        searcher = BraveSearcher(api_key="test-key")
        results = [
            SearchResult(title="Result 1", snippet="Snippet 1", url="https://a.com"),
            SearchResult(title="Result 2", snippet="Snippet 2", url="https://b.com"),
        ]
        ctx = searcher.format_context(results)
        assert "Result 1" in ctx
        assert "Snippet 1" in ctx
        assert "Result 2" in ctx

    def test_format_context_empty(self):
        searcher = BraveSearcher(api_key="test-key")
        ctx = searcher.format_context([])
        assert "Ingen" in ctx
