"""The one owner of every write into configs/ (P134).

Nothing else in KTSB opens a config file for writing. Every write here is:
- **locked** — a thread lock inside the process plus ``fcntl.flock`` on ``<dir>/.lock/<name>.lock`` between
  processes and containers (verified across two Docker Desktop containers on a bind mount, P134 Phase 0), so two
  GUI saves at once both survive;
- **atomic** — temp file in the same directory → fsync → ``os.replace`` → fsync the directory: a reader sees the
  old file or the new one, never an empty or half-written one;
- **kept** — the previous version is copied to ``<dir>/.history/<name>/<UTC-ts>_<reason><ext>`` first; the newest
  ``HISTORY_KEEP`` per file are kept (``_corrupt`` copies are never pruned);
- **comment-preserving** — YAML is edited with ruamel.yaml round-trip, so the user's comments and the shipped
  documentation comments survive a save;
- **verified** — the dumped YAML is read back with PyYAML (what every KTSB reader uses) and must equal the intended
  data, else the write is refused and the old file stays.

Reads retry briefly on ENOENT/ENODATA: on Docker Desktop's bind mount a reader can hit that for a moment while
another container replaces the file (P134 Phase 0: 19 of 12 186 reads), even though the replace is atomic.

`null` in a config file is a value like any other here; whether it means «missing → fill from the template» is the
migration's rule (P134 D6), not the store's.

All functions are synchronous. Writes are small and rare (GUI saves, migrations); the lock waits at most
``LOCK_TIMEOUT_S``.
"""

import contextlib
import copy
import datetime as _dt
import errno
import fcntl
import io
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.representer import RoundTripRepresenter
from ruamel.yaml.resolver import VersionedResolver
from ruamel.yaml.scalarint import OctalInt

_log = logging.getLogger(__name__)

CONFIG_DIR = Path("/kaare/configs")
DEFAULT_DIR = Path("/kaare/configs_default")
HISTORY_DIRNAME = ".history"
LOCK_DIRNAME = ".lock"
SNAPSHOT_DIRNAME = "_snapshots"
HISTORY_KEEP = 20  # versions kept per file (P134 P-d: fixed for now, not a setting)
SNAPSHOT_KEEP = 10  # whole-directory snapshots kept (pre-migrate, pre-reset, …)
LOCK_TIMEOUT_S = 10.0
CORRUPT_TAG = "_corrupt"

_READ_RETRY_ERRNOS = {errno.ENOENT, errno.ENODATA, errno.ESTALE}
_READ_RETRIES = 6
_READ_RETRY_SLEEP_S = 0.005


class ConfigStoreError(Exception):
    """Base class for config-store failures. The file on disk is unchanged when one is raised."""


class ConfigCorruptError(ConfigStoreError):
    """The file exists but is empty, not valid YAML, or not a mapping — it is never edited in place."""


class ConfigLockTimeout(ConfigStoreError):
    """Another writer held the file's lock longer than LOCK_TIMEOUT_S."""


class ConfigVerifyError(ConfigStoreError):
    """The YAML about to be written would not read back as the intended data."""


# ── locking ───────────────────────────────────────────────────────────────────


class _FileLock:
    """Re-entrant per-thread lock for one config file: thread lock + flock held at the outermost level."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.rlock = threading.RLock()
        self.depth = 0
        self.fd: int | None = None


_locks: dict[str, _FileLock] = {}
_locks_guard = threading.Lock()


def _lock_path(path: Path) -> Path:
    return path.parent / LOCK_DIRNAME / f"{path.name}.lock"


def _get_lock(path: Path) -> _FileLock:
    key = os.path.abspath(path)
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = _FileLock(_lock_path(Path(key)))
        return lk


def _shared_dir(d: Path, mode: int) -> None:
    """Create `d` usable by every KTSB process user: bare metal runs services as `kaare` while admins run CLI
    tools as themselves, sharing the group of configs/ (setgid keeps it). Best effort — not owner → unchanged."""
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(d, mode)


def _open_lock_fd(lock_path: Path) -> int:
    _shared_dir(lock_path.parent, 0o2775)
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
    except PermissionError:
        # Lock file created by another user (root backup job, a CLI run) — flock works on a read-only fd too.
        fd = os.open(lock_path, os.O_RDONLY)
    with contextlib.suppress(OSError):
        os.chmod(lock_path, 0o666)
    return fd


@contextlib.contextmanager
def lock(path: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the write lock for `path` (re-entrant within a thread). Raises ConfigLockTimeout."""
    path = Path(path)
    lk = _get_lock(path)
    deadline = time.monotonic() + timeout
    if not lk.rlock.acquire(timeout=timeout):
        raise ConfigLockTimeout(f"{path}: lock busy for {timeout:.0f}s")
    try:
        if lk.depth == 0:
            fd = _open_lock_fd(lk.lock_path)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise ConfigLockTimeout(f"{path}: lock busy for {timeout:.0f}s") from None
                    time.sleep(0.02)
            lk.fd = fd
        lk.depth += 1
        try:
            yield
        finally:
            lk.depth -= 1
            if lk.depth == 0 and lk.fd is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(lk.fd, fcntl.LOCK_UN)
                os.close(lk.fd)
                lk.fd = None
    finally:
        lk.rlock.release()


