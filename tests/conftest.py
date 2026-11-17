"""Shared test setup: tests never write into the live tool-call log (`logs/tool_calls.log` feeds Kåre's self_trace
and Argus — a test run had left 42 fake rows such as `tv_bed`/`tv_living` there, 2026-10-19)."""

import builtins
import io
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

try:  # the voice venv has no qdrant-client (the Wyoming tests run there) — then there is nothing to guard
    from qdrant_client import QdrantClient
except ImportError:
    QdrantClient = None

from kaare_core import image_store, log_buffer, model_enforcement, receipts
from kaare_core.tools import shared_tools

try:  # the STM pulls in crypto (PyNaCl), which the voice venv does not have
    from kaare_core.memory.short_term import ShortTermMemory
except ImportError:
    ShortTermMemory = None

# timer_service pulls in the user/auth stack (bcrypt), which the voice venv deliberately does not have — the
# Wyoming tests (test_p138_*) run under the voice venv and never touch timers (P145, 2026-10-27).
try:
    from kaare_core.tools import timer_service
except ImportError:
    timer_service = None
try:  # the knowledge store may be missing from a slim venv (voice) — then nothing reads it
    from kaare_core.memory import knowledge_store
except ImportError:
    knowledge_store = None
try:  # same reason: the Frigate store pulls in the user stack
    from kaare_core.frigate_user import store as frigate_store
except ImportError:
    frigate_store = None
try:  # DT04 R0.6a: the camera memory's sealing (needs PyNaCl — not in every venv)
    from kaare_core.frigate_user import sealing as frigate_sealing
except ImportError:
    frigate_sealing = None
try:  # P149: the weight store (same reason: pulls in the user stack via frigate_consent)
    from kaare_core.weights import store as weight_store
except ImportError:
    weight_store = None
try:  # N39: the question-card file (same reason: pulls in the Frigate user stack)
    from kaare_core.memory import chat_questions
except ImportError:
    chat_questions = None
try:  # P150: the raw-log guard below
    from kaare_core.storage import rawlog
except ImportError:
    rawlog = None
try:  # P150: the Argus rollup guard below (same reason: not in every venv's import closure)
    from kaare_core.retention import argus_size
except ImportError:
    argus_size = None
try:  # P144-s24: the Frigate recordings guard below (same reason: not in the voice venv's import closure)
    from adapters import frigate_adapter
except ImportError:
    frigate_adapter = None
# The house's frigate.db path as shipped (read before any fixture repoints it) — for tests that check path rules.
LIVE_FRIGATE_DB = frigate_store.DB_PATH if frigate_store is not None else None


@pytest.fixture(autouse=True)
def _private_tool_log(tmp_path, monkeypatch):
    log = tmp_path / "tool_calls.log"
    monkeypatch.setattr(shared_tools, "_TOOL_CALLS_LOG", log)
    if timer_service is not None:
        monkeypatch.setattr(timer_service, "_TOOL_LOG", log)


@pytest.fixture(autouse=True)
def _private_enforcement_log(tmp_path, monkeypatch):
    """Tests never write into the live enforcement.log (it feeds the GUI's Model guard counts and the
    dev slot) — a P142 test without its own log fixture had left 6 fake rid-less lines there, 2026-10-22."""
    monkeypatch.setattr(model_enforcement, "_ENFORCE_LOG", tmp_path / "enforcement.log")


@pytest.fixture(autouse=True)
def _enforcer_default_modes(monkeypatch):
    """Tests see every detector in its own default mode — not the modes set in the live GUI (P142)."""
    monkeypatch.setattr(model_enforcement, "get_enforcer_modes", dict)


# ── no test writes under the live logs/ (P144-s14) ──────────────────────────────
# 2026-10-27 a test run wrote fake «disk on fire» errors and five «standard admin created» lines into the live
# logs/night_sequence.log: the night sequence attached its FileHandler on import. Argus and humans read those logs.
LIVE_LOGS = Path("/kaare/logs").resolve()


def _is_live_log(file) -> bool:
    if isinstance(file, int):
        return False
    try:
        return Path(os.fsdecode(file)).resolve().is_relative_to(LIVE_LOGS)
    except (TypeError, ValueError, OSError):
        return False


def live_log_handlers() -> list[str]:
    """«logger → file» for every logging FileHandler that writes under the live logs/."""
    loggers = [logging.getLogger()] + [
        lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]
    return [
        f"{lg.name or 'root'} → {h.baseFilename}"
        for lg in loggers
        for h in lg.handlers
        if isinstance(h, logging.FileHandler) and _is_live_log(h.baseFilename)
    ]


