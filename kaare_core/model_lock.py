"""
Cross-process lock for Ollama instances shared by multiple processes.

Uses fcntl.flock with LOCK_NB polling — released automatically if the process crashes.
One lock file per Ollama instance (keyed by its base_url), so serialisation stays scoped to
the actual resource being called instead of one global lock covering every instance.
Use as async context manager: async with lock_agent(role_url("miss_kare"), "miss_kare"):

The agent pool is read from llm.yaml, never hardcoded (CLAUDE.md rule 24, DT03 D1): every
role tagged by `agent_pool.roles` contributes its own base_url, and the distinct URLs are the
pool. This house: two instances (127.0.0.1:11445 fallback, 127.0.0.1:11447 Miss Kåre /
Mechanic / Miss Library) that back each other up. A plain Docker install: one instance
(http://ollama:11434) — the same mechanism with nothing to overflow onto. All pool members run
the same model with identical num_ctx (CLAUDE.md GPU policy), which is what makes it safe for
a caller to land on either instance.

Mutual overflow (2026-09-02, i37-ada-dual-9b-fallback-and-num-parallel.md): use
lock_agent_pool(pool_order(own_url)) — it tries every instance in the list on each poll pass
and yields the URL it actually won, so the caller routes its HTTP request there. Used by every
latency-sensitive caller: miss_kare (evaluator.py, nightjob), mechanic (mechanic/tools.py +
executor_agents.py's mechanic_search, dev slot), library (executor_library.py), the dev-slot
verifier and kare_fallback (llm_adapter.py). Plain lock_agent() (one instance, no overflow) is
used by kaare_reflection.py.

Before 2026-10-06 callers kept only the *port* of base_url and called http://127.0.0.1:<port>,
with the pool hardcoded to (11445, 11447) — so in Docker (http://ollama:11434) Miss Kåre,
Mechanic and Miss Library reached nothing (DT03 F1).
"""

import asyncio
import fcntl
import logging
import os
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

log = logging.getLogger("model_lock")

_LOCK_DIR = "/kaare/runtime"
os.makedirs(_LOCK_DIR, exist_ok=True)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _cfg():
    """kaare_core.config, imported late on purpose — circular import (rule 14 exception):
    config → _load_tool_permissions → tools.definitions → model_lock.restartable_containers.
    A top-level import breaks whenever model_lock is imported before config."""
    # PLC0415: circular: model_lock must import before config (tests/test_d1_agent_pool_topology.py)
    from kaare_core import config  # noqa: PLC0415

    return config


def restartable_containers() -> list[str]:
    """Docker containers restart_docker_container may target on this install — sourced live
    from llm.yaml via config.get_known_containers(), never hardcoded here or at any caller.
    A container name only needs to change in one place (llm.yaml) to be picked up everywhere
    (this tool's allowlist, its GUI enum, the settings GUI's own editable container field) —
    see 2026-09-09: a hardcoded tuple here drifted out of sync with the real container names
    after the 2026-09-02 rename (i37-ada-dual-9b-fallback-and-num-parallel.md), silently
    breaking Kåre/Mechanic's own restart tool for a week."""
    return _cfg().get_known_containers()


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _lock_path(url: str) -> str:
    """One lock file per instance. Local instances keep the old port-only name
    (model_agent_11447.lock), so processes on old and new code share the same lock during a
    rolling restart; other hosts get host+port in the name."""
    parsed = urlparse(_norm(url))
    host = parsed.hostname or "unknown"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host in _LOCAL_HOSTS:
        return f"{_LOCK_DIR}/model_agent_{port}.lock"
    safe_host = "".join(c if c.isalnum() or c in ".-" else "_" for c in host)
    return f"{_LOCK_DIR}/model_agent_{safe_host}_{port}.lock"


def role_url(role: str) -> str:
    """The base_url a role really uses (share_with resolved), read at call time so a GUI
    change takes effect after reload_config() without a restart."""
    return _norm(_cfg().get_llm_config(role).get("base_url", ""))


def pool_urls() -> list[str]:
    """Distinct base_urls of the enabled Ollama roles in llm.yaml's agent pool, in llm.yaml
    order. Empty when no pool is configured."""
    config = _cfg()
    urls: list[str] = []
    for role in list((config._LLM_CFG or {}).keys()):
        try:
            cfg = config.get_llm_config(role)
        except Exception:
            continue
        if not isinstance(cfg, dict) or not cfg.get("agent_pool"):
            continue
        if cfg.get("provider", "ollama") != "ollama" or cfg.get("enabled") is False:
            continue
        url = _norm(cfg.get("base_url", ""))
        if url and url not in urls:
            urls.append(url)
    return urls


def pool_order(preferred_url: str) -> list[str]:
    """Poll order for lock_agent_pool(): the caller's own instance first, then the other pool
    instances. An instance outside the pool gets no overflow — just itself."""
    own = _norm(preferred_url)
    pool = pool_urls()
    if own not in pool:
        return [own]
    return [own] + [u for u in pool if u != own]


class LockTimeout(Exception):
    pass