# ── raw I/O ───────────────────────────────────────────────────────────────────


def _read_bytes(path: Path) -> bytes | None:
    """File content, or None when the file does not exist. Retries the transient bind-mount errors."""
    for attempt in range(_READ_RETRIES):
        try:
            return path.read_bytes()
        except OSError as e:
            if e.errno not in _READ_RETRY_ERRNOS:
                raise
            last = attempt == _READ_RETRIES - 1
            # A file the store has never written cannot be mid-replace: a missing one is simply missing.
            if e.errno == errno.ENOENT and (last or not _lock_path(path).exists()):
                return None
            if last:
                raise
            time.sleep(_READ_RETRY_SLEEP_S * (attempt + 1))
    return None


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace `path` with `data` atomically, keeping the old file's permission bits (or `mode` / 0o664)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        keep_mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        keep_mode = mode if mode is not None else 0o664
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, keep_mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)  # our own temp file, never the config
        raise
    _fsync_dir(path.parent)


# ── history ───────────────────────────────────────────────────────────────────


def _slug(reason: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", (reason or "write").lower()).strip("-")
    return s[:40] or "write"


def _utc_stamp() -> str:
    return _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")


def history_dir(path: Path) -> Path:
    path = Path(path)
    return path.parent / HISTORY_DIRNAME / path.name


def history(path: Path) -> list[Path]:
    """Saved versions of `path`, newest first."""
    d = history_dir(path)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.name, reverse=True)


def _save_history(path: Path, reason: str, tag: str = "") -> Path | None:
    """Copy the current file into its history folder. Returns the copy, or None if there was no file."""
    if not path.exists():
        return None
    d = history_dir(path)
    _shared_dir(d.parent, 0o2770)  # may hold .env copies: group only
    _shared_dir(d, 0o2770)
    dest = d / f"{_utc_stamp()}_{_slug(reason)}{tag}{path.suffix}"
    shutil.copy2(path, dest)
    _prune(d)
    return dest


def _prune(d: Path) -> None:
    """Keep the newest HISTORY_KEEP ordinary versions; `_corrupt` copies are evidence and stay."""
    try:
        normal = sorted(
            (p for p in d.iterdir() if p.is_file() and CORRUPT_TAG not in p.name),
            key=lambda p: p.name,
            reverse=True,
        )
        for old in normal[HISTORY_KEEP:]:
            old.unlink()
    except OSError:
        _log.warning("[CONFIG] could not prune history in %s", d, exc_info=True)


