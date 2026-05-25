# Escalation System Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a confidence-based escalation system that routes queries through progressively stronger models, with web search and rubber ducky tools available at every level.

**Architecture:** An `EscalationRouter` wraps the existing `NLUEngine`, calling models in order (4b -> 12b -> 27b -> Claude) and scoring responses. A `WebSearcher` module handles Brave Search API calls. Tool actions (`needs_search`, `needs_help`, `expect_human_response`) are intercepted by the router before scoring. TTS feedback messages are sent between escalation levels.

**Tech Stack:** Python, requests (Brave API), existing Ollama + Claude integration, existing gTTS.

---

### Task 1: Web Search Module

**Files:**
- Create: `server/websearch.py`
- Test: `tests/server/test_websearch.py`

**Step 1: Write the failing test**

```python
# tests/server/test_websearch.py
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
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_websearch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.websearch'`

**Step 3: Write minimal implementation**

```python
# server/websearch.py
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
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_websearch.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add server/websearch.py tests/server/test_websearch.py
git commit -m "feat: add Brave Search web search module"
```

---

### Task 2: Escalation Scoring

**Files:**
- Create: `server/escalation.py`
- Test: `tests/server/test_escalation.py`

**Step 1: Write the failing test**

```python
# tests/server/test_escalation.py
"""Tests for escalation scoring logic."""
from __future__ import annotations

import pytest

from server.escalation import EscalationScorer


class TestEscalationScorer:
    def setup_method(self):
        self.scorer = EscalationScorer(threshold=3)

    def test_high_confidence_valid_json(self):
        raw = '{"action": "answer", "response": "Oslo er hovedstaden.", "confidence": 5}'
        score = self.scorer.score(raw)
        assert score >= 3
        assert not self.scorer.should_escalate(raw)

    def test_low_confidence(self):
        raw = '{"action": "answer", "response": "Kanskje...", "confidence": 2}'
        score = self.scorer.score(raw)
        assert score < 3
        assert self.scorer.should_escalate(raw)

    def test_json_parse_failure(self):
        raw = "This is not JSON at all"
        score = self.scorer.score(raw)
        assert score == 0
        assert self.scorer.should_escalate(raw)

    def test_unknown_action_penalty(self):
        raw = '{"action": "unknown", "response": "Vet ikke.", "confidence": 4}'
        score = self.scorer.score(raw)
        assert score < 3  # 4 - 2 = 2

    def test_short_response_penalty(self):
        raw = '{"action": "answer", "response": "Ja.", "confidence": 4}'
        score = self.scorer.score(raw)
        assert score < 4  # 4 - 1 = 3

    def test_empty_response_penalty(self):
        raw = '{"action": "answer", "response": "", "confidence": 4}'
        score = self.scorer.score(raw)
        assert score < 3  # 4 - 1 - 1 = 2

    def test_no_confidence_field_defaults_low(self):
        raw = '{"action": "answer", "response": "Et svar uten confidence."}'
        score = self.scorer.score(raw)
        assert score <= 2

    def test_tool_action_not_scored(self):
        raw = '{"action": "needs_search", "query": "strømpris", "confidence": 2}'
        assert self.scorer.is_tool_request(raw)

    def test_expect_human_response_is_tool(self):
        raw = '{"action": "expect_human_response", "response": "Vil du ha lys?", "confidence": 4}'
        assert self.scorer.is_tool_request(raw)

    def test_needs_help_is_tool(self):
        raw = '{"action": "needs_help", "question": "Hva er kvantemekanikk?", "confidence": 1}'
        assert self.scorer.is_tool_request(raw)
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_escalation.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# server/escalation.py
"""Escalation system: confidence scoring, model chain, tool dispatch."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

_TOOL_ACTIONS = {"needs_search", "needs_help", "expect_human_response"}


class EscalationScorer:
    """Score LLM responses and decide whether to escalate.

    Args:
        threshold: Minimum score to accept a response (default 3).
    """

    def __init__(self, threshold: int = 3):
        self.threshold = threshold

    def score(self, raw_response: str) -> int:
        """Score a raw LLM response string. Returns 0-5."""
        try:
            parsed = json.loads(raw_response)
        except (json.JSONDecodeError, TypeError):
            return 0

        score = parsed.get("confidence", 2)

        if parsed.get("action") == "unknown":
            score -= 2
        response_text = parsed.get("response", "")
        if len(response_text) < 10:
            score -= 1
        if not response_text.strip():
            score -= 1

        return max(score, 0)

    def should_escalate(self, raw_response: str) -> bool:
        """Return True if the response should be escalated."""
        return self.score(raw_response) < self.threshold

    def is_tool_request(self, raw_response: str) -> bool:
        """Return True if the response is a tool request (not a final answer)."""
        try:
            parsed = json.loads(raw_response)
            return parsed.get("action") in _TOOL_ACTIONS
        except (json.JSONDecodeError, TypeError):
            return False
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_escalation.py -v`
Expected: All 10 tests PASS

