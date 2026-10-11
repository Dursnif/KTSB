"""
S6 — consolidation (P118 B6, T2-D4 / T2-D8 / PR3).

Levels: week → month → half-year → year, in the household's local time zone. A period is due
when it has fully ended and has no consolidation row yet; a higher level is due only when every
lower period inside it is consolidated (so Janus can read their summaries).

Code counts and links (covers = inbox row ids for a week, child consolidation ids above that);
Janus writes the short summary. No row is written when Janus gives nothing usable — the period
stays due and is tried again the next night. Nothing is deleted: after a week is consolidated,
its open rows older than 7 days leave the ACTIVE inbox (status 'consolidated') and stay linked.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

from kaare_core.config import get_local_tz
from kaare_core.dev_slot.json_out import extract_json
from kaare_core.memory import learning_store
from kaare_core.tools.i18n import t

log = logging.getLogger("dev_slot")

LEVELS = ("week", "month", "half_year", "year")
_CHILD = {"month": "week", "half_year": "month", "year": "half_year"}
MAX_PER_NIGHT = 8  # Janus calls per night for consolidation (oldest first)
ACTIVE_DAYS = 7  # PR3: rows leave the active inbox after this, once their week is done


# ── Periods ─────────────────────────────────────────────────────────────────


def _local_midnight(d: date, tz) -> datetime:
    return datetime.combine(d, dtime.min, tzinfo=tz)


def period_of(level: str, d: date) -> tuple[str, date, date]:
    """(label, first day, first day of the next period) for the period containing `d`."""
    if level == "week":
        start = d - timedelta(days=d.weekday())
        iso = start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}", start, start + timedelta(days=7)
    if level == "month":
        start = d.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return f"{start:%Y-%m}", start, nxt
    if level == "half_year":
        if d.month <= 6:
            return f"{d.year}-H1", date(d.year, 1, 1), date(d.year, 7, 1)
        return f"{d.year}-H2", date(d.year, 7, 1), date(d.year + 1, 1, 1)
    if level == "year":
        return f"{d.year}", date(d.year, 1, 1), date(d.year + 1, 1, 1)
    raise ValueError(level)


def ended_periods(level: str, first_day: date, today: date) -> list[tuple[str, date, date]]:
    """All periods of `level` from the one containing first_day that ended before today."""
    out = []
    cur = period_of(level, first_day)
    while cur[2] <= today:
        out.append(cur)
        cur = period_of(level, cur[2])
    return out


def _children(level: str, start: date, end: date) -> list[str]:
    """Labels of the lower-level periods that overlap [start, end)."""
    child = _CHILD[level]
    labels, d = [], start
    while d < end:
        label, _s, nxt = period_of(child, d)
        if label not in labels:
            labels.append(label)
        d = nxt
    return labels


def _utc(d: date, tz) -> str:
    return _local_midnight(d, tz).astimezone(UTC).isoformat(timespec="seconds")


# ── Janus summary ───────────────────────────────────────────────────────────


async def _summary(janus_call, *, level: str, period: str, data: dict, lang: str, rid: str) -> str:
    text = await janus_call(
        t("slot_janus_system", lang),
        t(
            "slot_consolidate_user",
            lang,
            level=t(f"slot_level_{level}", lang),
            period=period,
            data=json.dumps(data, ensure_ascii=False, default=str)[:16000],
        ),
        rid,
    )
    return str((extract_json(text) or {}).get("summary", "")).strip()[:1500]


# ── S6 ──────────────────────────────────────────────────────────────────────


async def run_s6(
    janus_call,
    *,
    lang: str,
    rid: str,
    now: datetime | None = None,
    budget=None,
    max_per_night: int = MAX_PER_NIGHT,
) -> dict:
    """Consolidate every due period (oldest first, lower levels first), then retire aged open rows
    of consolidated weeks. `janus_call(system, user, rid) -> str` is injected (steps._janus_call)."""
    tz = get_local_tz()
    now = (now or datetime.now(UTC)).astimezone(tz)
    today = now.date()
    first = learning_store.earliest_activity()
    result = {"written": [], "retired": 0, "skipped_no_summary": []}
    if not first:
        return result
    first_day = datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone(tz).date()
    calls = 0

    for level in LEVELS:
        for label, start, end in ended_periods(level, first_day, today):
            if learning_store.consolidation_exists(level, label):
                continue
            if calls >= max_per_night or (budget is not None and not budget.left()):
                return _retire(result, first_day, today, tz, now)
            s_utc, e_utc = _utc(start, tz), _utc(end, tz)
            counts = learning_store.period_counts(s_utc, e_utc)
            if level == "week":
                if counts["inbox_rows"] == 0 and counts["findings"] == 0:
                    # Nothing happened: record the empty week mechanically, no Janus call.
                    learning_store.add_consolidation(level, label, "", [], counts)
                    result["written"].append(f"{level}:{label}")
                    continue
                material = learning_store.period_material(s_utc, e_utc)
                covers = material.pop("item_ids")
                data = {"counts": counts, **material}
            else:
                kids = _children(level, start, end)
                rows = learning_store.get_consolidations(_CHILD[level], kids)
                if len(rows) < len(kids):
                    continue  # a lower period inside is not consolidated yet — wait
                covers = [r["id"] for r in rows]
                data = {
                    "counts": counts,
                    "parts": [
                        {"period": r["period"], "summary": r["summary"]}
                        for r in rows
                        if r["summary"]
                    ],
                }
                if not data["parts"] and counts["inbox_rows"] == 0:
                    learning_store.add_consolidation(level, label, "", covers, counts)
                    result["written"].append(f"{level}:{label}")
                    continue
            calls += 1
            summary = await _summary(
                janus_call, level=level, period=label, data=data, lang=lang, rid=rid
            )
            if not summary:
                result["skipped_no_summary"].append(f"{level}:{label}")
                continue
            learning_store.add_consolidation(level, label, summary, covers, counts)
            result["written"].append(f"{level}:{label}")
    return _retire(result, first_day, today, tz, now)


def _retire(result: dict, first_day: date, today: date, tz, now: datetime) -> dict:
    cutoff = (now - timedelta(days=ACTIVE_DAYS)).astimezone(UTC).isoformat(timespec="seconds")
    for label, start, end in ended_periods("week", first_day, today):
        if learning_store.consolidation_exists("week", label):
            result["retired"] += learning_store.retire_aged_open_rows(
                _utc(start, tz), _utc(end, tz), cutoff
            )
    return result
