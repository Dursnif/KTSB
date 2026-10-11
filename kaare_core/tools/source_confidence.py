"""
How likely a claim is, from where it comes from (P121 Phase 4) — arithmetic in code, never the
model's guess. Stian's model (P121 D4–D10): knowing is binary; everything else is «don't know»
with a probability, said in words.

  1. Each supporting source has a value by kind (D7/D9.2, «Balanced» defaults):
     public authority 0.90 · official site / encyclopedia / newsroom / research / other trusted
     0.85 · Wikipedia and the local wiki 0.80 · open web 0.60. Trust «high» halves the doubt,
     «low» doubles it. The caution setting scales doubt (careful ×1.5, open ×0.75).
  2. Combined in *doubt* (1 − p): the strongest source first; a source of the same kind, the
     same site, or with nearly the same text halves the doubt; an independent source multiplies
     it by its own doubt (D7, D-P11).
  3. Caps by the best kind present (D10.2): open web only ≤ 0.85; Wikipedia/wiki only ≤ 0.90;
     one public-authority/official/trusted source ≤ 0.95; two independent ones ≤ 0.99.
     Nothing from the web reaches 1.0.
  4. Conflicting claims about the same topic weigh against each other:
     p_a' = p_a(1 − p_b) / (p_a(1 − p_b) + p_b(1 − p_a)).
  5. Words (D5, Q1): ≥ 0.99 highly likely · 0.75–0.98 likely · 0.51–0.74 uncertain · ≤ 0.50 unlikely.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from kaare_core.config import get_service

_BASE = {
    "public_authority": 0.90,
    "official": 0.85,
    "encyclopedia": 0.85,
    "newsroom": 0.85,
    "research": 0.85,
    "other_serious": 0.85,
    "wiki": 0.80,
    "open": 0.60,
}
_HIGH_CALIBRE = {
    "public_authority",
    "official",
    "encyclopedia",
    "newsroom",
    "research",
    "other_serious",
}
_CAUTION_DOUBT = {"careful": 1.5, "balanced": 1.0, "open": 0.75}
_CAP_OPEN_ONLY = 0.85
_CAP_WIKI_BEST = 0.90
_CAP_ONE_HIGH = 0.95
_CAP_MAX = 0.99
_WORD_THRESHOLDS = ((0.99, "highly_likely"), (0.75, "likely"), (0.51, "uncertain"))
_SHINGLE_RE = re.compile(r"\w+", re.UNICODE)


def caution() -> str:
    value = str((get_service("web_search") or {}).get("caution", "balanced")).strip().lower()
    return value if value in _CAUTION_DOUBT else "balanced"


def source_kind(src: dict) -> str:
    """The kind that decides a source's value: wiki, official, a whitelist type, or open."""
    host = (urlparse(src.get("url", "")).hostname or "").lower()
    if src.get("wiki") or host == "wikipedia.org" or host.endswith(".wikipedia.org"):
        return "wiki"
    tier = src.get("tier")
    if tier == "official":
        return "official"
    if tier == "trusted":
        return src.get("type") if src.get("type") in _BASE else "other_serious"
    return "open"


def source_probability(src: dict, mode: str = "balanced") -> float:
    doubt = 1.0 - _BASE[source_kind(src)]
    trust = src.get("trust")
    if trust == "high":
        doubt /= 2
    elif trust == "low":
        doubt *= 2
    doubt *= _CAUTION_DOUBT.get(mode, 1.0)
    return max(0.05, min(_CAP_MAX, 1.0 - doubt))


def _site(src: dict) -> str:
    if src.get("wiki"):
        return f"wiki:{src['wiki']}"
    host = (urlparse(src.get("url", "")).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _shingles(text: str) -> set[tuple]:
    words = [w.lower() for w in _SHINGLE_RE.findall(text or "")]
    return {tuple(words[i : i + 3]) for i in range(max(0, len(words) - 2))}


def related(a: dict, b: dict) -> bool:
    """Same kind, same site, or nearly the same quoted text (a copy counts once, D-P11)."""
    if source_kind(a) == source_kind(b) or _site(a) == _site(b):
        return True
    sa, sb = _shingles(a.get("_quote", "")), _shingles(b.get("_quote", ""))
    return bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) >= 0.8


def combine(supporters: list[dict], mode: str = "balanced") -> float:
    """Probability from the distinct sources backing one claim, with the caps."""
    if not supporters:
        return 0.0
    ranked = sorted(supporters, key=lambda s: source_probability(s, mode), reverse=True)
    doubt = 1.0 - source_probability(ranked[0], mode)
    for i, src in enumerate(ranked[1:], start=1):
        if any(related(src, prev) for prev in ranked[:i]):
            doubt *= 0.5
        else:
            doubt *= 1.0 - source_probability(src, mode)
    p = 1.0 - doubt
    kinds = [source_kind(s) for s in ranked]
    high = [s for s in ranked if source_kind(s) in _HIGH_CALIBRE]
    if not high:
        cap = _CAP_WIKI_BEST if "wiki" in kinds else _CAP_OPEN_ONLY
    elif len(high) == 1 or all(related(h, high[0]) for h in high[1:]):
        cap = _CAP_ONE_HIGH
    else:
        cap = _CAP_MAX
    return min(p, cap, _CAP_MAX)


def weigh_conflict(p_a: float, p_b: float) -> float:
    """p_a after weighing against a conflicting claim with p_b."""
    num = p_a * (1.0 - p_b)
    den = num + p_b * (1.0 - p_a)
    return num / den if den > 0 else 0.5


def word_key(p: float) -> str:
    for threshold, key in _WORD_THRESHOLDS:
        if p >= threshold:
            return key
    return "unlikely"
