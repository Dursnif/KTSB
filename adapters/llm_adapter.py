# /kaare/adapters/llm_adapter.py

import base64
import fcntl
import io
import json
import logging
import os
import re
import time
import yaml
import httpx
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

from kaare_core.llm_fallback import (
    is_fallback_active, activate_fallback, deactivate_fallback,
    increment_turn, should_retry_main, update_last_failure,
)
from kaare_core.model_lock import lock_11445

logger = logging.getLogger(__name__)

GPU_LOCK_PATH = "/kaare/runtime/gpu.lock"

# Intern rask-sjekk: settes True mellom "vi bestemmer oss for kare" og
# "proxyen faktisk låser gpu.lock". Dekker race-vinduet.
_kare_active = False


def _kare_is_busy() -> bool:
    """
    Sjekker om GPU-gate-låsen er holdt av proxyen.
    Bruker ikke-blokkerende flock — returnerer umiddelbart.
    Fanger opp alle requests som går gjennom proxyen (inkl. Frigate via proxy).
    """
    try:
        with open(GPU_LOCK_PATH, "r") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False   # Låsen var fri → kare er ledig
    except FileNotFoundError:
        return False       # Ingen lås-fil = ingen proxy kjører (f.eks. Docker) → ledig
    except (IOError, OSError):
        return True        # Låsen holdes → kare er opptatt

# -------------------------------------------------
# Config
# -------------------------------------------------

CFG_PATH = "/kaare/configs/llm.yaml"
CFG = yaml.safe_load(open(CFG_PATH, "r", encoding="utf-8"))

_CAPABILITY_MAP_PATH = "/kaare/capability_map.yaml"
try:
    _CAPABILITY_MAP: dict = yaml.safe_load(open(_CAPABILITY_MAP_PATH, "r", encoding="utf-8")) or {}
except Exception:
    _CAPABILITY_MAP: dict = {}

from kaare_core.config import get_model as _get_model, get_local_tz as _get_local_tz, get_tool_permissions as _get_tool_permissions

# -------------------------------------------------
# Personlighet
# Laster personality_core.md (kjerne) og alle varianter i configs/personalities/
# ved oppstart — ingen disk-I/O per request, kun dict-lookup.
# Rekkefølge: kjerne → adferd → lokasjon → tid → tekniske instruksjoner.
# -------------------------------------------------

import glob as _glob

def _load_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as _f:
            return _f.read().strip()
    except Exception:
        return ""

_PERSONALITY_CORES: dict[str, str] = {
    "nb": _load_text("/kaare/configs/personality_core.md"),
    "en": _load_text("/kaare/configs/personality_core_en.md"),
    "de": _load_text("/kaare/configs/personality_core_de.md"),
}
_PERSONALITY_CORES_LETVEKT: dict[str, str] = {
    "nb": _load_text("/kaare/configs/personality_core_letvekt.md"),
    "en": _load_text("/kaare/configs/personality_core_letvekt_en.md"),
    "de": _load_text("/kaare/configs/personality_core_letvekt_de.md"),
}
_PERSONALITY_CORES_MINIMAL: dict[str, str] = {
    "nb": _load_text("/kaare/configs/personality_core_minimal.md"),
    "en": _load_text("/kaare/configs/personality_core_minimal_en.md"),
    "de": _load_text("/kaare/configs/personality_core_minimal_de.md"),
}

def _get_personality_core(lang: str, mode: str) -> str:
    lang_key = lang if lang in ("nb", "en", "de") else "en"
    if mode == "minimal":
        return _PERSONALITY_CORES_MINIMAL.get(lang_key) or _PERSONALITY_CORES_MINIMAL["en"]
    if mode == "letvekt":
        return _PERSONALITY_CORES_LETVEKT.get(lang_key) or _PERSONALITY_CORES_LETVEKT["en"]
    return _PERSONALITY_CORES.get(lang_key) or _PERSONALITY_CORES["en"]

_PERSONALITY_MODE          = "standard"
_PERSONALITY_CORE          = _PERSONALITY_CORES["nb"]

# Laster alle adferds-varianter fra configs/personalities/*.md ved oppstart
_PERSONALITIES: dict[str, str] = {}
for _p in _glob.glob("/kaare/configs/personalities/*.md"):
    _key = os.path.splitext(os.path.basename(_p))[0]
    _text = _load_text(_p)
    if _text:
        _PERSONALITIES[_key] = _text

# Fallback: gammel personality_behavior.md hvis ingen varianter finnes
_PERSONALITY_BEHAVIOR_FALLBACK = _load_text("/kaare/configs/personality_behavior.md")
if not _PERSONALITIES and _PERSONALITY_BEHAVIOR_FALLBACK:
    _PERSONALITIES["standard"] = _PERSONALITY_BEHAVIOR_FALLBACK

_SETTINGS_PATH = "/kaare/configs/settings.yaml"
_LOKASJON_BLOKK = ""
_ASSISTANT_NAME_BLOKK = ""
_LANGUAGE_BLOKK = ""
_LOCAL_TZ: ZoneInfo = ZoneInfo("UTC")

_KARE_LANGUAGE_MAP = {
    "nb": "Svar alltid på norsk.",
    "en": "Always reply in English.",
    "de": "Antworte immer auf Deutsch.",
    "fr": "Réponds toujours en français.",
    "es": "Responde siempre en español.",
    "sv": "Svara alltid på svenska.",
    "da": "Svar altid på dansk.",
    "nl": "Antwoord altijd in het Nederlands.",
    "fi": "Vastaa aina suomeksi.",
    "it": "Rispondi sempre in italiano.",
    "pl": "Odpowiadaj zawsze po polsku.",
    "pt": "Responde sempre em português.",
    "ru": "Всегда отвечай по-русски.",
    "zh": "始终用中文回答。",
    "ja": "常に日本語で返答してください。",
    "ar": "أجب دائماً باللغة العربية.",
}

def _kare_lang_instruction(lang: str) -> str:
    return _KARE_LANGUAGE_MAP.get(lang.lower().strip(), f"Always reply in {lang}.")