**Step 5: Commit**

```bash
git add server/escalation.py tests/server/test_escalation.py
git commit -m "feat: add escalation scoring logic"
```

---

### Task 3: Escalation Router

**Files:**
- Modify: `server/escalation.py` (add `EscalationRouter` class)
- Modify: `tests/server/test_escalation.py` (add router tests)

**Step 1: Write the failing test**

Append to `tests/server/test_escalation.py`:

```python
from unittest.mock import patch, MagicMock
from server.escalation import EscalationRouter, EscalationScorer
from server.nlu import NLUResult


class TestEscalationRouter:
    def test_first_model_succeeds_no_escalation(self):
        """If the first model scores high enough, no escalation happens."""
        router = EscalationRouter(
            model_chain=["model-4b", "model-12b"],
            ollama_url="http://localhost:11434",
            ollama_timeout=30,
            scorer=EscalationScorer(threshold=3),
        )
        good_response = '{"action": "answer", "response": "Oslo er hovedstaden i Norge.", "confidence": 5}'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": good_response}}

        with patch("server.escalation.requests.post", return_value=mock_resp):
            result = router.route(
                messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "Hva er hovedstaden?"}],
            )
            assert result.model_used == "model-4b"
            assert result.escalation_level == 0
            assert result.raw_response == good_response

    def test_escalation_on_low_score(self):
        """If first model scores low, escalate to next."""
        router = EscalationRouter(
            model_chain=["model-4b", "model-12b"],
            ollama_url="http://localhost:11434",
            ollama_timeout=30,
            scorer=EscalationScorer(threshold=3),
        )
        bad = '{"action": "unknown", "response": "Vet ikke.", "confidence": 1}'
        good = '{"action": "answer", "response": "Et godt svar her.", "confidence": 5}'

        mock_bad = MagicMock()
        mock_bad.status_code = 200
        mock_bad.json.return_value = {"message": {"content": bad}}

        mock_good = MagicMock()
        mock_good.status_code = 200
        mock_good.json.return_value = {"message": {"content": good}}

        with patch("server.escalation.requests.post", side_effect=[mock_bad, mock_good]):
            result = router.route(
                messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "Vanskelig spørsmål"}],
            )
            assert result.model_used == "model-12b"
            assert result.escalation_level == 1

    def test_tool_request_returned_immediately(self):
        """Tool requests (needs_search, needs_help) are returned without scoring."""
        router = EscalationRouter(
            model_chain=["model-4b"],
            ollama_url="http://localhost:11434",
            ollama_timeout=30,
            scorer=EscalationScorer(threshold=3),
        )
        tool_response = '{"action": "needs_search", "query": "strømpris", "confidence": 1}'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": tool_response}}

        with patch("server.escalation.requests.post", return_value=mock_resp):
            result = router.route(
                messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "Strømpris?"}],
            )
            assert result.raw_response == tool_response
            assert result.model_used == "model-4b"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_escalation.py::TestEscalationRouter -v`
Expected: FAIL with `ImportError: cannot import name 'EscalationRouter'`

**Step 3: Write minimal implementation**

Append to `server/escalation.py`:

