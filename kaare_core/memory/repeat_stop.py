"""The night meeting's three-night stop (P149 D5/D6, run-queue J4; main session's decision 2026-11-06).

A theme Kåre or Miss Kåre proposes is compared with the themes of the last nights by ONE lean main-model call that sees
only the new proposal and the earlier themes with their dates — not who proposed them, not the leader's reasons — and
answers in a closed field: which earlier night it repeats, or «new». The LLM recognises paraphrase; CODE counts the
chain and enforces: at night 3 the Model Enforcer detector `repeated_theme` (profile `reflection_topic`, shadow until
measured, GUI mode) refuses it. Every night of a chain is a *self* evidence row in weights.db (Worry one step per
repeat, never Belief — echo rule); a refused chain is parked and becomes one neutral question.

Beside it (B, shadow): the semantic-embed cosine of the proposal against each earlier theme is logged as a number
only (measured 2026-11-06: too weak to refuse on — 0.70 caught 41 % of repeats, joined 2.8 % of others).
Nothing here runs unless the meeting calls it; no text of the user's meetings is stored in weights.db (keys/pointers only).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from kaare_core import weights
from kaare_core.tools.i18n import t

logger = logging.getLogger(__name__)

REFLECTIONS_BASE = Path("/kaare/state/memory/reflections")
LOOKBACK_NIGHTS = 8
STOP_AT_NIGHT = 3  # D6: «maks 3 ganger … før det stoppes»
WORRY_PER_REPEAT = 0.1  # W9 small step; repetition raises the urge to ask, never the belief (D5)
EMBED_SHADOW_TH = 0.70
_OPENING_RE = {who: re.compile(r"\*\*\[" + re.escape(who) + r"\]\*\*\n(.+?)(?:\n\n|\Z)", re.S)
               for who in ("Kåre", "Miss Kåre")}
_ANSWER_RE = re.compile(r"\bN(\d{1,2})\b")


@dataclass
class Theme:
    night: str  # YYYY-MM-DD
    who: str
    text: str


@dataclass
class Verdict:
    repeats: str | None  # the earlier night it repeats, or None (new / unknown)
    chain: str  # the chain id: the first night of the chain
    nights: int  # nights this chain has been raised, this one included
    embed_best: float | None = None
    raw: str = ""
    earlier: list[str] = field(default_factory=list)
    saved: bool = True  # N39: False when the night's row could not be written (it stays queued and is retried)


def earlier_themes(user_dir: Path, before: str, nights: int = LOOKBACK_NIGHTS) -> list[Theme]:
    """The opening themes (Kåre's and Miss Kåre's first paragraph) of the last `nights` reports before `before`."""
    files = sorted(p for p in Path(user_dir).glob("20??-??-??.md") if p.stem < before)[-nights:]
    out: list[Theme] = []
    for f in files:
        try:
            txt = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for who, rx in _OPENING_RE.items():
            m = rx.search(txt)
            if m and not m.group(1).startswith("[Unavailable"):
                out.append(Theme(f.stem, who, m.group(1).strip()[:400]))
    return out


def build_prompt(proposal: str, earlier: list[Theme], lang: str) -> tuple[str, dict[str, str]]:
    """Closed-form prompt: numbered earlier themes with their date, then the new proposal. Never who proposed what.
    Returns (prompt, {"N1": night, …})."""
    ids, lines = {}, []
    for i, th in enumerate(earlier, 1):
        ids[f"N{i}"] = th.night
        lines.append(f"N{i} ({th.night}): {th.text}")
    return t("rs_prompt", lang, earlier="\n".join(lines), proposal=proposal[:400]), ids


def parse_answer(text: str, ids: dict[str, str]) -> str | None:
    """«N3» → that night; «new» or anything unreadable → None (never a guess)."""
    raw = (text or "").strip()
    try:
        obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]) if "{" in raw else {}
        raw = str(obj.get("answer", raw))
    except (ValueError, AttributeError):
        pass
    m = _ANSWER_RE.search(raw.upper())
    return ids.get(f"N{m.group(1)}") if m else None


def _subject(user: str, chain: str) -> tuple[str, str, str]:
    return ("topic", f"refl:{user}:{chain}", user)


def _pointer(user: str, night: str, who: str = "") -> str:
    return f"theme:{user}:{night}:{who}" if who else f"theme:{user}:{night}"


def _chain_of(user: str, night: str, who: str = "") -> str | None:
    """The chain an earlier night's theme belongs to (pointer «theme:<user>:<night>[:<who>]»), if any."""
    st = weights.get_store()
    for kind, key, scope in st.subjects("topic"):
        if scope != user or not key.startswith(f"refl:{user}:"):
            continue
        rows = st._rows.get((kind, key, scope), {}).get("evidence") or []
        if any(r.get("pointer") == _pointer(user, night, who) for r in rows):
            return key.split(":", 2)[2]
    return None