try:
    _settings = yaml.safe_load(open(_SETTINGS_PATH, "r", encoding="utf-8"))
    _lok = _settings.get("location") or _settings.get("lokasjon", {})
    if _lok:
        _city = _lok.get("city") or _lok.get("sted", "")
        _postal = _lok.get("postal_code") or _lok.get("postnummer", "")
        _country = _lok.get("country") or _lok.get("land", "")
        _LOKASJON_BLOKK = (
            f"# Lokasjon\n"
            f"Jeg bor i {_city} ({_postal}) i {_country}. "
            f"Bruk dette automatisk ved vær- og stedsspesifikke søk."
        )
        _LOCAL_TZ = _get_local_tz()
    _aname = _settings.get("assistant_name", "").strip()
    if _aname:
        _ASSISTANT_NAME_BLOKK = f"Ditt navn er {_aname}. Brukere kaller deg dette."
    _lang = _settings.get("kare_language") or _settings.get("language", "nb")
    _LANGUAGE_BLOKK = _kare_lang_instruction(_lang)
    _PERSONALITY_MODE = _settings.get("personality_mode", "standard")
    if _PERSONALITY_MODE == "egendefinert":
        _PERSONALITY_CORE = _load_text("/kaare/configs/personality_core_custom.md") or _get_personality_core(_lang, "standard")
    else:
        _PERSONALITY_CORE = _get_personality_core(_lang, _PERSONALITY_MODE)
except Exception:
    pass


_UKEDAGER = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
_MÅNEDER  = ["januar", "februar", "mars", "april", "mai", "juni",
             "juli", "august", "september", "oktober", "november", "desember"]

def _tid_blokk() -> str:
    """Returns current date and time using the timezone from settings.yaml."""
    nå = datetime.now(tz=_LOCAL_TZ)
    dag   = _UKEDAGER[nå.weekday()]
    dato  = f"{nå.day}. {_MÅNEDER[nå.month - 1]} {nå.year}"
    kl    = nå.strftime("%H:%M")
    return f"# Tid og dato\nNå er det {dag} {dato}, klokken {kl}."


def _load_kare_huske() -> str:
    """Kompakt injeksjon av Kåres huskeliste. Tom streng hvis listen er tom."""
    try:
        from kaare_core.tools.lister import kare_les_for_injeksjon
        return kare_les_for_injeksjon()
    except Exception:
        return ""


def _build_household_block() -> str:
    """Build the household members block from all users' household_visible sections.
    Loaded at startup and on reload. Returns empty string if no data."""
    try:
        from kaare_core.users.profile_manager import get_all_household_visible, format_household_block
        all_visible = get_all_household_visible()
        return format_household_block(all_visible)
    except Exception:
        return ""


_HOUSEHOLD_BLOCK = _build_household_block()

_PERSONALITY_SELF_RAW = _load_text("/kaare/state/personality_self.md")
_PERSONALITY_SELF_CAP = 3000  # chars — ~750 tokens, fits ~20 recent observations

def _cap_personality_self(text: str) -> str:
    """Keep only the most recent observations — newest are at the bottom."""
    if len(text) <= _PERSONALITY_SELF_CAP:
        return text
    truncated = text[-_PERSONALITY_SELF_CAP:]
    first_newline = truncated.find("\n")
    if first_newline > 0:
        truncated = truncated[first_newline + 1:]
    return "[… eldre observasjoner ikke vist]\n\n" + truncated

_PERSONALITY_SELF = _cap_personality_self(_PERSONALITY_SELF_RAW)


def _load_user_obs(user_id: str) -> str:
    """
    Laster topp-seksjonen av brukerens observations.md til system-prompten.
    Alt over første '---'-linje = alltid inn. Ingen '---' = siste 14 dager.
    Myk grense: 1500 tegn — resten tilgjengelig via les_brukerprofil-tool.
    """
    from pathlib import Path
    if not user_id or user_id == "global":
        return ""
    path = Path(f"/kaare/state/users/{user_id}/observations.md")
    if not path.exists():
        return ""
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return ""
        if "\n---\n" in content:
            top = content.split("\n---\n", 1)[0].strip()
        else:
            from kaare_core.users.profile_manager import get_recent_observations
            top = get_recent_observations(user_id, days=14)
        if not top:
            return ""
        if len(top) > 1500:
            top = top[:1500] + "\n[… mer tilgjengelig via les_brukerprofil]"
        return f"# Kåres observasjoner om {user_id}\n{top}"
    except Exception:
        return ""


def _load_user_profile_top(user_id: str) -> str:
    """Load prompt_top from user profile.yaml for system prompt injection."""
    if not user_id or user_id == "global":
        return ""
    try:
        from kaare_core.users.profile_manager import get_profile_prompt_top
        return get_profile_prompt_top(user_id)
    except Exception:
        return ""


def _current_user_block(user_id: str) -> str:
    """Return an unambiguous 'Du snakker nå med X' block for the active user."""
    if not user_id or user_id == "global":
        return ""
    try:
        from kaare_core.users.profile_manager import get_display_name
        name = get_display_name(user_id)
    except Exception:
        name = user_id
    return f"# Nåværende bruker\nDu snakker nå med **{name}**. Bruk alltid dette navnet — aldri et annet."


def _build_disabled_modules_block() -> str:
    """Return a prompt block listing disabled agents/domains and Pettersmart tool limits, or '' if everything is active."""
    disabled: list[str] = []

    _agent_labels = {
        "pettersmart": "Pettersmart",
        "library":     "Frøken Library",
        "image_edit":  "bilderedigering",
    }
    for role, label in _agent_labels.items():
        if not CFG.get(role, {}).get("enabled", True):
            disabled.append(label)

    _domain_labels = {
        "home_assistant": "hjemmeautomatisering (Home Assistant)",
        "frigate":        "kamera / Frigate",
        "weather":        "vær",
    }
    domains = _CAPABILITY_MAP.get("domains", {})
    for key, label in _domain_labels.items():
        if not domains.get(key, {}).get("enabled", True):
            disabled.append(label)

    parts: list[str] = []
    if disabled:
        names = ", ".join(disabled)
        parts.append(
            f"## Deaktiverte moduler\n"
            f"{names} er slått av av administrator. "
            f"Ikke tilby, forsøk å bruke, eller henvis til disse funksjonene."
        )

    # Pettersmart tool-level awareness (only when Pettersmart itself is enabled)
    if CFG.get("pettersmart", {}).get("enabled", True):
        ps_perms = _get_tool_permissions().get("agent_tools", {}).get("pettersmart", {})
        _tool_labels = {
            "utforsk":        "utforsk (lese filer/kode)",
            "inspiser":       "inspiser (logger/tjenester/ressurser)",
            "nettsøk":        "nettsøk",
            "søk_vaktmester": "søk_vaktmester (systemlogg)",
            "shell":          "shell (kjøre kommandoer)",
            "hukommelse":     "hukommelse",
        }
        disabled_tools = [label for key, label in _tool_labels.items() if ps_perms.get(key) is False]
        if disabled_tools:
            parts.append(
                f"## Pettersmart — deaktiverte verktøy\n"
                f"Pettersmart kan ikke bruke: {', '.join(disabled_tools)}. "
                f"Ikke be Pettersmart utføre oppgaver som krever disse verktøyene."
            )

    return "\n\n".join(parts)