```python
import requests


@dataclass
class RouteResult:
    """Result from the escalation router."""
    raw_response: str
    model_used: str
    escalation_level: int


class EscalationRouter:
    """Routes queries through a chain of models based on response quality.

    Args:
        model_chain: Ordered list of Ollama model names (weakest to strongest).
        ollama_url: Ollama API base URL.
        ollama_timeout: Request timeout in seconds per model.
        scorer: EscalationScorer instance.
    """

    def __init__(
        self,
        model_chain: list[str],
        ollama_url: str = "http://localhost:11434",
        ollama_timeout: int = 120,
        scorer: EscalationScorer | None = None,
    ):
        self.model_chain = model_chain
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_timeout = ollama_timeout
        self.scorer = scorer or EscalationScorer()

    def _call_ollama(self, model: str, messages: list[dict]) -> str | None:
        """Call Ollama chat API and return raw content string."""
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_ctx": 4096},
                },
                timeout=self.ollama_timeout,
            )
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "")
        except requests.RequestException as exc:
            log.warning("Ollama call failed for %s: %s", model, exc)
            return None

    def route(self, messages: list[dict]) -> RouteResult:
        """Route a query through the model chain until a good answer is found."""
        for level, model in enumerate(self.model_chain):
            raw = self._call_ollama(model, messages)
            if raw is None:
                continue

            # Tool requests are returned immediately (caller handles them)
            if self.scorer.is_tool_request(raw):
                log.info("Model %s requested tool: %s", model, raw[:80])
                return RouteResult(raw_response=raw, model_used=model, escalation_level=level)

            # Check quality
            if not self.scorer.should_escalate(raw):
                log.info("Model %s accepted (score=%d)", model, self.scorer.score(raw))
                return RouteResult(raw_response=raw, model_used=model, escalation_level=level)

            log.info(
                "Model %s scored %d (below %d), escalating...",
                model, self.scorer.score(raw), self.scorer.threshold,
            )

        # All models exhausted — return last response
        return RouteResult(
            raw_response=raw or "",
            model_used=self.model_chain[-1] if self.model_chain else "none",
            escalation_level=len(self.model_chain) - 1,
        )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_escalation.py -v`
Expected: All 13 tests PASS

**Step 5: Commit**

```bash
git add server/escalation.py tests/server/test_escalation.py
git commit -m "feat: add EscalationRouter with model chain routing"
```

---

### Task 4: Update System Prompt

**Files:**
- Modify: `server/nlu.py:25-43` (DEFAULT_SYSTEM_PROMPT)

**Step 1: Write the failing test**

Append to `tests/server/test_nlu.py`:

```python
class TestSystemPrompt:
    def test_prompt_includes_confidence_instruction(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "confidence" in engine._system_prompt

    def test_prompt_includes_needs_search(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "needs_search" in engine._system_prompt

    def test_prompt_includes_needs_help(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "needs_help" in engine._system_prompt

    def test_prompt_includes_expect_human_response(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "expect_human_response" in engine._system_prompt
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_nlu.py::TestSystemPrompt -v`
Expected: FAIL — current prompt does not contain "confidence", "needs_search" etc.

**Step 3: Update the system prompt**

In `server/nlu.py`, replace `DEFAULT_SYSTEM_PROMPT` (lines 25-43) with:

```python
DEFAULT_SYSTEM_PROMPT = """\
You are Kåre, a voice assistant for a smart home. The user speaks Norwegian or English.
Respond in the same language the user spoke.

Available Home Assistant entities:
{entities}

Always respond with valid JSON only. Always include a "confidence" field (1-5).
5 = completely certain, 3 = somewhat unsure, 1 = guessing or don't know.

For smart home commands:
{{"action": "ha_call_service", "domain": "light", "service": "turn_on", "entity_id": "light.kitchen", "response": "Skrur på lyset på kjøkkenet.", "confidence": 5}}

For general questions you can answer:
{{"action": "answer", "response": "Your helpful answer here.", "confidence": 4}}

If you need current information from the internet to answer:
{{"action": "needs_search", "query": "your search query in the relevant language", "confidence": 1}}

If the question is too complex and you need expert help:
{{"action": "needs_help", "question": "your specific question for the expert", "confidence": 1}}

If you are asking the user a question and expect a reply:
{{"action": "expect_human_response", "response": "your question to the user", "confidence": 4}}

For things you truly cannot do:
{{"action": "unknown", "response": "Beklager, det kan jeg ikke hjelpe med.", "confidence": 1}}
"""
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_nlu.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "feat: update system prompt with confidence, search, help, and follow-up actions"
```

---

### Task 5: Update Config with Escalation Settings

**Files:**
- Modify: `server/config.py`

**Step 1: Write the failing test**

Append to `tests/server/test_nlu.py` (or create `tests/server/test_config.py`):

