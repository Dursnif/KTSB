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

Usage — the enforcer is one block per request (P142); the caller runs its own loop and the
enforcer owns every decision (see kaare_core/routers/router_generate.py for the reference caller):

    enforcer = ModelEnforcer.for_profile("chat", lang, rid=rid)   # detector set per caller
    ...
    correction = enforcer.check_tool_call(name, args)   # before running a tool (None = run it)
    enforcer.record_call(name, args, CALL_RAN)          # ledger: ran / refused / corrected / error
    ...
    action = enforcer.on_answer(text_out, meta, final_round=is_last_tool_round)
    if action:
        messages.append(result["message"])
        messages.append({"role": "user", "content": action.correction_message})
        if action.tools_left:
            continue      # exactly one forced round per request
        ...               # last round: one call without tools for an honest answer
    reply = enforcer.finish(text_out)   # resolves a correction; never an empty reply
    break

After a tool round, `if enforcer.needs_answer_round()`: the correction round made tool calls, so
run one more round without tools so the model answers with the real results in view (N24 A2).
New callers pick or add a profile in PROFILES instead of choosing detectors themselves.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from kaare_core import log_buffer, rooms
from kaare_core.config import get_enforcer_modes
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import t as _t_i18n


@dataclass
class EnforcementAction:
    detector_name: str
    correction_message: str
    original_text: str
    # False = the caller has no tool round left: the correction asks for an honest answer, and the
    # caller makes one call without tools instead of continuing its loop (N24 A6).
    tools_left: bool = True


# Status of one tool call in the request's call ledger (P142 D8). Only "ran" counts as used —
# refused, corrected and crashed calls cannot cover a claim in the reply (N24 A3).
CALL_RAN, CALL_REFUSED, CALL_CORRECTED, CALL_ERROR = "ran", "refused", "corrected", "error"


@dataclass
class CallRecord:
    """What one tool call did, as the caller saw it (facts the detectors can judge by, P142 D8)."""

    tool: str
    action: str
    list_name: str
    status: str


# Mode per detector (P142 D4/D7, set in the GUI): off = not run, shadow = run and log only (no
# correction — how every new rule starts until its false hits are measured), on = enforce.
MODE_OFF, MODE_SHADOW, MODE_ON = "off", "shadow", "on"
MODES = (MODE_OFF, MODE_SHADOW, MODE_ON)

# Policy per detector (P142 D4) — what an enforcing detector does:
#   on_fail  — the reaction to a hit. "reask" = one forced correction round (the only one in use;
#              Guardrails-style "fix" / "refrain" get a value here when a detector needs them).
#   on_empty — when the correction round ends with no usable text: "keep_original" = the original
#              reply minus the matched claim, "refrain" = the honest «no response» text.
ON_FAIL_REASK = "reask"
# Correction budget (P142 Phase 4, N24 A5): one correction per class of problem, at most
# MAX_CORRECTIONS per request — a language correction no longer leaves the corrected answer
# unchecked for false claims, and latency stays bounded (each correction is one more round).
CLASS_CLAIM, CLASS_LANGUAGE, CLASS_OUTPUT, CLASS_GROUNDING = "claim", "language", "output", "grounding"
MAX_CORRECTIONS = 2
ON_FAIL_CORRECT_CALL = "correct_call"  # tool-call detectors: correction instead of the call
ON_EMPTY_KEEP_ORIGINAL, ON_EMPTY_REFRAIN = "keep_original", "refrain"


class Detector(ABC):
    """One enforcement rule: detect a violation, say what's wrong, decide the
    final answer once the forced retry round has run."""

    name: str
    # The text span that made the detector fire, written to enforcement.log so false hits can be
    # measured afterwards (N24). Detectors without a text match leave it empty.
    matched_phrase: str = ""
    # Policy (see ON_FAIL_* / ON_EMPTY_*) and the mode used when the GUI has no override. New
    # detectors set default_mode = MODE_SHADOW until their false hits are measured (P142 D4).
    on_fail: str = ON_FAIL_REASK
    on_empty: str = ON_EMPTY_REFRAIN
    default_mode: str = MODE_ON
    klass: str = CLASS_OUTPUT  # correction-budget class (see MAX_CORRECTIONS)

    def update_facts(self, meta: dict) -> None:  # noqa: B027 — optional hook, default no-op
        """Called with the request's current facts just before resolve() — a detector that judges
        by facts (tool results) must see what the correction round added. Default: nothing."""

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


# A sentence that describes items already waiting for the dev meeting is not a promise to add one
# («Alt det andre ligger sporet med fast nummer til neste dev-møte» — P142 Phase 0, 2026-10-22).
_AGENDA_TRACKED_RE = re.compile(
    # nb
    r"\b(ligger|står|venter)\b|\b(er|blir)\s+(sporet|registrert|ført\s+opp|lagt\s+inn|på\s+agendaen)\b"
    r"|\bsporet\b"
    # en
    r"|\b(is|are|was|were)\s+(already\s+)?(tracked|queued|listed|logged|on\s+the\s+agenda)\b"
    r"|\b(waiting|pending)\s+(for|until)\b"
    # de
    r"|\b(liegt|liegen|steht|stehen|wartet|warten)\b|\b(ist|sind)\s+(bereits\s+)?(erfasst|vorgemerkt"
    r"|eingetragen|auf\s+der\s+agenda)\b",
    re.IGNORECASE,
)


def _agenda_sentences(text: str) -> list[str]:
    """Sentences of `text` that mention the next dev meeting and are not a description of items
    already tracked for it."""
    return [
        s
        for s in _sentences(text)
        if _AGENDA_PROMISE_RE.search(s) and not _AGENDA_TRACKED_RE.search(s)
    ]


