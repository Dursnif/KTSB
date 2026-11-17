#!/usr/bin/env python3
"""
kaare_night_sequence.py — sequential night orchestrator.

Replaces three separate systemd timers with one ordered sequence:

  03:00  Step 0 — data retention + Frigate memory tiers (P144-s07)
         Step 1 — kaare_nightjob         (LTM compression, STM summary, Jing, Qdrant)
         Step 2 — kaare_miss_kare_nightjob (per-user portrait synthesis) [Fase 2]
         Step 2b — knowledge_reflection   (weekly knowledge_store re-validation, P105 Fase 3)
         Step 3 — reflection meeting       (per-user, under meeting_active.lock)
         Step 4 — dev meeting              (under meeting_active.lock)

Failures or timeouts in one step do not stop subsequent steps.
Log: /kaare/logs/night_sequence.log

Started by kaare_night_runner.py at the GUI start time (N17), or by hand. Only one run at a time:
the process holds state/night_sequence.lock and exits if another run holds it.
  --only reflection   run just the reflection meeting (the GUI's «Start reflection meeting» button)
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from kaare_core.storage import sqlite_files

sys.path.insert(0, "/kaare")


from adapters import llm_adapter as _llm_ctx
from adapters import lovdata_adapter

# P118 B5: the dev slot (agenda → Janus + Kåre → verified findings → morning report) replaces the
# persona debate in kaare_dev_meeting.py, which stays on disk unused (rule 1).
from kaare_core.dev_slot.config import get_dev_slot_config
from kaare_core.dev_slot.run import main as dev_slot_main
from kaare_core.frigate_user import store as frigate_store
from kaare_core.memory import knowledge_reflection, meeting_activity
from kaare_core.storage import rawlog
from kaare_core.users.store import list_users
from kaare_night_runner import acquire_run_lock
from kaare_nightjob import run_nightjob
from kaare_reflection import main as reflection_main

try:
    from kaare_miss_kare_nightjob import run as miss_kare_portrait_run

    _HAS_PORTRAIT_JOB = True
except ImportError:
    miss_kare_portrait_run = None  # type: ignore[assignment]
    _HAS_PORTRAIT_JOB = False

# ── Logging ───────────────────────────────────────────────────────────────────
# kaare_nightjob's module-level basicConfig runs first during import and
# configures the root logger. When the sequence actually runs (main()), we add our
# own file handler on top so all module loggers (nightjob, reflection, dev_meeting)
# also write here. Never on import: a test importing this module wrote fake errors
# into the live log (P144-s14, 2026-10-27).
_LOG_PATH = Path("/kaare/logs/night_sequence.log")

log = logging.getLogger("night_sequence")


def attach_file_log(path: Path = _LOG_PATH) -> logging.Handler | None:
    """Attach the night-sequence file handler to the root logger. None when the file cannot be opened."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(path), encoding="utf-8")
    except PermissionError:
        return None  # Running as non-service user; stdout only
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler

# ── Constants ─────────────────────────────────────────────────────────────────
_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_LOCK_PATH = Path("/kaare/state/meeting_active.lock")

_TIMEOUT_NIGHTJOB = 3600  # 60 min
_TIMEOUT_PORTRAIT = 1800  # 30 min
_TIMEOUT_KNOWLEDGE_REFLECTION = 900  # 15 min — single LLM call, small data volume
_TIMEOUT_REFLECTION = 5400  # 90 min
_TIMEOUT_DEV_MEETING = 3600  # 60 min — floor; grows with the GUI time budget (_dev_slot_timeout)
_DEV_SLOT_MARGIN_S = 20 * 60  # S6 + S5 run on their own 5-min budgets after the main budget
_TIMEOUT_LOVDATA = 900  # 15 min — two downloads + a few seconds of indexing
_TIMEOUT_FRIGATE_RETENTION = 1800  # 30 min — short chunks; a cut-off run resumes the next night
_TIMEOUT_RAW_LOG_RETENTION = 1800  # 30 min — the one-time split of a multi-GB old file is the long part


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_settings() -> dict:
    try:
        return yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.warning("Could not load settings.yaml: %s", e)
        return {}


def _reflection_enabled(settings: dict) -> bool:
    return bool(settings.get("kare_reflection", {}).get("enabled", False))


def _dev_meeting_enabled(settings: dict) -> bool:
    return bool(settings.get("dev_meeting", {}).get("enabled", True))


def _get_active_users() -> list[str]:
    try:
        return [u["username"] for u in list_users() if u["is_active"] and u["username"] != "admin"]
    except Exception as e:
        log.warning("Could not fetch active users: %s", e)
        return []


