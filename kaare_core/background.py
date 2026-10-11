"""
Fire-and-forget background tasks that are not lost.

asyncio only keeps a weak reference to a task. A task started with asyncio.create_task() whose
return value nobody stores can be garbage-collected while it is still running — the work then
silently stops. spawn() keeps a strong reference until the task is done and logs any exception
the task ends with (otherwise it only surfaces as "Task exception was never retrieved").
"""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_running: set[asyncio.Task] = set()


def _on_done(task: asyncio.Task) -> None:
    _running.discard(task)
    try:
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Background task %s failed: %r", task.get_name(), task.exception())
    except Exception:
        pass


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Start coro as a background task and keep it alive until it finishes."""
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_on_done)
    return task
