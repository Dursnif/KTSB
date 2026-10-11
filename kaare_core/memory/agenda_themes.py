"""
Themes for the dev-meeting agenda (P118 B8, B8-D1–D3).

Every agenda row gets one theme when it is written, so the nightly dev slot can take topics
from each theme instead of letting one noisy area fill the whole meeting. The writer picks the
theme in the same call (Kåre: `theme` on `note(list_name='agenda')`; GUI: dropdown). When a
write has no valid theme, `resolve_theme()` sets it by code: the source, then the tools that
ran in the same rid (live user turns only), then a small nb/en/de keyword table, else `other`.
The value is stored once and never re-judged.
"""

from __future__ import annotations

import re
from collections import Counter

# Fixed list (Stian 2026-10-09). Order = round-robin order in the dev slot; `other` last.
THEMES = ("answers", "tools", "memory", "agents", "house", "system", "other")
FALLBACK_THEME = "other"

# Sources where the tools in the same rid say what the row is about: a live turn's tools are
# the turn itself. The reflection loop and meetings call review tools (self_trace,
# inner_thoughts …) whatever the topic is, so their tools are no hint.
TOOL_HINT_SOURCES = {"user", "gui", "stt", "admin", "frigate", "timer"}

_TOOL_THEMES = {
    "camera": "house",
    "household": "house",
    "ha_read": "house",
    "ha_control": "tools",
    "timer": "tools",
    "media": "tools",
    "announce": "tools",
    "web_search": "tools",
    "get_weather": "tools",
    "library": "tools",
    "kare_image": "tools",
    "view_images": "tools",
    "memory": "memory",
    "user_profile": "memory",
    "world": "memory",
    "self_image": "memory",
    "mechanic": "agents",
    "inner_thoughts": "agents",
    "thought_history": "agents",
    "reason_freely": "agents",
    "self_trace": "system",
    "search_argus": "system",
    "restart_docker_container": "system",
}

# Whole words only: «bil» must not hit «bilde»; a hyphen is a boundary («Jang-refleksjon»).
# nb / en / de in one pattern per theme.
_KEYWORDS = {
    "answers": r"svar\w*|answer\w*|antwort\w*|språk\w*|language|sprache|formulering\w*|"
    r"misforst\w*|misunderst\w*|hallusin\w*|hallucinat\w*|halluzin\w*|personlighet\w*|"
    r"personality|persönlichkeit|tone|tonen",
    "tools": r"verktøy\w*|tool\w*|werkzeug\w*|ha|ha_\w+|home assistant|alias\w*|entity|"
    r"entitet\w*|entität\w*|timer\w*|radio|plex|media\w*|weather\.\w+|nettsøk\w*|websearch|"
    r"web_search|tool_call\w*",
    "memory": r"minne\w*|hukommelse\w*|memory|memories|erinnerung\w*|gedächtnis\w*|ltm|stm|"
    r"qdrant|embedding\w*|semantisk\w*|semantic|semantisch\w*|knowledge\w*|kunnskap\w*|"
    r"wissen\w*",
    "agents": r"miss kåre|miss library|frøken library|library|mechanic|jing\w*|jang\w*|"
    r"agent\w*|refleksjon\w*|reflection\w*|reflexion\w*",
    "house": r"kamera\w*|camera\w*|frigate|sensor\w*|motion|bevegelse\w*|beweg\w*|flakk\w*|"
    r"flapp\w*|flap\w*|ringeklokke\w*|doorbell|türklingel\w*|deteksjon\w*|detection\w*|"
    r"erkennung\w*|tilhenger\w*|trailer|anhänger\w*|occupancy|bil|bilen|biler|car|cars|"
    r"kjøretøy\w*|vehicle\w*|fahrzeug\w*|cam_\w+|binary_sensor\.\w+|garasje\w*|"
    r"garage|lys|lyset|light|lights|licht|lampe\w*|lamp\w*|temperatur\w*|temperature",
    "system": r"modell\w*|model\w*|vllm|ollama|fallback\w*|token\w*|gpu|vram|latens\w*|"
    r"latency|ytelse\w*|performance|leistung|timeout\w*|restart\w*|docker|container\w*|"
    r"argus|logg\w*|log|logs|disk|cpu|npu|tjeneste\w*|service\w*|dienst\w*",
}
# Who said it, not what it is about: «[Jang 2026-10-03 17:44] …», «09:30 (Jang): …», «Jing: …».
# Removed before keyword matching so reflection rows are not all filed under `agents`.
_SPEAKER_RE = re.compile(
    r"[\[(]\s*(?:jing|jang|argus|kåre|miss kåre)\b[^\])]{0,40}[\])]\s*:?|"
    r"(?<!\w)(?:jing|jang)\s*:",
    re.IGNORECASE,
)
_KEYWORD_RES = {
    theme: re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", re.IGNORECASE)
    for theme, pattern in _KEYWORDS.items()
}


def normalize_theme(value) -> str | None:
    """A valid theme key, or None (empty, unknown, wrong type)."""
    key = str(value or "").strip().lower()
    return key if key in THEMES else None


def _pick(counts: Counter) -> str | None:
    """Most hits wins; a tie goes to the earlier theme in THEMES."""
    if not counts:
        return None
    best = max(counts.values())
    return next(t for t in THEMES if counts.get(t) == best)


def theme_from_tools(tools: list[str]) -> str | None:
    counts = Counter(_TOOL_THEMES[t] for t in tools if t in _TOOL_THEMES)
    return _pick(counts)


def theme_from_text(text: str, allowed: tuple[str, ...] | None = None) -> str | None:
    counts: Counter = Counter()
    text = _SPEAKER_RE.sub(" ", text or "")
    for theme, rx in _KEYWORD_RES.items():
        if allowed and theme not in allowed:
            continue
        hits = len(rx.findall(text))
        if hits:
            counts[theme] = hits
    return _pick(counts)


def resolve_theme(
    theme=None,
    *,
    text: str = "",
    context: str = "",
    source: str = "",
    rid_tools: list[str] | None = None,
) -> str:
    """The theme to store for one agenda row. Writer's choice first; code rules only when the
    writer gave none (B8-D2: B with A as fallback)."""
    chosen = normalize_theme(theme)
    if chosen:
        return chosen
    words = f"{text or ''} {context or ''}"
    if source == "review":
        # A reviewed user turn is about an answer, unless the review names a tool problem.
        return theme_from_text(words, allowed=("answers", "tools")) or "answers"
    if source in TOOL_HINT_SOURCES and rid_tools:
        by_tools = theme_from_tools(rid_tools)
        if by_tools:
            return by_tools
    return theme_from_text(words) or FALLBACK_THEME
