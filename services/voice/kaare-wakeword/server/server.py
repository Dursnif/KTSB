"""WebSocket voice server.

Accepts audio streams from satellites and orchestrates the
STT -> NLU -> TTS pipeline. Each satellite connection gets its own
handler coroutine.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import re

import numpy as np

from server.config import ServerConfig
from server.stt import WhisperSTT, STTResult
from server.nlu import NLUEngine, NLUResult, _strip_code_fences
from server.tts import PiperTTS
from server.escalation import EscalationRouter, EscalationScorer
from server.websearch import BraveSearcher
from server.tools.time_tool import TimeTool
from server.tools.ha_tool import HomeAssistantTool
from server.ha import HomeAssistantClient
from server.sonos import SonosOutput
from server.registry import SatelliteRegistry
from server.reminders import ReminderScheduler, parse_reminder_time

log = logging.getLogger(__name__)

_ESCALATION_FEEDBACK = {
    "needs_search": "Vent litt, jeg sjekker på nett...",
    "needs_help": "Hmm, la meg spørre en kollega...",
    "escalation": "La meg tenke litt hardere på det...",
    "claude": "Jeg henter inn eksperthjelp...",
}

_MAX_TOOL_CALLS = 3

# Pre-NLU shortcuts: bypass LLM entirely for simple, deterministic queries.
# This prevents the LLM from guessing wrong actions (e.g. ha_api for time).
_TIME_PATTERN = re.compile(
    r'\b(klokk[ea]|tid[ea]n?|what time|hva er klokk|hvor mye er klokk'
    r'|current time)\b',
    re.IGNORECASE,
)
_DATE_PATTERN = re.compile(
    r'\b(dato|hvilken dag|what day|what date|hva er dato'
    r'|hvilken dato)\b',
    re.IGNORECASE,
)
_DISMISS_PATTERN = re.compile(
    r'\b(ok(?:ay)?|stopp|mottatt|slutt|dismiss)\b',
    re.IGNORECASE,
)


class SessionLogger:
    """Logs raw debug data per satellite session to JSONL files."""

    def __init__(self, log_dir: str = "logs"):
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, satellite_id: str) -> Path:
        safe = satellite_id.replace("/", "_").replace(" ", "_")
        return self._dir / f"{safe}.jsonl"

    def log(self, satellite_id: str, event: str, data: dict) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "satellite": satellite_id,
            "event": event,
            **data,
        }
        with open(self._path(satellite_id), "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


@dataclass
class PipelineResult:
    """Result of processing an utterance."""
    transcript: STTResult
    nlu: NLUResult
    tts_audio: bytes
    feedback_audio: list[bytes] | None = None


class ServerPipeline:
    """STT -> Escalation Router -> Tool Dispatch -> TTS pipeline.

    Orchestrates STT, multi-model NLU with escalation, tool dispatch
    (web search, rubber ducky, time, HA API), and TTS.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self._stt = WhisperSTT(
            model_size=config.whisper_model,
            language=config.whisper_language,
        )
        self._nlu = NLUEngine(
            ollama_url=config.ollama_url,
            ollama_model=config.ollama_model,
            ollama_timeout=config.ollama_timeout,
            claude_api_key=config.claude_api_key,
            system_prompt_file=config.system_prompt_file,
        )
        self._tts = PiperTTS(voice=config.piper_voice)
        self._router = EscalationRouter(
            model_chain=list(config.model_chain),
            ollama_url=config.ollama_url,
            ollama_timeout=config.ollama_timeout,
            scorer=EscalationScorer(threshold=config.escalation_threshold),
        )
        self._searcher = BraveSearcher(api_key=config.brave_api_key)
        self._time_tool = TimeTool()
        self._ha_tool = HomeAssistantTool(url=config.ha_url, token=config.ha_token)

        self._sonos = self._init_sonos(config)

        self._session_log = SessionLogger()
        self._reminders = ReminderScheduler()

        # Pre-cache feedback TTS on first use
        self._feedback_cache: dict[str, bytes] = {}

    def _init_sonos(self, config: ServerConfig) -> SonosOutput | None:
        """Initialize Sonos output if configured."""
        if not (config.sonos_enabled and config.ha_token):
            return None
        from server.sonos import load_sonos_config
        speakers, satellites = {}, {}
        if config.sonos_config_file:
            speakers, satellites = load_sonos_config(config.sonos_config_file)
        sonos = SonosOutput(
            ha_url=config.ha_url,
            ha_token=config.ha_token,
            speakers=speakers or None,
            satellites=satellites or None,
            volume=config.sonos_volume,
            tts_script=config.sonos_tts_script,
            broadcast_script=config.sonos_broadcast_script,
        )
        log.info("Sonos output enabled (via HA scripts)")
        return sonos

    def _synthesize_and_play(self, text: str, satellite_id: str = "default") -> bytes:
        """Synthesize TTS and also play on Sonos if configured."""
        audio = self._tts.synthesize(text)
        if self._sonos and text.strip():
            self._sonos.play_tts(text, satellite_id)
        return audio

    def _get_feedback_audio(self, key: str) -> bytes:
        """Get pre-cached TTS audio for escalation feedback."""
        if key not in self._feedback_cache:
            text = _ESCALATION_FEEDBACK.get(key, "")
            if text:
                self._feedback_cache[key] = self._tts.synthesize(text)
            else:
                self._feedback_cache[key] = b""
        return self._feedback_cache[key]

    @staticmethod
    def _extract_entity_ids(entities: dict) -> list[str]:
        """Extract entity IDs from NLU entities dict.

        LLMs use inconsistent keys: entity_id, entities, entity_ids.
        This handles all variants.
        """
        # Try list keys first
        for key in ("entities", "entity_ids"):
            ids = entities.get(key, [])
            if isinstance(ids, list) and ids:
                return ids
        # Fall back to single entity_id
        eid = entities.get("entity_id", "")
        return [eid] if eid else []

    def _execute_ha_service(self, nlu_result: NLUResult, satellite_id: str) -> int:
        """Actually execute ha_call_service against Home Assistant.

        Returns number of successfully executed entity calls.
        """
        entities = nlu_result.entities
        domain = entities.get("domain", "")
        service = entities.get("service", "")
        if not domain or not service:
            log.warning("ha_call_service missing domain/service: %s", entities)
            return 0

        entity_ids = self._extract_entity_ids(entities)
        success_count = 0

        for eid in entity_ids:
            # Auto-fix missing domain prefix (LLM often omits it)
            if "." not in eid and domain:
                eid = f"{domain}.{eid}"

            try:
                result = self._ha_tool.handle({
                    "method": "POST",
                    "path": f"/api/services/{domain}/{service}",
                    "body": {"entity_id": eid},
                })
                # handle() returns error strings on failure instead of raising
                if "Feil" in result or "error" in result.lower():
                    log.warning("HA call returned error for %s: %s", eid, result[:200])
                    self._session_log.log(satellite_id, "ha_exec_error", {
                        "entity_id": eid, "error": result[:200],
                    })
                else:
                    log.info("HA executed: %s.%s -> %s", domain, service, eid)
                    self._session_log.log(satellite_id, "ha_executed", {
                        "domain": domain, "service": service, "entity_id": eid,
                    })
                    success_count += 1
            except Exception as exc:
                log.warning("HA execution failed for %s: %s", eid, exc)
                self._session_log.log(satellite_id, "ha_exec_error", {
                    "entity_id": eid, "error": str(exc),
                })
        return success_count

    def _dispatch_tool(self, action: str, parsed: dict) -> str:
        """Dispatch a tool action and return the result text."""
        if action == "needs_search":
            query = parsed.get("query", "")
            results = self._searcher.search(query)
            return self._searcher.format_context(results)
        elif action == "needs_help":
            question = parsed.get("question", "")
            return self._nlu.ask_expert(question)
        elif action == "get_time":
            return self._time_tool.handle(parsed)
        elif action == "ha_api":
            return self._ha_tool.handle(parsed)
        elif action == "ha_list":
            return self._ha_tool.handle_list(parsed)
        return ""

    def _validate_ha_call(self, nlu_result: NLUResult, satellite_id: str) -> NLUResult:
        """Validate entity IDs in ha_call_service. Auto-search if unknown."""
        entities = nlu_result.entities
        entity_ids = self._extract_entity_ids(entities)

        if not entity_ids:
            return nlu_result

        # Auto-fix missing domain prefix
        domain = entities.get("domain", "")
        fixed_ids = []
        for eid in entity_ids:
            if "." not in eid and domain:
                fixed_ids.append(f"{domain}.{eid}")
            else:
                fixed_ids.append(eid)
        entity_ids = fixed_ids

        # Check if entities actually exist
        known = self._ha_tool._fetch_entities()
        known_ids = {e["entity_id"] for e in known}
        valid = [eid for eid in entity_ids if eid in known_ids]
        invalid = [eid for eid in entity_ids if eid not in known_ids]

        if not invalid:
            # All valid — update entity IDs in result (may have been prefix-fixed)
            if entity_ids != self._extract_entity_ids(entities):
                # Write fixed IDs back
                if len(entity_ids) == 1:
                    nlu_result.entities["entity_id"] = entity_ids[0]
                else:
                    nlu_result.entities["entities"] = entity_ids
            return nlu_result

        # Some/all entity IDs are guessed. Auto-search and re-prompt.
        domain = entities.get("domain", "")
        # Build search query from the original user message
        history = self._nlu._conversations.get(satellite_id, [])
        user_text = ""
        for msg in reversed(history):
            if msg["role"] == "user" and not msg["content"].startswith("["):
                user_text = msg["content"]
                break

        self._session_log.log(satellite_id, "ha_validate_fail", {
            "invalid_ids": invalid, "searching": True,
        })

        # Search HA for matching entities
        search_result = self._ha_tool.handle_list({
            "query": user_text, "domain": domain,
        })
        if not search_result or "Ingen enheter" in search_result:
            # Broaden search without domain filter
            search_result = self._ha_tool.handle_list({"query": user_text})

        self._session_log.log(satellite_id, "ha_auto_search", {
            "query": user_text, "domain": domain,
            "result": search_result[:500],
        })

        # Inject search results and re-prompt
        history.append({
            "role": "user",
            "content": (
                f"[De entity-IDene du foreslo finnes ikke: {', '.join(invalid)}. "
                f"Her er ekte enheter fra Home Assistant:\n{search_result}\n"
                f"Bruk KUN entity_id-er fra listen over.]"
            ),
        })

        try:
            import requests as req
            messages = self._nlu._build_messages(satellite_id, history)
            resp = req.post(
                f"{self._nlu.ollama_url}/api/chat",
                json={
                    "model": self._nlu.ollama_model,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_ctx": 4096},
                },
                timeout=self._nlu.ollama_timeout,
            )
            resp.raise_for_status()
            raw = _strip_code_fences(
                resp.json().get("message", {}).get("content", "")
            )
            history.append({"role": "assistant", "content": raw})
            corrected = self._nlu._parse_response(raw)
            self._session_log.log(satellite_id, "ha_corrected", {
                "raw": raw[:500], "action": corrected.action,
                "entities": corrected.entities,
            })
            return corrected
        except Exception as exc:
            log.warning("HA validation re-prompt failed: %s", exc)
            return NLUResult(
                action="error", entities={}, confidence=0.0, source="ollama",
                response_text="Beklager, jeg fant ikke riktige enheter.",
            )

    def _check_pre_nlu_shortcuts(
        self, text_lower: str, satellite_id: str, sl: SessionLogger, transcript: STTResult,
    ) -> PipelineResult | None:
        """Check for pre-NLU shortcuts (time, date, dismiss). Returns result or None."""
        if _TIME_PATTERN.search(text_lower):
            response = self._time_tool.handle({"query": "current_time"})
            log.info("Pre-NLU shortcut: time -> %s", response)
            sl.log(satellite_id, "shortcut", {"type": "time", "response": response})
        elif _DATE_PATTERN.search(text_lower):
            response = self._time_tool.handle({"query": "current_date"})
            log.info("Pre-NLU shortcut: date -> %s", response)
            sl.log(satellite_id, "shortcut", {"type": "date", "response": response})
        elif _DISMISS_PATTERN.search(text_lower) and self._reminders.has_active_reminder():
            dismissed = self._reminders.dismiss_most_recent()
            if not dismissed:
                return None
            response = "Påminnelsen er avvist."
            log.info("Pre-NLU shortcut: dismiss reminder")
            sl.log(satellite_id, "shortcut", {"type": "dismiss_reminder"})
        else:
            return None

        nlu_result = NLUResult(
            action="answer", entities={}, response_text=response,
            confidence=1.0, source="shortcut",
        )
        tts_audio = self._synthesize_and_play(response)
        return PipelineResult(transcript=transcript, nlu=nlu_result, tts_audio=tts_audio)

    def _handle_broadcast(
        self, nlu_result: NLUResult, satellite_id: str, sl: SessionLogger, transcript: STTResult,
    ) -> PipelineResult:
        """Handle broadcast action: send message to all satellites and Sonos."""
        message = nlu_result.entities.get("message", nlu_result.response_text)
        sl.log(satellite_id, "broadcast", {"message": message})

        tts_audio = self._tts.synthesize(message)
        if hasattr(self, '_registry') and self._registry:
            self._registry.broadcast_audio(tts_audio)
        if self._sonos:
            self._sonos.broadcast(message)

        confirmation = f"Jeg har sendt beskjeden: {message}"
        confirm_audio = self._tts.synthesize(confirmation)
        return PipelineResult(
            transcript=transcript, nlu=nlu_result, tts_audio=confirm_audio,
        )

    def _handle_set_reminder(
        self, nlu_result: NLUResult, satellite_id: str, sl: SessionLogger, transcript: STTResult,
    ) -> PipelineResult:
        """Handle set_reminder action: schedule a reminder."""
        message = nlu_result.entities.get("message", "")
        time_str = nlu_result.entities.get("time", "")
        trigger_at = parse_reminder_time(time_str)
        if trigger_at and message:
            self._reminders.add_reminder(
                message=message,
                trigger_at=trigger_at,
                satellite_id=satellite_id,
            )
            import datetime as _dt
            dt = _dt.datetime.fromtimestamp(trigger_at)
            response = f"Jeg minner deg på det klokka {dt.strftime('%H:%M')}."
            sl.log(satellite_id, "reminder_set", {
                "message": message, "trigger_at": trigger_at,
            })
        else:
            response = "Beklager, jeg forstod ikke tidspunktet."
        nlu_result.response_text = response
        tts_audio = self._synthesize_and_play(response)
        return PipelineResult(transcript=transcript, nlu=nlu_result, tts_audio=tts_audio)

    def _handle_ha_call(
        self, nlu_result: NLUResult, satellite_id: str, sl: SessionLogger, transcript: STTResult,
    ) -> tuple[PipelineResult | None, NLUResult, int]:
        """Handle ha_call_service: validate and execute. Returns (result, nlu_result, ha_count)."""
        nlu_result = self._validate_ha_call(nlu_result, satellite_id)
        ha_executed_count = 0
        if nlu_result.action == "ha_call_service":
            ha_executed_count = self._execute_ha_service(nlu_result, satellite_id)
        if nlu_result.action in ("answer", "ha_call_service", "error"):
            sl.log(satellite_id, "final", {
                "action": nlu_result.action, "response": nlu_result.response_text,
                "model": self._nlu.ollama_model, "success": ha_executed_count > 0,
                "ha_executed": ha_executed_count,
            })
            tts_audio = self._synthesize_and_play(nlu_result.response_text)
            return (
                PipelineResult(transcript=transcript, nlu=nlu_result, tts_audio=tts_audio),
                nlu_result,
                ha_executed_count,
            )
        return None, nlu_result, ha_executed_count

    def process_text(self, text: str, satellite_id: str = "cli-debug") -> PipelineResult:
        """Process text directly (skip STT). Used for CLI debugging."""
        transcript = STTResult(text=text, language="no", confidence=1.0)
        return self._process_transcript(transcript, satellite_id)

    def process(self, audio: np.ndarray, satellite_id: str = "default") -> PipelineResult:
        """Process an utterance through the full pipeline with escalation."""
        transcript = self._stt.transcribe(audio)
        log.info("Transcript: %s", transcript.text)

        # Skip empty/garbage transcripts (false wake, silence, noise)
        text = transcript.text.strip()
        if not text or text in ("<|nocaptions|>", "<|nospeech|>", "<|endoftext|>"):
            log.info("Empty/special transcript '%s' — ignoring (likely false wake)", text)
            return PipelineResult(
                transcript=transcript,
                nlu=NLUResult(
                    action="silence", entities={},
                    response_text="", confidence=0.0, source="skip",
                ),
                tts_audio=b"",
            )

        return self._process_transcript(transcript, satellite_id)

    def _process_transcript(
        self, transcript: STTResult, satellite_id: str,
    ) -> PipelineResult:
        """Core pipeline: NLU -> Tool dispatch -> TTS."""
        sl = self._session_log
        sl.log(satellite_id, "input", {
            "text": transcript.text, "language": transcript.language,
        })

        # Pre-NLU shortcuts: handle time/date/dismiss without LLM
        text_lower = transcript.text.lower()
        shortcut = self._check_pre_nlu_shortcuts(text_lower, satellite_id, sl, transcript)
        if shortcut:
            return shortcut

        nlu_result = self._nlu.process_local(
            transcript.text, transcript.language, satellite_id=satellite_id,
        )
        # Grab raw LLM output for training data
        history = self._nlu._conversations.get(satellite_id, [])
        raw_llm = history[-1]["content"] if history and history[-1]["role"] == "assistant" else ""
        sl.log(satellite_id, "nlu", {
            "action": nlu_result.action,
            "entities": nlu_result.entities,
            "response": nlu_result.response_text,
            "confidence": nlu_result.confidence,
            "model": self._nlu.ollama_model,
            "raw_response": raw_llm,
        })
        ha_executed_count = 0

        # Escalate if the primary model gave up or has low confidence
        if nlu_result.action == "unknown" or nlu_result.confidence < 3:
            log.info(
                "Escalating: action=%s confidence=%.1f",
                nlu_result.action, nlu_result.confidence,
            )
            sl.log(satellite_id, "escalating", {
                "reason": nlu_result.action,
                "confidence": nlu_result.confidence,
                "primary_model": self._nlu.ollama_model,
            })
            messages = self._nlu._build_messages(satellite_id, history)
            route_result = self._router.route(messages, start_level=1)
            if route_result.raw_response:
                escalated = self._nlu._parse_response(route_result.raw_response)
                # Only use escalated result if it's actually better
                if escalated.action != "unknown" or escalated.confidence > nlu_result.confidence:
                    nlu_result = escalated
                    nlu_result.source = f"escalated:{route_result.model_used}"
                    # Update conversation history with better response
                    if history and history[-1]["role"] == "assistant":
                        history[-1]["content"] = route_result.raw_response
                    raw_llm = route_result.raw_response
                    log.info(
                        "Escalation succeeded: model=%s action=%s",
                        route_result.model_used, nlu_result.action,
                    )
                    sl.log(satellite_id, "escalated", {
                        "model": route_result.model_used,
                        "level": route_result.escalation_level,
                        "action": nlu_result.action,
                        "response": nlu_result.response_text,
                        "confidence": nlu_result.confidence,
                    })

        # LLM decided this wasn't directed at the assistant — stay silent
        if nlu_result.action == "ignore":
            log.info("LLM chose to ignore: %s", nlu_result.entities.get("reason", "not directed at assistant"))
            sl.log(satellite_id, "ignored", {
                "transcript": transcript.text,
                "reason": nlu_result.entities.get("reason", ""),
            })
            return PipelineResult(
                transcript=transcript, nlu=nlu_result, tts_audio=b"",
            )

        # If NLU handled it directly (clear command, error), return as-is
        if nlu_result.action in ("answer", "error", "unknown"):
            sl.log(satellite_id, "final", {
                "action": nlu_result.action, "response": nlu_result.response_text,
                "model": nlu_result.source, "success": nlu_result.action == "answer",
            })
            tts_audio = self._synthesize_and_play(nlu_result.response_text)
            return PipelineResult(
                transcript=transcript, nlu=nlu_result, tts_audio=tts_audio,
            )

        # Action handlers
        if nlu_result.action == "broadcast":
            return self._handle_broadcast(nlu_result, satellite_id, sl, transcript)

        if nlu_result.action == "set_reminder":
            return self._handle_set_reminder(nlu_result, satellite_id, sl, transcript)

        if nlu_result.action == "ha_call_service":
            result, nlu_result, ha_executed_count = self._handle_ha_call(
                nlu_result, satellite_id, sl, transcript,
            )
            if result:
                return result

        # Tool dispatch loop
        feedback_clips: list[bytes] = []
        tool_calls = 0
        current_result = nlu_result

        while tool_calls < _MAX_TOOL_CALLS:
            action = current_result.action

            if action == "expect_human_response":
                break

            if action not in ("needs_search", "needs_help", "get_time", "ha_api", "ha_list"):
                break

            # Generate feedback audio
            feedback = self._get_feedback_audio(action)
            if feedback:
                feedback_clips.append(feedback)

            # Dispatch tool
            try:
                last_content = self._nlu._conversations.get(satellite_id, [{}])[-1].get("content", "{}")
                if isinstance(last_content, dict):
                    parsed = last_content
                else:
                    parsed = json.loads(_strip_code_fences(last_content))
            except (json.JSONDecodeError, IndexError, TypeError):
                parsed = current_result.entities

            tool_result = self._dispatch_tool(action, {**current_result.entities, **parsed})
            tool_calls += 1
            log.info("Tool %s returned: %s", action, tool_result[:100])
            sl.log(satellite_id, "tool_call", {
                "action": action, "params": {**current_result.entities, **parsed},
                "result": tool_result[:500],
            })

            if not tool_result:
                break

            # Inject tool result back into conversation and re-prompt
            history = self._nlu._conversations.get(satellite_id, [])
            history.append({
                "role": "user",
                "content": f"[Verktøyresultat for {action}: {tool_result}]",
            })

            try:
                import requests as req
                messages = self._nlu._build_messages(satellite_id, history)
                resp = req.post(
                    f"{self._nlu.ollama_url}/api/chat",
                    json={
                        "model": self._nlu.ollama_model,
                        "messages": messages,
                        "stream": False,
                        "options": {"num_ctx": 4096},
                    },
                    timeout=self._nlu.ollama_timeout,
                )
                resp.raise_for_status()
                raw = _strip_code_fences(
                    resp.json().get("message", {}).get("content", "")
                )
                history.append({"role": "assistant", "content": raw})
                current_result = self._nlu._parse_response(raw)
                log.info("Re-prompt result: action=%s", current_result.action)
                sl.log(satellite_id, "re_prompt", {
                    "raw_response": raw,
                    "action": current_result.action,
                    "entities": current_result.entities,
                    "response": current_result.response_text,
                    "confidence": current_result.confidence,
                    "model": self._nlu.ollama_model,
                })
            except Exception as exc:
                log.warning("Re-prompt after tool failed: %s", exc)
                break

        # If the tool loop ended with ha_call_service, validate and execute
        if current_result.action == "ha_call_service":
            current_result = self._validate_ha_call(current_result, satellite_id)
            if current_result.action == "ha_call_service":
                ha_executed_count = self._execute_ha_service(current_result, satellite_id)

        # TTS for final response
        sl.log(satellite_id, "final", {
            "action": current_result.action,
            "response": current_result.response_text,
            "confidence": current_result.confidence,
            "model": self._nlu.ollama_model,
            "tool_calls": tool_calls,
            "success": current_result.action in ("answer", "expect_human_response") or ha_executed_count > 0,
            "ha_executed": ha_executed_count,
        })
        tts_audio = self._synthesize_and_play(current_result.response_text)

        return PipelineResult(
            transcript=transcript,
            nlu=current_result,
            tts_audio=tts_audio,
            feedback_audio=feedback_clips if feedback_clips else None,
        )


