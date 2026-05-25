"""Tests for escalation scoring and routing logic."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from server.escalation import EscalationScorer, EscalationRouter, RouteResult


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

    def test_get_time_is_tool(self):
        raw = '{"action": "get_time", "query": "current_time", "confidence": 5}'
        assert self.scorer.is_tool_request(raw)

    def test_ha_api_is_tool(self):
        raw = '{"action": "ha_api", "method": "GET", "path": "/api/states/light.kitchen", "confidence": 4}'
        assert self.scorer.is_tool_request(raw)

    def test_answer_is_not_tool(self):
        raw = '{"action": "answer", "response": "Hei!", "confidence": 5}'
        assert not self.scorer.is_tool_request(raw)


class TestEscalationRouter:
    def test_first_model_succeeds_no_escalation(self):
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
                messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "Vanskelig"}],
            )
            assert result.model_used == "model-12b"
            assert result.escalation_level == 1

    def test_tool_request_returned_immediately(self):
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

    def test_all_models_fail_returns_last(self):
        router = EscalationRouter(
            model_chain=["model-4b", "model-12b"],
            ollama_url="http://localhost:11434",
            ollama_timeout=30,
            scorer=EscalationScorer(threshold=3),
        )
        bad = '{"action": "unknown", "response": "Vet ikke.", "confidence": 1}'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": bad}}

        with patch("server.escalation.requests.post", return_value=mock_resp):
            result = router.route(
                messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "Umulig"}],
            )
            assert result.model_used == "model-12b"
            assert result.escalation_level == 1