def _build_system(base: str, personality: str = "standard", user_id: str = "") -> str:
    """
    Kombiner kjerne, adferds-variant, lokasjon, nåværende tid og operasjonell
    system prompt fra llm.yaml.
    Nivåer (styres av _PERSONALITY_MODE):
      minimal/letvekt  → kun kjerne, ingen behavior, ingen personality_self
      standard         → kjerne + personality_self
      full             → kjerne + behavior + personality_self
      komplett         → kjerne + behavior + personality_self + world.md (maks 2000 tegn)
      egendefinert     → egendefinert kjerne + behavior + personality_self
    _tid_blokk() sist — endres hvert minutt og ville ugyldiggjort KV-cache.
    """
    _include_behavior = _PERSONALITY_MODE in ("full", "komplett", "egendefinert")
    _include_self     = _PERSONALITY_MODE not in ("minimal", "letvekt")
    _include_world    = _PERSONALITY_MODE == "komplett"

    behavior = (_PERSONALITIES.get(personality) or _PERSONALITIES.get("standard", "")) if _include_behavior else ""
    personality_self = _PERSONALITY_SELF if _include_self else ""
    world_ctx = ""
    if _include_world:
        raw = _load_text("/kaare/state/world.md")
        world_ctx = raw[:2000] if raw else ""

    profile_top = _load_user_profile_top(user_id)
    user_obs = _load_user_obs(user_id)
    kare_huske = _load_kare_huske()
    current_user = _current_user_block(user_id)
    parts = [p for p in [
        _ASSISTANT_NAME_BLOKK, _PERSONALITY_CORE, current_user, behavior,
        _LOKASJON_BLOKK, _LANGUAGE_BLOKK, (base or "").strip(),
        personality_self, kare_huske, _HOUSEHOLD_BLOCK, profile_top, user_obs,
        world_ctx, _build_disabled_modules_block(), _tid_blokk()
    ] if p]
    return "\n\n---\n\n".join(parts)


def _build_system_fallback() -> str:
    """
    Stripped system prompt for 9B fallback mode.
    Only core personality + fallback notice + current time.
    Omits behavior variants, self-image, user observations — the 9B
    model handles a short prompt better, and those layers are not
    needed for basic functional responses.
    """
    fallback_note = (
        "# Reservemodus\n"
        "Kjernemodellen er midlertidig utilgjengelig. Du kjører på backup-modell (9B). "
        "Svar kort og direkte. Prioriter smarthjem og enkle spørsmål. "
        "Unngå lange resonnement og komplekse flertrinnsoppgaver."
    )
    parts = [p for p in [_PERSONALITY_CORE, fallback_note, _tid_blokk()] if p]
    return "\n\n---\n\n".join(parts)


def list_personalities() -> list[dict]:
    """Returnerer tilgjengelige personlighetsvarianter (nøkkel + filnavn)."""
    return [{"key": k, "label": k.replace("_", " ").capitalize()} for k in sorted(_PERSONALITIES)]


# -------------------------------------------------
# Helpers
# -------------------------------------------------

def _extract_text(data: Dict[str, Any]) -> str:
    """
    Normaliserer alle kjente Ollama / VLM / OpenAI-lignende svar til ren tekst.
    """
    if not isinstance(data, dict):
        return ""

    # Ollama /api/generate (LLM)
    if isinstance(data.get("response"), str):
        return data["response"]

    # Enkle wrappers
    if isinstance(data.get("text"), str):
        return data["text"]

    # message.content som string
    msg = data.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"]

    # OpenAI / VLM-format: choices[].message.content[]
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice0 = choices[0]
        msg = choice0.get("message") if isinstance(choice0, dict) else None
        content = msg.get("content") if isinstance(msg, dict) else None

        if isinstance(content, list):
            texts = [
                c.get("text")
                for c in content
                if isinstance(c, dict)
                and c.get("type") == "text"
                and isinstance(c.get("text"), str)
            ]
            return "\n".join(texts)

        if isinstance(content, str):
            return content

    return ""


_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>",
    re.DOTALL,
)
_PARAM_RE = re.compile(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", re.DOTALL)


def _parse_ollama_tool_calls(content: str, id_prefix: str = "call"):
    """
    Parse Ollama-style tool call XML from model output when vLLM's hermes
    parser doesn't catch them (model outputs <function=name> format instead
    of JSON-inside-<tool_call> format).

    Returns (tool_calls_list_or_None, cleaned_content).
    Each tool call gets a stable ID so the router can build OpenAI-compatible history.
    """
    matches = list(_TOOL_CALL_RE.finditer(content))
    if not matches:
        return None, content

    tool_calls = []
    for i, m in enumerate(matches):
        func_name = m.group(1)
        arguments = {
            pm.group(1): pm.group(2).strip()
            for pm in _PARAM_RE.finditer(m.group(2))
        }
        tool_calls.append({
            "id": f"{id_prefix}_{i}",
            "function": {"name": func_name, "arguments": arguments},
        })

    cleaned = _TOOL_CALL_RE.sub("", content).strip()
    return tool_calls, cleaned


_VLLM_VISUAL_TOKENS_PER_PIXEL = 1.0 / 966  # ~966 px per visual token (measured on Qwen3VL 27B)
_VLLM_MAX_VISUAL_TOKENS = 2000             # stays safely below model's 2304 limit


def _resize_image_for_vllm(data_uri: str, max_pixels: int) -> str:
    """
    Resize an image (given as a data URI) so it contains at most max_pixels pixels.
    Returns a data URI (JPEG) at the reduced size. No-ops if image is already small enough.
    """
    from PIL import Image

    if data_uri.startswith("data:"):
        _header, _b64 = data_uri.split(",", 1)
    else:
        _b64 = data_uri

    raw = base64.b64decode(_b64)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    if w * h <= max_pixels:
        return data_uri  # already small enough, no change

    scale = (max_pixels / (w * h)) ** 0.5
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64_new = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64_new}"