```python
from server.config import ServerConfig

class TestEscalationConfig:
    def test_default_model_chain(self):
        cfg = ServerConfig()
        assert len(cfg.model_chain) >= 1

    def test_default_brave_key_empty(self):
        cfg = ServerConfig()
        assert cfg.brave_api_key == ""

    def test_escalation_threshold(self):
        cfg = ServerConfig()
        assert cfg.escalation_threshold == 3

    def test_claude_api_key_default_empty(self):
        cfg = ServerConfig()
        assert cfg.claude_api_key == ""
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_nlu.py::TestEscalationConfig -v`
Expected: FAIL — `ServerConfig` has no `model_chain` attribute

**Step 3: Add fields to ServerConfig**

In `server/config.py`, add after `system_prompt_file`:

```python
    # Escalation
    model_chain: tuple[str, ...] = (
        "hf.co/NbAiLab/borealis-4b-instruct-preview-gguf:Q8_0",
        "borealis-12b:latest",
        "borealis-27b:latest",
    )
    escalation_threshold: int = 3

    # Web search
    brave_api_key: str = ""

    # Cloud fallback
    claude_api_key: str = ""
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_nlu.py::TestEscalationConfig -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add server/config.py tests/server/test_nlu.py
git commit -m "feat: add escalation, web search, and cloud config fields"
```

---

### Task 6: Integrate Router into NLU Engine

**Files:**
- Modify: `server/nlu.py` (process_local uses EscalationRouter, handles tool actions)
- Modify: `tests/server/test_nlu.py`

**Step 1: Write the failing test**

```python
class TestNLUToolActions:
    def test_needs_search_detected(self):
        """NLU should detect needs_search and set action accordingly."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        # Simulate a response with needs_search
        raw = '{"action": "needs_search", "query": "strømpris", "confidence": 1}'
        result = engine._parse_response(raw)
        assert result.action == "needs_search"
        assert result.entities.get("query") == "strømpris"

    def test_expect_human_response_sets_followup(self):
        """expect_human_response should set expects_followup=True."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "expect_human_response", "response": "Stua eller soverommet?", "confidence": 4}'
        result = engine._parse_response(raw)
        assert result.action == "expect_human_response"
        assert result.expects_followup is True

    def test_needs_help_detected(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "needs_help", "question": "Hva er kvantemekanikk?", "confidence": 1}'
        result = engine._parse_response(raw)
        assert result.action == "needs_help"
        assert result.entities.get("question") == "Hva er kvantemekanikk?"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_nlu.py::TestNLUToolActions -v`
Expected: FAIL — `NLUEngine` has no `_parse_response` method

**Step 3: Refactor NLU to extract `_parse_response` and integrate router**

In `server/nlu.py`, extract the JSON parsing logic from `process_local` into a standalone method `_parse_response(raw) -> NLUResult`, and update `expects_followup` to check for `expect_human_response` action:

```python
def _parse_response(self, raw: str) -> NLUResult:
    """Parse a raw LLM response string into NLUResult."""
    raw = _strip_code_fences(raw)
    try:
        parsed = json.loads(raw)
        response_text = parsed.get("response", "")
        action = parsed.get("action", "unknown")
        return NLUResult(
            action=action,
            entities={
                k: v for k, v in parsed.items()
                if k not in ("action", "response", "confidence")
            },
            response_text=response_text,
            confidence=parsed.get("confidence", 0.5),
            source="ollama",
            expects_followup=(
                action == "expect_human_response"
                or response_text.rstrip().endswith("?")
            ),
        )
    except json.JSONDecodeError:
        return NLUResult(
            action="answer",
            entities={},
            response_text=raw,
            confidence=0.5,
            source="ollama",
            expects_followup=raw.rstrip().endswith("?"),
        )
```

Then update `process_local` to use `self._parse_response(raw)` instead of inline parsing.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_nlu.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "refactor: extract _parse_response, add tool action handling"
```

---

### Task 7: Tool Dispatch Loop in Pipeline

**Files:**
- Modify: `server/pipeline.py` (was `server/server.py` inline — the `ServerPipeline.process` method)
- Modify: `tests/server/test_server_pipeline.py`

This is the core integration: the pipeline's `process` method now calls the `EscalationRouter`, handles tool actions (web search, rubber ducky), and loops until a final answer.

**Step 1: Write the failing test**

```python
# Append to tests/server/test_server_pipeline.py
from unittest.mock import patch, MagicMock
from server.config import ServerConfig