def _acquire(path: str, caller: str) -> object:
    """Blocking acquire with periodic progress logging. Runs in a thread."""
    fd = open(path, "w")  # noqa: SIM115 — returned to the caller; the flock lives while it is open
    t0 = time.monotonic()
    last_log = 0.0

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            waited = time.monotonic() - t0
            if waited > 2:
                log.info("[model_lock] %s acquired lock after %.0fs", caller, waited)
            return fd
        except BlockingIOError:
            elapsed = time.monotonic() - t0
            if elapsed - last_log >= 30:
                log.warning("[model_lock] %s waiting for lock (%.0fs, no timeout)", caller, elapsed)
                last_log = elapsed
            time.sleep(0.25)


def _acquire_timed(path: str, caller: str, max_wait: float) -> object | None:
    """Non-blocking acquire with timeout. Returns None if max_wait exceeded."""
    fd = open(path, "w")  # noqa: SIM115 — returned to the caller; the flock lives while it is open
    t0 = time.monotonic()
    last_log = 0.0

    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            waited = time.monotonic() - t0
            if waited > 2:
                log.info("[model_lock] %s acquired lock after %.0fs", caller, waited)
            return fd
        except BlockingIOError:
            elapsed = time.monotonic() - t0
            if elapsed >= max_wait:
                log.error("[model_lock] %s: lock timeout after %.0fs — giving up", caller, max_wait)
                fd.close()
                return None
            if elapsed - last_log >= 30:
                log.warning(
                    "[model_lock] %s waiting for lock (%.0fs/%.0fs)", caller, elapsed, max_wait
                )
                last_log = elapsed
            time.sleep(0.25)


def _release(fd) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except Exception as e:
        log.warning("[model_lock] failed to release lock: %s", e)


def _acquire_pool(
    paths: list[str], caller: str, max_wait: float | None
) -> tuple[object, str] | None:
    """
    Every poll pass, tries each path in order (non-blocking) — whichever instance is free
    first wins, not necessarily the first in the list. Runs in a thread.
    Returns (fd, path) for the winning lock, or None if max_wait was exceeded.
    """
    fds = [open(p, "w") for p in paths]  # noqa: SIM115 — the winner is returned still open (flock)
    t0 = time.monotonic()
    last_log = 0.0
    try:
        while True:
            for fd, path in zip(fds, paths, strict=False):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    waited = time.monotonic() - t0
                    if waited > 2:
                        log.info(
                            "[model_lock] %s acquired %s after %.0fs (pool of %d)",
                            caller,
                            path,
                            waited,
                            len(paths),
                        )
                    for other_fd, other_path in zip(fds, paths, strict=False):
                        if other_path != path:
                            other_fd.close()
                    return fd, path
                except BlockingIOError:
                    continue
            elapsed = time.monotonic() - t0
            if max_wait is not None and elapsed >= max_wait:
                log.error(
                    "[model_lock] %s: pool lock timeout after %.0fs — giving up (%s)",
                    caller,
                    max_wait,
                    paths,
                )
                for fd in fds:
                    fd.close()
                return None
            if elapsed - last_log >= 30:
                log.warning(
                    "[model_lock] %s waiting for pool lock %s (%.0fs)", caller, paths, elapsed
                )
                last_log = elapsed
            time.sleep(0.25)
    except Exception:
        for fd in fds:
            try:
                fd.close()
            except Exception:
                pass
        raise


@asynccontextmanager
async def lock_agent(url: str, caller: str = "ukjent", max_wait: float | None = None):
    """
    Serialises access to one Ollama instance (by base_url) across processes.

    max_wait: if set and exceeded, raises LockTimeout instead of blocking forever.
    Usage:
        async with lock_agent(role_url("miss_kare"), "miss_kare", max_wait=60): ...
    """
    url = _norm(url)
    path = _lock_path(url)
    caller_tag = f"{caller}@{url}"
    if max_wait is not None:
        fd = await asyncio.to_thread(_acquire_timed, path, caller_tag, max_wait)
        if fd is None:
            raise LockTimeout(
                f"{caller}: could not acquire {url} model lock within {max_wait:.0f}s"
            )
    else:
        fd = await asyncio.to_thread(_acquire, path, caller_tag)
    try:
        yield
    finally:
        _release(fd)


@asynccontextmanager
async def lock_agent_pool(urls: list[str], caller: str = "ukjent", max_wait: float | None = None):
    """
    Serialises access to a *pool* of Ollama instances — tries every URL in `urls` on each
    poll pass (in order, but whichever frees first wins), so a caller overflows onto another
    instance instead of queueing behind traffic on its own. Yields the base_url actually
    acquired; the caller must route its HTTP request there.

    max_wait: if set and exceeded on ALL instances, raises LockTimeout.
    Usage:
        async with lock_agent_pool(pool_order(role_url("mechanic")), "mechanic", max_wait=300) as url:
            endpoint = url + "/api/chat"
    """
    urls = [_norm(u) for u in urls if _norm(u)]
    if not urls:
        raise LockTimeout(f"{caller}: no Ollama instance configured")
    paths = [_lock_path(u) for u in urls]
    caller_tag = f"{caller}@pool{urls}"
    result = await asyncio.to_thread(_acquire_pool, paths, caller_tag, max_wait)
    if result is None:
        raise LockTimeout(f"{caller}: could not acquire any of {urls} within {max_wait:.0f}s")
    fd, path = result
    won_url = urls[paths.index(path)]
    try:
        yield won_url
    finally:
        _release(fd)
