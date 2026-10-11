"""
The nightly dev slot, end to end (P118 B5/B6): S0 collect → Janus → S1 → S2 → S3 (+ S4 verify,
9B verifier) → S6 consolidate → S5 report. Called by kaare_night_sequence.py (step 4, after the reflection
meeting) and by the GUI's "start now" button (`python -m kaare_core.dev_slot.run`).

Progress goes to stdout as one English line per step, «[DEV_SLOT] step n/7 <code>», which
router_meetings.py turns into a step code the GUI shows in the user's language.
The slot never writes to the house, configs or code; the old kaare_dev_meeting.py stays on disk.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

from kaare_core.dev_slot import collect, consolidate, steps
from kaare_core.dev_slot.config import get_dev_slot_config
from kaare_core.memory import learning_store
from kaare_core.tools.i18n import get_lang

log = logging.getLogger("dev_slot")

STEPS = ("S0", "janus", "S1", "S2", "S3", "S6", "S5")
_MEETING_LOCK = Path("/kaare/state/meeting_active.lock")


def progress(code: str) -> None:
    """One stdout line per step — parsed by router_meetings._parse_dev_slot_step()."""
    try:
        n = STEPS.index(code) + 1
    except ValueError:
        return
    print(f"[DEV_SLOT] step {n}/{len(STEPS)} {code}", flush=True)
    # Same line into the log file too, so the GUI can follow the nightly run (P119 B5).
    log.info("[DEV_SLOT] step %d/%d %s", n, len(STEPS), code)


async def main(*, report_dir: Path | None = None) -> Path | None:
    cfg = get_dev_slot_config()
    lang = get_lang("global")
    ms = int(time.time() * 1000)
    run_id = f"slot-{ms}"
    rid = f"rid-meet-{ms}"
    budget = steps.Budget(cfg["time_budget_minutes"])
    log.info(
        "dev slot %s starting (budget %d min, %d topics, rid %s)",
        run_id,
        cfg["time_budget_minutes"],
        cfg["topics_per_meeting"],
        rid,
    )
    try:
        # B8: rows written before themes existed get one by code rules (idempotent, no LLM).
        try:
            learning_store.backfill_themes()
        except Exception as e:
            log.warning("dev slot %s: theme backfill failed: %s", run_id, e)
        progress("S0")
        bundle = await collect.collect(run_id)
        b3 = await steps.run_b3(
            bundle,
            run_id=run_id,
            rid=rid,
            lang=lang,
            budget=budget,
            round_cap=cfg["round_cap_per_topic"],
            on_step=progress,
            topics_per_meeting=cfg["topics_per_meeting"],
        )
        # S6 before S5 so the morning report can say what was consolidated. Own small budget:
        # consolidation must not starve when S1–S3 used the whole slot (PR3 depends on it).
        progress("S6")
        try:
            b3["s6"] = await consolidate.run_s6(
                steps._janus_call, lang=lang, rid=rid, budget=steps.Budget(5)
            )
            learning_store.save_artifact(run_id, "S6", b3["s6"])
        except Exception as e:
            log.warning("dev slot %s: consolidation failed: %s", run_id, e)
            b3["s6"] = {"written": [], "retired": 0, "error": str(e)}
        # The report always gets written, even when S1–S3 used the whole budget.
        path = await steps.run_b4(
            b3,
            run_id=run_id,
            rid=rid,
            lang=lang,
            budget=steps.Budget(5),
            report_dir=report_dir,
            on_step=progress,
        )
        learning_store.finish_run(run_id)
        print(f"[DEV_SLOT] done — report {path}", flush=True)
        return path
    except Exception as e:
        log.exception("dev slot %s failed: %s", run_id, e)
        print(f"[DEV_SLOT] failed: {e}", flush=True)
        return None


def cli() -> int:
    """GUI 'start now': runs the slot and removes the meeting lock the API set before spawning."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        path = asyncio.run(main())
    finally:
        _MEETING_LOCK.unlink(missing_ok=True)
    return 0 if path else 1


if __name__ == "__main__":
    sys.exit(cli())
