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
        text_out = enforcer.resolve(text_out, call_results)
    break
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

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

    @abstractmethod
    def matches(self, *, text_out: str, used_tools: list[str], meta: dict) -> bool:
        """Cheap, deterministic check — no LLM call. Called once per round."""

    @abstractmethod
    def correction_message(self, lang: str) -> str:
        """The nudge sent back to the model for the forced retry round."""

    @abstractmethod
    def resolve(self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str) -> str:
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
_TOOL_PROMISE_RULES: list[tuple[set[str], str, re.Pattern]] = [
    (
        {"note"},
        "gen_promise_correction_note",
        re.compile(
            # nb
            r'\bjeg\s+(noterte|noterer|har\s+notert|skriver?\s+det\s+ned|har\s+skrevet'
            r'|husker\s+det|vil\s+huske|la\s+det\s+til|har\s+lagt\s+(det\s+)?til'
            r'|følger\s+opp|merker\s+meg|noterer\s+meg)\b'
            r'|\b(huskelisten\s+min|min\s+huskeliste|på\s+lista\s+mi'
            r'|lagt\s+til\s+på\s+(min|kåres?)\s+(liste|huskeliste))\b'
            # en
            r"|I'?ve?\s+(noted\s+that|jotted\s+that\s+down|written\s+(that\s+)?down"
            r'|added\s+(it\s+)?to\s+(my\s+)?(list|notes?))\b'
            r"|\bI('ll|'m going to)\s+(note|jot|write\s+(that\s+)?down)\b",
            re.IGNORECASE,
        ),
    ),
    (
        {"timer"},
        "gen_promise_correction_timer",
        re.compile(
            # nb
            r'\bjeg\s+(setter?\s+(en\s+)?(timer|påminnelse|alarm)'
            r'|minner\s+deg\s+om|vil\s+minne\s+deg|kommer\s+til\s+å\s+minne'
            r'|har\s+satt\s+(en\s+)?(timer|påminnelse))\b'
            r'|\b(timer\s+er\s+satt|påminnelse\s+er\s+(satt|opprettet))\b'
            # en
            r"|I('ll|'ve|'m going to)\s+(set\s+(a\s+)?(timer|reminder|alarm)"
            r'|remind\s+you)\b'
            r"|\bI('ve|have)\s+set\s+(a\s+)?(timer|reminder)\b"
            # de
            r'|\bich\s+(stelle\s+(einen?\s+)?(timer|wecker|erinnerung)'
            r'|erinnere\s+dich|werde\s+(dich\s+)?erinnern'
            r'|habe\s+(einen?\s+)?(timer|erinnerung)\s+gestellt)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"user_profile"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r'\bjeg\s+(oppdaterer\s+(profilen\s+din|brukerinfo(en)?)'
            r'|lagrer\s+det\s+i\s+profilen|har\s+(oppdatert|lagret)\s+(profilen|det\s+i\s+profilen))\b'
            # en
            r"|I('ve|'ll)\s+(updated?|saved?|stored?)\s+(your\s+)?(profile|user\s+info|preferences)\b"
            # de
            r'|\bich\s+(habe\s+(dein\s+)?profil\s+aktualisiert'
            r'|aktualisiere\s+(dein\s+)?profil|speichere\s+(es\s+)?in\s+(deinem\s+)?profil)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"world"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r'\bjeg\s+(oppdaterer\s+verdensbildet|lagrer\s+det\s+i\s+verdensbildet'
            r'|har\s+oppdatert\s+verdensbildet)\b'
            # en
            r"|I('ve|'ll)\s+(updated?|saved?)\s+(the\s+)?(world\s+(model|view|knowledge))\b"
            # de
            r'|\bich\s+(habe\s+(das\s+)?weltbild\s+aktualisiert|aktualisiere\s+(das\s+)?weltbild)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"memory"},
        "gen_promise_correction_generic",
        re.compile(
            # nb
            r'\bjeg\s+(lagrer?\s+det\s+i\s+minnet|har\s+lagret\s+det\s+i\s+minnet'
            r'|søker\s+i\s+minnet\s+etter)\b'
            # en
            r"|I('ve|'ll)\s+(saved?\s+(that\s+)?to\s+(my\s+)?memory|stored?\s+that\s+in\s+memory)\b"
            # de
            r'|\bich\s+(speichere\s+(es\s+)?im\s+(gedächtnis|speicher)'
            r'|habe\s+(es\s+)?im\s+gedächtnis\s+gespeichert)\b',
            re.IGNORECASE,
        ),
    ),
    (
        {"household"},
        "gen_promise_correction_household",
        re.compile(
            # nb (covers both "jeg setter X" and V2 word order "setter jeg X")
            r'\b(jeg\s+(setter|aktiverer|har\s+(satt|aktivert))|(setter|aktiverer|har\s+(satt|aktivert))\s+jeg)'
            r'\s+(husstanden|huset|dere)\s+(til\s+)?["\']?(borte\S*|bortreise\S*|hjemme\S*)\b'
            r'|\b(bortreisemodus|borte-?modus)\s+(er\s+)?(nå\s+)?(aktivert|satt|slått\s+på|skrudd\s+på)\b'
            r'|\bhusstanden\s+er\s+(nå\s+)?satt\s+til\s+(borte|hjemme)\b'
            # en
            r"|\bI('ve|'ll|'m going to)\s+(set|activate|switch)\s+(the\s+)?(household|house)\s+(to\s+)?(away|home)\b"
            r'|\baway\s+mode\s+is\s+(now\s+)?(activated|set|on)\b'
            # de
            r'|\bich\s+(setze|aktiviere)\s+den\s+(haushalt|abwesenheitsmodus)\b'
            r'|\bhabe\s+den\s+haushalt\s+auf\s+(abwesend|zuhause)\s+gesetzt\b'
            r'|\babwesenheitsmodus\s+ist\s+(jetzt\s+)?(aktiviert|eingeschaltet|gesetzt)\b',
            re.IGNORECASE,
        ),
    ),
]


class ToolPromiseDetector(Detector):
    """Kåre's text claims a tool action it never actually called this round."""

    name = "tool_promise"

    def __init__(self) -> None:
        self._matched_key: str | None = None

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict) -> bool:
        used_set = set(used_tools)
        for expected, corr_key, pattern in _TOOL_PROMISE_RULES:
            if expected & used_set:
                continue  # tool was already called this request
            if pattern.search(text_out):
                self._matched_key = corr_key
                return True
        return False

    def correction_message(self, lang: str) -> str:
        return _t_i18n(self._matched_key or "gen_promise_correction_generic", lang)

    def resolve(self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str) -> str:
        # The original claim was a legitimate answer — it just needed the tool
        # call behind it. Keep it, tag on what actually happened.
        tags: list[str] = []
        for fn_name, _fn_args, tool_result in (call_results or []):
            if fn_name in ("note", "notat"):
                tags.append(_t_i18n("model_enforce_noted_tag", lang))
            else:
                tags.append(f"_({tool_result[:120]})_")
        text_out = original_text.rstrip()
        if tags:
            text_out += "\n\n" + " · ".join(tags)
        return text_out


# ── Truncated-think detector ────────────────────────────────────────────────

class TruncatedThinkDetector(Detector):
    """
    Generation was cut off before the model closed its own <think> block —
    content came back empty because there was never a real answer, only a
    fragment of reasoning (see P88, kaare_core/think_strip.py). Never treat
    that fragment as a finished answer; ask for a real one instead.
    """

    name = "truncated_think"

    def matches(self, *, text_out: str, used_tools: list[str], meta: dict) -> bool:
        return bool(meta.get("think_truncated")) and not text_out

    def correction_message(self, lang: str) -> str:
        return _t_i18n("gen_correction_truncated_think", lang)

    def resolve(self, *, original_text: str, new_text: str, call_results: list[tuple] | None, lang: str) -> str:
        # The original text was never usable (a reasoning fragment, not an
        # answer) — the retry round's own response is the real answer here,
        # unlike the tool-promise case.
        return new_text


DEFAULT_DETECTORS: list[Detector] = [ToolPromiseDetector(), TruncatedThinkDetector()]


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

    def __init__(self, lang: str, rid: str = "", detectors: list[Detector] | None = None) -> None:
        self.lang = lang
        self.rid = rid
        self._detectors = detectors if detectors is not None else DEFAULT_DETECTORS
        self.retry_used = False
        self.pending: Detector | None = None
        self._original_text = ""

    def check(self, *, text_out: str, used_tools: list[str], meta: dict) -> EnforcementAction | None:
        """Returns an EnforcementAction (and forces exactly one retry) on the
        first detector match, or None if nothing fired or a retry already
        happened this request."""
        if self.retry_used:
            return None
        for detector in self._detectors:
            if detector.matches(text_out=text_out, used_tools=used_tools, meta=meta):
                self.retry_used = True
                self.pending = detector
                self._original_text = text_out
                print(f"[MODEL_ENFORCEMENT] rid={self.rid} detector={detector.name} — forcing retry")
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
        return self.pending.resolve(
            original_text=self._original_text,
            new_text=new_text,
            call_results=call_results,
            lang=self.lang,
        )