def record(user: str, night: str, repeats: str | None, who: str = "") -> Verdict:
    """Code counts: a repeat joins the earlier night's chain, a new theme starts one. One self row per night (Worry
    step only when it repeats). Returns the chain and how many nights it has been raised."""
    chain = (_chain_of(user, repeats, who) or repeats) if repeats else night
    subj = _subject(user, chain)
    weights.record(subj, source_class="self", source=f"reflection:{who}" if who else "reflection", context=night,
                   pointer=_pointer(user, night, who),
                   worry=WORRY_PER_REPEAT if repeats else None)
    saved = weights.flush_ok()
    st = weights.get_store()
    rows = st._rows.get(subj, {}).get("evidence") or []
    nights = len({r.get("context") for r in rows if r.get("context")})
    return Verdict(repeats=repeats, chain=chain, nights=nights, saved=saved)


def park(user: str, chain: str) -> bool:
    """A refused chain is parked (P14) — one neutral question waits for the user; nothing is deleted. Returns whether
    the park row is saved (N39)."""
    weights.park(_subject(user, chain), reason="night_meeting_round_limit")
    return weights.flush_ok()


def cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
    den = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return num / den if den else 0.0


async def judge(user: str, proposal: str, night: str, lang: str, *, llm, embed=None, who: str | None = None,
                base: Path | None = None) -> Verdict:
    """One proposal: the lean call names the night it repeats (or new), code records and counts. `llm(prompt) -> str`
    and `embed(texts) -> vectors | None` are passed in (the meeting gives the real ones, tests give fakes). `who`: code
    compares with that participant's own earlier openings (measured 2026-11-06: fewer false repeats); the model is never
    told who. Never raises."""
    earlier = earlier_themes(Path(base or REFLECTIONS_BASE) / user, night)
    if who:
        earlier = [th for th in earlier if th.who == who]
    raw, repeats = "", None
    if earlier:
        prompt, ids = build_prompt(proposal, earlier, lang)
        try:
            raw = await llm(prompt)
            repeats = parse_answer(raw, ids)
        except Exception as e:
            logger.warning("repeat_stop: call failed (%s) — counted as new", type(e).__name__)
    v = record(user, night, repeats, who or "")
    v.raw, v.earlier = raw[:80], [th.night for th in earlier]
    if embed and earlier:
        try:
            vecs = await embed([proposal, *(th.text for th in earlier)])
            if vecs:
                v.embed_best = max(cosine(vecs[0], x) for x in vecs[1:])
        except Exception as e:
            logger.debug("repeat_stop: embed shadow failed (%s)", type(e).__name__)
    logger.info("[REPEAT_STOP] user=%s night=%s repeats=%s chain=%s nights=%d embed_best=%s (B shadow, th %.2f)",
                user, night, v.repeats, v.chain, v.nights,
                f"{v.embed_best:.3f}" if v.embed_best is not None else "-", EMBED_SHADOW_TH)
    return v


REFUSAL_ALLOWED = False  # main session 2026-11-06: no refusal before a measurement shows the call is right (J4 log)


async def _lean_call(prompt: str, rid: str) -> str:
    # PLC0415: heavy import side effects (the LLM stack) — only when the meeting really calls
    from adapters.llm_adapter import call_llm_chat  # noqa: PLC0415
    r = await call_llm_chat("default", [{"role": "user", "content": prompt}], disable_thinking=True, rid=rid,
                            source="refl", timeout=90)
    return r.get("text") or ""


async def _embed(texts: list[str]):
    # PLC0415: heavy import (the Qdrant client)
    from kaare_core.memory.semantic_memory import embed_texts  # noqa: PLC0415
    return await embed_texts(texts)


async def shadow_check(user: str, proposal: str, who: str, lang: str, rid: str = "", night: str | None = None) -> dict:
    """The meeting's hook (J4, shadow): judge the proposal, count the chain, let the `repeated_theme` detector see the
    facts (its GUI mode decides shadow logging), park when it would refuse. The proposal itself is never changed while
    REFUSAL_ALLOWED is False. Returns {nights, chain, repeats, would_refuse}. Never raises."""
    # PLC0415: import cycle (model_enforcement → tools → memory)
    from kaare_core.model_enforcement import ModelEnforcer  # noqa: PLC0415

    try:
        v = await judge(user, proposal, night or today(), lang, llm=lambda p: _lean_call(p, rid), embed=_embed, who=who)
        enforcer = ModelEnforcer.for_profile("reflection_topic", lang, rid=rid)
        action = enforcer.check(text_out=proposal, used_tools=[],
                                meta={"theme_nights": v.nights, "theme_stop_at": STOP_AT_NIGHT})
        would = action is not None or v.nights >= STOP_AT_NIGHT
        saved = v.saved
        if would:
            saved = park(user, v.chain) and saved
        return {"nights": v.nights, "chain": v.chain, "repeats": v.repeats, "would_refuse": would,
                "refuse": bool(action) and REFUSAL_ALLOWED, "saved": saved}
    except Exception as e:
        logger.warning("repeat_stop: shadow check failed (%s)", type(e).__name__)
        return {"nights": 0, "chain": "", "repeats": None, "would_refuse": False, "refuse": False, "saved": False}


def today() -> str:
    return _date.today().isoformat()