class VoiceServer:
    """Async WebSocket server for voice satellites.

    Each connected satellite gets a dedicated handler that accumulates
    audio, runs STT on end-of-utterance, processes the transcript
    through NLU, and sends back TTS audio.

    Args:
        config: Server configuration.
    """

    def __init__(
        self,
        config: ServerConfig,
        pipeline: ServerPipeline | None = None,
        preload: bool = True,
    ):
        self.config = config
        self._pipeline = pipeline
        self._preload = preload
        self._server = None
        self._port: int | None = None
        self._stop_event = asyncio.Event()
        self._registry = SatelliteRegistry()

        # Wake clip review directories
        self._review_dir = Path(__file__).resolve().parent.parent / 'data'
        self._pos_review = self._review_dir / 'positive_to_review'
        self._neg_review = self._review_dir / 'negative_to_review'
        self._pos_review.mkdir(parents=True, exist_ok=True)
        self._neg_review.mkdir(parents=True, exist_ok=True)

    def _save_wake_clip(self, audio: np.ndarray, confirmed: bool, transcript: str) -> None:
        """Save wake word clip for training review."""
        import datetime
        import wave as _wave
        import io as _io

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        safe_text = transcript[:30].replace(' ', '_').replace('/', '') if transcript else 'empty'

        if confirmed:
            out_dir = self._pos_review
            fname = f'wake_pos_{ts}_{safe_text}.wav'
        else:
            out_dir = self._neg_review
            fname = f'wake_neg_{ts}_{safe_text}.wav'

        # Trim to 1.5s centered
        clip_samples = int(1.5 * 16000)
        if len(audio) > clip_samples:
            center = len(audio) // 2
            half = clip_samples // 2
            audio = audio[max(0, center - half):center + half]

        # Write 16-bit WAV
        int16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)
        buf = _io.BytesIO()
        with _wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(int16.tobytes())

        (out_dir / fname).write_bytes(buf.getvalue())
        log.info('Saved wake clip: %s (%s)', fname, 'confirmed' if confirmed else 'rejected')

    @property
    def port(self) -> int:
        """Actual port the server is listening on."""
        if self._port is not None:
            return self._port
        return self.config.port

    async def _send_pipeline_result(self, websocket, result: PipelineResult, satellite_id: str) -> None:
        """Send feedback clips, transcript, intent, TTS audio, and listen/done to satellite."""
        # Feedback audio clips (escalation transparency)
        if result.feedback_audio:
            for clip in result.feedback_audio:
                if clip:
                    await websocket.send(json.dumps({
                        "type": "audio_response",
                        "payload": base64.b64encode(clip).decode("ascii"),
                        "sample_rate": 22050,
                        "is_last": False,
                    }))

        # Transcript
        await websocket.send(json.dumps({
            "type": "transcript",
            "text": result.transcript.text,
            "is_final": True,
            "confidence": result.transcript.confidence,
            "language": result.transcript.language,
        }))

        # Intent (skip on error)
        if result.nlu.action != "error":
            await websocket.send(json.dumps({
                "type": "intent",
                "action": result.nlu.action,
                "entities": result.nlu.entities,
                "response_text": result.nlu.response_text,
                "confidence": result.nlu.confidence,
            }))

        # TTS audio
        if result.tts_audio:
            log.info(
                "Sending %d bytes TTS audio to %s",
                len(result.tts_audio), satellite_id,
            )
            await websocket.send(json.dumps({
                "type": "audio_response",
                "payload": base64.b64encode(result.tts_audio).decode("ascii"),
                "sample_rate": 22050,
                "is_last": True,
            }))

        # Follow-up listening
        if result.nlu.expects_followup:
            await websocket.send(json.dumps({
                "type": "listen",
                "timeout_s": 10,
            }))

        # Done
        await websocket.send(json.dumps({"type": "done"}))

    async def _handle_client(self, websocket) -> None:
        """Handle a single satellite connection."""
        satellite_id = "unknown"
        audio_chunks: list[bytes] = []
        pre_roll_ms: float = 0

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Invalid JSON from client")
                    continue

                msg_type = msg.get("type")

                if msg_type == "register":
                    satellite_id = msg.get("satellite_id", "unknown")
                    room = msg.get("room", "unknown")
                    http_port = msg.get("http_port", 8080)
                    peer = websocket.remote_address
                    ip = peer[0] if peer else "unknown"
                    self._registry.register(satellite_id, room, ip, http_port)
                    log.info("Satellite registered via WebSocket: %s (%s:%d)", satellite_id, ip, http_port)

                elif msg_type == "verify_wake":
                    # STT verification of wake word audio
                    wake_sid = msg.get("satellite_id", satellite_id)
                    wake_audio = base64.b64decode(msg["audio"])
                    wake_array = np.frombuffer(wake_audio, dtype=np.float32)
                    log.info("Wake verify request from %s (%.1fs audio)", wake_sid, len(wake_array) / 16000)

                    async def _verify_wake(audio, ws):
                        try:
                            result = await asyncio.to_thread(self._pipeline._stt.transcribe, audio)
                            text = result.text.strip().lower()
                            # Accept various spellings/transcriptions of "Kåre"
                            confirmed = any(w in text for w in ("kåre", "kare", "kaare", "kåra", "kåren"))
                            log.info("Wake verify result: '%s' -> %s", result.text.strip(), "CONFIRMED" if confirmed else "REJECTED")

                            # Save clip for training review
                            self._save_wake_clip(audio, confirmed, result.text.strip())

                            await ws.send(json.dumps({
                                "type": "wake_result",
                                "confirmed": confirmed,
                                "transcript": result.text.strip(),
                            }))
                        except Exception as exc:
                            log.warning("Wake verify failed: %s — confirming by default", exc)
                            await ws.send(json.dumps({
                                "type": "wake_result",
                                "confirmed": True,
                                "transcript": "",
                            }))

                    asyncio.ensure_future(_verify_wake(wake_array, websocket))

                elif msg_type == "audio_start":
                    satellite_id = msg.get("satellite_id", "unknown")
                    pre_roll_ms = float(msg.get("pre_roll_ms", 0))
                    audio_chunks = []
                    log.info("Audio stream started from %s (pre_roll=%dms)", satellite_id, pre_roll_ms)

                elif msg_type == "audio_chunk":
                    payload = base64.b64decode(msg["payload"])
                    audio_chunks.append(payload)

                elif msg_type == "text_input":
                    # Debug: inject text directly, skip STT
                    satellite_id = msg.get("satellite_id", "cli-debug")
                    text = msg.get("text", "")
                    log.info("Text input from %s: %s", satellite_id, text)

                    if self._pipeline and text:
                        result = await asyncio.to_thread(
                            self._pipeline.process_text, text, satellite_id,
                        )
                        await self._send_pipeline_result(websocket, result, satellite_id)

                elif msg_type == "audio_end":
                    reason = msg.get("reason", "unknown")
                    log.info(
                        "Audio stream ended from %s (reason=%s, chunks=%d)",
                        satellite_id, reason, len(audio_chunks),
                    )
                    if audio_chunks and reason != "cancel":
                        # Concatenate all audio
                        all_audio = b"".join(audio_chunks)
                        audio_array = np.frombuffer(all_audio, dtype=np.float32)

                        # Strip pre-roll (contains wake word, confuses STT)
                        pre_roll_samples = int(pre_roll_ms * 16)
                        if pre_roll_samples > 0 and len(audio_array) > pre_roll_samples:
                            audio_array = audio_array[pre_roll_samples:]

                        log.info(
                            "Received %.1fs of audio from %s (pre-roll %.0fms stripped)",
                            len(audio_array) / 16000, satellite_id, pre_roll_ms,
                        )

                        if self._pipeline:
                            result = await asyncio.to_thread(
                                self._pipeline.process, audio_array, satellite_id,
                            )
                            await self._send_pipeline_result(websocket, result, satellite_id)
                        else:
                            # Placeholder (no pipeline configured)
                            await websocket.send(json.dumps({
                                "type": "transcript",
                                "text": f"[placeholder] received {len(audio_array)} samples",
                                "is_final": True,
                                "confidence": 0.0,
                                "language": "en",
                            }))
                    audio_chunks = []

        except Exception:
            log.exception("Error handling satellite %s", satellite_id)
        finally:
            if satellite_id != "unknown":
                self._registry.unregister(satellite_id)

    async def _on_reminder(self, message: str, reminder_id: int) -> None:
        """Deliver a triggered reminder to all outputs."""
        text = f"Påminnelse: {message}"
        tts_audio = self._pipeline._tts.synthesize(text)
        if self._registry:
            self._registry.broadcast_audio(tts_audio)
        if self._pipeline._sonos:
            self._pipeline._sonos.broadcast(text)
        if self._pipeline._ha_tool:
            try:
                self._pipeline._ha_tool.handle({
                    "method": "POST",
                    "path": "/api/services/notify/notify",
                    "body": {"message": text, "title": "Kåre påminnelse"},
                })
            except Exception as exc:
                log.warning("HA notify failed: %s", exc)
        log.info("Reminder delivered: %s", message)

    async def start(self) -> None:
        """Start the server."""
        import websockets

        self._server = await websockets.serve(
            self._handle_client,
            self.config.host,
            self.config.port,
            max_size=10 * 1024 * 1024,  # 10MB — audio chunks can be large
            ping_interval=60,   # ping every 60s (default 20s too aggressive)
            ping_timeout=120,   # allow 120s before timeout
        )
        # Record actual port (useful when port=0)
        for sock in self._server.sockets:
            addr = sock.getsockname()
            self._port = addr[1]
            break

        log.info("Voice server listening on %s:%d", self.config.host, self.port)

        # Preload Ollama model in background (non-blocking)
        if self._preload and self._pipeline:
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, self._pipeline._nlu.preload_model)

        # Start reminder poll loop
        if self._pipeline and hasattr(self._pipeline, '_reminders'):
            asyncio.create_task(
                self._pipeline._reminders.run_poll_loop(self._on_reminder)
            )

        await self._stop_event.wait()

    def stop(self) -> None:
        """Signal the server to stop."""
        self._stop_event.set()
        if self._server:
            self._server.close()


