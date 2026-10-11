"""
"What changed since last night" for the dev slot (P118 T2-D19) — mechanical and generic.

Three parts, compared against the previous snapshot stored in learning.db (change_snapshots):
  (a) running version — env KTSB_VERSION when the install sets it (Docker can), else "";
  (b) config keys — every dotted key in configs/*.yaml, with a hash of its value (values are
      never stored, so secrets or personal data in config never land in learning.db);
  (c) code manifest — sha1 per code file. Files whose size and mtime are unchanged since the
      previous snapshot reuse the stored hash, so a normal night reads almost nothing (rule 10).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import yaml

from kaare_core.memory import learning_store

KAARE_ROOT = Path("/kaare")
CONFIG_DIR = KAARE_ROOT / "configs"

_CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".sh", ".md"}
_SKIP_DIRS = {
    "venv",
    "node_modules",
    "__pycache__",
    ".git",
    "state",
    "logs",
    "backups",
    "runtime",
    "data",
    "dist",
    "models",
    ".cache",
    "ov_cache",
    "convert_venv",
    ".nv",
}
_SNAPSHOT_KIND = "changes_snapshot"


def _utc_ts() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _flatten(data, prefix: str = "") -> dict[str, str]:
    """Dotted key → short hash of the value (lists/dicts below a key are hashed whole at leaves)."""
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict) and v:
                out.update(_flatten(v, key))
            else:
                blob = json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)
                out[key] = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
    return out


def config_keys(config_dir: Path | None = None) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in sorted((config_dir or CONFIG_DIR).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {"__unreadable__": True}
        result[path.name] = _flatten(data)
    return result


def code_manifest(root: Path | None = None, previous: dict | None = None) -> dict[str, list]:
    """relpath → [size, mtime_ns, sha1]. Reuses the previous hash when size and mtime match."""
    root = root or KAARE_ROOT
    previous = previous or {}
    manifest: dict[str, list] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if Path(name).suffix not in _CODE_SUFFIXES:
                continue
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            rel = str(full.relative_to(root))
            prev = previous.get(rel)
            if prev and prev[0] == st.st_size and prev[1] == st.st_mtime_ns:
                manifest[rel] = prev
                continue
            try:
                digest = hashlib.sha1(full.read_bytes()).hexdigest()
            except OSError:
                continue
            manifest[rel] = [st.st_size, st.st_mtime_ns, digest]
    return manifest


def _diff_keys(old: dict, new: dict) -> dict[str, list[str]]:
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "changed": sorted(k for k in set(old) & set(new) if old[k] != new[k]),
    }


def diff(prev: dict | None, cur: dict) -> dict:
    """Mechanical difference between two snapshots. No previous snapshot → baseline only."""
    if not prev:
        return {
            "baseline": True,
            "version": cur.get("version", ""),
            "config_files": len(cur.get("config", {})),
            "code_files": len(cur.get("code", {})),
        }
    config: dict[str, dict] = {}
    for fname in sorted(set(prev.get("config", {})) | set(cur.get("config", {}))):
        d = _diff_keys(prev.get("config", {}).get(fname, {}), cur.get("config", {}).get(fname, {}))
        if any(d.values()):
            config[fname] = d
    old_code = {k: v[2] for k, v in prev.get("code", {}).items()}
    new_code = {k: v[2] for k, v in cur.get("code", {}).items()}
    return {
        "baseline": False,
        "since": prev.get("ts", ""),
        "version": {"from": prev.get("version", ""), "to": cur.get("version", "")},
        "config": config,
        "code": _diff_keys(old_code, new_code),
    }


def _latest_snapshot() -> dict | None:
    learning_store._ensure_db()
    conn = learning_store._get_conn()
    try:
        row = conn.execute(
            "SELECT payload FROM change_snapshots WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (_SNAPSHOT_KIND,),
        ).fetchone()
    finally:
        conn.close()
    try:
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def snapshot_and_diff(root: Path | None = None, config_dir: Path | None = None) -> dict:
    """Take tonight's snapshot, store it (nothing is ever deleted) and return the diff."""
    prev = _latest_snapshot()
    cur = {
        "ts": _utc_ts(),
        "version": os.environ.get("KTSB_VERSION", ""),
        "config": config_keys(config_dir),
        "code": code_manifest(root, (prev or {}).get("code")),
    }
    learning_store._ensure_db()
    conn = learning_store._get_conn()
    try:
        conn.execute(
            "INSERT INTO change_snapshots (ts, kind, payload) VALUES (?, ?, ?)",
            (cur["ts"], _SNAPSHOT_KIND, json.dumps(cur, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return diff(prev, cur)
