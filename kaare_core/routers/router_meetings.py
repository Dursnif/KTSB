import asyncio
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import kaare_core.app_state as app_state
from kaare_core.background import spawn
from kaare_core.client_ip import client_ip as _client_ip_of
from kaare_core.config import now_local
from kaare_core.memory import learning_store
from kaare_core.users.auth import require_admin as _require_admin
from kaare_core.users.auth import require_auth as _require_auth
from kaare_core.users.store import get_user_with_hash, verify_pin
from kaare_night_runner import is_sequence_running

_MEETING_LOCK = Path("/kaare/state/meeting_active.lock")
_TOPICS_FILE = Path("/kaare/state/meeting_topics.json")
_COMMENTS_DIR = Path("/kaare/state/meeting_comments")
_USER_TOPICS_DIR = Path("/kaare/state/meeting_topics_user")

# In-place-mutated dicts — same objects as in app_state
_MEETING_STATUS = app_state._MEETING_STATUS
_MEETING_PROCS = app_state._MEETING_PROCS

router = APIRouter()


# ── Access to a user's reflections (P120) ──────────────────────────────────────
# Logged in, own username (or admin), and the user's PIN — with at most 5 wrong PINs per
# 15 minutes per IP and user: a 4-digit PIN otherwise falls to 10 000 tries.
_PIN_FAIL_WINDOW = 900
_PIN_FAIL_MAX = 5
_pin_failures: dict[tuple[str, str], collections.deque] = {}


def _own_or_admin(username: str, payload: dict) -> None:
    if payload.get("sub") != username and payload.get("role") != "admin":
        raise HTTPException(403, "Not your reflections.")


def _pin_gate(username: str, pin: str, request: Request, payload: dict) -> None:
    _own_or_admin(username, payload)
    key = (_client_ip_of(request), username)
    now = time.monotonic()
    dq = _pin_failures.setdefault(key, collections.deque())
    while dq and now - dq[0] > _PIN_FAIL_WINDOW:
        dq.popleft()
    if len(dq) >= _PIN_FAIL_MAX:
        raise HTTPException(429, "Too many wrong PINs. Try again in 15 minutes.")
    user = get_user_with_hash(username)
    if not user or not verify_pin(pin, user["pin_hash"]):
        dq.append(now)
        raise HTTPException(403, "Invalid PIN")
    _pin_failures.pop(key, None)


# ── Pydantic bodies ───────────────────────────────────────────────────────────


class _PinBody(BaseModel):
    pin: str


class _TopicBody(BaseModel):
    topic: str
    theme: str = ""  # P118 B8: dev topic → agenda theme (dropdown); empty = set by code rules


class _CommentBody(BaseModel):
    comment: str


class _PinCommentBody(BaseModel):
    pin: str
    comment: str


class _PinTopicBody(BaseModel):
    pin: str
    topic: str


# ── Topic helpers ─────────────────────────────────────────────────────────────


def _load_topics() -> dict:
    try:
        return json.loads(_TOPICS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"reflection": "", "dev": ""}


def _save_topics(data: dict) -> None:
    _TOPICS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Subprocess helpers ────────────────────────────────────────────────────────


def _parse_meeting_round(line: str):
    # kaare_reflection.py logs «--- Round 1/6 (group 1, local 1) ---»; «Runde» kept for old output
    m = re.search(r"\b(?:Round|Runde) (\d+)/(\d+)", line)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


_DEV_SLOT_STEP_RE = re.compile(r"\[DEV_SLOT\] step (\d+)/(\d+) (\w+)")


def _parse_dev_slot_step(line: str):
    """«[DEV_SLOT] step 3/6 S1» (kaare_core/dev_slot/run.py) → (3, 6, "S1"); the GUI shows the
    step code in the user's language."""
    m = _DEV_SLOT_STEP_RE.search(line)
    if m:
        return int(m.group(1)), int(m.group(2)), m.group(3)
    return None