def mentions_agenda_promise(text: str) -> bool:
    """True when the text says something should go to the next dev meeting (P118). Used by paths
    that run without tools in the main call (Frigate event analysis) to decide whether a
    follow-up agenda round is needed."""
    return bool(text and _agenda_sentences(text))


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
        {"note:write"},
        "gen_promise_correction_note",
        re.compile(
            # nb
            r"\bjeg\s+(noterte|noterer|har\s+notert|skriver?\s+det\s+ned|har\s+skrevet"
            r"|vil\s+huske|la\s+det\s+til|har\s+lagt\s+(det\s+)?til"
            r"|følger\s+opp|merker\s+meg|noterer\s+meg)\b"
            # My list only together with a write verb — «huskelisten min sier …» is reading it
            # (P142 Phase 4 replay find).
            r"|\b(lagt|lagret|skrevet|notert|ført|satt)\s+(det\s+|den\s+)?(opp\s+|til\s+|inn\s+)?"
            r"(på|i|til)\s+(huskelisten\s+min|min\s+(egen\s+)?huskeliste|lista\s+mi"
            r"|(min|kåres?)\s+(liste|huskeliste))\b"
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
        {"timer:write"},
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
        {"user_profile:write"},
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
        {"world:write"},
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
        {"household:write"},
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


def _drop_claim(text: str, phrase: str) -> str:
    """`text` without the sentences that contain the matched claim `phrase` (line structure and
    the other sentences are kept). Empty phrase → text unchanged."""
    if not phrase:
        return text
    needle = phrase.translate(_APOSTROPHES).lower()
    lines = []
    for line in text.split("\n"):
        if needle in line.translate(_APOSTROPHES).lower():
            parts = re.split(r"(?<=[.!?…])\s+", line)
            line = " ".join(p for p in parts if needle not in p.translate(_APOSTROPHES).lower())
            if not line.strip():
                continue
        lines.append(line)
    return "\n".join(lines)


def find_claim(text: str, pattern: re.Pattern) -> str:
    """The matched claim phrase in `text` after the sentence filters, or "" if none."""
    for sentence in _sentences(text):
        m = _claim_in(sentence, pattern)
        if m:
            return m.group(0)
    return ""


# Actions that change something, per tool (P142 Phase 4, N24 A4): a claim of a write is covered
# only by one of these — a note read, a timer list or a household status check is not.
_WRITE_ACTIONS: dict[str, set[str]] = {
    "note": {"write", "edit"},
    "timer": {"set"},
    "user_profile": {"update", "update_house", "set_field", "edit", "delete", "curiosity"},
    "world": {"update_field", "add", "delete", "edit", "set_var", "delete_var"},
    "household": {"set_away", "set_home"},
}
_TOOL_ALIASES = {"notat": "note"}


def tool_usage_tags(name: str, args: dict | None) -> list[str]:
    """What a tool call counts as for the promise rules: its name; «name:write» when the action
    changes something (_WRITE_ACTIONS) or no action was given (cannot judge → never punish); and
    «note:agenda» for a note write to the dev-meeting agenda (P118 B1). Callers record calls in the
    enforcer's ledger, which turns them into these tags."""
    tags = [name]
    canon = _TOOL_ALIASES.get(name, name)
    args = args if isinstance(args, dict) else {}
    action = str(args.get("action") or "").strip().lower()
    writes = _WRITE_ACTIONS.get(canon)
    is_write = writes is not None and (not action or action in writes)
    if is_write:
        tags.append(f"{canon}:write")
    if canon == "note" and is_write:
        list_name = str(args.get("list_name") or args.get("liste") or "").strip().lower()
        if list_name.startswith("agenda"):
            tags.append("note:agenda")
    return tags


class ToolPromiseDetector(Detector):
    """Kåre's text claims a tool action it never actually called this round."""

    name = "tool_promise"
    klass = CLASS_CLAIM
    # A per-tool rule can fire on a side clause of an otherwise good reply (2026-10-22: «… ligger
    # sporet … til neste dev-møte»). If the retry then answers with nothing, losing the whole
    # reply is worse than keeping it; a retry that answers with text still wins (N24 A1).
    on_empty = ON_EMPTY_KEEP_ORIGINAL

    def __init__(self) -> None:
        self._matched_key: str | None = None

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        used_set = set(used_tools)
        for expected, corr_key, pattern in _TOOL_PROMISE_RULES:
            if expected & used_set:
                continue  # tool was already called this request
            if pattern is _AGENDA_PROMISE_RE:
                phrase = find_claim("\n".join(_agenda_sentences(text_out)), pattern)
            else:
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
        # An empty new_text is handled by ModelEnforcer.resolve() (on_empty policy).
        return new_text.strip()


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
    klass = CLASS_CLAIM

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
    klass = CLASS_LANGUAGE

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
    klass = CLASS_LANGUAGE
    # The rewrite came back empty (e.g. it made a tool call instead): the original reply is still
    # better than nothing — handled centrally by ModelEnforcer.resolve().
    on_empty = ON_EMPTY_KEEP_ORIGINAL

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
        return new_text  # empty → on_empty policy (keep the original)


# ── Action-claim detector (P142 Phase 4, N24 «tools without a rule») ────────
# A reply claims an action of a specific tool that no matching call performed. Judged by the
# call ledger (facts, D8): tool + action must have run. Claims only — first person or a
# participle that reports the act; a state («Lyset i stua er nå slått på») is never a claim,
# because home awareness makes it true without a call (N-D4). Shadow until measured (D4).
_HA_WRITES = {
    "turn_on", "turn_off", "toggle", "set_level", "set_color_temp", "set_color", "set_volume",
    "volume_up", "volume_down", "mute", "unmute", "select_source", "media_pause", "media_play",
    "media_stop",
}
_MEDIA_WRITES = {"plex_play", "radio_play", "radio_stop", "radio_volume"}
_DEVICE_TOOLS = {"ha_control": _HA_WRITES, "styr_enhet": None, "media": _MEDIA_WRITES}
_NOTE_P133 = {"resolve", "reopen", "pin", "unpin", "mark_important", "done", "mark_bought"}

# (tool label for the correction, {tool: actions that count | None = any}, pattern)
_ACTION_CLAIM_RULES: list[tuple[str, dict, re.Pattern]] = [
    (
        "ha_control",
        _DEVICE_TOOLS,
        re.compile(
            r"\bjeg\s+(har\s+)?(slått|slo|skrudd|skrudde|tent|tente|slukket|slukte|dimmet|dempet)"
            r"\s+(på|av|opp|ned)\b"
            r"|\b(har|slo|skrudde|tente|slukket)\s+jeg\s+((slått|skrudd|tent)\s+)?(på|av)?\b"
            r"|\bI('ve|\s+have)?\s+(turned|switched|dimmed)\s+(on|off|up|down)\b"
            r"|\bich\s+habe\b[^.!?]{0,40}\b(eingeschaltet|ausgeschaltet|angeschaltet|abgeschaltet|gedimmt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "media",
        _DEVICE_TOOLS,
        re.compile(
            r"^\s*(spiller|starter)\s+nå\b"
            r"|\bjeg\s+(har\s+)?(startet|satt\s+på)\s+(radio(en)?|musikk(en)?|nrk|p\d)\b"
            r"|^\s*now\s+playing\b|\bI('ve|\s+have)?\s+(started|put\s+on)\s+(the\s+)?(radio|music)\b"
            r"|^\s*(spiele|starte)\s+jetzt\b|\bich\s+habe\s+(das\s+)?(Radio|Musik)\s+gestartet\b",
            re.IGNORECASE,
        ),
    ),
    (
        "timer",
        {"timer": {"set"}},
        re.compile(
            r"\b(timeren|timer|påminnelsen|påminnelse|alarmen|alarm)\s+(er\s+)?(nå\s+)?satt\b"
            r"|\b(timer|reminder|alarm)\s+(is\s+)?(now\s+)?set\b"
            r"|\b(Timer|Erinnerung|Wecker)\s+(ist\s+)?(jetzt\s+)?(gestellt|gesetzt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mechanic",
        {"mechanic": None, "spør_mechanic": None},
        re.compile(
            r"\b(har\s+)?(bedt|spurt)\s+mechanic\b|\bsendt\s+(det\s+|saken\s+)?til\s+mechanic\b"
            r"|\b(asked|sent\s+(it\s+)?to)\s+mechanic\b"
            r"|\bMechanic\s+(gebeten|gefragt|beauftragt)\b|\ban\s+Mechanic\s+(geschickt|übergeben)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "self_image",
        {"self_image": {"update", "edit", "delete"}},
        re.compile(
            r"\bjeg\s+(har\s+)?(oppdatert|endret|justert)\s+(selvbildet|personligheten)\b"
            r"|\bselvbildet\s+(mitt\s+)?er\s+(nå\s+)?oppdatert\b"
            r"|\bI('ve|\s+have)?\s+updated\s+my\s+self-?image\b"
            r"|\bmein\s+Selbstbild\s+(aktualisiert|angepasst)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "announce",
        {"announce": {"say", "display"}},
        re.compile(
            r"\b(sagt\s+fra|annonsert|kunngjort|ropt\s+ut)\s+(på|i|over|gjennom)\b"
            r"|\bI('ve|\s+have)?\s+announced\b|\b(durchgesagt|angekündigt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "dev_proposals",
        {"dev_proposals": {"confirm", "reject"}},
        re.compile(
            r"\b(bekreftet|avvist|godkjent|forkastet)\s+forslag(et)?\b"
            r"|\b(confirmed|rejected|approved)\s+(the\s+)?proposal\b"
            r"|\bVorschlag\b[^.!?]{0,30}\b(bestätigt|abgelehnt|angenommen)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "write_reflex",
        {"write_reflex": {"confirm", "reject", "suggest"}},
        re.compile(
            r"\b(lagret|lært|bekreftet|avvist)\s+(den\s+nye\s+)?(refleksen|en\s+ny\s+refleks)\b"
            r"|\b(saved|learned|confirmed|rejected)\s+(the|a\s+new)\s+reflex\b"
            r"|\bReflex\b[^.!?]{0,20}\b(gespeichert|gelernt|bestätigt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "note",
        {"note": _NOTE_P133},
        re.compile(
            r"\b(markert|merket)\s+((den|det|saken|punktet|dem)\s+)?som\s+(løst|viktig|ferdig|kjøpt|gjort)\b"
            r"|\b(festet|løsnet)\s+((den|det)\s+)?(øverst|på\s+toppen)\b|\bgjenåpnet\s+(den|det|saken)\b"
            r"|\bmarked\s+(it\s+|that\s+)?as\s+(resolved|done|important|bought)\b"
            r"|\b(pinned|unpinned|reopened)\s+(it|that|the)\b"
            r"|\bals\s+(erledigt|gelöst|wichtig|gekauft)\s+markiert\b|\b(angeheftet|wieder\s+geöffnet)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "note",
        {"note": {"write", "edit"}},
        re.compile(r"\bich\s+habe\s+(es|das)\s+mir\s+gemerkt\b", re.IGNORECASE),
    ),
]


def _rule_satisfied(tools: dict, calls: list) -> bool:
    for c in calls:
        if c.status != CALL_RAN:
            continue
        canon = _TOOL_ALIASES.get(c.tool, c.tool)
        if canon in tools or c.tool in tools:
            actions = tools.get(canon, tools.get(c.tool))
            if actions is None or not c.action or c.action in actions:
                return True
    return False


_VISUAL_CLAIM_RE: dict[str, re.Pattern] = {}


def _visual_claim_re(lang: str) -> re.Pattern:
    if lang not in _VISUAL_CLAIM_RE:
        _VISUAL_CLAIM_RE[lang] = re.compile(_t_i18n("enf_visual_claim_phrases", lang), re.IGNORECASE)
    return _VISUAL_CLAIM_RE[lang]


class VisualClaimWithoutImageDetector(Detector):
    """R140: the answer says it sees something, and code knows no image reached the model in this turn (neither the
    user's own image nor an image tool's [VISION:] round — `meta["turn_has_image"]`, set by the chat loop). The claim is
    recognised with i18n expressions in nb/en/de (language bleed: all three are checked). Shadow until the owner has seen
    the numbers; the log holds the rid and the hit, never the answer's text (`matched_phrase` stays empty)."""

    name = "visual_claim_no_image"
    klass = CLASS_CLAIM
    default_mode = MODE_SHADOW
    on_empty = ON_EMPTY_KEEP_ORIGINAL

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""  # never the text (main session 2026-11-06)
        if meta.get("turn_has_image") is not False:
            return False  # an image came, or the caller did not say — never guess from the text alone
        return any(_visual_claim_re(lg).search(text_out or "") for lg in ("nb", "en", "de"))

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_visual_claim", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


class RepeatedThemeDetector(Detector):
    """J4 / P149 D6: a night-meeting theme raised for the third night in a chain (code counted it from the lean
    «repeats night X» call, `meta["theme_nights"]`) with nothing new from the user is refused; the participant is asked
    for another theme. Shadow until a measurement shows the call finds repeats without many false hits."""

    name = "repeated_theme"
    klass = CLASS_CLAIM
    default_mode = MODE_SHADOW
    on_empty = ON_EMPTY_REFRAIN

    def __init__(self) -> None:
        self._nights = 0

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""
        self._nights = int(meta.get("theme_nights") or 0)
        return self._nights >= int(meta.get("theme_stop_at") or 3) and not meta.get("theme_new_user_evidence")

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_repeated_theme", lang, nights=self._nights)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


class ActionClaimDetector(Detector):
    """The reply claims an act of a specific tool (light switched, timer set, Mechanic asked,
    item marked resolved …) and no call of that tool with a matching action ran."""

    name = "action_claim"
    klass = CLASS_CLAIM
    default_mode = MODE_SHADOW
    on_empty = ON_EMPTY_KEEP_ORIGINAL  # same reasoning as tool_promise: a side clause, a good reply

    def __init__(self) -> None:
        self._tool = ""

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""
        calls = meta.get("calls") or []
        for tool, tools, pattern in _ACTION_CLAIM_RULES:
            if _rule_satisfied(tools, calls):
                continue
            phrase = find_claim(text_out, pattern)
            if phrase:
                self._tool, self.matched_phrase = tool, phrase
                return True
        return False

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_action_claim", lang, tool=self._tool)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text.strip()  # empty → on_empty: the original minus the claim


# ── Agent-output detectors (P142 Phase 3, from P117 C19/C24) ───────────────
# Both start in shadow mode (default_mode) — they are measured on live traffic before they may
# correct anything (P142 D4).

# A tool call written as text instead of made (qwen3.5/3.8 print-instead-of-call: ollama#14745,
# llama.cpp#20837; 27B markup in chat, N27). Only call-shaped markup counts, not prose that names
# a tool («FORSLAG: `utforsk` …» is a proposal, not a call).
_CALL_MARKUP_RE = re.compile(
    r"<tool_call>|</tool_call>|<function=[\w-]+|<parameter=[\w-]+|<\|tool_call"
    # Bracketed line, «[Note tool call: action=read …]» (27B, DT04 retest 2026-11-16): the bracket must hold the
    # words «tool call» (or nb/de: verktøykall / Tool-Aufruf) followed by a colon — prose that only names a tool is not.
    r"|\[[^\]\n]{0,40}\b(?:tool[ _-]?call|verktøykall|(?:tool|werkzeug)-?aufruf)\s*:",
    re.IGNORECASE,
)
_CALL_JSON_RE = re.compile(r"\{\s*\"name\"\s*:\s*\"([\w\-æøåÆØÅ]+)\"\s*,\s*\"(arguments|parameters)\"")
# A reply that is nothing but a ```json fence around its content (the fence is stripped before parsing).
_JSON_FENCE_RE = re.compile(r"^```[\w-]*\s*\n?(.*?)\n?```$", re.DOTALL)


@lru_cache(maxsize=1)
def _tool_actions() -> dict[str, frozenset[str]]:
    """Tool name → the values its «action» enum allows (language-independent). Built once per process."""
    out: dict[str, frozenset[str]] = {}
    try:
        for tool in get_tools("en"):
            fn = tool.get("function", {})
            enum = fn.get("parameters", {}).get("properties", {}).get("action", {}).get("enum")
            if fn.get("name") and enum:
                out[fn["name"]] = frozenset(str(v) for v in enum)
    except Exception:  # never let the enforcer break the answer path
        return {}
    return out


def _bare_action_json(text: str, offered: set[str]) -> str:
    """The reply as written, when it is NOTHING but a JSON object / list of objects whose «action» is a real
    action of an offered tool — the 27B's «[{"action": "write", "list_name": …}]» (DT04-S05p). JSON inside
    prose, JSON without «action», or an action no offered tool has never counts (false hits weigh heavier)."""
    body = (text or "").strip()
    fence = _JSON_FENCE_RE.match(body)
    if fence:
        body = fence.group(1).strip()
    if not body or body[0] not in "[{" or body[-1] not in "]}":
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        return ""
    items = data if isinstance(data, list) else [data]
    if not items or not all(isinstance(i, dict) and isinstance(i.get("action"), str) for i in items):
        return ""
    actions: set[str] = set()
    for name in offered:
        actions |= _tool_actions().get(name, frozenset())
    return body[:80] if all(i["action"] in actions for i in items) else ""


class ToolCallAsTextDetector(Detector):
    """Tools were offered, but the reply contains a tool call written out as text."""

    name = "tool_call_as_text"
    # DT04 R2 2.16 (2026-11-16): markup shown as text on a fresh install, 3/3 with `minimal`, 1/3 with
    # `komplett`; the house has 0 events ever (no false hits) → corrects by default. GUI mode still wins.
    default_mode = MODE_ON

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""
        offered = set(meta.get("offered_tools") or [])
        if not offered or not text_out:
            return False
        m = _CALL_MARKUP_RE.search(text_out)
        if m:
            self.matched_phrase = m.group(0)
            return True
        for m in _CALL_JSON_RE.finditer(text_out):
            if m.group(1) in offered:
                self.matched_phrase = m.group(0)[:80]
                return True
        bare = _bare_action_json(text_out, offered)
        if bare:
            self._offered = offered
            self.matched_phrase = bare
            return True
        return False

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_tool_as_text", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # The written-out call is never the answer: if the retry wrote it out again, it is dropped
        # (→ empty → honest «no response» fallback, never «done»). DT04-s03.
        if new_text and (
            _CALL_MARKUP_RE.search(new_text)
            or _CALL_JSON_RE.search(new_text)
            or _bare_action_json(new_text, getattr(self, "_offered", set()))
        ):
            return ""
        return new_text


# Absolute paths with at least two parts, and file names with a code/log/config extension.
_PATH_RE = re.compile(
    r"(?<![\w.])/(?:[\w.\-]+/)+[\w.\-]+"
    r"|\b[\w\-]+\.(?:py|log|md|ya?ml|json|jsonl|db|sh|tsx?|env|conf|service)\b"
)


def _ungrounded_paths(text: str, grounding: str) -> list[str]:
    """Paths / file names in `text` that occur nowhere in `grounding` (also not as a base name)."""
    out: list[str] = []
    for m in _PATH_RE.finditer(text or ""):
        item = m.group(0).rstrip(".,:;)")
        base = item.rsplit("/", 1)[-1]
        if item in grounding or (base and base in grounding) or item in out:
            continue
        out.append(item)
    return out


class UngroundedPathDetector(Detector):
    """The reply names files or paths that no tool result, task text or earlier message of this
    request contains — the agent invented a finding (P117 C19: «kaare.log», a file that does not
    exist). The caller passes everything the agent was shown as `meta["grounding_text"]`."""

    name = "ungrounded_path"
    klass = CLASS_GROUNDING
    default_mode = MODE_SHADOW

    def __init__(self) -> None:
        self._grounding = ""
        self._items: list[str] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""
        grounding = meta.get("grounding_text")
        if not isinstance(grounding, str) or not grounding:
            return False  # no facts handed in → cannot judge, never fire
        self._grounding = grounding
        self._items = _ungrounded_paths(text_out, grounding)
        if self._items:
            self.matched_phrase = ", ".join(self._items[:5])
        return bool(self._items)

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_ungrounded_path", lang, items=", ".join(self._items[:5]))

    def update_facts(self, meta: dict) -> None:
        grounding = meta.get("grounding_text")
        if isinstance(grounding, str) and grounding:
            self._grounding = grounding

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        # Nothing is cut from free text: what still is not backed by a source gets an honest note.
        left = _ungrounded_paths(new_text, self._grounding)
        if new_text.strip() and left:
            note = _t_i18n("gen_ungrounded_path_note", lang, items=", ".join(left[:5]))
            return f"{new_text.rstrip()}\n\n{note}"
        return new_text


# ── Grounded-answer detectors (Miss Library, P111 Phase 5c) ─────────────────
# Miss Library answers in a fixed JSON shape where every claim carries a verbatim quote
# from the sources ("quote-first"). The caller verifies each quote against the source text
# in code (no LLM, no IO) and passes the result in `meta`; these detectors turn a failed
# check into exactly one correction round through the same enforcer as Kåre's.


class UngroundedQuoteDetector(Detector):
    """One or more quotes in the answer do not occur in the sources."""

    name = "ungrounded_quote"
    klass = CLASS_GROUNDING

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


class FrigateLangTagDetector(Detector):
    """A [LANG:xx] tag in a Frigate-user answer (P144-s06a): the JSON contract has no tag, so one there means the
    output step leaked Kåre's chat contract into the camera pipe (the `[LANG:nb]` bug, P144 `## Bugs`)."""

    name = "frigate_lang_tag"
    klass = CLASS_OUTPUT

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        m = re.search(r"\[LANG:[^\]]*\]", text_out or "")
        self.matched_phrase = m.group(0) if m else ""
        return bool(m)

    def correction_message(self, lang: str) -> str:
        return _t_i18n("frigate_user_correction_lang_tag", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text or original_text


class FrigateEmptyOutputDetector(Detector):
    """A Frigate-user answer that is empty or only a [LANG:xx] tag (P144-s06a)."""

    name = "frigate_empty_output"
    klass = CLASS_OUTPUT

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        return not strip_language_tag(text_out or "").strip()

    def correction_message(self, lang: str) -> str:
        return _t_i18n("frigate_user_correction_empty", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text


# A scene of fewer words than this, or a placeholder, says nothing about what the frames showed (P144-s14: the 9B
# answered «natt» / «night» in 9 of 15 acceptance runs).
THIN_SCENE_MIN_WORDS = 3
_SCENE_PLACEHOLDERS = {"", "...", "…", "unclear", "uklart", "unklar", "none", "n/a", "-"}


class FrigateThinSceneDetector(Detector):
    """The Frigate scene call's `scene` field is a placeholder or under THIN_SCENE_MIN_WORDS words (P144-s14). Only on
    the scene call (`meta["kind"] == "scene"`); unparseable JSON is `cut_or_invalid_output`'s job."""

    name = "frigate_thin_scene"
    klass = CLASS_OUTPUT
    default_mode = MODE_SHADOW

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        self.matched_phrase = ""
        if (meta or {}).get("kind") != "scene":
            return False
        raw = strip_language_tag(text_out or "")
        start, end = raw.find("{"), raw.rfind("}")
        try:
            data = json.loads(raw[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            return False
        if not isinstance(data, dict):
            return False
        scene = " ".join(str(data.get("scene") or "").split())
        thin = scene.lower().strip(".… ") in _SCENE_PLACEHOLDERS or len(scene.split()) < THIN_SCENE_MIN_WORDS
        self.matched_phrase = scene[:40] if thin else ""
        return thin

    def correction_message(self, lang: str) -> str:
        return _t_i18n("frigate_user_correction_thin_scene", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text or original_text


class _FrigateFramesFactDetector(Detector):
    """P144-s25 base: the frame-by-frame caller computes the fact in code and passes it in `meta[meta_key]` (a list);
    the detector only reports it (the `UnsupportedClaimDetector` pattern — code decides, the detector logs/corrects).
    Only on the frame-by-frame calls (`meta["kind"] == "frame"`)."""

    meta_key = ""
    message_key = ""
    klass = CLASS_GROUNDING
    default_mode = MODE_SHADOW

    def __init__(self) -> None:
        self._items: list[str] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        meta = meta or {}
        self._items = [str(x) for x in (meta.get(self.meta_key) or [])] if meta.get("kind") == "frame" else []
        self.matched_phrase = ", ".join(self._items)[:60]
        return bool(self._items)

    def correction_message(self, lang: str) -> str:
        return _t_i18n(self.message_key, lang, items=", ".join(self._items[:6])) + "\n" + _t_i18n("frigate_honesty", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text or original_text


SENSE_CATEGORIES = ("fire", "smoke", "weather", "sound", "smell")
_SENSE_LANGS = ("nb", "en", "de")
_SENSE_RX: dict[tuple[str, str, bool], re.Pattern] = {}


def _sense_rx(cat: str, lang: str, loose: bool) -> re.Pattern:
    key = (cat, lang, loose)
    if key not in _SENSE_RX:
        pattern = _t_i18n(f"enf_sense_{cat}", lang)
        _SENSE_RX[key] = re.compile(pattern.removesuffix(r"\b") if loose else pattern, re.IGNORECASE)
    return _SENSE_RX[key]


def unsupported_senses(text: str, lang: str, support: str) -> list[str]:
    """J16 (R143): the sense categories the text claims (i18n expressions in its own language — «fire» is a number in
    Norwegian) that nothing in `support` (the input the model got) mentions (looser stems, every language — frame text
    is English). A code fact; the detector only reports it."""
    lang = lang if lang in _SENSE_LANGS else "nb"
    return [cat for cat in SENSE_CATEGORIES
            if _sense_rx(cat, lang, False).search(text or "")
            and not any(_sense_rx(cat, lg, True).search(support or "") for lg in _SENSE_LANGS)]


class FrigateUnsupportedSenseDetector(Detector):
    """J16 (R143, ep 266 «med bål og vind»): a camera conclusion claims fire, smoke, weather, sound or smell that nothing
    in its input supports (`meta["unsupported_senses"]`, code). Shadow; when on, one correction round whose text never
    names the word (priming)."""

    name = "frigate_unsupported_sense"
    klass = CLASS_GROUNDING
    default_mode = MODE_SHADOW

    def __init__(self) -> None:
        self._items: list[str] = []

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict, lang: str) -> bool:
        meta = meta or {}
        self._items = [str(x) for x in (meta.get("unsupported_senses") or [])] if meta.get("kind") == "conclusion" else []
        self.matched_phrase = ",".join(self._items)  # the categories only, never the words
        return bool(self._items)

    def correction_message(self, lang: str) -> str:
        return _t_i18n("frigate_correction_unsupported_sense", lang)

    def resolve(
        self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str
    ) -> str:
        return new_text or original_text


class FrigateStateCarriedDetector(_FrigateFramesFactDetector):
    """A known person answered «in the image» on a frame where Frigate has no person track (the 9B copied the state
    forward — s23: 6/6 on empty last frames). `meta["state_carried"]` = those person ids."""

    name = "frigate_state_carried"
    meta_key = "state_carried"
    message_key = "frigate_frames_correction_carried"


class FrigateUnsupportedChangeDetector(_FrigateFramesFactDetector):
    """A change that nothing supports: a new person where Frigate tracks nobody on that camera, or more persons than
    Frigate ever tracked at once (the ghost-person class, s23 ep 103). `meta["unsupported_changes"]`."""

    name = "frigate_unsupported_change"
    meta_key = "unsupported_changes"
    message_key = "frigate_frames_correction_unsupported"


class FrigateMissedChangeDetector(_FrigateFramesFactDetector):
    """Frigate saw a person appear or leave on this camera since its previous frame, the answer reports no change in
    who is there. `meta["missed_changes"]`."""

    name = "frigate_missed_change"
    meta_key = "missed_changes"
    message_key = "frigate_frames_correction_missed"


class FrigateTextLanguageDetector(_FrigateFramesFactDetector):
    """The answer's text values are in another language than the household's (code-detected, `meta["text_lang"]`;
    Frigate answers carry no [LANG:xx])."""

    name = "frigate_wrong_language"
    meta_key = "wrong_language"
    message_key = "frigate_frames_correction_language"
    klass = CLASS_LANGUAGE

    def correction_message(self, lang: str) -> str:
        return _t_i18n(self.message_key, lang)


class UnsupportedClaimDetector(Detector):
    """A claim contains a number that is not in its own quote, or a name that occurs nowhere
    in the sources — the quote is real but does not back the claim."""

    name = "unsupported_claim"
    klass = CLASS_GROUNDING

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
    klass = CLASS_GROUNDING

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
    default_mode: str = MODE_ON  # see Detector.default_mode
    matched_phrase: str = ""  # what made it fire, for enforcement.log (as Detector)
    needs_grounding: bool = False  # True → the enforcer keeps what the model was shown and hands it over

    def update_facts(self, meta: dict) -> None:  # noqa: B027 — optional hook, default no-op
        """Called with the request's facts (`grounding_text`) just before matches()."""

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


# HA entity domains (an id is «domain.object_id»). Only these count, so «notes.py» or «v1.11» never look like an entity.
_HA_DOMAINS = (
    "alarm_control_panel", "automation", "binary_sensor", "button", "calendar", "camera", "climate", "cover", "date",
    "datetime", "device_tracker", "event", "fan", "humidifier", "image", "input_boolean", "input_button",
    "input_datetime", "input_number", "input_select", "input_text", "lawn_mower", "light", "lock", "media_player",
    "number", "person", "remote", "scene", "script", "select", "sensor", "siren", "switch", "text", "time", "todo",
    "update", "vacuum", "valve", "water_heater", "weather", "zone",
)
# Not followed by «-», «.» or a word char: «image.car-ha-events» is shorthand, not an entity id (replay 2026-11-16).
_ENTITY_RE = re.compile(r"(?<![\w.])(?:" + "|".join(_HA_DOMAINS) + r")\.[a-z0-9_]+(?![\w\-.])")
# HA *services* look like entity ids («light.turn_off», «weather.get_forecasts») — they name an action, never an
# object. Entity object ids do not start with these verbs (replay 2026-11-16: 40+ false hits without this).
_SERVICE_RE = re.compile(
    r"\.(?:turn_on|turn_off|toggle|get_\w+|set_\w+|open_\w+|close_\w+|stop_\w+|play_media|media_\w+|volume_\w+"
    r"|select_\w+|press|reload|trigger|start|pause|return_to_base|lock|unlock|send_\w+|snooze|dismiss|increment"
    r"|decrement|reset|update_\w+|clean_\w+|locate|install|skip|speak|notify\w*)$"
)


def _entity_refs(text: str) -> list[str]:
    return [e for e in _ENTITY_RE.findall(text) if not _SERVICE_RE.search(e)]


def _fs_path(item: str) -> bool:
    """A path that can name a file here: a bare file name, or an absolute path whose top folder exists on this
    machine. «/api/meetings/status» is a web route, not a file (replay 2026-11-16)."""
    if not item.startswith("/"):
        return True
    top = item.split("/", 2)[1] if item.count("/") >= 1 else ""
    return bool(top) and Path("/" + top).is_dir()
_NODE_REF_RE = re.compile(r"\bnode:([A-Za-z0-9_\-]+)")


def _known_objects() -> tuple[set[str] | None, set[str] | None]:
    """(HA entity ids from the house snapshot, KTSB node ids from nodes.yaml). None = that register does not exist
    on this install (no HA, no nodes file) → never judged (rule 24). Both are cached reads (mtime), no network."""
    entities: set[str] | None = None
    nodes: set[str] | None = None
    try:
        ha = rooms.get_register().ha_entities
        entities = set(ha) if ha else None
    except Exception:
        entities = None
    try:
        raw = (rooms.cs.read_yaml_cached(rooms.NODES_PATH, copy_result=False) or {}).get("nodes")
        nodes = set(raw) if isinstance(raw, dict) and raw else None
    except Exception:
        nodes = None
    return entities, nodes


def _object_refs(text: str) -> list[str]:
    """The object references in a text (HA entity ids, `node:<id>`, paths / file names) — what `ungrounded_object`
    needs from a tool result, so a profile without full grounding keeps these and not the output."""
    refs = _entity_refs(text) + [f"node:{n}" for n in _NODE_REF_RE.findall(text)]
    refs += [m.group(0).rstrip(".,:;)") for m in _PATH_RE.finditer(text)]
    return list(dict.fromkeys(refs))


def _arg_text(args: dict) -> str:
    """Every string value a write call carries (text, value, title …) — where an object reference would be stored."""
    parts = []
    for key, value in (args or {}).items():
        if key.startswith("_") or key == "action":
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(v for v in value if isinstance(v, str))
    return "\n".join(parts)


class UngroundedObjectDetector(ToolCallDetector):
    """A write tool would store an object that exists nowhere: an HA entity id not in the house's HA snapshot, a
    `node:<id>` not in nodes.yaml, or a file path no tool or user message of this request showed (P142 intake
    2026-11-16, from P139 #7 — Kåre stored the invented «media_player.samsung_stue» as a fact, world:305). Judged by the
    house's own registers plus this request's grounding — no classifier, no content judgement (KTSB-visjon drift test).
    A register that does not exist on this install is never judged (rule 24). Only write actions (_WRITE_ACTIONS)."""

    name = "ungrounded_object"
    default_mode = MODE_SHADOW  # P142 D4: measured before it may correct
    needs_grounding = True

    def __init__(self) -> None:
        self.matched_phrase = ""
        self._items: list[str] = []
        self._grounding = ""

    def update_facts(self, meta: dict) -> None:
        grounding = meta.get("grounding_text")
        if isinstance(grounding, str):
            self._grounding = grounding

    def matches(self, *, tool_name: str, args: dict) -> bool:
        self.matched_phrase, self._items = "", []
        if f"{_TOOL_ALIASES.get(tool_name, tool_name)}:write" not in tool_usage_tags(tool_name, args):
            return False
        text = _arg_text(args)
        if not text:
            return False
        entities, nodes = _known_objects()
        items: list[str] = []
        if entities is not None:
            items += [e for e in _entity_refs(text) if e not in entities and e not in self._grounding]
        if nodes is not None:
            items += [f"node:{n}" for n in _NODE_REF_RE.findall(text)
                      if n not in nodes and f"node:{n}" not in self._grounding]
        for p in _ungrounded_paths(text, self._grounding):
            # a web route is no file; a real file on this machine is grounded too
            if _fs_path(p) and not (p.startswith("/") and Path(p).exists()):
                items.append(p)
        self._items = list(dict.fromkeys(items))
        self.matched_phrase = ", ".join(self._items[:5])
        return bool(self._items)

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_ungrounded_object", lang, items=", ".join(self._items[:5]))


def default_tool_call_detectors() -> list[ToolCallDetector]:
    return [NearHomeMissingDetector(), StakesMissingDetector(), UngroundedObjectDetector()]


def default_detectors() -> list[Detector]:
    """Fresh detector instances per request — detectors keep per-call state between matches()
    and correction_message() (e.g. ToolPromiseDetector._matched_key), so a shared module-level
    list would let two concurrent requests read each other's state."""
    # Order = priority (only one retry per request): ForeignScriptDetector is last, so a broken
    # tool promise or a wrong-language reply still gets the retry when both fire.
    return [
        ToolPromiseDetector(),
        ClaimedDoneDetector(),
        ActionClaimDetector(),  # shadow by default (P142 Phase 4)
        TruncatedThinkDetector(),
        LanguageMismatchDetector(),
        ToolCallAsTextDetector(),  # corrects by default (DT04, 2026-11-16; was shadow, P142 Phase 3, N27)
        VisualClaimWithoutImageDetector(),  # shadow by default (R140, 2026-11-06)
        ForeignScriptDetector(),
    ]


def _reflection_topic_detectors() -> list[Detector]:
    # J4: the night meeting's theme proposals — only the three-night stop (facts from repeat_stop).
    return [RepeatedThemeDetector()]


def _reflection_detectors() -> list[Detector]:
    # Only the promise rule — the reflection prompt has no [LANG:xx] tag contract, so the language
    # detector would misfire (P118 B1).
    return [ToolPromiseDetector()]


# Detector sets per kind of caller (P142 D5). Attaching a new caller = choosing (or adding) a
# profile here, not writing detectors in the caller. Factories, so every request gets fresh
# instances (P119 B2).
def _no_tool_call_detectors() -> list[ToolCallDetector]:
    return []


def _mechanic_detectors() -> list[Detector]:
    # Mechanic has no note/timer tools, so the promise rules do not apply; no [LANG:xx] contract.
    return [ForeignScriptDetector(), ToolCallAsTextDetector(), UngroundedPathDetector()]


def _mechanic_summary_detectors() -> list[Detector]:
    # One-shot summary of fetched content, no tools offered (P117 C19 was seen here).
    return [ForeignScriptDetector(), UngroundedPathDetector()]


def _dev_slot_detectors() -> list[Detector]:
    # Kåre's JSON rounds in the dev slot: read-only tools, no [LANG:xx] contract, no note tool.
    return [TruncatedThinkDetector(), ForeignScriptDetector(), ToolCallAsTextDetector()]


def _frigate_detectors() -> list[Detector]:
    # The Frigate user's JSON answers (P144-s06a L3; s06b's episode conclusion reuses it): no tools, no [LANG:xx]
    # contract. Empty first, then the tag, then cut/unparseable JSON (`meta["parse_error"]`, `done_reason`), then a
    # scene that says almost nothing (s14, only on the scene call).
    return [FrigateEmptyOutputDetector(), FrigateLangTagDetector(), CutOrInvalidOutputDetector(),
            FrigateThinSceneDetector(), FrigateUnsupportedSenseDetector()]


def _frigate_frames_detectors() -> list[Detector]:
    # P144-s25: the frame-by-frame calls (one image + the code-written state). Empty and schema breaks as in `frigate`,
    # then the code facts: state carried while Frigate has no person, a change nothing supports, a missed change,
    # the wrong language. A separate profile so the GUI modes of the L3/scene calls stay apart.
    return [FrigateEmptyOutputDetector(), CutOrInvalidOutputDetector(), FrigateStateCarriedDetector(),
            FrigateUnsupportedChangeDetector(), FrigateMissedChangeDetector(), FrigateTextLanguageDetector()]


# Per profile: (reply detectors, tool-call detectors, profile default mode or None). A profile's
# default mode caps its detectors' own defaults — a caller attached later starts in shadow, so
# existing rules are measured there before they may correct it (P142 D4), without weakening the
# same rule where it is already proven (chat).
PROFILES: dict[str, tuple] = {
    "chat": (default_detectors, default_tool_call_detectors, None),
    "reflection": (_reflection_detectors, default_tool_call_detectors, None),
    "reflection_topic": (_reflection_topic_detectors, _no_tool_call_detectors, MODE_SHADOW),  # J4, shadow
    "grounded": (grounded_detectors, default_tool_call_detectors, None),
    "mechanic": (_mechanic_detectors, _no_tool_call_detectors, MODE_SHADOW),
    "mechanic_summary": (_mechanic_summary_detectors, _no_tool_call_detectors, MODE_SHADOW),
    "dev_slot": (_dev_slot_detectors, _no_tool_call_detectors, MODE_SHADOW),
    "frigate": (_frigate_detectors, _no_tool_call_detectors, MODE_SHADOW),  # P144-s06a, shadow until measured
    "frigate_frames": (_frigate_frames_detectors, _no_tool_call_detectors, MODE_SHADOW),  # P144-s25, shadow
}


def _min_mode(a: str, b: str | None) -> str:
    """The stricter-to-run of two modes: off < shadow < on."""
    if b is None:
        return a
    return min(a, b, key=MODES.index)


def profile_default_mode(profile: str | None, detector) -> str:
    """A detector's default mode within a profile (profile default caps the detector's own)."""
    cap = PROFILES[profile][2] if profile in PROFILES else None
    return _min_mode(detector.default_mode, cap)


def mode_key(profile: str | None, name: str) -> str:
    """The settings key of one detector's mode: «profile:detector» (P142 Phase 3)."""
    return f"{profile}:{name}" if profile else name


def detector_catalog() -> list[dict]:
    """Every detector in any profile, for the GUI status view (P142 D7): name, kind (reply /
    tool_call), its policy, and per profile its default mode (`profiles`: [{profile,
    default_mode}]). Built from PROFILES, so a new detector or caller shows up in the GUI as soon
    as a profile uses it."""
    out: dict[str, dict] = {}
    for profile, (reply_f, tool_f, _cap) in PROFILES.items():
        for kind, factory in (("reply", reply_f), ("tool_call", tool_f)):
            for d in factory():
                row = out.setdefault(
                    d.name,
                    {
                        "name": d.name,
                        "kind": kind,
                        # A tool-call detector answers the call with its correction instead
                        # of running it.
                        "on_fail": getattr(d, "on_fail", ON_FAIL_CORRECT_CALL),
                        "on_empty": getattr(d, "on_empty", ""),
                        "profiles": [],
                    },
                )
                if all(p["profile"] != profile for p in row["profiles"]):
                    row["profiles"].append(
                        {"profile": profile, "default_mode": profile_default_mode(profile, d)}
                    )
    return list(out.values())


def enforcement_stats(days: int = 30, recent: int = 25) -> dict:
    """Counts per detector from enforcement.log over the last `days` (retries, shadow hits, tool
    corrections, how corrections ended) plus the newest `recent` lines with their phrase — the
    data behind the GUI status view. Reads the file (GUI path only, never in a reply); never
    raises."""
    per: dict[str, dict] = {}
    rows: list[dict] = []
    cutoff = datetime.now(UTC).timestamp() - days * 86400
    try:
        log_buffer.flush(_ENFORCE_LOG)
        lines = _ENFORCE_LOG.read_text(encoding="utf-8").splitlines()
    except Exception:
        lines = []
    for line in lines:
        try:
            rec = json.loads(line)
            if datetime.fromisoformat(rec["ts"]).timestamp() < cutoff:
                continue
        except Exception:
            continue
        name, event = str(rec.get("detector", "")), str(rec.get("event", ""))
        c = per.setdefault(
            name,
            {"retry": 0, "shadow": 0, "tool_correction": 0, "outcomes": {}, "last_ts": ""},
        )
        if event in ("retry", "shadow", "tool_correction"):
            c[event] += 1
        if event == "resolved" and rec.get("outcome"):
            c["outcomes"][rec["outcome"]] = c["outcomes"].get(rec["outcome"], 0) + 1
        c["last_ts"] = max(c["last_ts"], str(rec.get("ts", "")))
        rows.append(
            {k: rec.get(k, "") for k in ("ts", "rid", "detector", "event", "phrase", "outcome")}
        )
    return {"days": days, "per_detector": per, "recent": rows[-recent:][::-1]}


_ENFORCE_LOG = Path("/kaare/logs/enforcement.log")


def _log_enforcement(
    rid: str, detector: str, event: str, phrase: str = "", outcome: str = ""
) -> None:
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
        if outcome:  # how a resolve ended: new_answer / kept_original / no_response (P142)
            rec["outcome"] = outcome
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
        modes: dict[str, str] | None = None,
    ) -> None:
        self.lang = lang
        self.rid = rid
        # GUI-set mode overrides, read once per request (cheap mtime check, P142 D7).
        self._modes = modes if modes is not None else get_enforcer_modes()
        self._shadow_logged: set[str] = set()
        # Every detector that matched in this request, in shadow or on (P144-s14): a caller may act on the result
        # itself (the Frigate scene's code text) without the enforcer rewriting anything.
        self.hits: list[str] = []
        self.profile: str | None = None  # set by for_profile(); scopes the mode keys
        self._detectors = detectors if detectors is not None else default_detectors()
        self._tool_call_detectors = (
            tool_call_detectors
            if tool_call_detectors is not None
            else default_tool_call_detectors()
        )
        self._tool_call_fired: set[str] = set()
        self.retry_used = False  # any correction happened in this request
        self.pending: Detector | None = None
        self._original_text = ""
        self.corrections = 0
        self._classes_used: set[str] = set()
        self._resolved: str | None = None  # a correction already resolved by on_answer (A5 chain)
        self.calls: list[CallRecord] = []
        self._calls_at_retry = 0
        # Facts KTSB reports into the box (D8/D10): which tools were offered, and — only when a
        # detector of this enforcer needs it — everything the model was shown (task + results).
        self.offered_tools: set[str] = set()
        self._full_grounding = any(isinstance(d, UngroundedPathDetector) for d in self._detectors)
        self._needs_grounding = self._full_grounding or any(
            getattr(d, "needs_grounding", False) for d in self._tool_call_detectors)
        self._grounding: list[str] = []

    # ── The block (P142 Phase 1): callers run their own loop, the enforcer owns every decision ──
    # One object per request. A caller records each tool call (record_call), hands every final
    # answer to on_answer(), asks needs_answer_round() after a tool round, and builds its reply
    # with finish(). Which detectors run is chosen by profile, not by the caller.

    @classmethod
    def for_profile(cls, profile: str, lang: str, rid: str = "") -> ModelEnforcer:
        """A fresh enforcer with the detector set for one kind of caller (see PROFILES)."""
        detectors, tool_call_detectors, _cap = PROFILES[profile]
        enf = cls(lang, rid=rid, detectors=detectors(), tool_call_detectors=tool_call_detectors())
        enf.profile = profile
        return enf

    def offer_tools(self, names) -> None:
        """The tool names offered to the model in this request (for tool_call_as_text)."""
        self.offered_tools.update(n for n in names if n)

    def add_grounding(self, text: str) -> None:
        """Something the model was shown (task text, fetched content). Kept only when a detector
        of this enforcer judges by it (ungrounded_path), so chat holds no copy of tool output.
        A profile where only tool-call detectors need it (chat: ungrounded_object) keeps just the object references
        found in the text — never the output itself."""
        if not (self._needs_grounding and text):
            return
        if self._full_grounding:
            self._grounding.append(str(text))
        elif refs := _object_refs(str(text)):
            self._grounding.append("\n".join(refs))

    def record_call(
        self, name: str, args: dict | None, status: str, result: str | None = None
    ) -> None:
        """Add one tool call to the request's ledger (status: CALL_RAN / CALL_REFUSED /
        CALL_CORRECTED / CALL_ERROR). `result` = the tool's output, kept as grounding when needed."""
        if result:
            self.add_grounding(result)
        args = args if isinstance(args, dict) else {}
        self.calls.append(
            CallRecord(
                tool=name,
                action=str(args.get("action") or ""),
                list_name=str(args.get("list_name") or args.get("liste") or ""),
                status=status,
            )
        )

    @property
    def used_tools(self) -> list[str]:
        """Usage tags of the calls that actually ran (what the promise rules count)."""
        tags: list[str] = []
        for c in self.calls:
            if c.status == CALL_RAN:
                tags.extend(tool_usage_tags(c.tool, {"action": c.action, "list_name": c.list_name}))
        return tags

    def ran_calls(self) -> list[tuple[str, str]]:
        """(tool, action) of the calls that actually ran in this request — for callers that need the turn's facts
        without re-implementing the ledger (J11: was the turn light? did Kåre store a question's answer?)."""
        return [(c.tool, c.action) for c in self.calls if c.status == CALL_RAN]

    def on_answer(
        self, text_out: str, meta: dict | None = None, *, final_round: bool = False
    ) -> EnforcementAction | None:
        """Check a final answer against the ledger. On a hit the caller appends the answer and
        `correction_message`, then continues its loop — or, when `tools_left` is False (its last
        tool round), makes one call without tools for an honest answer."""
        if self.pending is not None:
            # The answer to a correction: settle it first, then check the settled answer against
            # the classes that have not had their correction yet (A5).
            text_out = self.resolve(text_out, None)
            self._resolved = text_out
        action = self.check(text_out=text_out, used_tools=self.used_tools, meta=self._facts(meta))
        if action:
            self._resolved = None
        if action and final_round:
            action.correction_message = _t_i18n("gen_correction_no_tools_left", self.lang)
            action.tools_left = False
        return action

    def _facts(self, meta: dict | None = None) -> dict:
        """The caller's meta plus the facts KTSB reported into the box."""
        meta = dict(meta or {})
        meta.setdefault("calls", list(self.calls))
        if self.offered_tools:
            meta.setdefault("offered_tools", sorted(self.offered_tools))
        if self._needs_grounding:
            meta.setdefault("grounding_text", "\n".join(self._grounding))
        return meta

    def needs_answer_round(self) -> bool:
        """True when the correction round made tool calls: the model gets one more round without
        tools to answer from the real results (N24 A2)."""
        return self.pending is not None and len(self.calls) > self._calls_at_retry

    def finish(self, raw_text: str, fallback_key: str = "gen_no_response") -> str:
        """The reply for the user. After a correction: resolve(). Otherwise the raw text, or the
        honest fallback when it is empty or only a [LANG:xx] tag (never an empty bubble)."""
        if self.pending is not None:
            return self.resolve(raw_text, None)
        if self._resolved is not None:  # settled by on_answer() — the caller's raw text is that answer
            return self._resolved
        if strip_language_tag(raw_text or "").strip():
            return raw_text
        return _t_i18n(fallback_key, self.lang)

    def mode(self, detector: Detector | ToolCallDetector) -> str:
        """The detector's effective mode in this enforcer's profile: the GUI override
        («profile:detector») if valid, else the profile default (P142 Phase 3). Without a profile
        (direct construction, tests) the plain detector name is the key."""
        m = self._modes.get(mode_key(self.profile, detector.name))
        return m if m in MODES else profile_default_mode(self.profile, detector)

    def _shadow_hit(self, name: str, phrase: str = "") -> None:
        """A shadow-mode detector matched: log it (once per request), change nothing."""
        if name in self._shadow_logged:
            return
        self._shadow_logged.add(name)
        detail = f" phrase={phrase!r}" if phrase else ""
        print(f"[MODEL_ENFORCEMENT] rid={self.rid} detector={name}{detail} — shadow (no correction)")
        _log_enforcement(self.rid, name, "shadow", phrase)

    def check_tool_call(self, name: str, args: dict) -> str | None:
        """Call before running a tool. Returns a correction to hand back as the tool result (the
        tool must then NOT run), or None to run it. Each detector fires at most once per request;
        after that the call runs and the tool applies its own safe default."""
        for detector in self._tool_call_detectors:
            mode = self.mode(detector)
            if mode == MODE_OFF or detector.name in self._tool_call_fired:
                continue
            if detector.needs_grounding:
                detector.update_facts({"grounding_text": "\n".join(self._grounding)})
            if detector.matches(tool_name=name, args=args):
                if detector.name not in self.hits:
                    self.hits.append(detector.name)
                if mode == MODE_SHADOW:
                    self._shadow_hit(detector.name, detector.matched_phrase)
                    continue
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
        if self.corrections >= MAX_CORRECTIONS:
            return None
        for detector in self._detectors:
            mode = self.mode(detector)
            if mode == MODE_OFF:
                continue
            if mode == MODE_ON and detector.klass in self._classes_used:
                continue  # this class already had its correction (A5)
            if detector.matches(
                text_out=text_out, used_tools=used_tools, meta=meta, lang=self.lang
            ):
                if detector.name not in self.hits:
                    self.hits.append(detector.name)
                if mode == MODE_SHADOW:
                    # Measured, not enforced: the next detector still gets its turn (P142 D4).
                    self._shadow_hit(detector.name, detector.matched_phrase)
                    continue
                self.retry_used = True
                self.corrections += 1
                self._classes_used.add(detector.klass)
                self.pending = detector
                self._calls_at_retry = len(self.calls)
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
        self.pending.update_facts(self._facts())  # what the correction round added (Phase 3)
        # Strip the [LANG:xx] tag before anything judges emptiness: a retry that answered with
        # only the tag is an empty answer (2026-10-22, rid-1792664909839 → empty chat bubble).
        text = self.pending.resolve(
            original_text=self._original_text,
            new_text=strip_language_tag(new_text or ""),
            call_results=call_results,
            lang=self.lang,
        )
        text = strip_language_tag(text or "").strip()
        outcome = "new_answer"
        if not text:
            kept = ""
            if self.pending.on_empty == ON_EMPTY_KEEP_ORIGINAL:
                # Never the claim itself (N24 A1): only the rest of the original reply.
                kept = _drop_claim(self._original_text, self.pending.matched_phrase).strip()
            if kept:
                text, outcome = kept, "kept_original"
            else:
                text, outcome = _t_i18n("gen_no_response", self.lang), "no_response"
        print(
            f"[MODEL_ENFORCEMENT] rid={self.rid} detector={self.pending.name} resolved outcome={outcome}"
        )
        _log_enforcement(self.rid, self.pending.name, "resolved", outcome=outcome)
        self.pending = None  # resolved once; a later finish() is a plain answer again
        return text