def _ollama_images_to_openai_content(text: str, images: List[str]) -> List[Dict[str, Any]]:
    """
    Convert Ollama-style images list + text content to OpenAI multimodal content array.
    vLLM/OpenAI expects: [{"type":"text","text":"..."}, {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}]
    Images are resized so that the total visual token budget stays within the model limit.
    """
    n = len(images)
    max_pixels_per_image = int(_VLLM_MAX_VISUAL_TOKENS / max(n, 1) / _VLLM_VISUAL_TOKENS_PER_PIXEL)

    parts: List[Dict[str, Any]] = [{"type": "text", "text": text or ""}]
    for img in images:
        url = img if img.startswith("data:") else f"data:image/jpeg;base64,{img}"
        url = _resize_image_for_vllm(url, max_pixels_per_image)
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def _normalise_messages_for_vllm(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert Kåre's Ollama-style message history to OpenAI/vLLM format.

    Ollama user/system msg with images:
        {"role": "user", "content": "...", "images": ["base64..."]}
    OpenAI multimodal:
        {"role": "user", "content": [{"type":"text","text":"..."},
                                     {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}]}

    Ollama assistant msg:  {"content": "...", "tool_calls": [{"function": {...}}]}
    OpenAI assistant msg:  {"role": "assistant", "content": null,
                            "tool_calls": [{"id": "call_0", "type": "function",
                                            "function": {"name": ..., "arguments": JSON_STR}}]}

    Ollama tool msg:       {"role": "tool", "content": "..."}
    OpenAI tool msg:       {"role": "tool", "tool_call_id": "call_0", "content": "..."}
    """
    normalised = []
    # Stack of IDs from the last assistant tool-call block, consumed by tool msgs.
    pending_ids: list[str] = []

    for msg in messages:
        role = msg.get("role")
        tcs = msg.get("tool_calls")
        imgs = msg.get("images")

        # User/system message with embedded Ollama-style images → OpenAI multimodal
        if imgs and role in ("user", "system", None) and tcs is None:
            normalised.append({
                "role": role or "user",
                "content": _ollama_images_to_openai_content(msg.get("content", ""), imgs),
            })

        # Assistant message (with or without explicit role)
        elif tcs is not None and role in (None, "assistant"):
            fixed_tcs = []
            pending_ids = []
            for i, tc in enumerate(tcs):
                tc_id = tc.get("id") or f"call_{len(normalised)}_{i}"
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                fixed_tcs.append({
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": fn.get("name", ""), "arguments": args},
                })
                pending_ids.append(tc_id)
            normalised.append({
                "role": "assistant",
                "content": msg.get("content") or None,
                "tool_calls": fixed_tcs,
            })

        # Tool result message — attach matching tool_call_id
        elif role == "tool":
            tc_id = msg.get("tool_call_id")
            if not tc_id and pending_ids:
                tc_id = pending_ids.pop(0)
            normalised.append({
                "role": "tool",
                "tool_call_id": tc_id or "call_0",
                "content": msg.get("content", ""),
            })

        else:
            normalised.append(msg)

    return normalised


_model_size_cache: dict[str, float] = {}


async def get_model_size_b(model: str, base_url: str, provider: str = "ollama") -> float:
    """
    Return the model size in billions of parameters.

    For vLLM and cloud providers, returns 999.0 (no tool filtering).
    For Ollama, queries /api/show and parses details.parameter_size.
    Result is cached per (base_url, model) for the process lifetime.
    Falls back to 7.0 on any error (tier-3 tools available, tier-9 blocked).
    """
    if provider not in ("ollama", "openvino"):
        return 999.0
    cache_key = f"{base_url}|{model}"
    if cache_key in _model_size_cache:
        return _model_size_cache[cache_key]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(f"{base_url}/api/show", json={"name": model})
            r.raise_for_status()
            data = r.json()
        size_str = data.get("details", {}).get("parameter_size", "")
        m_b = re.search(r"([\d.]+)\s*[Bb]", size_str)
        m_m = re.search(r"([\d.]+)\s*[Mm]", size_str)
        if m_b:
            size_b = float(m_b.group(1))
        elif m_m:
            size_b = float(m_m.group(1)) / 1000.0  # 873.44M → 0.87B
        else:
            size_b = 7.0
    except Exception as exc:
        logger.warning("get_model_size_b: could not detect size for %s (%s) — defaulting to 7.0B", model, exc)
        size_b = 7.0
    _model_size_cache[cache_key] = size_b
    logger.info("get_model_size_b: %s = %.1fB", model, size_b)
    return size_b


def _clean_ollama_options(options: dict) -> dict:
    """Remove num_ctx if 0 or absent — lets Ollama use the model's native context window."""
    if not options:
        return options
    opts = dict(options)
    if not opts.get("num_ctx"):
        opts.pop("num_ctx", None)
    return opts


def _build_vllm_options(options: dict) -> dict:
    """Convert Ollama-style options to OpenAI API top-level parameters."""
    mapping = {
        "max_tokens":        "max_tokens",
        "num_predict":       "max_tokens",
        "temperature":       "temperature",
        "top_p":             "top_p",
        "presence_penalty":  "presence_penalty",
        "frequency_penalty": "frequency_penalty",
    }
    result = {}
    for src, dst in mapping.items():
        if src in options and dst not in result:
            result[dst] = options[src]
    return result


async def _call_vllm_chat(
    *,
    role: str,
    base_url: str,
    model: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]] | None = None,
    options: Dict[str, Any] | None = None,
    timeout: float | None = None,
    disable_thinking: bool = False,
) -> Dict[str, Any]:
    """
    OpenAI-compatible /v1/chat/completions call for vLLM.

    Returns a dict normalised to match what the rest of the code expects from
    ask_llm_with_tools():
      ok, text, tool_calls (arguments as dict, not JSON string), think_content, message
    """
    # Normalise message history: Ollama format → OpenAI/vLLM format (adds IDs etc.)
    messages = _normalise_messages_for_vllm(messages)

    payload: dict = {
        "model":  model,
        "messages": messages,
        "stream": False,
    }
    if options:
        payload.update(_build_vllm_options(options))
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers={"x-kaare-source": "kaare"},
        )
        if r.status_code >= 400:
            logger.warning("_call_vllm_chat %s: vLLM body: %s", r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {})
    content = (msg.get("content") or "").strip()
    # vLLM with --reasoning-parser puts think tokens in "reasoning" field
    think_content = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()

    # Normalise tool_calls: OpenAI has arguments as a JSON string, we want dict
    raw_tool_calls = msg.get("tool_calls")
    tool_calls = None
    if raw_tool_calls:
        tool_calls = []
        for tc in raw_tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append({"function": {"name": fn.get("name", ""), "arguments": args}})

    # Fallback: model outputs Ollama-style <function=name> XML which vLLM's
    # hermes parser doesn't handle — parse them ourselves from content.
    if not tool_calls and "<tool_call>" in content:
        parsed, content = _parse_ollama_tool_calls(content)
        if parsed:
            tool_calls = parsed

    # Build an OpenAI-compatible assistant message the router can append
    # directly to message history for the next round.
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": content or None}
    if tool_calls:
        assistant_msg["tool_calls"] = [
            {
                "id": tc.get("id", f"call_{i}"),
                "type": "function",
                "function": {
                    "name": tc["function"]["name"],
                    "arguments": json.dumps(tc["function"]["arguments"], ensure_ascii=False)
                    if isinstance(tc["function"]["arguments"], dict)
                    else tc["function"]["arguments"],
                },
            }
            for i, tc in enumerate(tool_calls)
        ]

    return {
        "ok":           bool(content or tool_calls),
        "text":         content,
        "tool_calls":   tool_calls,
        "think_content": think_content,
        "message":      assistant_msg,
        "meta":         {"role": role, "model": model, "base_url": base_url},
    }