@pytest.fixture(autouse=True)
def _no_live_log_writes(monkeypatch):
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(c in str(mode) for c in "wax+") and _is_live_log(file):
            raise AssertionError(f"test opened the live log {file!r} for writing — use tmp_path")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    attached = live_log_handlers()
    if attached:
        pytest.fail("a log handler writes into the live logs/ (attached on import?): " + "; ".join(attached))
    yield
    attached = live_log_handlers()
    if attached:
        pytest.fail("the test attached a log handler that writes into the live logs/: " + "; ".join(attached))


# ── no test opens a database or writes a file under the house's state/ (DT04, 2026-11-16) ───────────────────────────
# A test that opened the live frigate.db left -wal/-shm owned by the test user: kaare.service could no longer write the
# camera memory (866 «readonly database» lines). Same shape as the live-log guard above, for state/.
LIVE_STATE = Path("/kaare/state").resolve()


def _is_live_state(path) -> bool:
    if isinstance(path, int):
        return False
    try:
        text = os.fsdecode(path)
        if text.startswith("file:"):
            text = text[5:].split("?", 1)[0]
        return bool(text) and text != ":memory:" and Path(text).resolve().is_relative_to(LIVE_STATE)
    except (TypeError, ValueError, OSError):
        return False


@pytest.fixture(autouse=True)
def _no_live_question_cards(tmp_path, monkeypatch):
    """N39: the chat question cards are a file under the live state/ — every test gets its own (a test that sets its own
    path patches after this and wins). RAM is reset so no card leaks between tests."""
    if chat_questions is None:
        yield
        return
    monkeypatch.setattr(chat_questions, "CARDS_PATH", tmp_path / "question_cards_default.json")
    chat_questions.reset_for_tests()
    yield
    chat_questions.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_live_knowledge_db(tmp_path, monkeypatch):
    """DT04 2026-11-16: tests that build a system prompt read knowledge.db — the house's file (opening it in WAL mode as the
    test user can leave -wal/-shm owned by that user). It is a tmp file in every test; a test that sets its own path
    patches after this and wins."""
    if knowledge_store is None:
        yield
        return
    monkeypatch.setattr(knowledge_store, "DB_PATH", tmp_path / "knowledge_default" / "knowledge.db")
    monkeypatch.setattr(knowledge_store, "_initialized", False)
    yield


@pytest.fixture(autouse=True)
def _no_live_state_access(monkeypatch):
    real_open = builtins.open
    real_connect = sqlite3.connect

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(c in str(mode) for c in "wax+") and _is_live_state(file):
            raise AssertionError(f"test opened {file!r} under the live state for writing — use tmp_path")
        return real_open(file, mode, *args, **kwargs)

    def guarded_connect(database, *args, **kwargs):
        if _is_live_state(database):
            raise AssertionError(f"test opened the database {database!r} under the live state — use tmp_path")
        return real_connect(database, *args, **kwargs)

    real_mkdir = os.mkdir

    def guarded_mkdir(path, *args, **kwargs):
        # DT04-s08: code that does mkdir() before the guarded write left empty folders in the house when the caller
        # swallowed the AssertionError (state/images/u/{input,frigate}, 2026-11-16). Path.mkdir and os.makedirs both
        # end here. An existing folder is not refused (os.makedirs(exist_ok=True) on it creates nothing).
        if _is_live_state(path) and not os.path.isdir(path):
            raise AssertionError(f"test created the folder {path!r} under the live state — use tmp_path")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    monkeypatch.setattr(os, "mkdir", guarded_mkdir)
    yield


@pytest.fixture(autouse=True)
def _no_live_images(tmp_path, monkeypatch):
    """DT04-s08 / N41 «Later»: image_store keeps pictures under a fixed /kaare/state/images — every test gets a tmp root
    (a test that sets its own root patches after this and wins)."""
    monkeypatch.setattr(image_store, "_IMAGES_ROOT", tmp_path / "images_default")


@pytest.fixture(autouse=True)
def _no_live_log_buffer(monkeypatch, tmp_path, request):
    """N37: a line queued for a live log through log_buffer goes to tmp_path instead. log_buffer writes from a background
    thread with os.open, often after the test (or at process exit), so the open() guard above never sees it — every
    suite run left a fake `rid-test-refl` row in the house's logs/llm_calls.log (2026-11-06). Each redirect is recorded
    as a user property (`live_log_redirect`), visible with --junitxml."""
    real = log_buffer.write_line

    def redirected(path, line):
        if _is_live_log(path):
            name = Path(os.fsdecode(path)).name
            request.node.user_properties.append(("live_log_redirect", name))
            path = tmp_path / "live_logs" / name
        return real(path, line)

    monkeypatch.setattr(log_buffer, "write_line", redirected)