def main() -> None:
    """Entry point for voice server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Voice satellite server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--whisper-language", default=None, help="Force language (e.g. 'no')")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--ollama-model", default="hf.co/NbAiLab/borealis-4b-instruct-preview-gguf:Q8_0")
    parser.add_argument("--ollama-timeout", type=int, default=120, help="Ollama timeout in seconds")
    parser.add_argument("--system-prompt-file", default=None, help="Path to custom system prompt")
    parser.add_argument("--model-chain", nargs="+",
        default=["hf.co/NbAiLab/borealis-4b-instruct-preview-gguf:Q8_0", "borealis-12b:latest", "borealis-27b:latest"],
        help="Model escalation chain (weakest to strongest)")
    parser.add_argument("--escalation-threshold", type=int, default=3, help="Minimum score to accept (1-5)")
    parser.add_argument("--brave-api-key", default="", help="Brave Search API key for web search")
    parser.add_argument("--claude-api-key", default="", help="Anthropic API key for rubber ducky / cloud fallback")
    parser.add_argument("--piper-voice", default="en_US-lessac-medium")
    parser.add_argument("--ha-url", default="http://homeassistant.local:8123", help="Home Assistant URL")
    parser.add_argument("--ha-token", default="", help="Home Assistant long-lived access token")
    parser.add_argument("--no-pipeline", action="store_true", help="Disable STT/NLU/TTS pipeline")
    parser.add_argument("--no-preload", action="store_true", help="Skip preloading Ollama model")
    args = parser.parse_args()

    config = ServerConfig(
        host=args.host,
        port=args.port,
        whisper_model=args.whisper_model,
        whisper_language=args.whisper_language,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
        ollama_timeout=args.ollama_timeout,
        system_prompt_file=args.system_prompt_file,
        model_chain=tuple(args.model_chain),
        escalation_threshold=args.escalation_threshold,
        brave_api_key=args.brave_api_key,
        claude_api_key=args.claude_api_key,
        piper_voice=args.piper_voice,
        ha_url=args.ha_url,
        ha_token=args.ha_token,
    )
    pipeline = None if args.no_pipeline else ServerPipeline(config)

    # Verify Ollama model is available (fast check, non-blocking)
    if pipeline:
        if not pipeline._nlu.ensure_model():
            log.warning("Ollama model not available -- NLU will fail")

    server = VoiceServer(config, pipeline=pipeline, preload=not args.no_preload)
    asyncio.run(server.start())


if __name__ == "__main__":
    main()
