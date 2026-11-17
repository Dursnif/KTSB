"""Raw event logs as day segments, independent of Argus (P150 phase 5, 2026-11-13: «rålogg filen og argus burde være
uavhengige … argus kan bruke det, men ikke avhengig som i dag»).

A raw stream (ha_events, frigate_mqtt) is written to `logs/raw/<stream>/<YYYY-MM-DD>[.<n>].log` — one file per household
day, a new part when a file passes SEGMENT_MAX_BYTES. Nothing is ever cut in the middle of a file:
  - writers call `current_segment(stream)` (cached a few seconds) and append;
  - Argus keeps `{segment, offset}` per stream and reads with `read_new` — a finished segment is done for good, a segment
    is left only when a newer one exists and the old one has been quiet for a while (a late append is not lost), a segment
    deleted before Argus reached it is reported as a gap, never a re-read;
  - retention (`prune`) deletes whole old segments, oldest first, never today's and never the newest, keeping an optional
    floor of days for readers (the normalcy baseline reads 28 days);
  - readers use `tail_text`, `lines_since` or a `Tail` instead of opening one file.
The old single file (`logs/<stream>.log`) is read as the oldest segment, «legacy», until `split_legacy` turns it into
`<date>.legacy.log` day segments once Argus is past it (one time, at night).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path

from kaare_core.config import get_local_tz
from kaare_core.storage import limits

log = logging.getLogger(__name__)

RAW_DIR = Path("/kaare/logs/raw")  # read per call (tests redirect it); bootstrap path like logs/ itself
LEGACY = {"ha_events": Path("/kaare/logs/ha_events.log"), "frigate_mqtt": Path("/kaare/logs/frigate_mqtt.log")}
STREAMS = tuple(LEGACY)
SEGMENT_MAX_BYTES = 256 * 1_048_576  # a busy day gets more than one part
QUIET_S = 120.0  # a segment with a newer one after it is left only after this long without writes
_CACHE_TTL_S = 10.0
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.(legacy|\d+))?\.log$")
_TS_RE = re.compile(rb'"ts":\s*"(\d{4}-\d{2}-\d{2})')
_cache: dict[str, tuple[Path, float]] = {}


def _today() -> date:
    return datetime.now(get_local_tz()).date()


def reset_cache() -> None:
    _cache.clear()


def _key(name: str) -> tuple:
    if name == "legacy":
        return ("", -2)
    m = _NAME_RE.match(name)
    if not m:
        return ("~", 0)
    part = m.group(2)
    return (m.group(1), -1 if part == "legacy" else int(part or 0))


def _stream_dir(stream: str) -> Path:
    if stream not in LEGACY:
        raise ValueError(f"unknown raw stream {stream}")
    return Path(RAW_DIR) / stream


def segments(stream: str) -> list[tuple[str, Path]]:
    """(name, path) oldest first; the old single file, while it exists, first as «legacy»."""
    out: list[tuple[str, Path]] = []
    legacy = Path(LEGACY[stream])
    if legacy.exists():
        out.append(("legacy", legacy))
    d = _stream_dir(stream)
    if d.is_dir():
        out += sorted(((p.name, p) for p in d.iterdir() if _NAME_RE.match(p.name)), key=lambda x: _key(x[0]))
    return out


def current_segment(stream: str) -> Path:
    """The file a writer appends to now (today's newest part; a new part once it is full)."""
    hit = _cache.get(stream)
    now = time.monotonic()
    if hit and now - hit[1] < _CACHE_TTL_S:
        return hit[0]
    d = _stream_dir(stream)
    day = _today().isoformat()
    parts = sorted((p for p in d.glob(f"{day}*.log") if _key(p.name)[0] == day and _key(p.name)[1] >= 0),
                   key=lambda p: _key(p.name)) if d.is_dir() else []
    path = parts[-1] if parts else d / f"{day}.log"
    try:
        if path.stat().st_size >= SEGMENT_MAX_BYTES:
            path = d / f"{day}.{_key(path.name)[1] + 1}.log"
    except OSError:
        pass
    d.mkdir(parents=True, exist_ok=True)
    _cache[stream] = (path, now)
    return path


def _read_from(path: Path, offset: int) -> tuple[list[str], int]:
    """Whole lines from offset to the last newline; a half-written last line waits for the next read."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunk = data[: end + 1]
    lines = [ln.decode("utf-8", errors="replace").rstrip("\r") for ln in chunk.split(b"\n") if ln.strip()]
    return lines, offset + len(chunk)


def read_new(stream: str, state: dict, *, quiet_s: float = QUIET_S, max_lines: int = 200_000
             ) -> tuple[list[str], dict, list[str]]:
    """New lines since `state` ({segment, offset}; Argus' pre-P150 {file_id, offset} of the old file carries over).
    Returns (lines, new state, gaps). Never re-reads: a missing segment moves on to the next newer one."""
    segs = segments(stream)
    gaps: list[str] = []
    if not segs:
        return [], dict(state), gaps
    names = [n for n, _ in segs]
    seg, offset = state.get("segment"), int(state.get("offset", 0) or 0)
    if seg is None:
        legacy = Path(LEGACY[stream])
        if legacy.exists() and str(state.get("file_id")) == str(legacy.stat().st_ino):
            seg = "legacy"
        else:
            seg, offset = names[0], 0
    if seg in names:
        idx = names.index(seg)
    else:
        gaps.append(seg)
        log.warning("[RAWLOG] %s: segment %s is gone before it was read to the end — gap", stream, seg)
        later = [i for i, n in enumerate(names) if _key(n) > _key(seg)]
        if not later:
            return [], {"segment": seg, "offset": offset}, gaps
        idx, offset = later[0], 0
    out: list[str] = []
    while True:
        _name, path = segs[idx]
        try:
            st = path.stat()
        except OSError:
            break
        if offset > st.st_size:
            offset = 0
        lines, offset = _read_from(path, offset)
        out += lines
        at_end = offset >= st.st_size
        quiet = time.time() - st.st_mtime >= quiet_s
        if idx + 1 < len(segs) and at_end and quiet and len(out) < max_lines:
            idx, offset = idx + 1, 0
            continue
        break
    return out, {"segment": segs[idx][0], "offset": offset}, gaps


def _seg_day(name: str) -> str:
    return _key(name)[0] if name != "legacy" else ""


def total_bytes(stream: str) -> int:
    total = 0
    for _n, p in segments(stream):
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def prune(stream: str, max_bytes: int, *, min_keep_days: int = 0) -> list[str]:
    """Delete whole old segments, oldest first, until the stream fits `max_bytes` (0 = off). Never today's segments,
    never the newest one, never the unsplit «legacy» file, never one dated within `min_keep_days`."""
    if max_bytes <= 0:
        return []
    segs = segments(stream)
    sizes = {}
    for n, p in segs:
        try:
            sizes[n] = p.stat().st_size
        except OSError:
            sizes[n] = 0
    total = sum(sizes.values())
    today = _today()
    floor = (today - timedelta(days=min_keep_days)).isoformat() if min_keep_days else today.isoformat()
    gone: list[str] = []
    for n, p in segs[:-1]:
        if total <= max_bytes:
            break
        day = _seg_day(n)
        if not day or day >= today.isoformat() or (min_keep_days and day >= floor):
            continue
        try:
            p.unlink()
        except OSError as e:
            log.warning("[RAWLOG] could not delete %s/%s: %s", stream, n, e)
            continue
        total -= sizes[n]
        gone.append(n)
        log.info("[RAWLOG] %s: deleted segment %s (size limit)", stream, n)
    return gone


def split_legacy(stream: str, *, reader_done: bool, quiet_s: float = 3600.0) -> bool:
    """Turn the old single file into `<date>.legacy.log` day segments, once nobody writes to it any more and Argus is past
    it. Written to temporary names first, renamed at the end, the old file removed last. Returns True when done."""
    legacy = Path(LEGACY[stream])
    if not legacy.exists() or not reader_done:
        return False
    if time.time() - legacy.stat().st_mtime < quiet_s:
        return False
    d = _stream_dir(stream)
    d.mkdir(parents=True, exist_ok=True)
    handles: dict[str, object] = {}
    day = None
    try:
        with open(legacy, "rb") as src:
            for line in src:
                m = _TS_RE.search(line[:300])
                if m:
                    day = m.group(1).decode()
                if day is None or not line.strip():
                    continue
                fh = handles.get(day)
                if fh is None:
                    fh = handles[day] = open(d / f".{day}.legacy.log.tmp", "wb")  # noqa: SIM115 — closed below
                fh.write(line if line.endswith(b"\n") else line + b"\n")
    finally:
        for fh in handles.values():
            fh.close()
    for day_name in handles:
        os.replace(d / f".{day_name}.legacy.log.tmp", d / f"{day_name}.legacy.log")
    legacy.unlink()
    log.info("[RAWLOG] %s: old single file split into %d day segments", stream, len(handles))
    return True


def tail_text(stream: str, max_bytes: int) -> str:
    """The newest `max_bytes` across segments, starting at a whole line."""
    parts: list[bytes] = []
    need = max_bytes
    for _n, p in reversed(segments(stream)):
        try:
            size = p.stat().st_size
            with open(p, "rb") as f:
                f.seek(max(0, size - need))
                data = f.read()
        except OSError:
            continue
        parts.insert(0, data)
        need -= len(data)
        if need <= 0:
            break
    blob = b"".join(parts)
    if len(blob) >= max_bytes:
        nl = blob.find(b"\n")
        blob = blob[nl + 1:] if nl >= 0 else b""
    return blob.decode("utf-8", errors="replace")


def lines_since(stream: str, cutoff_iso: str) -> Iterator[bytes]:
    """Raw lines (bytes) of every segment that can hold lines at/after `cutoff_iso` (day segments before its date are
    skipped by name — a day segment holds only that household day; the legacy file is read whole). The caller filters the exact timestamp."""
    # Segments are named by the household's day, lines carry UTC: allow one day of slack for any time zone (rule 24)
    try:
        cut_day = (date.fromisoformat(cutoff_iso[:10]) - timedelta(days=1)).isoformat()
    except ValueError:
        cut_day = ""
    for n, p in segments(stream):
        day = _seg_day(n)
        if day and day < cut_day:  # a day segment holds only its own household day
            continue
        try:
            with open(p, "rb") as f:
                size = f.seek(0, os.SEEK_END)
                start = _bisect(f, size, cutoff_iso.encode())
                f.seek(start)
                if start:
                    f.readline()  # the partial line
                yield from f
        except OSError:
            continue


def _bisect(f, size: int, cutoff: bytes) -> int:
    """Offset at or just before the first line dated at/after `cutoff` — the file is append-only and in time order, so
    ~20 short reads replace a scan of a file of many GB (the old single file)."""
    lo, hi = 0, size
    while hi - lo > 65_536:
        mid = (lo + hi) // 2
        f.seek(mid)
        f.readline()
        ts = None
        for _ in range(50):
            line = f.readline()
            if not line:
                break
            m = re.search(rb'"ts":\s*"([^"]+)"', line[:300])
            if m:
                ts = m.group(1)
                break
        if ts is None or ts >= cutoff:
            hi = mid
        else:
            lo = mid
    return lo


ARGUS_STATE = Path("/kaare/state/argus/state.json")  # read only: is Argus past the old single file?
LIMIT_KEYS = {"ha_events": "raw_ha_events_mb", "frigate_mqtt": "raw_frigate_mqtt_mb"}  # Settings → Logs/Storage
# A floor of days a reader needs: the normalcy baseline reads 28 days of Frigate lines (normalcy._WINDOW_DAYS uses this).
MIN_KEEP_DAYS = {"ha_events": 0, "frigate_mqtt": 28}


def reader_past_legacy(stream: str) -> bool:
    """True when Argus' saved position for the stream is a day segment (it has finished the old single file). Unknown or
    unreadable → False (never split while it might still be read)."""
    try:
        st = json.loads(Path(ARGUS_STATE).read_text(encoding="utf-8"))["files"][str(LEGACY[stream])]
        seg = st.get("segment")
        return bool(seg) and seg != "legacy"
    except Exception:
        return False


def run_retention() -> dict:
    """The night step: per stream, split the old single file once Argus is past it, then delete whole old day files
    over the GUI limit. Sync — the night sequence runs it in a thread. Never raises."""
    report: dict = {}
    for stream in STREAMS:
        rep = {"split": False, "deleted": [], "error": None}
        try:
            rep["split"] = split_legacy(stream, reader_done=reader_past_legacy(stream))
            rep["deleted"] = prune(stream, limits.get_bytes(LIMIT_KEYS[stream]),
                                   min_keep_days=MIN_KEEP_DAYS[stream])
        except Exception as e:
            rep["error"] = str(e)
            log.error("[RAWLOG] %s retention failed: %s", stream, e)
        report[stream] = rep
    return report


class Tail:
    """Follow a stream from its current end (for in-process watchers like ha_context_task)."""

    def __init__(self, stream: str):
        self.stream = stream
        segs = segments(stream)
        if segs:
            name, path = segs[-1]
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            self.state = {"segment": name, "offset": size}
        else:
            self.state = {}

    def read_new(self) -> list[str]:
        if not self.state:
            segs = segments(self.stream)
            if not segs:
                return []
            self.state = {"segment": segs[0][0], "offset": 0}
        lines, self.state, _gaps = read_new(self.stream, self.state, quiet_s=5.0)
        return lines


def ts_day(line: bytes) -> str | None:
    m = _TS_RE.search(line[:300])
    return m.group(1).decode() if m else None