async def _call_ollama(
    *,
    role: str,
    base_url: str,
    model: str,
    prompt: str,
    images: List[str] | None = None,
    stream: bool = False,
    think: bool | None = None,
    options: Dict[str, Any] | None = None,
    system: str | None = None,
) -> Dict[str, Any]:
    """
    Lavnivå Ollama-kall. Antar at model allerede er resolvet.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
    }

    if system:
        payload["system"] = system

    if think is not None:
        payload["think"] = think

    if options:
        payload["options"] = _clean_ollama_options(options)

    if images:
        payload["images"] = images

    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(
            f"{base_url}/api/generate",
            json=payload,
            headers={"x-kaare-source": "kaare"},
        )

        if r.status_code == 404:
            return {
                "ok": False,
                "error": "model_not_available",
                "meta": {"role": role, "model": model},
            }

        r.raise_for_status()
        data = r.json()

    text = _extract_text(data)

    return {
        "ok": bool(text),
        "text": text.strip(),
        "meta": {
            "role": role,
            "model": model,
            "base_url": base_url,
            "has_images": bool(images),
        },
    }


# -------------------------------------------------
# Public adapter API
# -------------------------------------------------

async def ask_llm(
    prompt: str,
    raw_prompt: str | None = None,
    allow_cloud: bool = False,
    images: List[str] | None = None,
) -> Dict[str, Any]:
    """
    Waterfall: kare (27B) → 9B fallback → cloud (if allow_cloud=True).

    prompt      = full prompt incl. STM context (sent to kare)
    raw_prompt  = user text only, no STM (sent to cloud — avoids essays)
    allow_cloud = True only for real chat calls, never for intent/split/repair
    """
    global _kare_active

    cfg         = CFG["default"]
    kare_busy   = _kare_active or _kare_is_busy()
    in_fallback = is_fallback_active()
    skip_main   = in_fallback and not should_retry_main()

    backends: List[tuple] = []
    if not kare_busy and not skip_main:
        backends.append(("kare", cfg["base_url"]))
    if allow_cloud and CFG.get("cloud", {}).get("enabled", True):
        backends.append(("cloud", None))

    _newly_activated = False

    for instance, base_url in backends:
        if instance == "kare":
            _kare_active = True

        try:
            if instance == "cloud":
                result = await _call_cloud_brief(raw_prompt or prompt)
            elif cfg.get("provider") == "vllm":
                _t0_simple = time.perf_counter()
                system_text = _build_system(cfg.get("system", ""))
                vllm_messages: List[Dict[str, Any]] = []
                if system_text:
                    vllm_messages.append({"role": "system", "content": system_text})
                # Include images in OpenAI multimodal format if provided
                if images:
                    user_content: Any = _ollama_images_to_openai_content(prompt, images)
                else:
                    user_content = prompt
                vllm_messages.append({"role": "user", "content": user_content})
                result = await _call_vllm_chat(
                    role="llm",
                    base_url=base_url,
                    model=_get_model(cfg["model_role"]),
                    messages=vllm_messages,
                    options=cfg.get("options"),
                )
                result["meta"]["has_images"] = bool(images)
                _think = result.get("think_content", "")
                if _think:
                    try:
                        from kaare_core.tools.think_cache import log_think
                        log_think(
                            think_text=_think,
                            response=result.get("text", ""),
                            role="kare",
                            model=_get_model(cfg["model_role"]),
                            prompt_preview=prompt[:200],
                            latency_ms=int((time.perf_counter() - _t0_simple) * 1000),
                            recovered=bool(_think and not result.get("text")),
                        )
                    except Exception:
                        pass
            else:
                result = await _call_ollama(
                    role="llm",
                    base_url=base_url,
                    model=_get_model(cfg["model_role"]),
                    prompt=prompt,
                    images=images,
                    stream=cfg.get("stream", False),
                    think=cfg.get("think"),
                    options=cfg.get("options"),
                    system=_build_system(cfg.get("system", "")),
                )

            if result.get("ok"):
                result.setdefault("meta", {})["instance"] = instance
                if instance == "cloud":
                    logger.warning("llm_router: kare nede → svarte via cloud")
                elif in_fallback:
                    info = deactivate_fallback()
                    result["meta"]["fallback_deactivated"] = True
                    result["meta"]["fallback_info"] = info
                return result

            logger.warning("llm_router: %s svarte ikke ok, prøver neste", instance)

        except Exception as e:
            logger.warning("llm_router: %s feilet (%s), prøver neste", instance, e)
            if instance == "kare":
                if in_fallback:
                    update_last_failure()
                else:
                    activate_fallback()
                    _newly_activated = True
                    in_fallback = True

        finally:
            if instance == "kare":
                _kare_active = False

    # ── 9B fallback (generate endpoint) ──────────────────────────────────────
    if in_fallback and CFG.get("fallback", {}).get("enabled", True):
        cfg_fb = CFG.get("fallback", {})
        try:
            async with lock_11445("kare_fallback"):
                result = await _call_ollama(
                    role="llm_fallback",
                    base_url=cfg_fb.get("base_url", "http://127.0.0.1:11445"),
                    model=_get_model(cfg_fb.get("model_role", "miss_kare")),
                    prompt=prompt,
                    images=images,
                    stream=cfg_fb.get("stream", False),
                    think=cfg_fb.get("think"),
                    options=cfg_fb.get("options"),
                    system=_build_system_fallback(),
                )
            if result.get("ok"):
                increment_turn()
                result.setdefault("meta", {}).update({
                    "instance":           "fallback_9b",
                    "fallback_activated": _newly_activated,
                })
                return result
        except Exception as e:
            logger.warning("llm_router: 9B fallback feilet (%s)", e)

    return {
        "ok":   False,
        "text": "Jeg får ikke kontakt med noen av systemene akkurat nå.",
        "meta": {"instance": "none"},
    }


async def ask_llm_with_tools(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    rid: str = "",
    personality: str = "standard",
    user_id: str = "",
) -> Dict[str, Any]:
    """
    Tool-calling via Ollama /api/chat.

    Returns:
      tool_calls=None  → final answer, read 'text'
      tool_calls=[...] → Kåre wants to call tools, execute them and call again

    Waterfall: kare (27B) → 9B fallback.
    No cloud fallback — tool-calling requires a local model.

    meta flags on return:
      instance             "kare" | "fallback_9b" | "none"
      fallback_activated   True only on the first call where fallback was triggered
      fallback_deactivated True only on the first successful recovery call
      fallback_info        session dict from deactivate_fallback() when deactivated
    """
    global _kare_active

    cfg         = CFG["default"]
    kare_busy   = _kare_active or _kare_is_busy()
    in_fallback = is_fallback_active()
    skip_main   = in_fallback and not should_retry_main()

    backends: List[tuple] = []
    if not kare_busy and not skip_main:
        backends.append(("kare", cfg["base_url"]))

    system = _build_system(cfg.get("system", ""), personality=personality, user_id=user_id)
    full_messages = ([{"role": "system", "content": system}] + messages) if system else messages

    _newly_activated   = False
    _newly_deactivated = False
    _fallback_info: dict | None = None

    for instance, base_url in backends:
        if instance == "kare":
            _kare_active = True

        _t0 = time.perf_counter()
        try:
            if cfg.get("provider") == "vllm":
                vllm_result = await _call_vllm_chat(
                    role="llm",
                    base_url=base_url,
                    model=_get_model(cfg["model_role"]),
                    messages=full_messages,
                    tools=tools,
                    options=cfg.get("options"),
                )
                content       = vllm_result["text"]
                tool_calls    = vllm_result["tool_calls"]
                think_content = vllm_result["think_content"]
                msg           = vllm_result["message"]
            else:
                # Ollama /api/chat
                think_val = cfg.get("think", False)
                payload: dict = {
                    "model":    _get_model(cfg["model_role"]),
                    "messages": full_messages,
                    "tools":    tools,
                    "stream":   False,
                    "options":  _clean_ollama_options(cfg.get("options", {})),
                    "think":    think_val,
                }

                async with httpx.AsyncClient(timeout=None) as client:
                    r = await client.post(
                        f"{base_url}/api/chat",
                        json=payload,
                        headers={"x-kaare-source": "kaare"},
                    )
                    r.raise_for_status()
                    data = r.json()

                msg        = data.get("message", {})
                tool_calls = msg.get("tool_calls")
                content    = (msg.get("content") or "").strip()

                # Strip <think> blocks from Ollama responses
                think_content = ""
                think_end = content.upper().find("</THINK>")
                if think_end != -1:
                    think_start = content.upper().find("<THINK>")
                    if think_start != -1:
                        think_content = content[think_start + len("<THINK>"):think_end].strip()
                    content = content[think_end + len("</THINK>"):].strip()

            elapsed_ms = int((time.perf_counter() - _t0) * 1000)

            recovered = False
            if think_content:
                if not content and not tool_calls:
                    recovered = True
                try:
                    from kaare_core.tools.think_cache import log_think, extract_conclusion
                    log_think(
                        think_text=think_content,
                        response=content,
                        role="kare",
                        model=_get_model(cfg["model_role"]),
                        prompt_preview=(full_messages[-1].get("content", "") if full_messages else "")[:200],
                        latency_ms=elapsed_ms,
                        recovered=recovered,
                    )
                    if recovered:
                        content = extract_conclusion(think_content)
                except Exception:
                    if recovered:
                        content = think_content[:400]

            # Main model responded — deactivate fallback if we were in it
            if in_fallback:
                _fallback_info     = deactivate_fallback()
                _newly_deactivated = True

            try:
                os.makedirs("/kaare/logs", exist_ok=True)
                with open("/kaare/logs/llm_calls.log", "a", encoding="utf-8") as _f:
                    _f.write(json.dumps({
                        "ts":         datetime.now(timezone.utc).isoformat(),
                        "rid":        rid,
                        "instance":   instance,
                        "latency_ms": elapsed_ms,
                        "has_tools":  bool(tool_calls),
                        "has_think":  bool(think_content),
                        "recovered":  recovered,
                        "status":     "ok",
                    }, ensure_ascii=False) + "\n")
            except Exception:
                pass

            return {
                "ok":         True,
                "text":       content,
                "tool_calls": tool_calls,
                "message":    msg,
                "meta": {
                    "instance":             instance,
                    "fallback_activated":   False,
                    "fallback_deactivated": _newly_deactivated,
                    "fallback_info":        _fallback_info,
                },
            }

        except Exception as e:
            logger.warning("ask_llm_with_tools: %s feilet (%s)", instance, e)
            if instance == "kare":
                if in_fallback:
                    update_last_failure()
                else:
                    activate_fallback()
                    _newly_activated = True
                    in_fallback = True

        finally:
            if instance == "kare":
                _kare_active = False

    # ── 9B fallback (chat endpoint) ───────────────────────────────────────────
    if in_fallback:
        cfg_fb      = CFG.get("fallback", {})
        fb_url      = cfg_fb.get("base_url", "http://127.0.0.1:11445")
        fb_model    = _get_model(cfg_fb.get("model_role", "miss_kare"))
        fb_options  = cfg_fb.get("options", {})
        fb_timeout  = float(cfg_fb.get("timeout", 60.0))
        fb_system   = _build_system_fallback()
        # Use the original conversation history with the stripped fallback system prompt
        fb_messages = ([{"role": "system", "content": fb_system}] + messages) if fb_system else messages

        try:
            async with lock_11445("kare_fallback"):
                payload_fb: dict = {
                    "model":    fb_model,
                    "messages": fb_messages,
                    "tools":    tools,
                    "stream":   cfg_fb.get("stream", False),
                    "options":  _clean_ollama_options(fb_options),
                    "think":    cfg_fb.get("think", False),
                }
                async with httpx.AsyncClient(timeout=fb_timeout) as client:
                    r = await client.post(f"{fb_url}/api/chat", json=payload_fb)
                    r.raise_for_status()
                    data = r.json()

            msg        = data.get("message", {})
            tool_calls = msg.get("tool_calls")
            content    = (msg.get("content") or "").strip()
            increment_turn()

            return {
                "ok":         True,
                "text":       content,
                "tool_calls": tool_calls,
                "message":    msg,
                "meta": {
                    "instance":             "fallback_9b",
                    "fallback_activated":   _newly_activated,
                    "fallback_deactivated": False,
                    "fallback_info":        None,
                },
            }

        except Exception as e:
            logger.warning("ask_llm_with_tools: 9B fallback feilet (%s)", e)

    return {
        "ok":         False,
        "text":       "Jeg får ikke kontakt med noen av systemene akkurat nå.",
        "tool_calls": None,
        "message":    {},
        "meta": {
            "instance":             "none",
            "fallback_activated":   False,
            "fallback_deactivated": False,
            "fallback_info":        None,
        },
    }


async def _call_cloud_brief(prompt: str) -> Dict[str, Any]:
    """
    Cloud-fallback with Kåre's system prompt and a hard token limit.
    Used only as last resort when the local kare model is down.
    Never receives STM context — raw user text only.
    """
    env = _load_env_file("/kaare/configs/nvidia.env")
    api_key = env.get("CLOUD_API_KEY") or env.get("NVIDIA_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "no_api_key", "text": ""}

    cfg_default = CFG["default"]
    cfg_cloud = CFG.get("cloud", {})

    system = _build_system(cfg_default.get("system", ""))

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": _get_model(cfg_cloud.get("model_role", "cloud")),
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 300,   # hard limit — no essays in fallback mode
        "stream": False,
    }

    base_url = cfg_cloud.get("base_url", "")
    if not base_url:
        return {"ok": False, "error": "no_base_url", "text": ""}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code >= 400:
            return {"ok": False, "error": f"http_{r.status_code}", "text": ""}
        data = r.json()

    text = _extract_text(data)
    return {
        "ok": bool(text),
        "text": text.strip(),
        "meta": {"role": "cloud_llm", "model": payload["model"]},
    }


def _load_env_file(path: str) -> Dict[str, str]:
    """Leser en enkel KEY=VALUE .env-fil."""
    result: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    result[key.strip()] = val.strip()
    except Exception:
        pass
    return result


async def ask_llm_cloud(prompt: str) -> Dict[str, Any]:
    """
    Query the configured cloud LLM (OpenAI-compatible API).
    Provider and URL come from configs/llm.yaml [cloud].
    API key is read from configs/nvidia.env (CLOUD_API_KEY).
    """
    env = _load_env_file("/kaare/configs/nvidia.env")
    api_key = env.get("CLOUD_API_KEY") or env.get("NVIDIA_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "no_api_key", "text": ""}

    cfg = CFG.get("cloud", {})
    base_url = cfg.get("base_url", "")
    if not base_url:
        return {"ok": False, "error": "no_base_url", "text": ""}
    model = _get_model(cfg.get("model_role", "cloud"))
    system = cfg.get("system")

    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system.strip()})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": cfg.get("temperature", 0.2),
        "max_tokens": cfg.get("max_tokens", 2048),
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code >= 400:
            logger.warning("ask_llm_cloud: %s — %s", r.status_code, r.text[:300])
            return {"ok": False, "error": f"http_{r.status_code}", "text": ""}
        data = r.json()

    text = _extract_text(data)
    return {
        "ok": bool(text),
        "text": text.strip(),
        "meta": {"role": "cloud_llm", "model": model},
    }


async def call_llm_chat(
    role: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]] | None = None,
    options: Dict[str, Any] | None = None,
    timeout: float | None = None,
    disable_thinking: bool = False,
) -> Dict[str, Any]:
    """
    Provider-agnostic multi-turn chat call for background jobs and agents.

    Unlike ask_llm(), does NOT inject a system prompt — caller controls messages
    entirely. Provider (vLLM / Ollama) is read from llm.yaml for the given role.

    Returns {"ok": bool, "text": str, "tool_calls": ..., "message": ..., "meta": {...}}.
    """
    cfg = CFG.get(role)
    if not cfg:
        return {"ok": False, "error": f"unknown_role:{role}", "text": "", "tool_calls": None}

    base_url = cfg["base_url"]
    model    = _get_model(cfg["model_role"])
    _opts    = options if options is not None else cfg.get("options")
    _timeout = timeout if timeout is not None else cfg.get("timeout")
    provider = cfg.get("provider", "ollama")

    if provider == "vllm":
        try:
            return await _call_vllm_chat(
                role=role,
                base_url=base_url,
                model=model,
                messages=messages,
                tools=tools,
                options=_opts,
                timeout=_timeout,
                disable_thinking=disable_thinking,
            )
        except Exception as exc:
            logger.warning("call_llm_chat(%s) vLLM feilet: %s", role, exc)
            return {"ok": False, "error": str(exc), "text": "", "tool_calls": None}

    # Ollama /api/chat
    payload: dict = {
        "model":    model,
        "messages": messages,
        "stream":   False,
        "options":  _opts or {},
        "think":    cfg.get("think", False),
    }
    if tools:
        payload["tools"] = tools
    try:
        async with httpx.AsyncClient(timeout=_timeout) as client:
            r = await client.post(
                f"{base_url}/api/chat",
                json=payload,
                headers={"x-kaare-source": "kaare"},
            )
            r.raise_for_status()
            data = r.json()
        msg = data.get("message", {})
        content = (msg.get("content") or "").strip()
        think_end = content.upper().find("</THINK>")
        if think_end != -1:
            content = content[think_end + len("</THINK>"):].strip()
        tool_calls = msg.get("tool_calls")
        return {
            "ok":         bool(content or tool_calls),
            "text":       content,
            "tool_calls": tool_calls,
            "message":    msg,
            "meta":       {"role": role, "model": model, "base_url": base_url},
        }
    except Exception as exc:
        logger.warning("call_llm_chat(%s) feilet: %s", role, exc)
        return {"ok": False, "error": str(exc), "text": "", "tool_calls": None}


async def ask_vlm(prompt: str, images: List[str]) -> Dict[str, Any]:
    cfg = CFG["vision"]
    return await _call_ollama(
        role="vision",
        base_url=cfg["base_url"],
        model=_get_model(cfg["model_role"]),
        prompt=prompt,
        images=images,
        stream=cfg.get("stream", False),
        options=cfg.get("options"),
        system=cfg.get("system"),
    )


def reload_config() -> list[str]:
    """Hot-reload all file-based config caches without restarting the service."""
    global CFG, _CAPABILITY_MAP, _LOKASJON_BLOKK, _ASSISTANT_NAME_BLOKK, _LANGUAGE_BLOKK, _LOCAL_TZ, _PERSONALITY_CORE, _PERSONALITY_MODE, _PERSONALITIES, _PERSONALITY_SELF, _HOUSEHOLD_BLOCK, _PERSONALITY_CORES, _PERSONALITY_CORES_LETVEKT, _PERSONALITY_CORES_MINIMAL
    reloaded = []

    try:
        CFG = yaml.safe_load(open(CFG_PATH, "r", encoding="utf-8"))
        reloaded.append("llm.yaml")
    except Exception:
        pass

    try:
        _CAPABILITY_MAP = yaml.safe_load(open(_CAPABILITY_MAP_PATH, "r", encoding="utf-8")) or {}
        reloaded.append("capability_map.yaml")
    except Exception:
        pass

    try:
        _s = yaml.safe_load(open(_SETTINGS_PATH, "r", encoding="utf-8")) or {}
        _lok = _s.get("location") or _s.get("lokasjon", {})
        if _lok:
            _city = _lok.get("city") or _lok.get("sted", "")
            _postal = _lok.get("postal_code") or _lok.get("postnummer", "")
            _country = _lok.get("country") or _lok.get("land", "")
            _LOKASJON_BLOKK = (
                f"# Lokasjon\n"
                f"Jeg bor i {_city} ({_postal}) i {_country}. "
                f"Bruk dette automatisk ved vær- og stedsspesifikke søk."
            )
            _LOCAL_TZ = _get_local_tz()
        else:
            _LOKASJON_BLOKK = ""
        _aname = _s.get("assistant_name", "").strip()
        _ASSISTANT_NAME_BLOKK = f"Ditt navn er {_aname}. Brukere kaller deg dette." if _aname else ""
        _lang = _s.get("kare_language") or _s.get("language", "nb")
        _LANGUAGE_BLOKK = _kare_lang_instruction(_lang)
        reloaded.append("settings.yaml")
    except Exception:
        pass

    try:
        _s2 = yaml.safe_load(open(_SETTINGS_PATH, "r", encoding="utf-8")) or {}
        _PERSONALITY_MODE = _s2.get("personality_mode", "standard")
        _rl_lang = _s2.get("kare_language") or _s2.get("language", "nb")
        _PERSONALITY_CORES["nb"] = _load_text("/kaare/configs/personality_core.md")
        _PERSONALITY_CORES["en"] = _load_text("/kaare/configs/personality_core_en.md")
        _PERSONALITY_CORES["de"] = _load_text("/kaare/configs/personality_core_de.md")
        _PERSONALITY_CORES_LETVEKT["nb"] = _load_text("/kaare/configs/personality_core_letvekt.md")
        _PERSONALITY_CORES_LETVEKT["en"] = _load_text("/kaare/configs/personality_core_letvekt_en.md")
        _PERSONALITY_CORES_LETVEKT["de"] = _load_text("/kaare/configs/personality_core_letvekt_de.md")
        _PERSONALITY_CORES_MINIMAL["nb"] = _load_text("/kaare/configs/personality_core_minimal.md")
        _PERSONALITY_CORES_MINIMAL["en"] = _load_text("/kaare/configs/personality_core_minimal_en.md")
        _PERSONALITY_CORES_MINIMAL["de"] = _load_text("/kaare/configs/personality_core_minimal_de.md")
        if _PERSONALITY_MODE == "egendefinert":
            _PERSONALITY_CORE = _load_text("/kaare/configs/personality_core_custom.md") or _get_personality_core(_rl_lang, "standard")
        else:
            _PERSONALITY_CORE = _get_personality_core(_rl_lang, _PERSONALITY_MODE)
        reloaded.append(f"personality_core ({_PERSONALITY_MODE}/{_rl_lang})")
    except Exception:
        pass

    try:
        new_p: dict[str, str] = {}
        for _pp in _glob.glob("/kaare/configs/personalities/*.md"):
            _key = os.path.splitext(os.path.basename(_pp))[0]
            _text = _load_text(_pp)
            if _text:
                new_p[_key] = _text
        _PERSONALITIES = new_p
        reloaded.append(f"personalities/ ({len(_PERSONALITIES)})")
    except Exception:
        pass

    try:
        _PERSONALITY_SELF = _cap_personality_self(_load_text("/kaare/state/personality_self.md"))
        reloaded.append("personality_self.md")
    except Exception:
        pass

    try:
        _HOUSEHOLD_BLOCK = _build_household_block()
        reloaded.append("household_visible")
    except Exception:
        pass

    return reloaded