def snapshot(tag: str, config_dir: Path = CONFIG_DIR) -> Path:
    """Copy the whole config directory (all files and sub-folders, not history/locks) into
    `<dir>/.history/_snapshots/<UTC-ts>_<tag>/`. Keeps the newest SNAPSHOT_KEEP snapshots."""
    config_dir = Path(config_dir)
    root = config_dir / HISTORY_DIRNAME / SNAPSHOT_DIRNAME
    _shared_dir(root.parent, 0o2770)
    _shared_dir(root, 0o2770)
    dest = root / f"{_utc_stamp()}_{_slug(tag)}"
    shutil.copytree(
        config_dir,
        dest,
        ignore=lambda d, names: [
            n for n in names if n in (HISTORY_DIRNAME, LOCK_DIRNAME) or n.endswith(".tmp")
        ],
    )
    try:
        snaps = sorted(
            (p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True
        )
        for old in snaps[SNAPSHOT_KEEP:]:
            shutil.rmtree(old)
    except OSError:
        _log.warning("[CONFIG] could not prune snapshots in %s", root, exc_info=True)
    _log.info("[CONFIG] snapshot %s", dest)
    return dest


# ── YAML: PyYAML-compatible ruamel round-trip ─────────────────────────────────

_PY_RESOLVER = yaml.resolver.Resolver()


def _plain_reads_as_str(value: str) -> bool:
    return _PY_RESOLVER.resolve(yaml.ScalarNode, value, (True, False)) == "tag:yaml.org,2002:str"


class _Representer(RoundTripRepresenter):
    """Round-trip representer that quotes any string PyYAML (YAML 1.1) would read as something else —
    ruamel's YAML 1.2 output leaves `on`, `yes`, `1:30` unquoted, which every KTSB reader would turn into
    a bool or an int (P134 Phase 0)."""


def _represent_str(rep: RoundTripRepresenter, data: str):
    if data and not _plain_reads_as_str(data) and "\n" not in data:
        return rep.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    return RoundTripRepresenter.represent_str(rep, data)


def _represent_octal(rep: RoundTripRepresenter, data: OctalInt):
    # ruamel keeps `0123` as an octal and writes it back YAML-1.2 style (`0o123`), which PyYAML reads as a string.
    return rep.represent_int(int(data))


_Representer.add_representer(str, _represent_str)
_Representer.add_representer(OctalInt, _represent_octal)


class _PyYamlResolver(VersionedResolver):
    """Resolve plain scalars with PyYAML's own rules. ruamel's YAML 1.1 table also reads `y`/`n` as booleans;
    PyYAML (every KTSB reader) does not — a key `y:` would come back as True (P134 Phase 3)."""

    @property
    def versioned_resolver(self):
        return yaml.resolver.Resolver.yaml_implicit_resolvers


def _yaml(load: bool) -> YAML:
    y = YAML(typ="rt")
    y.Representer = _Representer
    y.preserve_quotes = True
    y.allow_duplicate_keys = True  # PyYAML keeps the last value; so does this
    if load:
        y.Resolver = _PyYamlResolver
        y.version = (1, 1)  # 1.1 constructors: `1:30` → 90, `0123` → octal, as PyYAML
    return y


def plain(obj: Any) -> Any:
    """ruamel round-trip types → plain dict/list/scalars (what yaml.safe_load would give)."""
    if isinstance(obj, dict):
        return {plain(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if type(obj).__name__ == "ScalarBoolean":
        return bool(obj)
    if isinstance(obj, str):
        return str(obj)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if isinstance(obj, _dt.datetime):
        return _dt.datetime.fromisoformat(obj.isoformat())
    return obj


def _typed(obj: Any) -> Any:
    """Comparable form that keeps type identity (True != 1, '1' != 1)."""
    obj = plain(obj)
    if isinstance(obj, dict):
        return (
            "map",
            sorted(((repr(_typed(k)), _typed(v)) for k, v in obj.items()), key=lambda kv: kv[0]),
        )
    if isinstance(obj, list):
        return ("seq", [_typed(v) for v in obj])
    if isinstance(obj, float) and obj != obj:
        return ("float", "nan")
    return (type(obj).__name__, obj)


def same_data(a: Any, b: Any) -> bool:
    return _typed(a) == _typed(b)


def _parse_for_edit(text: str, path: Path) -> Any:
    """Round-trip document for editing. Raises ConfigCorruptError for empty/invalid/non-mapping files.
    Falls back to plain PyYAML data (comments lost for this save) if ruamel reads a value differently."""
    if not text.strip():
        raise ConfigCorruptError(f"{path.name} is empty")
    try:
        ref = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigCorruptError(f"{path.name} is not valid YAML: {e}") from e
    if not isinstance(ref, dict):
        raise ConfigCorruptError(f"{path.name} is not a mapping (got {type(ref).__name__})")
    try:
        data = _yaml(load=True).load(text)
    except Exception:
        data = None
    if data is None or not same_data(data, ref):
        _log.warning(
            "[CONFIG] %s: round-trip reader disagrees with PyYAML — saving without comments",
            path.name,
        )
        return ref
    return data


def dump_yaml(data: Any) -> str:
    """YAML text that PyYAML reads back as `data`. Raises ConfigVerifyError otherwise."""
    buf = io.StringIO()
    _yaml(load=False).dump(data, buf)
    out = buf.getvalue()
    try:
        back = yaml.safe_load(out)
    except yaml.YAMLError as e:
        raise ConfigVerifyError(f"dumped YAML does not parse: {e}") from e
    if not same_data(back, data if data is not None else None):
        raise ConfigVerifyError("dumped YAML reads back as different data")
    return out


def _sync(rt: Any, new: Any) -> Any:
    """Make round-trip node `rt` equal to `new`, keeping comments/quoting wherever the structure survives."""
    if isinstance(rt, dict) and isinstance(new, dict):
        for k in [k for k in rt if k not in new]:
            del rt[k]
        for k, v in new.items():
            rt[k] = _sync(rt[k], v) if k in rt else v
        return rt
    if isinstance(rt, list) and isinstance(new, list):
        for i, v in enumerate(new):
            if i < len(rt):
                rt[i] = _sync(rt[i], v)
            else:
                rt.append(v)
        del rt[len(new) :]
        return rt
    return rt if same_data(rt, new) else new


def _commit(
    path: Path, out: str, old: bytes | None, reason: str, mode: int | None, tag: str = ""
) -> bool:
    """Write `out` if it differs from the current content. Returns True if the file changed."""
    data = out.encode("utf-8")
    if old is not None and old == data:
        return False
    _save_history(path, reason, tag)
    _atomic_write(path, data, mode)
    try:
        _log.info("[CONFIG] wrote %s (%s)", path.name, reason)
    except Exception:
        pass
    return True


# ── public API ────────────────────────────────────────────────────────────────


def read_text(path: Path, default: str = "") -> str:
    raw = _read_bytes(Path(path))
    return default if raw is None else raw.decode("utf-8")


def read_yaml(path: Path, default: Any = None) -> Any:
    """Parsed YAML (PyYAML, like every reader). Missing file → `default` (or {}); empty file → {}.
    Raises yaml.YAMLError for invalid YAML."""
    raw = _read_bytes(Path(path))
    if raw is None:
        return copy.deepcopy(default) if default is not None else {}
    data = yaml.safe_load(raw)
    return {} if data is None else data


_cache: dict[str, tuple[tuple, Any]] = {}
_cache_guard = threading.Lock()


def read_yaml_cached(path: Path, *, copy_result: bool = True) -> Any:
    """read_yaml() re-parsed only when the file changed (mtime_ns, inode, size). On a read error the last
    good value is returned ({} if none). `copy_result=False` returns the shared object — never mutate it."""
    path = Path(path)
    key = os.path.abspath(path)
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_ino, st.st_size)
    except OSError:
        sig = None
    with _cache_guard:
        hit = _cache.get(key)
    if hit is None or hit[0] != sig:
        try:
            data = read_yaml(path)
            with _cache_guard:
                _cache[key] = (sig, data)
            hit = (sig, data)
        except Exception:
            _log.warning(
                "[CONFIG] %s unreadable — keeping last good value", path.name, exc_info=True
            )
            if hit is None:
                return {}
    return copy.deepcopy(hit[1]) if copy_result else hit[1]


class Tracked(dict):
    """A mapping from load_for_update(): remembers what was read, so write_yaml() can apply only the caller's
    changes onto the file as it is when the lock is taken."""

    p134_base: dict


def load_for_update(path: Path) -> Tracked:
    """Read a YAML mapping to change and pass back to write_yaml(). Between this read and that write another writer
    may have saved; write_yaml() then applies only the keys this caller changed (three-way), so both saves
    survive. Missing/empty file → empty mapping; invalid YAML raises yaml.YAMLError, as a direct read did."""
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise ConfigCorruptError(f"{Path(path).name} is not a mapping")
    doc = Tracked(data)
    doc.p134_base = copy.deepcopy(data)
    return doc


_MISSING = object()


def _apply_changes(cur: dict, base: dict, new: dict) -> None:
    """Apply what changed from `base` to `new` onto `cur` (in place). Keys the caller did not touch keep the value
    `cur` has now; on a key both changed, this caller wins."""
    for k in [k for k in base if k not in new]:
        cur.pop(k, None)
    for k, v in new.items():
        b = base.get(k, _MISSING)
        if b is not _MISSING and same_data(b, v):
            continue
        c = cur.get(k, _MISSING) if isinstance(cur, dict) else _MISSING
        if isinstance(v, dict) and isinstance(b, dict) and isinstance(c, dict):
            _apply_changes(c, b, v)
        else:
            cur[k] = _sync(c, v) if c is not _MISSING else v


@contextlib.contextmanager
def edit_yaml(path: Path, reason: str, *, mode: int | None = None) -> Iterator[Any]:
    """Read-modify-write under the lock. Mutate the yielded mapping in place; it is written when the block
    ends without an exception (nothing is written if it raises, or if nothing changed). A missing file starts
    as an empty mapping. Raises ConfigCorruptError for an empty/invalid file — repair is the migration's job."""
    path = Path(path)
    with lock(path):
        old = _read_bytes(path)
        data = CommentedMap() if old is None else _parse_for_edit(old.decode("utf-8"), path)
        yield data
        _commit(path, dump_yaml(data), old, reason, mode)


def update_yaml(path: Path, fn, reason: str, *, mode: int | None = None) -> Any:
    """Functional form of edit_yaml(): `fn(data)` mutates in place or returns a replacement mapping."""
    with edit_yaml(path, reason, mode=mode) as data:
        result = fn(data)
        if result is not None and result is not data:
            _sync(data, result)
    return plain(data)


def write_yaml(path: Path, data: Any, reason: str, *, mode: int | None = None) -> bool:
    """Write `data` to `path`, keeping comments of keys that survive. `data` from load_for_update() is applied as
    a change set onto the current file (three-way); any other `data` replaces the whole document. An unreadable
    current file is kept in history as `_corrupt` before it is replaced. Returns True if the file changed."""
    path = Path(path)
    base = getattr(data, "p134_base", None)
    if isinstance(data, Tracked):
        data = dict(data)
    with lock(path):
        old = _read_bytes(path)
        tag = ""
        doc: Any = None
        if old is not None:
            try:
                doc = _parse_for_edit(old.decode("utf-8"), path)
            except ConfigCorruptError:
                tag = CORRUPT_TAG
        if doc is not None and base is not None and isinstance(doc, dict):
            _apply_changes(doc, base, data)
        else:
            doc = _sync(doc, data) if doc is not None else data
        return _commit(path, dump_yaml(doc), old, reason, mode, tag)


class TextDoc:
    """Mutable holder for edit_text(): set `.text`."""

    def __init__(self, text: str) -> None:
        self.text = text


@contextlib.contextmanager
def edit_text(path: Path, reason: str, *, mode: int | None = None) -> Iterator[TextDoc]:
    """Read-modify-write of a text config (.env, .md) under the lock. Missing file starts as ''."""
    path = Path(path)
    with lock(path):
        old = _read_bytes(path)
        doc = TextDoc("" if old is None else old.decode("utf-8"))
        yield doc
        _commit(path, doc.text, old, reason, mode)


def write_text(path: Path, text: str, reason: str, *, mode: int | None = None) -> bool:
    path = Path(path)
    with lock(path):
        return _commit(path, text, _read_bytes(path), reason, mode)


def write_bytes(path: Path, data: bytes, reason: str, *, mode: int | None = None) -> bool:
    """Replace a config file with exact bytes (backup restore, reset to template): locked, atomic, kept."""
    path = Path(path)
    with lock(path):
        old = _read_bytes(path)
        if old is not None and old == data:
            return False
        _save_history(path, reason)
        _atomic_write(path, data, mode)
    try:
        _log.info("[CONFIG] wrote %s (%s)", path.name, reason)
    except Exception:
        pass
    return True


def set_env_keys(
    path: Path, values: dict[str, str], reason: str, *, mode: int | None = 0o640
) -> None:
    """Set KEY=value lines in a .env file (first uncommented match replaced, others appended), keeping
    comments and every other line. `mode` applies only when the file is created."""
    with edit_text(path, reason, mode=mode) as doc:
        lines = doc.text.splitlines()
        for key, value in values.items():
            for i, line in enumerate(lines):
                s = line.strip()
                if s and not s.startswith("#") and s.partition("=")[0].strip() == key:
                    lines[i] = f"{key}={value}"
                    break
            else:
                lines.append(f"{key}={value}")
        doc.text = "\n".join(lines) + "\n"


def ensure_file(target: Path, source: Path) -> Path:
    """Create `target` as a copy of `source` if it does not exist yet (atomic, under the lock). Never
    overwrites, never raises — returns `target` either way."""
    target = Path(target)
    if target.exists():
        return target
    try:
        with lock(target):
            if not target.exists():
                src = Path(source)
                _atomic_write(target, src.read_bytes(), src.stat().st_mode & 0o7777)
                _log.info("[CONFIG] %s created from %s", target.name, src)
    except Exception:
        _log.exception("[CONFIG] %s: could not create from %s", target.name, source)
    return target


def replace_corrupt(path: Path, data: Any, reason: str) -> Path | None:
    """Keep an unreadable/empty `path` in history as `_corrupt`, then write `data` in its place.
    Returns the kept copy (None if there was no file)."""
    path = Path(path)
    with lock(path):
        kept = _save_history(path, reason, CORRUPT_TAG)
        _atomic_write(path, dump_yaml(data).encode("utf-8"))
    _log.warning("[CONFIG] %s was unreadable — kept as %s, replaced (%s)", path.name, kept, reason)
    return kept