async def _run_retention_cleanup(settings: dict) -> None:
    """Delete interactions and episodes older than the configured retention period.

    Skipped entirely if retention_days == 0 (keep forever).
    Runs a VACUUM after bulk deletes to reclaim disk space.
    Note: NAS rsync backups retain deleted rows — document this gap in security.md.
    """
    cfg = settings.get("data_retention", {})
    interactions_days = int(cfg.get("interactions_retention_days", 0))
    episodes_days = int(cfg.get("episodes_retention_days", 0))

    db_path = Path("/kaare/state/memory/interactions.db")

    deleted_total = 0

    if interactions_days > 0 or episodes_days > 0:
        if not db_path.exists():
            log.info("[retention] interactions.db not found — skipping")
        else:
            try:
                conn = sqlite_files.connect(str(db_path))
                now = datetime.now(UTC)
                if interactions_days > 0:
                    cutoff = (now - timedelta(days=interactions_days)).isoformat()
                    cur = conn.execute("DELETE FROM interactions WHERE ts < ?", (cutoff,))
                    n = cur.rowcount
                    deleted_total += n
                    log.info(
                        "[retention] deleted %d interactions older than %d days",
                        n,
                        interactions_days,
                    )
                if episodes_days > 0:
                    cutoff = (now - timedelta(days=episodes_days)).isoformat()
                    cur = conn.execute("DELETE FROM episodes WHERE ts_created < ?", (cutoff,))
                    n = cur.rowcount
                    deleted_total += n
                    log.info("[retention] deleted %d episodes older than %d days", n, episodes_days)
                conn.commit()
                if deleted_total > 0:
                    conn.execute("VACUUM")
                    log.info("[retention] VACUUM complete")
                conn.close()
            except Exception as e:
                log.error("[retention] DB cleanup failed: %s", e)

    # P150: the think cache is kept by size (kaare_core.tools.think_cache), no longer by days here.

    if deleted_total == 0 and interactions_days == 0 and episodes_days == 0:
        log.info("[retention] all retention settings are 0 (keep forever) — nothing to do")


async def _run_frigate_retention() -> dict | None:
    """The Frigate memory's retention tiers + rollup (P144-s07). Runs whether or not the shadow pipe is on (retention
    is a promise about data already stored); skipped when there is no frigate.db, so a fresh install never gets an
    empty one. Dry run (GUI setting) only logs what it would trim."""
    if not frigate_store.DB_PATH.exists():
        log.info("[frigate_retention] no frigate.db — skipping")
        return None
    return await asyncio.to_thread(frigate_store.run_retention)


async def _run_raw_log_retention() -> dict:
    """The raw log archive's night step (P150 phase 5, kaare_core.storage.rawlog.run_retention)."""
    rep = await asyncio.to_thread(rawlog.run_retention)
    log.info("[raw_log_retention] %s", rep)
    return rep


def _dev_slot_timeout() -> int:
    """Hard stop for the dev slot step: the GUI time budget plus room for S6 and the report, never
    below the old fixed 60 min. A fixed 60 min killed the slot before its report whenever the
    budget was set near or above it (P118 B8)."""
    try:
        budget_s = int(get_dev_slot_config()["time_budget_minutes"]) * 60
    except Exception:
        return _TIMEOUT_DEV_MEETING
    return max(_TIMEOUT_DEV_MEETING, budget_s + _DEV_SLOT_MARGIN_S)


async def _step(name: str, coro, timeout: int) -> bool:
    """Run coro with timeout. Returns True on success, False on timeout/error."""
    rid = f"rid-night-{name}-{int(time.time() * 1000)}"  # J23: one rid for everything the step calls
    log.info("=== [%s] starting (timeout=%ds, rid=%s) ===", name, timeout, rid)
    t_start = time.monotonic()
    _rid_tok, _src_tok = _llm_ctx._current_rid.set(rid), _llm_ctx._current_source.set(f"night_{name}")
    try:
        await asyncio.wait_for(coro, timeout=timeout)
        elapsed = int(time.monotonic() - t_start)
        log.info("=== [%s] done in %ds ===", name, elapsed)
        return True
    except TimeoutError:
        elapsed = int(time.monotonic() - t_start)
        log.error("=== [%s] TIMED OUT after %ds ===", name, elapsed)
        return False
    except Exception as e:
        elapsed = int(time.monotonic() - t_start)
        log.error("=== [%s] FAILED after %ds: %s ===", name, elapsed, e)
        return False
    finally:
        _llm_ctx._current_rid.reset(_rid_tok)
        _llm_ctx._current_source.reset(_src_tok)


async def _run_reflection_all_users(users: list[str], skip_idle: bool = True) -> None:
    """One meeting per user. At night a user with nothing new since their last meeting is skipped (DT04 R0.9c — a
    meeting without data only burns the main model); the GUI button passes skip_idle=False (an explicit request)."""
    for user_id in users:
        if skip_idle:
            needed, why = await asyncio.to_thread(meeting_activity.needs_meeting, user_id)
            if not needed:
                log.info("--- reflection skipped for user: %s — %s ---", user_id, why)
                continue
        log.info("--- reflection for user: %s ---", user_id)
        try:
            await reflection_main(user_id=user_id)
        except Exception as e:
            log.error("Reflection for %s failed: %s", user_id, e)
        log.info("--- reflection done: %s ---", user_id)


