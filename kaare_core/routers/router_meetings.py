import asyncio
import json
import os
import re
import subprocess as _sp
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import kaare_core.app_state as app_state
from kaare_core.users.store import get_user_with_hash, verify_pin

_MEETING_LOCK  = Path("/kaare/state/meeting_active.lock")
_TOPICS_FILE   = Path("/kaare/state/meeting_topics.json")
_COMMENTS_DIR  = Path("/kaare/state/meeting_comments")

# In-place-mutated dicts — same objects as in app_state
_MEETING_STATUS = app_state._MEETING_STATUS
_MEETING_PROCS  = app_state._MEETING_PROCS

router = APIRouter()


# ── Pydantic bodies ───────────────────────────────────────────────────────────

class _PinBody(BaseModel):
    pin: str

class _TopicBody(BaseModel):
    topic: str

class _CommentBody(BaseModel):
    comment: str


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
    m = re.search(r"Runde (\d+)/(\d+)", line)
    if m:
        return int(m.group(1)), int(m.group(2))
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
            if rounds:
                st["round"], st["max_rounds"] = rounds
                st["progress"] = int(rounds[0] / rounds[1] * 90)
                st["step"] = line
            elif "ferdig" in line.lower() or line.startswith("==="):
                st["step"] = line
        await proc.wait()
        if proc.returncode == 0:
            st["progress"] = 100
    except Exception as exc:
        st["log"].append(f"[feil: {exc}]")
    finally:
        st["running"] = False


def _detect_systemd_meeting(key: str, svc: str) -> None:
    st = _MEETING_STATUS[key]
    try:
        r = _sp.run(["systemctl", "is-active", f"{svc}.service"],
                    capture_output=True, text=True, timeout=2)
        is_active = r.stdout.strip() == "active"
        if is_active and not st["running"]:
            st.update({"running": True, "source": "timer",
                       "started_at": st["started_at"] or datetime.now().isoformat()})
        elif not is_active and st["running"] and st["source"] == "timer":
            st["running"] = False
    except Exception:
        pass

    if st["running"] and st["source"] == "timer":
        try:
            jr = _sp.run(
                ["journalctl", "-u", f"{svc}.service", "-n", "30",
                 "--no-pager", "--output=cat"],
                capture_output=True, text=True, timeout=3,
            )
            lines = [ln for ln in jr.stdout.splitlines() if ln.strip()]
            if lines:
                st["log"] = lines[-15:]
                for line in reversed(lines):
                    rounds = _parse_meeting_round(line)
                    if rounds:
                        st["round"] = rounds[0]
                        st["max_rounds"] = rounds[1]
                        st["progress"] = int(rounds[0] / rounds[1] * 90)
                        st["step"] = line
                        break
        except Exception:
            pass


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
def api_reflections_list(username: str):
    d = Path(f"/kaare/state/memory/reflections/{username}")
    if not d.exists():
        return []
    files = sorted(d.glob("[0-9][0-9][0-9][0-9]-*.md"), reverse=True)
    return [f.stem for f in files]


@router.post("/api/reflections/{username}/{date}")
def api_reflection_get(username: str, date: str, body: _PinBody):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date format")
    user = get_user_with_hash(username)
    if not user or not verify_pin(body.pin, user["pin_hash"]):
        raise HTTPException(403, "Invalid PIN")
    p = Path(f"/kaare/state/memory/reflections/{username}/{date}.md")
    if not p.exists():
        raise HTTPException(404, "Not found")
    return {"date": date, "content": p.read_text(encoding="utf-8")}


@router.get("/api/dev-meetings")
def api_dev_meetings_list():
    d = Path("/kaare/state/memory/dev_meetings")
    if not d.exists():
        return []
    files = sorted(d.glob("*.md"), reverse=True)
    return [f.stem for f in files]


@router.get("/api/dev-meetings/{date}")
def api_dev_meeting_get(date: str):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date format")
    p = Path(f"/kaare/state/memory/dev_meetings/{date}.md")
    if not p.exists():
        raise HTTPException(404, "Not found")
    return {"date": date, "content": p.read_text(encoding="utf-8")}


@router.get("/api/meetings/topic/{meeting_type}")
def api_get_topic(meeting_type: str):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    return {"topic": _load_topics().get(meeting_type, "")}


@router.post("/api/meetings/topic/{meeting_type}")
def api_set_topic(meeting_type: str, body: _TopicBody):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    data = _load_topics()
    data[meeting_type] = body.topic.strip()
    _save_topics(data)
    return {"ok": True}


@router.get("/api/meetings/comment/{meeting_type}/{date}")
def api_get_comment(meeting_type: str, date: str):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date")
    p = _COMMENTS_DIR / meeting_type / f"{date}.txt"
    return {"comment": p.read_text(encoding="utf-8").strip() if p.exists() else ""}


@router.post("/api/meetings/comment/{meeting_type}/{date}")
def api_set_comment(meeting_type: str, date: str, body: _CommentBody):
    if meeting_type not in ("reflection", "dev"):
        raise HTTPException(400, "Invalid meeting type")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(400, "Invalid date")
    p = _COMMENTS_DIR / meeting_type / f"{date}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.comment.strip(), encoding="utf-8")
    return {"ok": True}


@router.get("/api/meetings/status")
async def api_meetings_status():
    _detect_systemd_meeting("reflection", "kaare-reflection")
    _detect_systemd_meeting("dev", "kaare-dev-meeting")
    return {"reflection": _MEETING_STATUS["reflection"], "dev": _MEETING_STATUS["dev"]}


@router.post("/api/reflections/start")
async def api_reflections_start():
    st = _MEETING_STATUS["reflection"]
    if st["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        _MEETING_LOCK.touch()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "/kaare/kaare_reflection_runner.py",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare", env=env,
        )
        _MEETING_PROCS["reflection"] = proc
        st.update({"running": True, "progress": 0, "round": 0, "step": "Starter…",
                   "log": [], "started_at": datetime.now().isoformat(), "source": "manual"})
        asyncio.create_task(_stream_meeting_proc("reflection", proc))
        return {"status": "started"}
    except Exception as exc:
        _MEETING_LOCK.unlink(missing_ok=True)
        return {"status": "error", "detail": str(exc)}


@router.post("/api/dev-meetings/start")
async def api_dev_meetings_start():
    st = _MEETING_STATUS["dev"]
    if st["running"]:
        return {"status": "already_running"}
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": "/kaare"}
        _load_env_file("/kaare/configs/kare_llm.env", env)
        _MEETING_LOCK.touch()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "/kaare/kaare_dev_meeting.py",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd="/kaare", env=env,
        )
        _MEETING_PROCS["dev"] = proc
        st.update({"running": True, "progress": 0, "round": 0, "step": "Starter…",
                   "log": [], "started_at": datetime.now().isoformat(), "source": "manual"})
        asyncio.create_task(_stream_meeting_proc("dev", proc))
        return {"status": "started"}
    except Exception as exc:
        _MEETING_LOCK.unlink(missing_ok=True)
        return {"status": "error", "detail": str(exc)}
