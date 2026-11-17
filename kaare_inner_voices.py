#!/usr/bin/env python3
"""Jing / Jang as KTSB processes (run-queue J27): the model is the role `jing` / `jang` in llm.yaml (GUI: Settings →
LLM/Models), wherever it runs. One thought per interval (Settings → inner voices), one rid per thought.

    python kaare_inner_voices.py --voice jing            # loop (a systemd unit)
    python kaare_inner_voices.py --voice jang --once     # one thought
    python kaare_inner_voices.py --voice jing --once --dry-run   # print, write nothing

The older runners in services/inner_voices/ (model in-process, OpenVINO) stay for installs that run the models locally
without a model server (rule 24).
"""

import argparse
import asyncio
import logging
import sys

from kaare_core import inner_voice_runner as ivr

log = logging.getLogger("inner_voices")


async def _loop(voice: str, once: bool, dry_run: bool) -> int:
    run = ivr.jing_once if voice == "jing" else ivr.jang_once
    while True:
        try:
            res = await run(write=not dry_run)
            if dry_run:
                print(f"[{voice}] {res['rid']}\n{res['text']}", flush=True)
        except Exception as e:
            log.warning("[%s] thought failed: %s", voice, type(e).__name__)
        if once:
            return 0
        cfg = ivr._settings(voice)
        default = 180 if voice == "jing" else 600
        await asyncio.sleep(int(cfg.get("interval_seconds", cfg.get("reflection_interval_seconds", default))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", choices=("jing", "jang"), required=True)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the thought, write nothing")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return asyncio.run(_loop(a.voice, a.once, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
