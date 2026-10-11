"""
Model enforcement — forces the LLM to actually do what a round's output claims,
or to produce a real answer when raw generation didn't (e.g. a truncated
<think> block), instead of silently accepting a broken or dishonest response.

One shared mechanism instead of one-off corrections scattered per caller. New
correction needs — as local-model behavior and capabilities change across
generations — are added here as new Detector implementations; detectors that
stop being needed are removed from the registry. Nothing else in the codebase
should need to change.

This is the standard "post-LLM guardrail with a self-correction loop" pattern
(cheap deterministic detection + one targeted retry round, no extra LLM call
needed just to decide whether to retry) — see BUG-log.md 2026-08-28 for the
bug this replaced (a "promise interceptor" that prepared a retry but never
actually sent it) and P88's Phase 2 (the second detector this module was
built to host).

Usage (see kaare_core/routers/router_generate.py for the reference caller):

    enforcer = ModelEnforcer(lang)
    ...
    action = enforcer.check(text_out=text_out, used_tools=used_tools, meta=result.get("meta", {}))
    if action:
        messages.append(result["message"])
        messages.append({"role": "user", "content": action.correction_message})
        continue  # force exactly one more round — enforcer.check() only fires once per instance
    if enforcer.pending:
        text_out = enforcer.resolve(text_out, None)
    break

If the retry round makes tool calls instead, run them and then one more round without tools so
the model answers with the real results in view; resolve() that round's text (N24 A2).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kaare_core import log_buffer
from kaare_core.tools.i18n import t as _t_i18n


@dataclass
class EnforcementAction:
    detector_name: str
    correction_message: str
    original_text: str


class Detector(ABC):
    """One enforcement rule: detect a violation, say what's wrong, decide the
    final answer once the forced retry round has run."""

    name: str
    # The text span that made the detector fire, written to enforcement.log so false hits can be
    # measured afterwards (N24). Detectors without a text match leave it empty.
    matched_phrase: str = ""

    @abstractmethod
    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        """Cheap, deterministic check — no LLM call. Called once per round."""

    @abstractmethod
    def correction_message(self, lang: str) -> str:
        """The nudge sent back to the model for the forced retry round."""

    @abstractmethod
    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        """
        Build the final text_out after the forced retry round completed.

        original_text: text_out from the round that triggered this detector.
        new_text:      text_out from the forced retry round (may be empty if
                        that round made tool calls instead of answering).
        call_results:  (name, args, result) tuples if the retry round made
                        tool calls, else None.
        """


# ── Tool-promise detector ───────────────────────────────────────────────────
# Detects Kåre claiming a tool action ("jeg har notert...") without actually
# calling the tool. Patterns cover nb / en / de. Each entry:
# (expected_tool_names, i18n_correction_key, compiled_pattern).
# «… at the next dev meeting» in nb / en / de. Future-only words: «nattens»/«heutigen» are left
# out because they also name last night's meeting («i nattens dev-møte ble det …»).
_AGENDA_PROMISE_RE = re.compile(
    # nb
    r"\b(neste|kommende)\s+(dev[- ]?møte\w*|utvikler?møte\w*|utviklingsmøte\w*)"
    # en
    r"|\b(next|tonight'?s|upcoming)\s+dev(elopment|eloper)?[- ]?meeting\b"
    # de
    r"|\b(nächsten?|kommenden?)\s+(entwickler|dev)[- ]?(treffen|meeting|besprechung)\b",
    re.IGNORECASE,
)


def mentions_agenda_promise(text: str) -> bool:
    """True when the text says something should go to the next dev meeting (P118). Used by paths
    that run without tools in the main call (Frigate event analysis) to decide whether a
    follow-up agenda round is needed."""
    return bool(text and _AGENDA_PROMISE_RE.search(text))


_TOOL_PROMISE_RULES: list[tuple[set[str], str, re.Pattern]] = [
    (
        # «I'll raise it at the next dev meeting» without writing to the agenda (P118 B1).
        # Checked before the generic note rule; only a note write to list_name='agenda'
        # (tag "note:agenda", see tool_usage_tags) satisfies it — a kare-list note does not.
        {"note:agenda"},
        "gen_promise_correction_agenda",
        _AGENDA_PROMISE_RE,
    ),
    (
        {"note"},
        "gen_promise_correction_note",
        re.compile(
            # nb
            r"\bjeg\s+(noterte|noterer|har\s+notert|skriver?\s+det\s+ned|har\s+skrevet"
            r"|vil\s+huske|la\s+det\s+til|har\s+lagt\s+(det\s+)?til"
            r"|følger\s+opp|merker\s+meg|noterer\s+meg)\b"
            r"|\b(huskelisten\s+min|min\s+huskeliste|på\s+lista\s+mi"
            r"|lagt\s+til\s+på\s+(min|kåres?)\s+(liste|huskeliste))\b"
            # en
            r"|I'?ve?\s+(noted\s+that|jotted\s+that\s+down|written\s+(that\s+)?down"
            r"|added\s+(it\s+)?to\s+(my\s+)?(list|notes?))\b"
            r"|\bI('ll|'m going to)\s+(note|jot|write\s+(that\s+)?down)\b"
            # de
            r"|\bich\s+(habe\s+(es|das)\s+notiert|notiere\s+(es|das|mir)|schreibe\s+(es|das)\s+auf"
            r"|merke\s+mir\s+(das|es))\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"timer"},
        "gen_promise_correction_timer",
        re.compile(
            # nb
            r"\bjeg\s+(setter\s+(en\s+)?(timer|påminnelse|alarm)"
            r"|minner\s+deg\s+om|vil\s+minne\s+deg|kommer\s+til\s+å\s+minne"
            r"|har\s+satt\s+(en\s+)?(timer|påminnelse))\b"
            r"|\b(timer\s+er\s+satt|påminnelse\s+er\s+(satt|opprettet))\b"
            # en
            r"|I('ll|'ve|'m going to)\s+(set\s+(a\s+)?(timer|reminder|alarm)"
            r"|remind\s+you)\b"
            r"|\bI('ve|have)\s+set\s+(a\s+)?(timer|reminder)\b"
            # de
            r"|\bich\s+(stelle\s+(einen?\s+)?(timer|wecker|erinnerung)"
            r"|erinnere\s+dich|werde\s+(dich\s+)?erinnern"
            r"|habe\s+(einen?\s+)?(timer|erinnerung)\s+gestellt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"user_profile"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r"\bjeg\s+(oppdaterer\s+(profilen\s+din|brukerinfo(en)?)"
            r"|lagrer\s+det\s+i\s+profilen|har\s+(oppdatert|lagret)\s+(profilen|det\s+i\s+profilen))\b"
            # en
            r"|I('ve|'ll)\s+(updated?|saved?|stored?)\s+(your\s+)?(profile|user\s+info|preferences)\b"
            # de
            r"|\bich\s+(habe\s+(dein\s+)?profil\s+aktualisiert"
            r"|aktualisiere\s+(dein\s+)?profil|speichere\s+(es\s+)?in\s+(deinem\s+)?profil)\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"world"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r"\bjeg\s+(oppdaterer\s+verdensbildet|lagrer\s+det\s+i\s+verdensbildet"
            r"|har\s+oppdatert\s+verdensbildet)\b"
            # en
            r"|I('ve|'ll)\s+(updated?|saved?)\s+(the\s+)?(world\s+(model|view|knowledge))\b"
            # de
            r"|\bich\s+(habe\s+(das\s+)?weltbild\s+aktualisiert|aktualisiere\s+(das\s+)?weltbild)\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"memory"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r"\bjeg\s+(lagrer?\s+det\s+i\s+minnet|har\s+lagret\s+det\s+i\s+minnet"
            r"|søker\s+i\s+minnet\s+etter)\b"
            # en
            r"|I('ve|'ll)\s+(saved?\s+(that\s+)?to\s+(my\s+)?memory|stored?\s+that\s+in\s+memory)\b"
            # de
            r"|\bich\s+(speichere\s+(es\s+)?im\s+(gedächtnis|speicher)"
            r"|habe\s+(es\s+)?im\s+gedächtnis\s+gespeichert)\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"household"},
        "gen_promise_correction_household",
        re.compile(
            # nb (covers both "jeg setter X" and V2 word order "setter jeg X")
            r"\b(jeg\s+(setter|aktiverer|har\s+(satt|aktivert))|(setter|aktiverer|har\s+(satt|aktivert))\s+jeg)"
            r'\s+(husstanden|huset|dere)\s+(til\s+)?["\']?(borte\S*|bortreise\S*|hjemme\S*)\b'
            r"|\b(bortreisemodus|borte-?modus)\s+(er\s+)?(nå\s+)?(aktivert|satt|slått\s+på|skrudd\s+på)\b"
            r"|\bhusstanden\s+er\s+(nå\s+)?satt\s+til\s+(borte|hjemme)\b"
            # en
            r"|\bI('ve|'ll|'m going to)\s+(set|activate|switch)\s+(the\s+)?(household|house)\s+(to\s+)?(away|home)\b"
            r"|\baway\s+mode\s+is\s+(now\s+)?(activated|set|on)\b"
            # de
            r"|\bich\s+(setze|aktiviere)\s+den\s+(haushalt|abwesenheitsmodus)\b"
            r"|\bhabe\s+den\s+haushalt\s+auf\s+(abwesend|zuhause)\s+gesetzt\b"
            r"|\babwesenheitsmodus\s+ist\s+(jetzt\s+)?(aktiviert|eingeschaltet|gesetzt)\b",
            re.IGNORECASE,
        ),
    ),
]


# ── Sentence-level filters (N24) ────────────────────────────────────────────
# A false hit pushes the model into a write the user never asked for, which is worse than a missed
# claim, so a sentence is skipped when it is a question, an offer or a condition, or when the matched
# claim is negated, or when it describes how things already are. Curly apostrophes are normalised first («I’ve» must match like «I've»).
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u00b4": "'"})
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")
_OFFER_RE = re.compile(
    # nb
    r"\b(skal\s+jeg|kan\s+jeg|vil\s+du\s+(at\s+jeg|jeg\s+skal)|ønsker\s+du\s+at\s+jeg"
    r"|hvis|dersom|om\s+du\s+vil)\b"
    # en
    r"|\b(shall\s+I|should\s+I|can\s+I|do\s+you\s+want\s+me|would\s+you\s+like\s+me"
    r"|want\s+me\s+to|if)\b"
    # de
    r"|\b(soll\s+ich|kann\s+ich|möchtest\s+du|willst\s+du|wenn|falls)\b",
    re.IGNORECASE,
)
# A description of how things already are (or an earlier write), not a claim of a new one («var allerede registrert»,
# «er lagret i minnet fra før»).
_STATE_RE = re.compile(
    r"\b(allerede|fra\s+før|already|bereits|schon"
    r"|er\s+lagret\s+i|is\s+(stored|saved)\s+in|ist\s+in\b)\b",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(ikke|aldri|not|never|nicht|nie|kein\w*)\b|n't\b", re.IGNORECASE)
# Negation counts only close to the claim («jeg noterte det ikke»), not anywhere in the sentence.
_NEG_BEFORE, _NEG_AFTER = 15, 12


def _sentences(text: str) -> list[str]:
    text = strip_language_tag(text or "").translate(_APOSTROPHES)
    return [x for x in _SENTENCE_SPLIT_RE.split(text) if x.strip()]


def _claim_in(sentence: str, pattern: re.Pattern) -> re.Match | None:
    """The first match of `pattern` in `sentence` that is a real claim, or None."""
    if sentence.rstrip().endswith("?") or _OFFER_RE.search(sentence) or _STATE_RE.search(sentence):
        return None
    for m in pattern.finditer(sentence):
        window = sentence[max(0, m.start() - _NEG_BEFORE) : m.end() + _NEG_AFTER]
        if not _NEGATION_RE.search(window):
            return m
    return None


def find_claim(text: str, pattern: re.Pattern) -> str:
    """The matched claim phrase in `text` after the sentence filters, or "" if none."""
    for sentence in _sentences(text):
        m = _claim_in(sentence, pattern)
        if m:
            return m.group(0)
    return ""


def tool_usage_tags(name: str, args: dict | None) -> list[str]:
    """What a tool call counts as for the promise rules: its name, plus "note:agenda" for a
    note write to the dev-meeting agenda (P118 B1). Callers add these to `used_tools`."""
    tags = [name]
    if name in ("note", "notat") and isinstance(args, dict):
        list_name = str(args.get("list_name") or args.get("liste") or "").strip().lower()
        if list_name.startswith("agenda"):
            tags.append("note:agenda")
    return tags


class ToolPromiseDetector(Detector):
    """Kåre's text claims a tool action it never actually called this round."""

    name = "tool_promise"

    def __init__(self) -> None:
        self._matched_key: str | None = None

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        used_set = set(used_tools)
        for expected, corr_key, pattern in _TOOL_PROMISE_RULES:
            if expected & used_set:
                continue  # tool was already called this request
            phrase = find_claim(text_out, pattern)
            if phrase:
                self._matched_key = corr_key
                self.matched_phrase = phrase
                return True
        return False

    def correction_message(self, lang: str) -> str:
        return _t_i18n(self._matched_key or "gen_promise_correction_generic", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # The original text claimed something that had not happened, so it is never the answer
        # (N24 A1). The caller runs one answer round after any tool calls of the retry round, so
        # new_text is the model's own answer with the real tool results in view — no «✓» tag on
        # top (N24 A2): code cannot tell from a tool's text whether the write succeeded.
        return new_text.strip() or _t_i18n("gen_no_response", lang)


# ── Claimed-done detector (safety net, N24) ─────────────────────────────────
# Behind the per-tool rules: the reply says something was written/saved/added while no tool ran at
# all in this request («Ferdig – skrevet til lessons», «Lagt på agendaen som sak #4», «Markert som
# løst» — DT03 R8). Write/save verbs only: device-state words («slått på») are often true without a
# tool call because the house state is in the context; those belong in a per-tool ha_control rule.
_CLAIMED_DONE_RE = re.compile(
    # nb
    r"\b(notert|lagt\s+((det|den|dem|dette|saken)\s+)?(inn\s+)?(på|til|inn)"
    r"|skrevet\s+((det|den|dem|dette)\s+)?(ned|inn|til)|lagret|markert\s+som|ført\s+opp)\b"
    r"|^\s*ferdig\s*[–—:-]"
    # en
    r"|\b(noted|jotted\s+down|written\s+(down|to)|wrote\s+(it\s+|that\s+)?(down|to)"
    r"|added\s+((it|that|this)\s+)?(to|on)|saved\s+((it|that|this)\s+)?(to|in)|marked\s+((it|that)\s+)?as)\b"
    r"|^\s*done\s*[–—:-]"
    # de
    r"|\b(notiert|aufgeschrieben|eingetragen|gespeichert|hinzugefügt|vermerkt|markiert\s+als)\b"
    r"|^\s*erledigt\s*[–—:-]",
    re.IGNORECASE,
)
# The user (not Kåre) did it: «du har lagt til melk».
_OTHER_SUBJECT_RE = re.compile(
    r"\b(du|dere|you|ihr)\s+(har|hadde|have|had|hast|habt)\b", re.IGNORECASE
)


class ClaimedDoneDetector(Detector):
    """The reply claims a write/save although no tool was executed in this request."""

    name = "claimed_done"

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""
        if used_tools:
            return False
        for sentence in _sentences(text_out):
            m = _claim_in(sentence, _CLAIMED_DONE_RE)
            if m and not _OTHER_SUBJECT_RE.search(sentence[: m.start()]):
                self.matched_phrase = m.group(0).strip()
                return True
        return False

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_claimed_done", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # Same as the tool-promise case: the original claim is unverified, the answer round's
        # own text is the answer.
        return new_text.strip() or _t_i18n("gen_no_response", lang)


# ── Truncated-think detector ────────────────────────────────────────────────


class TruncatedThinkDetector(Detector):
    """
    Generation was cut off before the model closed its own <think> block —
    content came back empty because there was never a real answer, only a
    fragment of reasoning (see P88, kaare_core/think_strip.py). Never treat
    that fragment as a finished answer; ask for a real one instead.
    """

    name = "truncated_think"

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        return bool(meta.get("think_truncated")) and not text_out

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_truncated_think", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # The original text was never usable (a reasoning fragment, not an
        # answer) — the retry round's own response is the real answer here,
        # unlike the tool-promise case.
        return new_text


# ── Language-mismatch detector ──────────────────────────────────────────────
# Kåre is instructed (see adapters/llm_adapter.py::_LANG_TAG_INSTRUCTION) to end every
# reply with a [LANG:xx] tag stating what language it just used — cheaper and more
# reliable than trying to infer the language from arbitrary free text after the fact
# (short replies like "Ja"/"Ok" carry no distinguishing signal either way). Stripped
# from the final text in router_generate.py before it reaches the user/STM.
_LANG_TAG_RE = re.compile(r"\[LANG:\s*([a-zA-Z]{2,3})\]\s*$")


def strip_language_tag(text: str) -> str:
    """Remove a trailing [LANG:xx] tag before text reaches the user/STM. Safe no-op if absent."""
    return _LANG_TAG_RE.sub("", text).rstrip()


# Small models write "no" for Norwegian Bokmål, or 3-letter codes — same language, not a mismatch.
_LANG_ALIASES = {"no": "nb", "nob": "nb", "nor": "nb", "eng": "en", "deu": "de", "ger": "de"}


def _norm_lang(code: str) -> str:
    c = code.strip().lower()
    return _LANG_ALIASES.get(c, c)


def norm_lang(code: str) -> str:
    """Public form of the language-code normaliser ("no"/"nob" → "nb", "eng" → "en" …)."""
    return _norm_lang(code)


class LanguageMismatchDetector(Detector):
    """Kåre's reply carries a [LANG:xx] tag that doesn't match the expected language.
    A *missing* tag does not fire on its own — the model occasionally drops the tag
    even when the language itself was correct, and forcing a retry on every such drop
    would tax every turn for a rare, usually harmless omission. Only an explicit
    mismatch (tag present, wrong value) is a confident enough signal to force a retry.

    JSON-shaped answers (Miss Library, P111 Phase 5d) can't carry a trailing tag — they
    self-report the language in a schema field instead, passed in as meta["reported_lang"].
    Same rule: no report → no fire.
    """

    name = "language_mismatch"

    def __init__(self) -> None:
        self._via_json = False

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        m = _LANG_TAG_RE.search(text_out)
        if m is not None:
            self._via_json = False
            reported = m.group(1)
        else:
            reported = str(meta.get("reported_lang") or "").strip()
            if not reported:
                return False
            self._via_json = True
        return _norm_lang(reported) != _norm_lang(lang)

    def correction_message(self, lang: str) -> str:
        # The tag-style correction asks for a [LANG:xx] line, which would break a JSON answer.
        return _t_i18n(
            "lib_correction_language" if self._via_json else "gen_language_mismatch_correction",
            lang,
        )

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # The original reply was in the wrong language — not usable. The retry
        # round's own response is the real answer here, same as TruncatedThinkDetector.
        return new_text


# ── Foreign-script detector ─────────────────────────────────────────────────
# Quantized/abliterated models occasionally emit a token from another writing system in the
# middle of a word («rett각ige», «kjører da周期性 avting» — P124, 2026-10-05). In a reply that
# should be nb/en/de those characters are never correct by accident, so this is a confident,
# cheap (~1 µs) signal. Only applied when the reply language is Latin-script; the correction
# text tells the model to keep such characters if they were intended (e.g. «write hello in
# Japanese»), so a legitimate use costs at most one retry and is not stripped.
_FOREIGN_SCRIPT_RE = re.compile(
    "["
    "Ѐ-ӿ"  # Cyrillic
    "֐-ۿ"  # Hebrew, Arabic
    "฀-๿"  # Thai
    "　-ヿ"  # CJK punctuation, Hiragana, Katakana
    "㐀-鿿"  # CJK ideographs
    "가-힯"  # Hangul
    "＀-￯"  # full-width forms
    "]"
)
_LATIN_SCRIPT_LANGS = {"nb", "nn", "en", "de", "sv", "da"}


def foreign_script_chars(text: str) -> list[str]:
    """Characters from non-Latin writing systems in `text` (empty list if none)."""
    return _FOREIGN_SCRIPT_RE.findall(text or "")


class ForeignScriptDetector(Detector):
    """A Latin-script reply (nb/en/de) contains characters from another writing system —
    almost always a corrupted token, not intended text. Forces one rewrite."""

    name = "foreign_script"

    def __init__(self) -> None:
        self.matched_chars: list[str] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        if _norm_lang(lang) not in _LATIN_SCRIPT_LANGS:
            return False
        self.matched_chars = foreign_script_chars(text_out)
        return bool(self.matched_chars)

    def correction_message(self, lang: str) -> str:
        chars = "".join(dict.fromkeys(self.matched_chars))[:20]
        return _t_i18n("gen_correction_foreign_script", lang).format(chars=chars)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # The retry round rewrites the whole reply; if it came back empty (e.g. it made a
        # tool call instead), the original reply is still better than nothing.
        return new_text or original_text


# ── Grounded-answer detectors (Miss Library, P111 Phase 5c) ─────────────────
# Miss Library answers in a fixed JSON shape where every claim carries a verbatim quote
# from the sources ("quote-first"). The caller verifies each quote against the source text
# in code (no LLM, no IO) and passes the result in `meta`; these detectors turn a failed
# check into exactly one correction round through the same enforcer as Kåre's.


class UngroundedQuoteDetector(Detector):
    """One or more quotes in the answer do not occur in the sources."""

    name = "ungrounded_quote"

    def __init__(self) -> None:
        self._quotes: list[str] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self._quotes = list(meta.get("unverified_quotes") or [])
        return bool(self._quotes)

    def correction_message(self, lang: str) -> str:
        quotes = "\n".join(f"- «{q}»" for q in self._quotes[:8])
        return _t_i18n("lib_correction_ungrounded", lang, quotes=quotes)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


class CutOrInvalidOutputDetector(Detector):
    """The answer was cut at num_predict or is not valid JSON in the required shape."""

    name = "cut_or_invalid_output"

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        return meta.get("done_reason") == "length" or bool(meta.get("parse_error"))

    def correction_message(self, lang: str) -> str:
        return _t_i18n("lib_correction_cut", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


class UnsupportedClaimDetector(Detector):
    """A claim contains a number that is not in its own quote, or a name that occurs nowhere
    in the sources — the quote is real but does not back the claim."""

    name = "unsupported_claim"

    def __init__(self) -> None:
        self._items: list[tuple[str, list[str]]] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self._items = list(meta.get("unsupported_claims") or [])
        return bool(self._items)

    def correction_message(self, lang: str) -> str:
        items = "\n".join(
            f"- «{claim}»: {', '.join(missing)}" for claim, missing in self._items[:8]
        )
        return _t_i18n("lib_correction_unsupported", lang, items=items)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


class IrrelevantQuoteDetector(Detector):
    """None of the verified quotes touches the task, while the sources hold sentences that do
    (P121 D-P15). "Touches" = shares a word stem with Kåre's keywords or the task text — the
    caller computes it in code and passes the candidate sentences in meta; no model decides."""

    name = "irrelevant_quote"

    def __init__(self) -> None:
        self._sentences: list[str] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self._sentences = list(meta.get("relevant_unquoted") or [])
        return bool(self._sentences)

    def correction_message(self, lang: str) -> str:
        sentences = "\n".join(f"- «{x}»" for x in self._sentences[:3])
        return _t_i18n("lib_correction_irrelevant", lang, sentences=sentences)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


def grounded_detectors() -> list[Detector]:
    """Fresh detector instances per request (the grounded detectors keep per-call state)."""
    # Kåre's valves first (P111 Phase 5d): a cut think block also leaves invalid JSON, and its
    # correction is the more precise one. ToolPromiseDetector is left out on purpose — its
    # correction says "call the tool", and Miss Library has no tools in this call.
    return [
        TruncatedThinkDetector(),
        CutOrInvalidOutputDetector(),
        LanguageMismatchDetector(),
        UngroundedQuoteDetector(),
        UnsupportedClaimDetector(),
        IrrelevantQuoteDetector(),
    ]


# ── Tool-argument detectors ─────────────────────────────────────────────────
# Checked on each tool call BEFORE it runs: a required decision the model left out. The call is
# not executed; the correction goes back as that call's tool result, so the model re-issues it in
# the next round. Own budget (one correction per detector per request), separate from the single
# text-retry round above — a missing argument must not use up the language/promise retry.


class ToolCallDetector(ABC):
    """One rule on a tool call's arguments."""

    name: str

    @abstractmethod
    def matches(self, *, tool_name: str, args: dict) -> bool:
        """Cheap, deterministic check — no LLM call."""

    @abstractmethod
    def correction_message(self, lang: str) -> str:
        """Returned as the tool result instead of running the tool."""


class NearHomeMissingDetector(ToolCallDetector):
    """web_search without a true/false near_home: Kåre must decide whether the home place applies
    (local question, no other place or name mentioned) — code never guesses it from the text."""

    name = "near_home_missing"

    def matches(self, *, tool_name: str, args: dict) -> bool:
        if tool_name not in ("web_search", "søk_nett"):
            return False
        value = args.get("near_home")
        if isinstance(value, bool):
            return False
        return not (isinstance(value, str) and value.strip().lower() in ("true", "false"))

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_near_home", lang)


STAKES = ("trivial", "normal", "high", "extraordinary")


class StakesMissingDetector(ToolCallDetector):
    """web_search without a valid `stakes`: Kåre must judge what is at stake (P121 D6/D-P13) —
    health/safety/money/law vs. trivia is language judgement, never guessed by code."""

    name = "stakes_missing"

    def matches(self, *, tool_name: str, args: dict) -> bool:
        if tool_name not in ("web_search", "søk_nett"):
            return False
        return str(args.get("stakes") or "").strip().lower() not in STAKES

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_stakes", lang)


def default_tool_call_detectors() -> list[ToolCallDetector]:
    return [NearHomeMissingDetector(), StakesMissingDetector()]


def default_detectors() -> list[Detector]:
    """Fresh detector instances per request — detectors keep per-call state between matches()
    and correction_message() (e.g. ToolPromiseDetector._matched_key), so a shared module-level
    list would let two concurrent requests read each other's state."""
    # Order = priority (only one retry per request): ForeignScriptDetector is last, so a broken
    # tool promise or a wrong-language reply still gets the retry when both fire.
    return [
        ToolPromiseDetector(),
        ClaimedDoneDetector(),
        TruncatedThinkDetector(),
        LanguageMismatchDetector(),
        ForeignScriptDetector(),
    ]


_ENFORCE_LOG = Path("/kaare/logs/enforcement.log")


def _log_enforcement(rid: str, detector: str, event: str, phrase: str = "") -> None:
    """One JSONL line per forced retry / resolve, rid-tagged, read by trace_reader (P118 S0
    anchors). `phrase` = the matched text, so false hits can be measured later (N24). Never
    raises — logging must not break a reply."""
    try:
        rec = {
            "ts": datetime.now(UTC).isoformat(),
            "rid": rid,
            "detector": detector,
            "event": event,
        }
        if phrase:
            rec["phrase"] = phrase[:200]
        log_buffer.write(_ENFORCE_LOG, rec)
    except Exception:
        pass


class ModelEnforcer:
    """
    Runs the detector registry after each round, forces at most one retry
    round per request (regardless of which detector fires), and hands the
    final resolve() call back to whichever detector matched.

    Owns its own rid-tagged logging (matches KTSB's RID correlation
    convention — see llm_calls.log / think_cache.jsonl / Argus's active_rids
    tracker) so any future caller gets consistent, traceable output for free
    instead of having to remember to log it themselves.
    """

    def __init__(
        self,
        lang: str,
        rid: str = "",
        detectors: list[Detector] | None = None,
        tool_call_detectors: list[ToolCallDetector] | None = None,
    ) -> None:
        self.lang = lang
        self.rid = rid
        self._detectors = detectors if detectors is not None else default_detectors()
        self._tool_call_detectors = (
            tool_call_detectors
            if tool_call_detectors is not None
            else default_tool_call_detectors()
        )
        self._tool_call_fired: set[str] = set()
        self.retry_used = False
        self.pending: Detector | None = None
        self._original_text = ""

    def check_tool_call(self, name: str, args: dict) -> str | None:
        """Call before running a tool. Returns a correction to hand back as the tool result (the
        tool must then NOT run), or None to run it. Each detector fires at most once per request;
        after that the call runs and the tool applies its own safe default."""
        for detector in self._tool_call_detectors:
            if detector.name in self._tool_call_fired:
                continue
            if detector.matches(tool_name=name, args=args):
                self._tool_call_fired.add(detector.name)
                print(
                    f"[MODEL_ENFORCEMENT] rid={self.rid} detector={detector.name} tool={name} — correction"
                )
                _log_enforcement(self.rid, detector.name, "tool_correction")
                return detector.correction_message(self.lang)
        return None

    def check(
        self, *, text_out: str, used_tools: list[str], meta: dict
    ) -> EnforcementAction | None:
        """Returns an EnforcementAction (and forces exactly one retry) on the
        first detector match, or None if nothing fired or a retry already
        happened this request."""
        if self.retry_used:
            return None
        for detector in self._detectors:
            if detector.matches(
                text_out=text_out, used_tools=used_tools, meta=meta, lang=self.lang
            ):
                self.retry_used = True
                self.pending = detector
                # Strip the [LANG:xx] self-report tag before storing — a resolve()
                # that appends onto original_text (e.g. ToolPromiseDetector) would
                # otherwise push the tag out of trailing position, where the final
                # strip_language_tag() call in router_generate.py can no longer
                # find it (its regex is end-anchored) — see BUG-log.md 2026-09-04.
                self._original_text = strip_language_tag(text_out)
                phrase = detector.matched_phrase
                if isinstance(detector, ForeignScriptDetector):
                    phrase = "".join(dict.fromkeys(detector.matched_chars))[:20]
                detail = f" phrase={phrase!r}" if phrase else ""
                print(
                    f"[MODEL_ENFORCEMENT] rid={self.rid} detector={detector.name}{detail} — forcing retry"
                )
                _log_enforcement(self.rid, detector.name, "retry", phrase)
                return EnforcementAction(
                    detector_name=detector.name,
                    correction_message=detector.correction_message(self.lang),
                    original_text=text_out,
                )
        return None

    def resolve(self, new_text: str, call_results: list[tuple] | None) -> str:
        """Call after the forced retry round completes to build the final
        text_out. No-op (returns new_text unchanged) if nothing is pending."""
        if self.pending is None:
            return new_text
        print(f"[MODEL_ENFORCEMENT] rid={self.rid} detector={self.pending.name} resolved")
        _log_enforcement(self.rid, self.pending.name, "resolved")
        return self.pending.resolve(
            original_text=self._original_text,
            new_text=new_text,
            call_results=call_results,
            lang=self.lang,
        )
