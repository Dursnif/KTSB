"""Size-based rotation helpers — oldest first, never «keep N days» (P150 phase 4).

Small, pure building blocks the stores call themselves (think_cache, inner thoughts, STM history, observations):
  - `iter_lines_reverse` / `read_tail` read a file from its end, so a reader of a big file never loads all of it (rule 10);
  - `trim_lines_to_bytes` keeps the newest whole lines of a line file under a byte limit (atomic rewrite);
  - `trim_blocks` keeps the newest text blocks whose joined size fits (the newest is always kept);
  - `trim_dated_sections` drops the oldest «## YYYY-MM-DD» sections of a markdown file, the preamble stays;
  - `prune_dirs_oldest` removes the oldest dated folders until a folder tree fits, only where `can_delete` says so
    (e.g. its day has a summary — condense before delete, D2) and never the newest.
A limit of 0 (or less) means off: nothing is trimmed. Trimming goes a little under the limit (TRIM_TO) so a file at its limit
is not rewritten on every write.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

TRIM_TO = 0.9  # trim to 90 % of the limit (hysteresis)
_SECTION_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}", re.MULTILINE)


def iter_lines_reverse(path: Path, chunk: int = 64 * 1024, max_bytes: int | None = None) -> Iterator[str]:
    """Lines of a text file, newest (last) first, read backwards in chunks. Stops after `max_bytes` scanned."""
    try:
        f = open(path, "rb")  # noqa: SIM115 — closed in finally; a generator cannot use a with-block across yields cleanly
    except OSError:
        return
    try:
        pos = f.seek(0, os.SEEK_END)
        scanned = 0
        rest = b""
        while pos > 0:
            if max_bytes is not None and scanned >= max_bytes:
                return
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + rest
            scanned += step
            lines = data.split(b"\n")
            rest = lines.pop(0)  # possibly a partial line — joined with the next (earlier) chunk
            for line in reversed(lines):
                if line.strip():
                    yield line.decode("utf-8", errors="replace")
        if rest.strip():
            yield rest.decode("utf-8", errors="replace")
    finally:
        f.close()


def read_tail(path: Path, max_bytes: int) -> str:
    """The last `max_bytes` of a text file, starting at a whole line. '' when missing."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - max_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return ""
    if start > 0:
        nl = data.find(b"\n")
        data = data[nl + 1:] if nl >= 0 else b""
    return data.decode("utf-8", errors="replace")


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            st = path.stat()
            os.chmod(tmp, st.st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def trim_lines_to_bytes(path: Path, max_bytes: int) -> int:
    """Keep the newest whole lines under `max_bytes` (trim to TRIM_TO of it). Returns how many lines went."""
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if max_bytes <= 0 or size <= max_bytes:
        return 0
    keep = int(max_bytes * TRIM_TO)
    with open(path, "rb") as f:
        f.seek(size - keep)
        tail = f.read()
        f.seek(0)
        head_lines = f.read(size - keep).count(b"\n")
    nl = tail.find(b"\n")
    tail = tail[nl + 1:] if nl >= 0 else b""
    _atomic_write(path, tail)
    return head_lines + (1 if nl >= 0 else 0)


def trim_blocks(blocks: list[str], max_bytes: int, sep: str = "\n\n") -> list[str]:
    """The newest blocks whose joined size fits `max_bytes` — at least the newest one. 0 = off (all kept)."""
    if max_bytes <= 0 or not blocks:
        return list(blocks)
    kept: list[str] = []
    total = 0
    for b in reversed(blocks):
        size = len(b.encode("utf-8")) + (len(sep) if kept else 0)
        if kept and total + size > max_bytes:
            break
        kept.append(b)
        total += size
    return list(reversed(kept))


def trim_dated_sections(text: str, max_bytes: int) -> str:
    """Drop the oldest «## YYYY-MM-DD» sections until the text fits; the preamble before the first one stays. 0 = off."""
    if max_bytes <= 0 or len(text.encode("utf-8")) <= max_bytes:
        return text
    starts = [m.start() for m in _SECTION_RE.finditer(text)]
    if not starts:
        return text
    preamble = text[:starts[0]]
    sections = [text[a:b] for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]
    budget = max(0, int(max_bytes * TRIM_TO) - len(preamble.encode("utf-8")))
    kept: list[str] = []
    total = 0
    for s in reversed(sections):
        size = len(s.encode("utf-8"))
        if kept and total + size > budget:
            break
        kept.append(s)
        total += size
    return preamble + "".join(reversed(kept))


def _tree_bytes(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(p):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def prune_dirs_oldest(root: Path, max_bytes: int, *, can_delete: Callable[[str], bool],
                      pattern: str = r"\d{4}-\d{2}-\d{2}(\.json)?") -> list[str]:
    """Remove the oldest dated entries (folders or files named YYYY-MM-DD[.json]) of `root` until it fits `max_bytes`.
    Skips an entry `can_delete(name)` refuses; never removes the newest. Returns the names removed, oldest first."""
    if max_bytes <= 0 or not root.is_dir():
        return []
    rx = re.compile(pattern)
    entries = sorted((p for p in root.iterdir() if rx.fullmatch(p.name)), key=lambda p: p.name)
    sizes = {p.name: _tree_bytes(p) for p in entries}
    total = sum(sizes.values())
    gone: list[str] = []
    for p in entries[:-1]:  # never the newest
        if total <= max_bytes:
            break
        if not can_delete(p.name[:10]):
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        total -= sizes[p.name]
        gone.append(p.name)
    return gone
