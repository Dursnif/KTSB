"""
gpu_gate.py
===========

Global GPU arbitration for Kåre / Ollama proxy.

Ansvar:
- Én eksklusiv GPU-holder om gangen
- Prioritetskø (lavere tall = høyere prioritet)
- Kåre er alltid sjefen (prioritet 0)
- Timeout-profiler (strict / normal / relaxed)
- Watchdog (PID-basert)
- Forced release som siste skanse

Prioriteter:
  0  = kaare      (alltid først)
  10 = frigate    (venter på Kåre)
  20 = andre      (venter på alle)
"""

import os
import time
import json
import fcntl
import signal
import threading
from dataclasses import dataclass, field
from typing import Optional, List, Callable

# ------------------------------------------------------------
# Konfig
# ------------------------------------------------------------

GPU_LOCK_PATH = "/kaare/runtime/gpu.lock"
os.makedirs(os.path.dirname(GPU_LOCK_PATH), exist_ok=True)

PROFILE_MULTIPLIER = {
    "strict": 0.25,
    "normal": 0.50,
    "relaxed": 1.00,
}

WATCHDOG_INTERVAL_S = 5

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------

def _log(event: str, **fields):
    payload = {
        "ts": time.time(),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)

# ------------------------------------------------------------
# GpuRequest
# ------------------------------------------------------------

@dataclass
class GpuRequest:
    rid: str
    source: str
    container: str
    host_pid: int

    timeout_profile: str
    baseline_timeout_s: int
    priority: int = 10          # 0=kaare, 10=frigate, 20=andre

    enqueue_ts: float = field(default_factory=time.monotonic)
    acquire_ts: Optional[float] = None
    state: str = "NEW"
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def effective_timeout_s(self) -> int:
        mult = PROFILE_MULTIPLIER.get(self.timeout_profile, 1.0)
        return int(self.baseline_timeout_s * mult)

# ------------------------------------------------------------
# GPU Gate (singleton)
# ------------------------------------------------------------

class GpuGate:
    def __init__(self):
        self._queue: List[GpuRequest] = []
        self._current: Optional[GpuRequest] = None
        self._lock_fd = open(GPU_LOCK_PATH, "w")
        self._mutex = threading.Lock()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
        )
        self._watchdog_thread.start()

    # --------------------------------------------------------

    def acquire(self, req: GpuRequest):
        with self._mutex:
            if self._current is None:
                self._lock_gpu(req)
                return

            req.state = "QUEUED"
            self._queue.append(req)
            # Sorter: lavest prioritet-tall = først. Ved likt tall: eldst enqueued = først.
            self._queue.sort(key=lambda r: (r.priority, r.enqueue_ts))

            _log(
                "gpu_enqueue",
                rid=req.rid,
                source=req.source,
                priority=req.priority,
                queue_depth=len(self._queue),
                queue_order=[r.source for r in self._queue],
            )

        # Blokker til GPU blir ledig og vi er først i køen
        while True:
            time.sleep(0.2)
            with self._mutex:
                if self._current is None and self._queue and self._queue[0] is req:
                    self._queue.pop(0)
                    self._lock_gpu(req)
                    return

    # --------------------------------------------------------

    def release(self, rid: str):
        with self._mutex:
            if not self._current or self._current.rid != rid:
                return

            req = self._current
            hold_ms = int((time.monotonic() - req.acquire_ts) * 1000)

            self._unlock_gpu()
            self._current = None
            req.state = "RELEASED"

            _log(
                "gpu_release_ok",
                rid=req.rid,
                source=req.source,
                priority=req.priority,
                run_ms=hold_ms,
                queue_remaining=len(self._queue),
            )

    # --------------------------------------------------------

    def _lock_gpu(self, req: GpuRequest):
        fcntl.flock(self._lock_fd, fcntl.LOCK_EX)

        req.acquire_ts = time.monotonic()
        req.state = "RUNNING"
        self._current = req

        _log(
            "gpu_acquired",
            rid=req.rid,
            source=req.source,
            priority=req.priority,
            host_pid=req.host_pid,
            effective_timeout_s=req.effective_timeout_s,
        )

    # --------------------------------------------------------

    def _unlock_gpu(self):
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        except Exception:
            pass

    # --------------------------------------------------------
    # Watchdog
    # --------------------------------------------------------

    def _watchdog_loop(self):
        while True:
            time.sleep(WATCHDOG_INTERVAL_S)

            with self._mutex:
                req = self._current
                if not req:
                    continue

                if not self._pid_alive(req.host_pid):
                    _log("gpu_pid_dead", rid=req.rid, source=req.source, host_pid=req.host_pid)
                    self._force_release(req, reason="pid_dead")
                    continue

                run_s = time.monotonic() - req.acquire_ts
                if run_s > req.effective_timeout_s:
                    _log(
                        "gpu_timeout_exceeded",
                        rid=req.rid,
                        source=req.source,
                        run_s=int(run_s),
                        timeout_s=req.effective_timeout_s,
                    )
                    self._force_release(req, reason="timeout")

    # --------------------------------------------------------

    def _force_release(self, req: GpuRequest, reason: str):
        self._unlock_gpu()
        self._current = None
        req.state = "FORCED_RELEASE"
        req.cancel_event.set()

        _log(
            "gpu_forced_release",
            rid=req.rid,
            source=req.source,
            reason=reason,
            action="lock_released_and_http_cancelled",
        )

    # --------------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


# ------------------------------------------------------------
# Singleton
# ------------------------------------------------------------

GPU_GATE = GpuGate()
