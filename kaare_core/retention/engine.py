"""Generic retention tiers + rollup runner (P144-s07; shared with the Argus retention job, I19).

A data store plugs in through an adapter; this module knows nothing about SQLite, Qdrant or Frigate:
  - `Tiers` — how long each detail level is kept (days; 0 = keep forever) → cutoff timestamps;
  - `Adapter.rollup(limit)` — fold up to `limit` source rows into the coarser tiers in ONE short transaction and
    mark them rolled; returns how many (0 = done). Runs in a dry run too: rollups only add, never lose data;
  - `Adapter.trim_steps(now)` — the destructive steps in order, each a `TrimStep` whose `apply(limit)` changes at
    most `limit` rows in one short transaction (returns how many; 0 = done) and whose `count()` says how many it
    would change now. A step must only touch rows that are already rolled up (counts are never forgotten);
  - `Adapter.record(report)` — keep the run's report where the store wants it.

`run()` rolls up, then trims — or, in a dry run, only counts and logs what it would trim. Short transactions with a
pause between them keep live writers (the pipe, Argus) from waiting on a long lock (rule 10). A crash between two
chunks leaves a consistent store; the next run continues where this one stopped. One failing step is logged and the
others still run. Sync by design: callers run it with `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Protocol

log = logging.getLogger(__name__)

DAY_S = 86400


@dataclass(frozen=True)
class Cutoffs:
    detail: float  # full-detail rows starting before this are trimmed
    hourly: float | None  # hourly rows before this are deleted (None = kept forever)
    daily: float | None  # daily rows before this are deleted (None = kept forever)


@dataclass(frozen=True)
class Tiers:
    detail_days: int
    hourly_days: int  # 0 = keep forever
    daily_days: int  # 0 = keep forever

    def cutoffs(self, now: float) -> Cutoffs:
        """A coarser tier never ends before a finer one (its rows are what is left of the finer tier)."""
        detail = max(1, self.detail_days)
        hourly = max(self.hourly_days, detail) if self.hourly_days else 0
        daily = max(self.daily_days, hourly or detail) if self.daily_days and hourly else 0
        return Cutoffs(
            detail=now - detail * DAY_S,
            hourly=now - hourly * DAY_S if hourly else None,
            daily=now - daily * DAY_S if daily else None,
        )


@dataclass
class TrimStep:
    name: str  # «<table>.<action>», e.g. events.delete — the key in the report and the log
    count: Callable[[], int]
    apply: Callable[[int], int]


class Adapter(Protocol):
    name: str

    def rollup(self, limit: int) -> int: ...

    def trim_steps(self, now: float) -> list[TrimStep]: ...

    def record(self, report: Report) -> None: ...


@dataclass
class Report:
    adapter: str
    dry_run: bool
    started_ts: float
    rolled: int = 0
    steps: dict[str, int] = field(default_factory=dict)  # rows changed — or, in a dry run, rows it would change
    errors: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def hour_floor(ts: float) -> int:
    return int(ts // 3600 * 3600)


def local_day(ts: float, tz: tzinfo | None) -> str:
    """The household's calendar day of a UTC timestamp («home Tuesday afternoon» is a local-time fact)."""
    return datetime.fromtimestamp(ts, tz or UTC).strftime("%Y-%m-%d")


def day_bounds(day: str, tz: tzinfo | None) -> tuple[float, float]:
    """UTC timestamps [start, end) of a household calendar day «YYYY-MM-DD» (23 or 25 h around DST)."""
    d = datetime.strptime(day, "%Y-%m-%d").date()
    zone = tz or UTC
    start = datetime(d.year, d.month, d.day, tzinfo=zone)
    end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    return start.timestamp(), end.timestamp()


def _drain(fn: Callable[[int], int], chunk: int, pause_s: float) -> int:
    total = 0
    while True:
        n = fn(chunk)
        total += n
        if n <= 0:
            return total
        if pause_s:
            time.sleep(pause_s)  # let live writers in between two chunks


def run(adapter: Adapter, *, dry_run: bool, now: float | None = None, chunk: int = 500,
        pause_s: float = 0.02) -> Report:
    """Roll up, then trim (or count, in a dry run). Never raises; problems land in `report.errors`."""
    now = time.time() if now is None else now
    t0 = time.monotonic()
    rep = Report(adapter=adapter.name, dry_run=dry_run, started_ts=now)
    try:
        rep.rolled = _drain(adapter.rollup, chunk, pause_s)
    except Exception as e:
        rep.errors["rollup"] = str(e)
        log.error("[RETENTION] %s rollup failed (resumes next run): %s", adapter.name, e)
    try:
        steps = adapter.trim_steps(now)
    except Exception as e:
        rep.errors["trim_steps"] = str(e)
        log.error("[RETENTION] %s trim steps unavailable: %s", adapter.name, e)
        steps = []
    for step in steps:
        try:
            rep.steps[step.name] = step.count() if dry_run else _drain(step.apply, chunk, pause_s)
        except Exception as e:
            rep.steps.setdefault(step.name, 0)
            rep.errors[step.name] = str(e)
            log.error("[RETENTION] %s %s failed: %s", adapter.name, step.name, e)
    rep.seconds = round(time.monotonic() - t0, 3)
    verb = "would change" if dry_run else "changed"
    log.info("[RETENTION] %s%s: rolled up %d; %s %s", adapter.name, " (dry run)" if dry_run else "", rep.rolled,
             verb, ", ".join(f"{k}={v}" for k, v in rep.steps.items()) or "nothing")
    try:
        adapter.record(rep)
    except Exception as e:
        log.warning("[RETENTION] %s could not record the report: %s", adapter.name, e)
    return rep