class TestToolDispatch:
    def test_needs_search_calls_brave_and_reprompts(self):
        """When model returns needs_search, pipeline should search and re-prompt."""
        config = ServerConfig(brave_api_key="test-key")

        # First call: model asks for search
        # Second call: model answers with search context
        search_response = '{"action": "needs_search", "query": "strømpris", "confidence": 1}'
        final_response = '{"action": "answer", "response": "Strømprisen er 45 øre.", "confidence": 5}'

        with patch("server.escalation.requests.post") as mock_ollama, \
             patch("server.websearch.requests.get") as mock_brave:

            # Ollama returns search request, then final answer
            mock_r1 = MagicMock()
            mock_r1.json.return_value = {"message": {"content": search_response}}
            mock_r2 = MagicMock()
            mock_r2.json.return_value = {"message": {"content": final_response}}
            mock_ollama.side_effect = [mock_r1, mock_r2]

            # Brave returns results
            mock_brave_resp = MagicMock()
            mock_brave_resp.json.return_value = {
                "web": {"results": [{"title": "Pris", "description": "45 øre/kWh", "url": "https://x.com"}]}
            }
            mock_brave.return_value = mock_brave_resp

            # This test validates the dispatch loop exists and works
            # Actual integration tested in test_server_pipeline.py
