"""Escalation system: confidence scoring, model chain, tool dispatch."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

_TOOL_ACTIONS = {
    "needs_search", "needs_help", "expect_human_response",
    "get_time", "ha_api", "ha_list",
}


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

        s = parsed.get("confidence", 2)

        if parsed.get("action") == "unknown":
            s -= 2
        response_text = parsed.get("response", "")
        if len(response_text) < 10:
            s -= 1
        if not response_text.strip():
            s -= 1

        return max(s, 0)

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

    def route(self, messages: list[dict], start_level: int = 0) -> RouteResult:
        """Route a query through the model chain until a good answer is found.

        Args:
            messages: Chat messages to send.
            start_level: Skip models below this level (0-indexed).
        """
        raw = None
        for level, model in enumerate(self.model_chain):
            if level < start_level:
                continue
            raw = self._call_ollama(model, messages)
            if raw is None:
                continue

            # Tool requests are returned immediately (caller handles them)
            if self.scorer.is_tool_request(raw):
                log.info("Model %s requested tool: %s", model, raw[:80])
                return RouteResult(
                    raw_response=raw, model_used=model, escalation_level=level,
                )

            # Check quality
            if not self.scorer.should_escalate(raw):
                log.info("Model %s accepted (score=%d)", model, self.scorer.score(raw))
                return RouteResult(
                    raw_response=raw, model_used=model, escalation_level=level,
                )

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
