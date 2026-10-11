"""
One background model job at a time, with progress a GUI can poll (P126).

Used by the model-download routers (semantic memory, Whisper). A job is a plain dict so it can be
returned as JSON: id, kind, model, status (running|done|error), phase, bytes/total_bytes (download
progress), progress (free-form counters), error (a short code the GUI translates), detail, result.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import HTTPException


class SingleJob:
    def __init__(self) -> None:
        self.state: dict = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return bool(self.state) and self.state.get("status") == "running"

    def snapshot(self) -> dict | None:
        return dict(self.state) if self.state else None

    async def start(self, kind: str, model: str, runner: Callable[[], Awaitable[None]]) -> dict:
        """Start `runner` as the job; 409 if another job is still running."""
        async with self._lock:
            if self.running:
                raise HTTPException(409, "Another download or switch is running")
            self.state.clear()
            self.state.update(
                id=uuid.uuid4().hex[:12],
                kind=kind,
                model=model,
                status="running",
                phase="start",
                bytes=0,
                total_bytes=0,
                progress={},
                error=None,
                detail=None,
                result=None,
            )
            self._task = asyncio.create_task(self._run(runner))
            return dict(self.state)

    async def _run(self, runner: Callable[[], Awaitable[None]]) -> None:
        try:
            await runner()
        except Exception as e:  # a runner bug must still end the job, never leave it «running»
            self.fail("generic", str(e))
        if self.state.get("status") == "running":
            self.state["status"] = "done"

    def fail(self, code: str, detail: str = "") -> None:
        self.state.update(status="error", error=code, detail=(detail or "")[:300])

    async def watch_folder(self, folder: Path, interval: float = 1.0) -> None:
        """Report the bytes on disk under `folder` while the job is in its download phase
        (huggingface_hub writes *.incomplete files there as it goes)."""
        while self.state.get("status") == "running" and self.state.get("phase") == "download":
            try:
                self.state["bytes"] = sum(
                    f.stat().st_size for f in folder.rglob("*") if f.is_file()
                )
            except Exception:
                pass
            await asyncio.sleep(interval)
