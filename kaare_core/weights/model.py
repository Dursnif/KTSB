"""KTSB's one weight model (P149) — pure math, no IO.

A weight is never stored: it is folded from evidence rows and human answers every time (P149 D1 «evidence stored,
weights computed»). The fold runs in time order, so the same rows always give the same weight and the formula can
change later without losing anything.

The trust scale (2026-11-06, P149 decision D1):
  - below 0.5 is KTSB's own room — what it builds from incidental observations (it may believe, try and be wrong, D0);
  - 0.5 is the most a machine can give (sensors, the LLM, KTSB itself);
  - above 0.5 only humans, up to a ceiling of 0.95 («then KTSB may say it knows»).

Three axes (Weight model v2, P115 D13): Belief (from observations), Validated (only from human answers), Worry (how
much KTSB wants to find out). Maturity is a property of a domain, not a fourth axis (P149 Q8, runner decision J1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

# ── The model's constants (named, documented; they ARE the model, like checks.TRUST_WEIGHTS) ──────────────────────

SOURCE_CLASSES = ("user", "world", "self")  # P13: user > world > self
VERDICTS = ("yes", "no", "correct", "private", "skip")

BELIEF_PRIOR = 0.25  # middle of KTSB's own room: «vet ikke»
MACHINE_CAP = 0.5  # D1: the most machines (and incidental observations) can give
SELF_CAP = 0.3  # P4: KTSB's own inference alone never lifts a belief above this
HUMAN_CEILING = 0.95  # D1: the ceiling, «KTSB vet»
# D2: «10 sånne gir KTSB mulighet til å øke/senke med 0.1» — one distinct angle moves Belief 0.01; user said > world.
BELIEF_STEP = {"user": 0.015, "world": 0.01, "self": 0.005}

# Validated (P149 Q9, runner decision J1): «ja» and «nei» kept apart; certainty grows in the W7 shape 1 − (1 − r)^n.
VALIDATED_RATE = 0.3  # W7: r per verbatim answer
VAGUE_WEIGHT = 0.25  # an answer in passing / «ja, kanskje» / a range-wide «det stemmer» counts a quarter (P4, D3)

# Worry (W9): steps +0.1 / +0.3, decays over days, reset by an answer.
WORRY_STEP_SMALL = 0.1
WORRY_STEP_BIG = 0.3
WORRY_HALF_LIFE_DAYS = 3.0
# D4 b: one clear contradiction from a credible source raises Worry at once.
CONTRADICTION_WORRY = {"user": WORRY_STEP_BIG, "world": WORRY_STEP_SMALL}

# Maturity per domain (P149 Q8): passive days plus confirmations, one confirmation worth a week of passive data.
CONFIRMATION_DAYS = 7.0
MATURITY_DAYS = {"sensor": 35.0, "baseline": 30.0}  # sensor: ~5 answers × 7 days; baseline: normalcy's 30 days
MATURITY_DAYS_DEFAULT = 365.0  # a household/person understanding takes about a year (I40)
# «lærer: N av ~5» (G13): how many human answers a domain's subject aims at; None = no goal shown.
LEARNED_GOAL = {"sensor": 5}

# Word ladder on the trust value (checks.TRUST_WEIGHTS words). Machines alone can never pass «uncertain» (cap 0.5).
WORD_LADDER = ((0.90, "very_likely"), (0.70, "likely"), (0.20, "uncertain"))
WORD_FLOOR = "unlikely"
ACTION_FLOOR = 0.5  # D10: action starts cautious — at maturity 0 KTSB acts on half its trust


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def valid_at(row: dict, at: datetime) -> bool:
    """Time is part of a fact (D4 a): a row counts only inside its own from/to period. None = open."""
    vf, vt = parse_ts(row.get("valid_from")), parse_ts(row.get("valid_to"))
    return (vf is None or vf <= at) and (vt is None or at < vt)


def word_for(trust: float) -> str:
    for floor, word in WORD_LADDER:
        if trust >= floor:
            return word
    return WORD_FLOOR


@dataclass
class Weight:
    belief: float = BELIEF_PRIOR
    validated: float | None = None  # None = no human has answered
    worry: float = 0.0
    maturity: float = 0.0
    action: float = 0.0
    attention: float = 0.0
    trust: float = BELIEF_PRIOR
    word: str = "uncertain"
    learned_n: int = 0
    learned_goal: int | None = None
    yes: float = 0.0
    no: float = 0.0
    angles: int = 0
    parked: bool = False
    source: str = "none"  # none | machine | human
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def _decayed(steps: list[tuple[datetime, float]], now: datetime) -> float:
    total = 0.0
    for ts, step in steps:
        days = max(0.0, (now - ts).total_seconds() / 86400.0)
        total += step * math.pow(0.5, days / WORRY_HALF_LIFE_DAYS)
    return min(1.0, total)


def _validated(yes: float, no: float) -> float | None:
    n = yes + no
    if n <= 0:
        return None
    ratio = yes / n  # 1 of 1 and 50 of 50 differ in certainty, not only in ratio (C9)
    certainty = 1.0 - math.pow(1.0 - VALIDATED_RATE, n)
    return 0.5 + (ratio - 0.5) * 2.0 * (HUMAN_CEILING - 0.5) * certainty


def _lean(belief: float, yes: float, no: float) -> int:
    v = _validated(yes, no)
    ref = v - 0.5 if v is not None else belief - BELIEF_PRIOR
    return (ref > 1e-9) - (ref < -1e-9)


def fold(evidence: list[dict], answers: list[dict], *, kind: str, now: datetime | None = None,
         maturity: float = 0.0, parked: bool = False) -> Weight:
    """Fold one subject's rows into a Weight. `evidence` rows: {ts, source_class, source, context, polarity, pointer,
    worry, valid_from, valid_to}; `answers` rows: {ts, by_user, verdict, verbatim, context, valid_from, valid_to}.
    Rows outside their validity period at `now` are skipped (kept in the store, D4)."""
    now = now or datetime.now(UTC)
    # N39: a person withdrew/corrected their own answer — a *user* evidence row «supersede:<by_user>:<pointer>» on the
    # same subject. That answer row is skipped (kept in the store, append-only); only the same human can withdraw it
    # (D1: a machine never moves Validated). No marker → the fold is exactly as before.
    superseded = {str(r.get("pointer"))[len("supersede:"):] for r in evidence
                  if r.get("source_class") == "user" and str(r.get("pointer") or "").startswith("supersede:")}
    if superseded:
        answers = [r for r in answers if f"{r.get('by_user') or ''}:{r.get('pointer') or ''}" not in superseded]
    rows = [("e", r) for r in evidence if valid_at(r, now)] + [("a", r) for r in answers if valid_at(r, now)]
    rows.sort(key=lambda x: (parse_ts(x[1].get("ts")) or now, x[1].get("_seq", 0)))

    outside = 0.0  # user/world Belief movement
    own = 0.0  # self Belief movement (echo rule: only with a pointer to something new)
    seen_angles: set[tuple] = set()
    seen_answers: set[tuple] = set()
    yes = no = 0.0
    n_answers = 0
    worry_steps: list[tuple[datetime, float]] = []
    reasons: list[str] = []

    def belief_now() -> float:
        b = min(MACHINE_CAP, max(0.0, BELIEF_PRIOR + outside))
        if own > 0:
            b = max(b, min(b + own, SELF_CAP))
        elif own < 0:
            b = max(0.0, b + own)
        return b

    for typ, r in rows:
        ts = parse_ts(r.get("ts")) or now
        if typ == "a":
            verdict = r.get("verdict")
            key = (r.get("by_user") or "", r.get("context") or r.get("ts") or "")
            if verdict == "skip" or verdict not in VERDICTS or key in seen_answers:
                continue  # an unsure/hidden human stays unknown (D1); one answer per conversation (D2)
            seen_answers.add(key)
            w = 1.0 if r.get("verbatim") else VAGUE_WEIGHT
            if verdict in ("yes", "private"):
                yes += w
            else:
                no += w
            n_answers += 1
            worry_steps = []  # an answer resets Worry (W9)
            continue

        cls = r.get("source_class")
        if cls not in SOURCE_CLASSES:
            continue
        has_pointer = bool(r.get("pointer"))
        if cls == "self" and not has_pointer:
            continue  # echo rule (W8): KTSB re-reading itself moves nothing; the row is still kept
        wstep = r.get("worry")
        if isinstance(wstep, (int, float)) and wstep > 0:
            worry_steps.append((ts, min(float(wstep), WORRY_STEP_BIG)))
        polarity = r.get("polarity") or 0
        if not polarity:
            continue
        sign = 1 if polarity > 0 else -1
        angle = (cls, r.get("source") or "", r.get("context") or (r.get("ts") or "")[:10])
        if angle in seen_angles:
            continue  # repeats in one conversation / one source-context count once (D2, P13)
        lean = _lean(belief_now(), yes, no)
        if lean and sign != lean and cls in CONTRADICTION_WORRY:
            worry_steps.append((ts, CONTRADICTION_WORRY[cls]))  # D4 b: contradiction → Worry at once
            reasons.append(f"contradiction:{cls}")
        seen_angles.add(angle)
        if cls == "self":
            own += sign * BELIEF_STEP["self"]
        else:
            outside += sign * BELIEF_STEP[cls]

    w = Weight(parked=parked, maturity=max(0.0, min(1.0, maturity)), reasons=reasons)
    w.belief = belief_now()
    w.validated = _validated(yes, no)
    w.yes, w.no = yes, no
    w.worry = _decayed(worry_steps, now)
    w.angles = len(seen_angles)
    w.learned_n = n_answers
    w.learned_goal = LEARNED_GOAL.get(kind)
    w.trust = w.validated if w.validated is not None else w.belief
    w.source = "human" if w.validated is not None else ("machine" if seen_angles else "none")
    w.word = word_for(w.trust)
    w.action = w.trust * (ACTION_FLOOR + (1.0 - ACTION_FLOOR) * w.maturity)
    certainty = abs(w.trust - 0.5) * 2.0
    # Attention (P7 + D10): Worry, plus uncertainty that counts more while the domain is young.
    w.attention = min(1.0, w.worry + (1.0 - certainty) * 0.5 * (1.0 - 0.5 * w.maturity))
    return w


def maturity_for(kind: str, first_ts: datetime | None, confirmations: int, now: datetime | None = None) -> float:
    """Domain maturity (P149 Q8): passive days since the domain's first evidence plus a week per human answer, over the
    domain's own time constant (W2)."""
    now = now or datetime.now(UTC)
    days = max(0.0, (now - first_ts).total_seconds() / 86400.0) if first_ts else 0.0
    tau = MATURITY_DAYS.get(kind, MATURITY_DAYS_DEFAULT)
    return min(1.0, (days + CONFIRMATION_DAYS * confirmations) / tau)