async def _stream_meeting_proc(key: str, proc) -> None:
    st = _MEETING_STATUS[key]
    try:
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            st["log"].append(line)
            if len(st["log"]) > 60:
                st["log"].pop(0)
            rounds = _parse_meeting_round(line)
            slot = _parse_dev_slot_step(line)
            if slot:
                st["round"], st["max_rounds"], st["step_code"] = slot
                st["progress"] = int(slot[0] / slot[1] * 90)
                st["step"] = line
            elif rounds:
                st["round"], st["max_rounds"] = rounds
                st["progress"] = int(rounds[0] / rounds[1] * 90)
                st["step"] = line
            elif "ferdig" in line.lower() or line.startswith(("===", "[DEV_SLOT]")):
                st["step"] = line
        await proc.wait()
        if proc.returncode == 0:
            st["progress"] = 100
    except Exception as exc:
        st["log"].append(f"[feil: {exc}]")
    finally:
        st["running"] = False


# Both night meetings run in-process inside the night sequence (kaare_night_sequence.py), which
# logs «=== [<step>] starting ===» / «=== [<step>] done … ===» to its log file. There is no
# separate systemd unit per meeting (P119 B5). Whether a run is active is read from the night
# sequence's own lock (N17) — not systemctl, which neither Docker nor the night runner has.
_NIGHT_LOG = Path("/kaare/logs/night_sequence.log")
_NIGHT_TAIL_BYTES = 262144  # last 256 KB — the file is ~280k lines, never read it whole
_NIGHT_STEP_RE = re.compile(r"=== \[(\w+)\] (starting|done|TIMED OUT|FAILED)")
_NIGHT_STEP_TO_MEETING = {"reflection": "reflection", "dev_slot": "dev"}