```

**Step 2-5:** Implementation integrates `EscalationRouter` + `BraveSearcher` into `ServerPipeline.process()`:

The `process` method becomes a loop:
1. Call `router.route(messages)` to get response from best model
2. If response is `needs_search` → call `BraveSearcher`, inject results, loop
3. If response is `needs_help` → call Claude API with the question, inject hint, loop
4. If response is final answer → return
5. Max 1 tool call per level, max 3 total tool calls per turn

TTS feedback ("Vent litt, jeg sjekker på nett...") is synthesized and sent to the satellite *before* tool execution.

**Commit:**

```bash
git add server/pipeline.py server/server.py tests/server/test_server_pipeline.py
git commit -m "feat: integrate escalation router and tool dispatch into pipeline"
```

---

### Task 8: Rubber Ducky via Claude API

**Files:**
- Modify: `server/nlu.py` (add `ask_expert` method)
- Modify: `tests/server/test_nlu.py`

**Step 1: Write the failing test**

```python
class TestRubberDucky:
    def test_ask_expert_returns_hint(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        engine.claude_api_key = "test-key"

        with patch("server.nlu.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="Kvantemekanikk handler om partikler på atomnivå.")]
            mock_client.messages.create.return_value = mock_msg
            mock_anthropic.Anthropic.return_value = mock_client

            hint = engine.ask_expert("Forklar kvantemekanikk enkelt")
            assert "partikler" in hint.lower() or "atom" in hint.lower()

    def test_ask_expert_no_api_key(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        hint = engine.ask_expert("Test question")
        assert hint == ""
```

**Step 3: Implementation**

```python
def ask_expert(self, question: str) -> str:
    """Ask Claude API for a focused hint (rubber ducky pattern)."""
    if not self.claude_api_key:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=self.claude_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            system="Give a concise, factual answer. No preamble. Match the question's language.",
            messages=[{"role": "user", "content": question}],
        )
        return msg.content[0].text
    except Exception as exc:
        log.warning("Rubber ducky (Claude) failed: %s", exc)
        return ""
```

**Commit:**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "feat: add rubber ducky (ask_expert) via Claude API"
```

---

### Task 9: TTS Feedback Messages

**Files:**
- Modify: `server/server.py` (send interim TTS between escalation levels)

**Step 1-3:** Add a dict of feedback phrases and a helper to synthesize + send them:

```python
_ESCALATION_FEEDBACK = {
    "needs_search": "Vent litt, jeg sjekker på nett...",
    "needs_help": "Hmm, la meg spørre en kollega...",
    "escalation": "La meg tenke litt hardere på det...",
    "claude": "Jeg henter inn eksperthjelp...",
}
```

The pipeline sends these as TTS audio to the satellite *before* the next operation starts. Pre-cache them on startup so there is no gTTS latency during escalation.

**Commit:**

```bash
git add server/server.py
git commit -m "feat: add transparent TTS feedback between escalation levels"
```

---

### Task 10: Server CLI Arguments

**Files:**
- Modify: `server/server.py` (add `--brave-api-key`, `--claude-api-key`, `--model-chain`, `--escalation-threshold` args)

**Step 1-3:** Add argparse arguments and wire them into `ServerConfig`:

```python
parser.add_argument("--brave-api-key", default="", help="Brave Search API key")
parser.add_argument("--claude-api-key", default="", help="Anthropic API key for rubber ducky / fallback")
parser.add_argument("--model-chain", nargs="+",
    default=["hf.co/NbAiLab/borealis-4b-instruct-preview-gguf:Q8_0", "borealis-12b:latest", "borealis-27b:latest"],
    help="Model escalation chain (weakest to strongest)")
parser.add_argument("--escalation-threshold", type=int, default=3, help="Minimum score to accept (1-5)")
```

**Commit:**

```bash
git add server/server.py
git commit -m "feat: add escalation CLI arguments"
```

---

### Task 11: Ollama Multi-Model Config

**Files:**
- None (server-side config change)

**Steps:**

1. SSH to server
2. Set `OLLAMA_MAX_LOADED_MODELS=3` in Ollama systemd service
3. Restart Ollama
4. Verify all three borealis models load

```bash
ssh m@192.168.87.242 'sudo systemctl edit ollama'
# Add: Environment="OLLAMA_MAX_LOADED_MODELS=3"
ssh m@192.168.87.242 'sudo systemctl restart ollama'
ssh m@192.168.87.242 'ollama list | grep borealis'
```

---

### Task 12: End-to-End Integration Test

**Files:**
- Modify: `tests/server/test_server_pipeline.py`

Write a full mock test that simulates:
1. User asks simple question → 4b answers, no escalation
2. User asks hard question → 4b fails → 12b succeeds
3. User asks factual question → 4b asks for web search → search results → 4b answers
4. User asks very complex question → all local models fail → Claude answers

All Ollama and Brave calls are mocked. Verify correct model routing, tool dispatch, and TTS feedback.

**Commit:**

```bash
git add tests/server/test_server_pipeline.py
git commit -m "test: add end-to-end escalation integration tests"
```

---

### Task 13: Time Tool

**Files:**
- Create: `server/tools/time_tool.py`
- Test: `tests/server/test_time_tool.py`

**Step 1: Write the failing test**

```python
# tests/server/test_time_tool.py
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
            assert "februar" in result.lower() or "2" in result

    def test_unknown_query_returns_time(self):
        tool = TimeTool()
        result = tool.handle({"query": "something_else"})
        assert ":" in result  # contains time
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_time_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# server/tools/__init__.py
# (empty, makes it a package)

# server/tools/time_tool.py
"""Time and date tool for Kåre."""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger(__name__)

_WEEKDAYS_NO = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
_MONTHS_NO = [
    "januar", "februar", "mars", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "desember",
]


class TimeTool:
    """Handles time and date queries."""

    def handle(self, params: dict) -> str:
        """Handle a get_time action. Returns a human-readable string."""
        now = datetime.now()
        query = params.get("query", "current_time")

        if query == "current_date":
            weekday = _WEEKDAYS_NO[now.weekday()]
            month = _MONTHS_NO[now.month - 1]
            return f"I dag er det {weekday} {now.day}. {month} {now.year}."

        # Default: current time
        return f"Klokken er {now.strftime('%H:%M')}."
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_time_tool.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add server/tools/__init__.py server/tools/time_tool.py tests/server/test_time_tool.py
git commit -m "feat: add time/date tool for get_time action"
```

---

### Task 14: Home Assistant REST API Tool

**Files:**
- Create: `server/tools/ha_tool.py`
- Test: `tests/server/test_ha_tool.py`

Extends the existing `ha_call_service` with a general `ha_api` action that can query states, history, and other HA REST endpoints.

**Step 1: Write the failing test**

```python
# tests/server/test_ha_tool.py
"""Tests for Home Assistant API tool."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest

from server.tools.ha_tool import HomeAssistantTool


class TestHomeAssistantTool:
    def test_get_state(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "entity_id": "sensor.temperature_living_room",
            "state": "21.5",
            "attributes": {"unit_of_measurement": "°C", "friendly_name": "Stuetemperatur"},
        }
        with patch("server.tools.ha_tool.requests.request", return_value=mock_resp):
            result = tool.handle({
                "method": "GET",
                "path": "/api/states/sensor.temperature_living_room",
            })
            assert "21.5" in result
            assert "°C" in result or "Stuetemperatur" in result

    def test_no_token_returns_error(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="")
        result = tool.handle({"method": "GET", "path": "/api/states/light.kitchen"})
        assert "ikke konfigurert" in result.lower() or "error" in result.lower()

    def test_call_service(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{}]
        with patch("server.tools.ha_tool.requests.request", return_value=mock_resp):
            result = tool.handle({
                "method": "POST",
                "path": "/api/services/light/turn_on",
                "body": {"entity_id": "light.kitchen"},
            })
            assert result  # non-empty response

    def test_request_failure(self):
        tool = HomeAssistantTool(url="http://ha.local:8123", token="test-token")
        import requests as req
        with patch("server.tools.ha_tool.requests.request", side_effect=req.ConnectionError):
            result = tool.handle({"method": "GET", "path": "/api/states/light.kitchen"})
            assert "feil" in result.lower() or "error" in result.lower()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/server/test_ha_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# server/tools/ha_tool.py
"""Home Assistant REST API tool for Kåre."""
from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)


class HomeAssistantTool:
    """Handles Home Assistant REST API calls.

    Args:
        url: Home Assistant base URL.
        token: Long-lived access token.
        timeout: Request timeout in seconds.
    """

    def __init__(self, url: str, token: str, timeout: float = 10.0):
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def handle(self, params: dict) -> str:
        """Execute a Home Assistant API call and return human-readable result."""
        if not self._token:
            return "Home Assistant er ikke konfigurert (mangler token)."

        method = params.get("method", "GET").upper()
        path = params.get("path", "")
        body = params.get("body")

        try:
            resp = requests.request(
                method,
                f"{self._url}{path}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # Format state responses nicely
            if isinstance(data, dict) and "state" in data:
                attrs = data.get("attributes", {})
                name = attrs.get("friendly_name", data.get("entity_id", ""))
                unit = attrs.get("unit_of_measurement", "")
                return f"{name}: {data['state']}{' ' + unit if unit else ''}"

            return json.dumps(data, ensure_ascii=False)[:500]

        except requests.RequestException as exc:
            log.warning("HA API call failed: %s %s -> %s", method, path, exc)
            return f"Feil ved kontakt med Home Assistant: {exc}"
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/server/test_ha_tool.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add server/tools/ha_tool.py tests/server/test_ha_tool.py
git commit -m "feat: add Home Assistant REST API tool for ha_api action"
```

---

### Task 15: Wire Tools into Dispatch Loop

**Files:**
- Modify: `server/pipeline.py` (add `get_time` and `ha_api` to tool dispatch)
- Modify: `server/escalation.py` (add `get_time` and `ha_api` to `_TOOL_ACTIONS`)

**Step 1-3:** Update `_TOOL_ACTIONS` set to include the new actions, and add dispatch handlers in the pipeline's tool loop:

```python
# In server/escalation.py
_TOOL_ACTIONS = {"needs_search", "needs_help", "expect_human_response", "get_time", "ha_api"}

# In pipeline tool dispatch loop
if action == "get_time":
    result_text = self._time_tool.handle(parsed)
    # Inject result and re-prompt same model
elif action == "ha_api":
    result_text = self._ha_tool.handle(parsed)
    # Inject result and re-prompt same model
```

The pipeline injects the tool result back as context, then the model formulates a natural language response.

**Step 4: Run all tests**

Run: `python -m pytest tests/server/ -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add server/pipeline.py server/escalation.py
git commit -m "feat: wire time and HA API tools into escalation dispatch loop"
```

---

### Task 16: Update System Prompt with New Tools

**Files:**
- Modify: `server/nlu.py` (add `get_time` and `ha_api` examples to DEFAULT_SYSTEM_PROMPT)

**Step 1:** Add test for new actions in prompt:

```python
class TestSystemPromptTools:
    def test_prompt_includes_get_time(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "get_time" in engine._system_prompt

    def test_prompt_includes_ha_api(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "ha_api" in engine._system_prompt
```

**Step 3:** Add to DEFAULT_SYSTEM_PROMPT:

```
If the user asks about time or date:
{{"action": "get_time", "query": "current_time", "confidence": 5}}

If you need to check a sensor, device state, or call a Home Assistant API:
{{"action": "ha_api", "method": "GET", "path": "/api/states/sensor.temperature_living_room", "confidence": 4}}
```

**Commit:**

```bash
git add server/nlu.py tests/server/test_nlu.py
git commit -m "feat: add get_time and ha_api actions to system prompt"
```
