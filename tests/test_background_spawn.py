"""kaare_core.background.spawn — background tasks are kept alive and their failures logged (P128)."""

import asyncio
import gc
import logging

from kaare_core import background


def test_spawned_task_survives_garbage_collection():
    done = []

    async def work():
        await asyncio.sleep(0.01)
        done.append(True)

    async def main():
        background.spawn(work())  # return value deliberately dropped
        gc.collect()
        await asyncio.sleep(0.05)

    asyncio.run(main())
    assert done == [True]
    assert not background._running  # reference released once finished


def test_failure_is_logged(caplog):
    async def boom():
        raise RuntimeError("kaboom")

    async def main():
        background.spawn(boom(), name="boom-task")
        await asyncio.sleep(0.01)

    with caplog.at_level(logging.WARNING, logger="kaare_core.background"):
        asyncio.run(main())
    assert "boom-task" in caplog.text and "kaboom" in caplog.text