# ── Main sequence ─────────────────────────────────────────────────────────────


async def run_sequence() -> None:
    log.info("====== Night sequence starting ======")
    settings = _load_settings()

    # Step 0: Data retention cleanup (runs before LTM compression to avoid compressing stale data)
    await _step("retention", _run_retention_cleanup(settings), 120)

    # Step 0b: Frigate memory tiers — events → hourly → daily, nightly rollup (P144-s07)
    await _step("frigate_retention", _run_frigate_retention(), _TIMEOUT_FRIGATE_RETENTION)

    # Step 0c: raw log archive (P150) — split the old single files once Argus is past them, then delete whole old day
    # files over the GUI limit (Settings → Logs/Storage). Never cuts a file; Argus and Kåre keep running.
    await _step("raw_log_retention", _run_raw_log_retention(), _TIMEOUT_RAW_LOG_RETENTION)

    # Step 1: LTM compression, STM daily summary, Jing ingestion, Qdrant indexing
    await _step("nightjob", run_nightjob(), _TIMEOUT_NIGHTJOB)

    # Step 1b: Norwegian law from Lovdata's open dataset (P123) — only when switched on in the
    # GUI. One download per dataset per night; a failure keeps yesterday's index.
    if lovdata_adapter.config()["enabled"]:
        await _step("lovdata", lovdata_adapter.refresh(), _TIMEOUT_LOVDATA)

    # Step 2: Miss Kåre portrait synthesis (Fase 2 — skipped until file exists)
    if _HAS_PORTRAIT_JOB:
        await _step("miss_kare_portrait", miss_kare_portrait_run(), _TIMEOUT_PORTRAIT)
    else:
        log.info("[miss_kare_portrait] module not yet installed — skipping (Fase 2).")

    # Step 2b: knowledge_store weekly re-validation (P105 Fase 3, Decision 7 —
    # runs on the main 27B with Kåre's full personality+LTM, not the 9B pool).
    # Hybrid frequency: weekly baseline, or immediately if an important-tagged
    # row is pending — see knowledge_reflection.should_run().
    if knowledge_reflection.should_run():
        await _step(
            "knowledge_reflection", knowledge_reflection.run(), _TIMEOUT_KNOWLEDGE_REFLECTION
        )
    else:
        log.info(
            "[knowledge_reflection] not due yet (< 7 days since last pass, no important tag pending) — skipping."
        )

    # Steps 3+4 under meeting_active.lock
    run_reflection = _reflection_enabled(settings)
    run_dev_meeting = _dev_meeting_enabled(settings)

    if not run_reflection and not run_dev_meeting:
        log.info("Both reflection and dev meeting disabled — skipping meeting block.")
        log.info("====== Night sequence complete ======")
        return

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_PATH.touch()
    log.info("meeting_active.lock set")
    try:
        # Step 3: Reflection meeting (per active user)
        if run_reflection:
            users = _get_active_users()
            if not users:
                log.info("No active users — skipping reflection.")
            else:
                log.info("Active users: %s", ", ".join(users))
                await _step(
                    "reflection",
                    _run_reflection_all_users(users),
                    _TIMEOUT_REFLECTION,
                )
        else:
            log.info("Reflection disabled in settings — skipping.")

        # Step 4: Dev slot (P118) — runs only after the reflection step has finished.
        if run_dev_meeting:
            await _step("dev_slot", dev_slot_main(), _dev_slot_timeout())
        else:
            log.info("Dev meeting disabled in settings — skipping.")
    finally:
        _LOCK_PATH.unlink(missing_ok=True)
        log.info("meeting_active.lock removed")

    log.info("====== Night sequence complete ======")


async def run_reflection_only() -> None:
    """The reflection meeting alone, for every active user, under meeting_active.lock — started
    from the GUI button. Runs even when the nightly reflection is switched off (explicit request)."""
    log.info("====== Reflection meeting (manual) starting ======")
    users = _get_active_users()
    if not users:
        log.info("No active users — skipping reflection.")
        return
    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_PATH.touch()
    log.info("meeting_active.lock set")
    try:
        log.info("Active users: %s", ", ".join(users))
        await _step("reflection", _run_reflection_all_users(users, skip_idle=False), _TIMEOUT_REFLECTION)
    finally:
        _LOCK_PATH.unlink(missing_ok=True)
        log.info("meeting_active.lock removed")
    log.info("====== Reflection meeting (manual) complete ======")


def main() -> int:
    parser = argparse.ArgumentParser(description="KTSB night sequence")
    parser.add_argument("--only", choices=["reflection"], help="run a single step")
    args = parser.parse_args()
    attach_file_log()
    lock = acquire_run_lock()
    if lock is None:
        log.warning("Another night sequence run holds state/night_sequence.lock — not starting.")
        return 1
    try:
        asyncio.run(run_reflection_only() if args.only == "reflection" else run_sequence())
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
