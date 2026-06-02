"""Brave Search API wrapper for web search tool."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    snippet: str
    url: str


class BraveSearcher:
    """Brave Search API client.

    Args:
        api_key: Brave Search API key.
        max_results: Maximum number of results to return.
        timeout: Request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str = "",
        max_results: int = 5,
        timeout: float = 5.0,
    ):
        self._api_key = api_key
        self._max_results = max_results
        self._timeout = timeout

    def search(self, query: str) -> list[SearchResult]:
        """Search the web and return results."""
        if not self._api_key:
            log.warning("No Brave API key configured, skipping search")
            return []

        try:
            resp = requests.get(
                _BRAVE_SEARCH_URL,
                params={"q": query, "count": self._max_results},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": self._api_key,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("web", {}).get("results", []):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    snippet=item.get("description", ""),
                    url=item.get("url", ""),
                ))
            log.info("Brave search '%s': %d results", query[:40], len(results))
            return results
        except requests.RequestException as exc:
            log.warning("Brave search failed: %s", exc)
            return []

    def format_context(self, results: list[SearchResult]) -> str:
        """Format search results as context text for LLM injection."""
        if not results:
            return "Ingen søkeresultater funnet."
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.title}: {r.snippet}")
        return "\n".join(lines)