def _tail_lines(path: Path, max_bytes: int = _NIGHT_TAIL_BYTES) -> list[str]:
    """Last lines of a file, read from the end (first partial line dropped)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    return lines[1:] if size > max_bytes else lines


def _night_sequence_state() -> tuple[bool, str | None, list[str]]:
    """(night sequence running, running step or None, lines of that step). Blocking — call via
    asyncio.to_thread."""
    active = is_sequence_running()
    if not active:
        return False, None, []
    lines = _tail_lines(_NIGHT_LOG)
    current, start_idx = None, 0
    for i, line in enumerate(lines):
        m = _NIGHT_STEP_RE.search(line)
        if not m:
            continue
        if m.group(2) == "starting":
            current, start_idx = m.group(1), i
        elif m.group(1) == current:
            current = None
    return True, current, (lines[start_idx:] if current else [])


def _apply_night_status(active: bool, step: str | None, lines: list[str]) -> None:
    """Mirror the night sequence's running step into _MEETING_STATUS (source «timer»). A manual
    run started from the GUI keeps its own streamed status."""
    for key in ("reflection", "dev"):
        st = _MEETING_STATUS[key]
        if st["running"] and st.get("source") == "manual":
            continue
        running_now = active and _NIGHT_STEP_TO_MEETING.get(step or "") == key
        if running_now and not st["running"]:
            st.update(
                {
                    "running": True,
                    "source": "timer",
                    "progress": 0,
                    "round": 0,
                    "step": "",
                    "step_code": "",
                    "log": [],
                    "started_at": now_local().isoformat(),
                }
            )
        elif not running_now and st["running"] and st.get("source") == "timer":
            st["running"] = False
        if not running_now:
            continue
        shown = [ln for ln in lines if ln.strip() and " httpx " not in ln]
        st["log"] = shown[-15:]
        for line in reversed(lines):
            slot = _parse_dev_slot_step(line)
            rounds = None if slot else _parse_meeting_round(line)
            if slot:
                st["round"], st["max_rounds"], st["step_code"] = slot
                st["progress"] = int(slot[0] / slot[1] * 90)
                st["step"] = line
                break
            if rounds:
                st["round"], st["max_rounds"] = rounds
                st["progress"] = int(rounds[0] / rounds[1] * 90)
                st["step"] = line
                break


def _load_env_file(path: str, env: dict) -> None:
    try:
        for ln in Path(path).read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                env.setdefault(k.strip(), v.strip())
    except Exception:
        pass


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/api/reflections/{username}")
def api_reflections_list(username: str, _u=Depends(_require_auth)):
    _own_or_admin(username, _u)
    d = Path(f"/kaare/state/memory/reflections/{username}")
    if not d.exists():
        return []
    files = sorted(d.glob("[0-9][0-9][0-9][0-9]-*.md"), reverse=True)
    return [f.stem for f in files]


@router.post("/api/reflections/{username}/{date}")
def api_reflection_get(
    username: str, date: str, body: _PinBody, request: Request, _u=Depends(_require_auth)
):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date format")
    _pin_gate(username, body.pin, request, _u)
    p = Path(f"/kaare/state/memory/reflections/{username}/{date}.md")
    if not p.exists():
        raise HTTPException(404, "Not found")
    return {"date": date, "content": p.read_text(encoding="utf-8")}


@router.post("/api/reflections/{username}/comment/{date}/get")
def api_get_user_reflection_comment(
    username: str, date: str, body: _PinBody, request: Request, _u=Depends(_require_auth)
):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date format")
    _pin_gate(username, body.pin, request, _u)
    p = _COMMENTS_DIR / "reflection" / username / f"{date}.txt"
    return {"comment": p.read_text(encoding="utf-8").strip() if p.exists() else ""}


@router.post("/api/reflections/{username}/comment/{date}")
def api_set_user_reflection_comment(
    username: str, date: str, body: _PinCommentBody, request: Request, _u=Depends(_require_auth)
):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date format")
    _pin_gate(username, body.pin, request, _u)
    p = _COMMENTS_DIR / "reflection" / username / f"{date}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.comment.strip(), encoding="utf-8")
    return {"ok": True}


@router.post("/api/reflections/{username}/topic/get")
def api_get_user_reflection_topic(
    username: str, body: _PinBody, request: Request, _u=Depends(_require_auth)
):
    _pin_gate(username, body.pin, request, _u)
    p = _USER_TOPICS_DIR / f"{username}.txt"
    return {"topic": p.read_text(encoding="utf-8").strip() if p.exists() else ""}


@router.post("/api/reflections/{username}/topic")
def api_set_user_reflection_topic(
    username: str, body: _PinTopicBody, request: Request, _u=Depends(_require_auth)
):
    _pin_gate(username, body.pin, request, _u)
    _USER_TOPICS_DIR.mkdir(parents=True, exist_ok=True)
    p = _USER_TOPICS_DIR / f"{username}.txt"
    p.write_text(body.topic.strip(), encoding="utf-8")
    return {"ok": True}


@router.get("/api/dev-meetings")
def api_dev_meetings_list(_u=Depends(_require_admin)):
    d = Path("/kaare/state/memory/dev_meetings")
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), reverse=True)
    return [f.stem for f in files]


@router.get("/api/dev-meetings/{date}")
def api_dev_meeting_get(date: str, _u=Depends(_require_admin)):
    # «-2», «-3» …: a second dev-slot report the same day (reports are never overwritten, P118 B5)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(-\d{1,2})?", date):
        raise HTTPException(400, "Invalid date format")
    p = Path(f"/kaare/state/memory/dev_meetings/{date}.md")
    if not p.exists():
        raise HTTPException(404, "Not found")
    return {"date": date, "content": p.read_text(encoding="utf-8")}


@router.get("/api/meetings/topic/{meeting_type}")
def api_get_topic(meeting_type: str, _u=Depends(_require_admin)):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    return {"topic": _load_topics().get(meeting_type, "")}


@router.post("/api/meetings/topic/{meeting_type}")
def api_set_topic(meeting_type: str, body: _TopicBody, _u=Depends(_require_admin)):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    data = _load_topics()
    previous = (data.get(meeting_type) or "").strip()
    topic = body.topic.strip()
    data[meeting_type] = topic
    _save_topics(data)
    # P118 B1: an admin dev-meeting topic is also an agenda inbox row (source='admin'), so the
    # new dev slot sees it with the rest of the agenda. The JSON field stays for the old meeting.
    if meeting_type == "dev" and topic and topic != previous:
        try:
            learning_store.add_inbox_item(
                topic,
                source="admin",
                user_id="admin",
                pointer="meeting_topics.json#dev",
                theme=body.theme or None,
            )
        except Exception as e:
            print(f"[MEETINGS] admin topic → agenda inbox failed: {e}")
    return {"ok": True}


@router.get("/api/meetings/resolved")
def api_resolved_topics(_u=Depends(_require_admin)):
    """P133 C6: topics marked resolved in a conversation — conclusion, when, by whom, and how
    many later rows were caught as echoes of it."""
    try:
        return {"topics": learning_store.resolved_topics(limit=200)}
    except Exception as e:
        raise HTTPException(500, f"Could not read resolved topics: {e}") from e


@router.get("/api/meetings/comment/{meeting_type}/{date}")
def api_get_comment(meeting_type: str, date: str, _u=Depends(_require_admin)):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(-\d{1,2})?", date):
        raise HTTPException(400, "Invalid date")
    p = _COMMENTS_DIR / meeting_type / f"{date}.txt"
    return {"comment": p.read_text(encoding="utf-8").strip() if p.exists() else ""}


@router.post("/api/meetings/comment/{meeting_type}/{date}")
def api_set_comment(meeting_type: str, date: str, body: _CommentBody, _u=Depends(_require_admin)):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(-\d{1,2})?", date):
        raise HTTPException(400, "Invalid date")
    p = _COMMENTS_DIR / meeting_type / f"{date}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.comment.strip(), encoding="utf-8")
    return {"ok": True}


@router.get("/api/meetings/status")
async def api_meetings_status(_u=Depends(_require_admin)):
    # systemctl + file read off the event loop — the GUI polls this every few seconds (rule 10)
    active, step, lines = await asyncio.to_thread(_night_sequence_state)
    _apply_night_status(active, step, lines)
    return {"reflection": _MEETING_STATUS["reflection"], "dev": _MEETING_STATUS["dev"]}


@router.post("/api/reflections/start")
async def api_reflections_start(_u=Depends(_require_admin)):
    st = _MEETING_STATUS["reflection"]
    if st["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        # N17: the night sequence's reflection step (one run at a time, sets/removes
        # meeting_active.lock itself) — replaces kaare_reflection_runner.py, a 03:30–04:30 loop.
        if await asyncio.to_thread(is_sequence_running):
            return {"status": "already_running"}
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "/kaare/kaare_night_sequence.py",
            "--only",
            "reflection",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare",
            env=env,
        )
        _MEETING_PROCS["reflection"] = proc
        st.update(
            {
                "running": True,
                "progress": 0,
                "round": 0,
                "step": "Starter…",
                "log": [],
                "started_at": now_local().isoformat(),
                "source": "manual",
            }
        )
        spawn(_stream_meeting_proc("reflection", proc))
        return {"status": "started"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@router.post("/api/dev-meetings/start")
async def api_dev_meetings_start(_u=Depends(_require_admin)):
    st = _MEETING_STATUS["dev"]
    if st["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        _MEETING_LOCK.touch()
        # P118 B5: the dev slot replaces kaare_dev_meeting.py (which stays on disk unused).
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "kaare_core.dev_slot.run",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare",
            env=env,
        )
        _MEETING_PROCS["dev"] = proc
        st.update(
            {
                "running": True,
                "progress": 0,
                "round": 0,
                "max_rounds": 6,
                "step_code": "",
                "step": "",
                "log": [],
                "started_at": now_local().isoformat(),
                "source": "manual",
            }
        )
        spawn(_stream_meeting_proc("dev", proc))
        return {"status": "started"}
    except Exception as exc:
        _MEETING_LOCK.unlink(missing_ok=True)
        return {"status": "error", "detail": str(exc)}
