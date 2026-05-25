"""Natural Language Understanding.

Two-tier strategy:
1. Ollama (local, fast, free) -- try first
2. Claude API (cloud, accurate, costs money) -- fallback when user approves

The NLU prompt asks the LLM to classify the intent and extract entities
for Home Assistant actions.

System prompt can be customized via --system-prompt-file flag.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = """\
You are Kåre, a voice assistant for a smart home. The user speaks Norwegian or English.
Respond in the same language the user spoke.

You have conversation memory. The messages above are your conversation history with this user.
If the user asks what you have talked about, refers to something said earlier, or asks follow-up
questions, look at the previous messages in this conversation — DO NOT search the web for it.

You do NOT know which Home Assistant entities exist. You MUST use ha_list to discover them first.
NEVER guess entity IDs. Always search first, then act on what you find.

Always respond with valid JSON only. Always include a "confidence" field (1-5).
5 = completely certain, 3 = somewhat unsure, 1 = guessing or don't know.

IMPORTANT: For smart home commands, ALWAYS search for entities first.
Entity IDs are usually in English (e.g. "livingroom", "dinner_table", "bedroom").
Use English keywords when searching. Norwegian words are also translated automatically.
{{"action": "ha_list", "query": "livingroom dinner table", "domain": "light", "confidence": 5}}

After receiving the entity list, you can control them:
{{"action": "ha_call_service", "domain": "light", "service": "turn_on", "entity_id": "light.exact_entity_id_from_list", "response": "Skrur på lyset.", "confidence": 5}}

For general questions you can answer:
{{"action": "answer", "response": "Your helpful answer here.", "confidence": 4}}

If you need current information from the internet to answer:
{{"action": "needs_search", "query": "your search query in the relevant language", "confidence": 1}}

If the question is too complex and you need expert help:
{{"action": "needs_help", "question": "your specific question for the expert", "confidence": 1}}

If you are asking the user a question and expect a reply:
{{"action": "expect_human_response", "response": "your question to the user", "confidence": 4}}

If the user asks about time or date:
{{"action": "get_time", "query": "current_time", "confidence": 5}}

If you need to check a sensor, device state, or call a Home Assistant API:
{{"action": "ha_api", "method": "GET", "path": "/api/states/sensor.exact_entity_id", "confidence": 4}}

To search for available entities (lights, sensors, switches, etc.):
{{"action": "ha_list", "query": "search term", "domain": "light", "confidence": 5}}
The query matches entity IDs and friendly names. Domain is optional.

If the user asks to be reminded of something at a specific time:
{{"action": "set_reminder", "message": "what to remind about", "time": "10:00", "confidence": 5}}
Time formats: "HH:MM" (today/tomorrow), "+Xm" (relative minutes), "tomorrow HH:MM".

If the user asks you to broadcast/announce something to the whole house:
{{"action": "broadcast", "message": "the message to broadcast", "confidence": 5}}