@pytest.fixture(autouse=True)
def _no_live_stm_autosave(monkeypatch, tmp_path):
    """P144-s16: an STM autosave aimed at the live state/ goes to tmp_path instead. STMRegistry.get() hardcodes
    /kaare/state/stm_users and saves in a thread that swallows errors, so test runs wrote fake Frigate memory lines
    into the house's frigate.json (2026-10-28) — the open() guard above cannot see that."""
    if ShortTermMemory is None:  # voice venv: no STM there, nothing to redirect
        return
    real = ShortTermMemory.configure_autosave

    def redirected(self, path, min_interval=5.0):
        if str(path).startswith("/kaare/state/"):
            path = str(tmp_path / "stm_users" / Path(path).name)
        return real(self, path, min_interval)

    monkeypatch.setattr(ShortTermMemory, "configure_autosave", redirected)


# ── no test reaches the live Qdrant (P144-s17, BUG-log 2026-10-30) ──────────────
# P146-s01: the nightjob fixture let semantic_memory build a client on the house's URL; executor_library builds one at
# import (a remote client checks the server version on construction). Installed at conftest import, i.e. before any
# test module is imported, so import-time clients are covered too:
#   - built by a module's top-level code (import time) → a client that refuses every call and never contacts the server;
#   - built anywhere else during a run → LiveQdrantBlocked at once;
#   - every attempt is recorded, and the autouse check fails the test even when the code swallowed the exception.
# Local clients (`location=":memory:"`, `path=`) are left alone. Tests that need Qdrant use those or a fake.


class LiveQdrantBlocked(AssertionError):
    pass


_REAL_QDRANT_INIT = QdrantClient.__init__ if QdrantClient is not None else None
_blocked: list[str] = []


class _RefusingQdrantClient(QdrantClient or object):
    """What an import-time client becomes: every public call fails loudly; close() stays quiet (garbage collection)."""

    def __getattribute__(self, name):
        if name.startswith("_") or name == "close":
            return object.__getattribute__(self, name)
        target = object.__getattribute__(self, "_blocked_target")

        def refuse(*a, **kw):
            _blocked.append(target)
            raise LiveQdrantBlocked(f"test reached for Qdrant at {target} ({name}) — use a fake or :memory:")

        return refuse

    def close(self, *a, **kw):
        return None

    def __del__(self):
        return None


def _qdrant_target(args, kwargs) -> str | None:
    """The remote this client would talk to, or None for a local (in-memory / on-disk) client."""
    location = kwargs.get("location", args[0] if args else None)
    if location == ":memory:" or kwargs.get("path") is not None:
        return None
    return str(location or kwargs.get("url") or kwargs.get("host") or "http://localhost:6333")


def _guarded_qdrant_init(self, *args, **kwargs):
    target = _qdrant_target(args, kwargs)
    if target is None:
        return _REAL_QDRANT_INIT(self, *args, **kwargs)
    caller = sys._getframe(1)
    if caller.f_code.co_name == "<module>":
        self._blocked_target = target
        self.__class__ = _RefusingQdrantClient
        return None
    _blocked.append(target)
    raise LiveQdrantBlocked(f"test built a Qdrant client for {target} — use a fake or :memory:")


if QdrantClient is not None:
    QdrantClient.__init__ = _guarded_qdrant_init


def import_time_client(url: str) -> QdrantClient:
    """A client built the way module top-level code builds one (for the guard's own test)."""
    namespace = {"QdrantClient": QdrantClient, "url": url}
    exec(compile("client = QdrantClient(url=url)", "<import-time>", "exec"), namespace)
    return namespace["client"]


def blocked_qdrant() -> list[str]:
    return list(_blocked)


def forget_blocked_qdrant() -> None:
    _blocked.clear()


@pytest.fixture(autouse=True)
def _no_live_qdrant():
    _blocked.clear()
    yield
    if _blocked:
        seen = sorted(set(_blocked))
        _blocked.clear()
        pytest.fail("the test reached for a live Qdrant (blocked by conftest): " + ", ".join(seen))


@pytest.fixture(autouse=True)
def _no_live_frigate_db(tmp_path, monkeypatch):
    """P144-s17: frigate.db defaults to a tmp file in every test. The nightjob tests run the whole night, and the night
    now has a Frigate step that reads frigate.db and writes its progress marker — it must never be the house's file.
    A test that sets its own path still wins (it patches after this)."""
    if frigate_store is not None:
        monkeypatch.setattr(frigate_store, "DB_PATH", tmp_path / "frigate_default" / "frigate.db")
        frigate_store.reset_for_tests()


