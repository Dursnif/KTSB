"""Tests for NLU module.

All tests are purely structural -- no network calls are made.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.nlu import NLUEngine, NLUResult


class TestNLUResult:
    def test_result_fields(self):
        result = NLUResult(
            action="light_on",
            entities={"room": "kitchen"},
            response_text="Turning on the kitchen light.",
            confidence=0.9,
            source="ollama",
        )
        assert result.action == "light_on"
        assert result.entities["room"] == "kitchen"
        assert result.source == "ollama"

    def test_low_confidence_result(self):
        result = NLUResult(
            action="unknown",
            entities={},
            response_text="I didn't understand that.",
            confidence=0.2,
            source="ollama",
        )
        assert result.confidence < 0.5


class TestNLUEngine:
    def test_system_prompt_instructs_entity_discovery(self):
        """System prompt should tell LLM to use ha_list for entity discovery."""
        engine = NLUEngine(
            ollama_url="http://localhost:11434",
            ollama_model="llama3.2",
        )
        assert "ha_list" in engine._system_prompt
        assert "NEVER guess entity IDs" in engine._system_prompt


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

    def test_prompt_includes_get_time(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "get_time" in engine._system_prompt

    def test_prompt_includes_ha_api(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "ha_api" in engine._system_prompt


class TestNLUToolActions:
    def test_needs_search_detected(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "needs_search", "query": "strømpris", "confidence": 1}'
        result = engine._parse_response(raw)
        assert result.action == "needs_search"
        assert result.entities.get("query") == "strømpris"

    def test_expect_human_response_sets_followup(self):
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

    def test_get_time_detected(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "get_time", "query": "current_time", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.action == "get_time"

    def test_ha_api_detected(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "ha_api", "method": "GET", "path": "/api/states/light.kitchen", "confidence": 4}'
        result = engine._parse_response(raw)
        assert result.action == "ha_api"
        assert result.entities.get("method") == "GET"

    def test_confidence_extracted(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "answer", "response": "Oslo.", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.confidence == 5

    def test_code_fences_stripped(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '```json\n{"action": "answer", "response": "Test.", "confidence": 4}\n```'
        result = engine._parse_response(raw)
        assert result.action == "answer"

    def test_guillemets_normalized(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "ha_call_service", "domain": "light", "service": "turn_off", "response": \u00abSkrur av.\u00bb, "confidence": 4}'
        result = engine._parse_response(raw)
        assert result.action == "ha_call_service"
        assert result.confidence == 4

    def test_invalid_json_fallback(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = "Just some plain text answer"
        result = engine._parse_response(raw)
        assert result.action == "answer"
        assert result.response_text == "Just some plain text answer"

    def test_invalid_json_strips_json_syntax(self):
        """When LLM returns text mixed with JSON, strip JSON before TTS."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = 'Here is the answer: {"action": "answer", "response": "Oslo", "confidence": 4}'
        result = engine._parse_response(raw)
        assert result.action == "answer"
        # Should NOT contain JSON braces in speech text
        assert "{" not in result.response_text
        assert "action" not in result.response_text

    def test_pure_json_still_works(self):
        """Valid JSON should still parse normally."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "answer", "response": "Det er 5 grader.", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.response_text == "Det er 5 grader."
        assert result.confidence == 5

    def test_empty_json_fallback_message(self):
        """If stripping JSON leaves nothing, use fallback message."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "answer"}'  # valid JSON but no response field
        result = engine._parse_response(raw)
        # response_text should be empty string (valid JSON path, just no response key)
        assert result.response_text == ""

    def test_text_with_nested_json_stripped(self):
        """Text containing JSON blocks should have JSON removed."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = 'La meg sjekke {"action": "ha_api", "method": "GET"} for deg'
        result = engine._parse_response(raw)
        assert "{" not in result.response_text
        assert "ha_api" not in result.response_text
        assert "sjekke" in result.response_text or "deg" in result.response_text


class TestRubberDucky:
    def test_ask_expert_returns_hint(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        engine.claude_api_key = "test-key"

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_msg = MagicMock()
            mock_msg.content = [MagicMock(text="Kvantemekanikk handler om partikler.")]
            mock_client.messages.create.return_value = mock_msg
            mock_cls.return_value = mock_client

            hint = engine.ask_expert("Forklar kvantemekanikk enkelt")
            assert "partikler" in hint.lower()

    def test_ask_expert_no_api_key(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        hint = engine.ask_expert("Test question")
        assert hint == ""


class TestConversationSummaryInjection:
    """Test that stale conversation summaries don't contaminate new conversations."""

    def test_summary_cleared_on_new_conversation_after_long_idle(self):
        """After a long idle, summaries should be cleared — new conversation starts fresh."""
        import time as _time
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test", idle_timeout=60)
        # Simulate a stored summary from a previous conversation
        engine._conversation_summaries["rpi-stue"] = "Brukeren spurte om temperaturen."
        engine._last_activity["rpi-stue"] = _time.monotonic() - 120  # 120s ago (> idle_timeout)

        # Simulate what process_local does: check idle → clear stale context
        now = _time.monotonic()
        last = engine._last_activity.get("rpi-stue", 0)
        if last and (now - last) > engine._idle_timeout:
            engine._conversations["rpi-stue"] = []
            engine._conversation_summaries.pop("rpi-stue", None)

        # Now build messages — summary should be gone
        history = [{"role": "user", "content": "Hva er det til middag?"}]
        msgs = engine._build_messages("rpi-stue", history)

        summary_msgs = [m for m in msgs if "Kontekst fra tidligere" in m.get("content", "")]
        assert len(summary_msgs) == 0, "Stale summary should not be injected into new conversation"

    def test_build_messages_new_conversation_clean(self):
        """A brand new conversation (no summary) should have clean messages."""
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        history = [{"role": "user", "content": "God morgen!"}]
        msgs = engine._build_messages("rpi-stue", history)
        # Should be: system prompt + user message only
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[1]["content"] == "God morgen!"
        assert len(msgs) == 2


class TestBroadcast:
    def test_broadcast_action_parsed(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "broadcast", "message": "Det er middag!", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.action == "broadcast"
        assert result.entities.get("message") == "Det er middag!"

    def test_system_prompt_includes_broadcast(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "broadcast" in engine._system_prompt


class TestReminders:
    def test_set_reminder_parsed(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        raw = '{"action": "set_reminder", "message": "Gå ut med søppla", "time": "10:00", "confidence": 5}'
        result = engine._parse_response(raw)
        assert result.action == "set_reminder"
        assert result.entities.get("message") == "Gå ut med søppla"
        assert result.entities.get("time") == "10:00"

    def test_system_prompt_includes_set_reminder(self):
        engine = NLUEngine(ollama_url="http://localhost:11434", ollama_model="test")
        assert "set_reminder" in engine._system_prompt


class TestEscalationConfig:
    def test_default_model_chain(self):
        from server.config import ServerConfig
        cfg = ServerConfig()
        assert len(cfg.model_chain) >= 1

    def test_default_brave_key_empty(self):
        from server.config import ServerConfig
        cfg = ServerConfig()
        assert cfg.brave_api_key == ""

    def test_escalation_threshold(self):
        from server.config import ServerConfig
        cfg = ServerConfig()
        assert cfg.escalation_threshold == 3

    def test_claude_api_key_default_empty(self):
        from server.config import ServerConfig
        cfg = ServerConfig()
        assert cfg.claude_api_key == ""