For things you truly cannot do:
{{"action": "unknown", "response": "Beklager, det kan jeg ikke hjelpe med.", "confidence": 1}}
"""


_CLEAR_PATTERNS = re.compile(
    r'\b(glem alt|start på nytt|ny samtale|nullstill'
    r'|forget everything|start over|new conversation|reset)\b',
    re.IGNORECASE,
)


def _strip_code_fences(text) -> str:
    """Strip markdown code fences and normalize quotes from LLM response."""
    # Ollama sometimes returns content as dict instead of string
    if isinstance(text, dict):
        text = json.dumps(text, ensure_ascii=False)
    if not isinstance(text, str):
        text = str(text) if text else ""
    text = text.strip()
    m = re.match(r'^```(?:json)?\s*\n?(.*?)\n?\s*```$', text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # Norwegian LLMs often use guillemets instead of standard quotes
    text = text.replace('\u00ab', '"').replace('\u00bb', '"')
    # Also handle curly/smart quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    return text


@dataclass
class NLUResult:
    """NLU processing result."""
    action: str
    entities: dict
    response_text: str
    confidence: float
    source: str  # "ollama" or "claude"
    expects_followup: bool = False


class NLUEngine:
    """Two-tier NLU engine: Ollama local -> Claude cloud fallback.

    Args:
        ollama_url: Ollama API base URL.
        ollama_model: Ollama model name.
        ollama_timeout: Request timeout in seconds.
        claude_api_key: Anthropic API key (optional).
        ha_entities: List of Home Assistant entity IDs.
        system_prompt_file: Path to custom system prompt file (overrides default).
    """

    def __init__(
        self,
        ollama_url: str = "http://localhost:11434",
        ollama_model: str = "glm-4.7-flash:q8_0",
        ollama_timeout: int = 120,
        claude_api_key: str | None = None,
        ha_entities: list[str] | None = None,
        system_prompt_file: str | None = None,
        idle_timeout: float = 300.0,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model
        self.ollama_timeout = ollama_timeout
        self.claude_api_key = claude_api_key
        self._ha_entities = ha_entities or []

        # Load system prompt
        if system_prompt_file:
            path = Path(system_prompt_file)
            if path.exists():
                template = path.read_text()
                log.info("Loaded custom system prompt from %s", path)
            else:
                log.warning("System prompt file %s not found, using default", path)
                template = DEFAULT_SYSTEM_PROMPT
        else:
            template = DEFAULT_SYSTEM_PROMPT

        self._system_prompt = template.format(
            entities="\n".join(f"- {e}" for e in self._ha_entities) or "None configured"
        )

        # Conversation history per satellite (for multi-turn)
        self._conversations: dict[str, list[dict]] = {}
        self._last_activity: dict[str, float] = {}
        self._conversation_summaries: dict[str, str] = {}
        self._idle_timeout = idle_timeout

    def ensure_model(self) -> bool:
        """Verify Ollama model is available, pull if needed."""
        try:
            resp = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            base_name = self.ollama_model.split(":")[0]
            available = any(
                m == self.ollama_model or m.startswith(base_name + ":")
                for m in models
            )
            if available:
                log.info("Ollama model '%s' is available", self.ollama_model)
                return True
            log.warning(
                "Model '%s' not found. Available: %s",
                self.ollama_model,
                ", ".join(models[:10]),
            )
            return False
        except requests.RequestException as exc:
            log.error("Cannot reach Ollama at %s: %s", self.ollama_url, exc)
            return False

    def is_model_loaded(self) -> bool:
        """Check if model is already loaded in GPU memory via /api/ps."""
        try:
            resp = requests.get(f"{self.ollama_url}/api/ps", timeout=5)
            resp.raise_for_status()
            loaded = [m["name"] for m in resp.json().get("models", [])]
            return self.ollama_model in loaded
        except requests.RequestException:
            return False

    def preload_model(self) -> None:
        """Preload model into GPU memory (skips if already loaded)."""
        if self.is_model_loaded():
            log.info("Ollama model '%s' already in GPU memory", self.ollama_model)
            return
        log.info("Preloading Ollama model '%s' into GPU memory...", self.ollama_model)
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.ollama_model,
                    "prompt": "Hei",
                    "system": "Svar kort.",
                    "stream": False,
                    "options": {
                        "num_ctx": 2048,
                    },
                },
                timeout=self.ollama_timeout,
            )
            resp.raise_for_status()
            log.info("Model preloaded successfully")
        except requests.RequestException as exc:
            log.warning("Model preload failed (will load on first request): %s", exc)

    def _summarize_and_compact(self, satellite_id: str) -> None:
        """Summarize old conversation into a short context note, then clear details."""
        history = self._conversations.get(satellite_id, [])
        if len(history) < 4:
            # Less than 2 full turns — not worth summarizing, just clear
            self._conversations[satellite_id] = []
            self._conversation_summaries.pop(satellite_id, None)
            return

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": [
                        {"role": "system", "content": (
                            "Oppsummer samtalen under i 1-2 setninger. "
                            "Fokuser på tema, beslutninger og viktig kontekst. "
                            "Svar på samme språk som samtalen. Kun oppsummering."
                        )},
                        *history,
                        {"role": "user", "content": "Oppsummer samtalen kort."},
                    ],
                    "stream": False,
                    "options": {"num_ctx": 4096},
                },
                timeout=self.ollama_timeout,
            )
            resp.raise_for_status()
            summary = resp.json().get("message", {}).get("content", "").strip()
            if summary:
                self._conversation_summaries[satellite_id] = summary
                log.info("Conversation summarized for %s: %s", satellite_id, summary[:120])
            else:
                self._conversation_summaries.pop(satellite_id, None)
        except requests.RequestException as exc:
            log.warning("Failed to summarize conversation for %s: %s", satellite_id, exc)
            self._conversation_summaries.pop(satellite_id, None)

        self._conversations[satellite_id] = []

    def _parse_response(self, raw: str) -> NLUResult:
        """Parse a raw LLM response string into NLUResult."""
        raw = _strip_code_fences(raw)

        # Try to fix near-valid JSON (unescaped newlines in strings)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # LLMs often return JSON with unescaped newlines inside strings.
            # Try replacing literal newlines with \n (only inside the JSON).
            fixed = raw.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            try:
                parsed = json.loads(fixed)
            except json.JSONDecodeError:
                parsed = None

        if isinstance(parsed, dict):
            response_text = parsed.get("response", "")
            # Guard: if response is itself JSON (model echoing), extract inner
            if isinstance(response_text, str) and response_text.strip().startswith("{"):
                try:
                    inner = json.loads(response_text)
                    if isinstance(inner, dict) and "response" in inner:
                        response_text = inner["response"]
                except (json.JSONDecodeError, TypeError):
                    pass
            # Guard: if response is a dict (shouldn't happen, but defensive)
            if isinstance(response_text, dict):
                response_text = response_text.get("response", str(response_text))
            if not isinstance(response_text, str):
                response_text = str(response_text) if response_text else ""

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

        # Fallback: LLM returned non-JSON or unparseable text.
        # Extract any "response" value we can find via regex.
        resp_match = re.search(r'"response"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if resp_match:
            clean = resp_match.group(1).replace('\\n', '\n').replace('\\"', '"')
            action_match = re.search(r'"action"\s*:\s*"([^"]*)"', raw)
            action = action_match.group(1) if action_match else "answer"
            return NLUResult(
                action=action,
                entities={},
                response_text=clean,
                confidence=0.5,
                source="ollama",
                expects_followup=clean.rstrip().endswith("?"),
            )

        # Last resort: strip JSON fragments and use remaining text
        clean = re.sub(r'\{[^{}]*\}', '', raw).strip()
        clean = re.sub(r'\{.*?\}', '', clean, flags=re.DOTALL).strip()
        if not clean:
            clean = "Beklager, jeg forstod ikke helt."
        return NLUResult(
            action="answer",
            entities={},
            response_text=clean,
            confidence=0.5,
            source="ollama",
            expects_followup=clean.rstrip().endswith("?"),
        )

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
            hint = msg.content[0].text
            log.info("Rubber ducky hint: %s", hint[:80])
            return hint
        except Exception as exc:
            log.warning("Rubber ducky (Claude) failed: %s", exc)
            return ""

    def _build_messages(self, satellite_id: str, history: list[dict]) -> list[dict]:
        """Build message list with system prompt and optional prior summary."""
        msgs: list[dict] = [{"role": "system", "content": self._system_prompt}]
        summary = self._conversation_summaries.get(satellite_id)
        if summary:
            msgs.append({"role": "user", "content": f"[Kontekst fra tidligere: {summary}]"})
            msgs.append({"role": "assistant", "content": '{"action": "answer", "response": "Forstått, jeg husker konteksten."}'})
        msgs.extend(history)
        return msgs

    def process_local(
        self, transcript: str, language: str = "en", satellite_id: str = "default",
    ) -> NLUResult:
        """Process transcript with Ollama (local LLM) using chat API for multi-turn."""
        now = time.monotonic()

        # Check for conversation clear command
        if _CLEAR_PATTERNS.search(transcript):
            self._conversations.pop(satellite_id, None)
            self._conversation_summaries.pop(satellite_id, None)
            self._last_activity.pop(satellite_id, None)
            log.info("Conversation cleared for %s by voice command", satellite_id)
            return NLUResult(
                action="answer",
                entities={},
                response_text="Samtalen er nullstilt. Hva kan jeg hjelpe med?",
                confidence=1.0,
                source="ollama",
                expects_followup=True,
            )

        # Check idle timeout — clear stale conversation context
        last = self._last_activity.get(satellite_id, 0)
        if last and (now - last) > self._idle_timeout:
            history = self._conversations.get(satellite_id, [])
            if history:
                log.info(
                    "Idle %.0fs for %s — clearing %d messages (new conversation)",
                    now - last, satellite_id, len(history),
                )
            # Clear both history and summary — next speaker gets a clean slate.
            # This prevents stale context from a previous user/conversation
            # from contaminating the next one (off-by-one response bug).
            self._conversations[satellite_id] = []
            self._conversation_summaries.pop(satellite_id, None)

        self._last_activity[satellite_id] = now

        # Get or create conversation history for this satellite
        if satellite_id not in self._conversations:
            self._conversations[satellite_id] = []
        history = self._conversations[satellite_id]

        # Add user message
        history.append({"role": "user", "content": transcript})

        try:
            resp = requests.post(
                f"{self.ollama_url}/api/chat",
                json={
                    "model": self.ollama_model,
                    "messages": self._build_messages(satellite_id, history),
                    "stream": False,
                    "options": {"num_ctx": 4096},
                },
                timeout=self.ollama_timeout,
            )
            resp.raise_for_status()
            raw = _strip_code_fences(
                resp.json().get("message", {}).get("content", "")
            )

            # Store assistant response in history
            history.append({"role": "assistant", "content": raw})

            # Keep history manageable (last 10 turns = 20 messages)
            if len(history) > 20:
                self._conversations[satellite_id] = history[-20:]

            # Clear stale summary once new conversation is well underway
            if len(history) >= 6 and satellite_id in self._conversation_summaries:
                self._conversation_summaries.pop(satellite_id, None)

            return self._parse_response(raw)

        except requests.RequestException as exc:
            log.warning("Ollama request failed: %s", exc)
            # Remove the failed user message from history
            if history and history[-1]["role"] == "user":
                history.pop()
            return NLUResult(
                action="error",
                entities={},
                response_text="Beklager, jeg klarte ikke a behandle forespoerselen.",
                confidence=0.0,
                source="ollama",
            )

    def clear_conversation(self, satellite_id: str = "default") -> None:
        """Clear conversation history for a satellite."""
        self._conversations.pop(satellite_id, None)

    async def process_cloud(self, transcript: str, language: str = "en") -> NLUResult:
        """Process transcript with Claude API (cloud fallback)."""
        if not self.claude_api_key:
            return NLUResult(
                action="error",
                entities={},
                response_text="Cloud processing not configured.",
                confidence=0.0,
                source="claude",
            )

        import anthropic

        client = anthropic.Anthropic(api_key=self.claude_api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            system=self._system_prompt,
            messages=[{"role": "user", "content": transcript}],
        )

        raw = message.content[0].text
        try:
            parsed = json.loads(raw)
            return NLUResult(
                action=parsed.get("action", "unknown"),
                entities={
                    k: v for k, v in parsed.items()
                    if k not in ("action", "response")
                },
                response_text=parsed.get("response", ""),
                confidence=0.95,
                source="claude",
            )
        except json.JSONDecodeError:
            return NLUResult(
                action="answer",
                entities={},
                response_text=raw,
                confidence=0.7,
                source="claude",
            )