_TEST_SEAL_KEY = bytes(range(32))


@pytest.fixture(autouse=True)
def _no_live_system_key(monkeypatch):
    """DT04 R0.6a: the camera memory is sealed with a key derived from the machine's system master key. Tests use a
    fixed test key — never read (or create) the house's state/system_master.key."""
    if frigate_sealing is not None:
        monkeypatch.setattr(frigate_sealing, "_master", lambda: _TEST_SEAL_KEY)
        frigate_sealing.reset_keys()
    yield
    if frigate_sealing is not None:
        frigate_sealing.reset_keys()


@pytest.fixture(autouse=True)
def _no_live_raw_logs(tmp_path, monkeypatch):
    """P150 phase 5: raw log segments and the old single files point at tmp_path in every test — never logs/raw/."""
    if rawlog is not None:
        monkeypatch.setattr(rawlog, "RAW_DIR", tmp_path / "raw_logs")
        monkeypatch.setattr(rawlog, "LEGACY", {s: tmp_path / "raw_legacy" / f"{s}.log" for s in rawlog.STREAMS})
        rawlog.reset_cache()
    yield
    if rawlog is not None:
        rawlog.reset_cache()


@pytest.fixture(autouse=True)
def _no_live_argus_rollup(tmp_path, monkeypatch):
    """P150: the Argus rollup.db defaults to a tmp file and measure() to «unavailable» in every test — never the house."""
    if argus_size is not None:
        monkeypatch.setattr(argus_size, "ROLLUP_DB", tmp_path / "argus_default" / "rollup.db")
        # measure() reads the live Qdrant over HTTP (not through QdrantClient, which the guard above blocks)
        monkeypatch.setattr(argus_size, "measure", lambda: {"bytes": 0, "points": 0, "source": "unavailable"})
    yield


@pytest.fixture(autouse=True)
def _no_live_weights_db(tmp_path, monkeypatch):
    """P149: weights.db defaults to a tmp file in every test — the camera's sensor_weight() now reads KTSB's weight
    store, and «Var det deg?» answers write to it; neither may ever be the house's file. A test that sets its own path
    still wins (it patches after this)."""
    if weight_store is None:
        yield
        return
    monkeypatch.setattr(weight_store, "DB_PATH", tmp_path / "weights_default" / "weights.db")
    weight_store.reset_for_tests()
    yield
    weight_store.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_live_event_snapshots(tmp_path, monkeypatch):
    """P144 6e: the pipe keeps event snapshots in frigate_store.SNAPSHOT_DIR — never the house's state/frigate_snapshots
    in a test (2026-11-13: 68 fake test JPEGs got there before this guard)."""
    if frigate_store is None:
        return
    # PLC0415: the domain store pulls in the user stack (see the imports at the top of this file)
    from kaare_core.domain import frigate_store as domain_store  # noqa: PLC0415
    monkeypatch.setattr(domain_store, "SNAPSHOT_DIR", tmp_path / "frigate_snapshots_test")


@pytest.fixture(autouse=True)
def _no_live_receipts_pending():
    """Coalesced background receipts (receipts.COALESCE_KINDS) wait in RAM and are written at process exit — after this
    file's log redirects are gone. A test's pending receipts are dropped at teardown, so no test id ever reaches the house's
    logs/receipts.log (2026-11-12: 15 fake lines got there through atexit before this guard)."""
    yield
    receipts._pending.clear()


@pytest.fixture(autouse=True)
def _no_live_frigate_recordings(monkeypatch):
    """P144-s24: no test may GET the house's Frigate. Vehicle-only episodes now fetch their frames by code, and older
    tests run the conclude loop without faking Frigate — the adapter's base URL points at a closed local port (refused
    at once) unless a test sets its own (it patches after this)."""
    if frigate_adapter is None:
        return
    monkeypatch.setattr(frigate_adapter, "_frigate_url", lambda: "http://127.0.0.1:9")
    # J24: Frigate's cached camera names start empty in every test (no name leaks from one test into the next)
    monkeypatch.setattr(frigate_adapter, "_friendly", {})
    monkeypatch.setattr(frigate_adapter, "_friendly_ts", 0.0)
    # DT04-s08 S05l: no background name refresh thread outliving a test (a test that needs it patches the real one back)
    monkeypatch.setattr(frigate_adapter, "warm_camera_names", lambda: None)
