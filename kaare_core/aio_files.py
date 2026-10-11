"""
Small file helpers for async code (CLAUDE.md rule 10 — no blocking I/O on the event loop).

The blocking read/write runs in a worker thread via asyncio.to_thread, so a slow disk never
stalls Kåre's other requests while a log line is appended or a config file is read.
"""

import asyncio
from pathlib import Path
from typing import Any

import yaml


def _append_text_sync(path: str | Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


async def append_text(path: str | Path, text: str) -> None:
    """Append text to a file exactly as given (no newline added)."""
    await asyncio.to_thread(_append_text_sync, path, text)


async def append_line(path: str | Path, line: str) -> None:
    """Append one line (a newline is added) to a text file."""
    await append_text(path, line + "\n")


def _read_yaml_sync(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


async def read_yaml(path: str | Path) -> Any:
    """Parse a YAML file. Returns None for an empty file; raises like open()/yaml on errors."""
    return await asyncio.to_thread(_read_yaml_sync, path)
