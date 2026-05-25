# /kaare/kaare_core/tools/timer_service.py
"""
Kåres selvstyrte timer-motor v2.

Støtter:
  - in_seconds: enkel forsinkelse (som før)
  - at_time:    norsk klokkeslett/dato — "07:30", "fredag 08:00", "2026-05-01 09:00"
  - repeat:     "hourly" | "daily" | "weekdays" | "weekend" | "weekly"

Repeterende timere persisteres til disk (state/timers.json) og gjenopprettes ved restart.
Engangstimere lever kun i RAM.
"""
import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from kaare_core.config import get_service as _svc

_timers: Dict[str, Dict[str, Any]] = {}
_TOOL_LOG    = Path("/kaare/logs/tool_calls.log")
_PERSIST_PATH = Path("/kaare/state/timers.json")
_API_BASE    = _svc("internal", "kaare_api")

_DAYS_NO = {
    "mandag": 0, "tirsdag": 1, "onsdag": 2, "torsdag": 3,
    "fredag": 4, "lørdag": 5, "søndag": 6,
}

_REPEAT_LABELS = {
    "hourly":   "hver time",
    "daily":    "daglig",
    "weekdays": "hverdager",
    "weekend":  "helg",
    "weekly":   "ukentlig",
}

VALID_REPEATS = set(_REPEAT_LABELS.keys())


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(event: str, **fields):
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with open(_TOOL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Tidsparsing ───────────────────────────────────────────────────────────────

def _parse_at_time(at_time: str) -> Optional[datetime]:
    """
    Parser norsk tidstreng til neste forekomst (lokal tid).
      "07:30"            → i dag kl 07:30, eller i morgen hvis passert
      "fredag 08:00"     → neste fredag kl 08:00
      "2026-05-01"       → den datoen kl 00:00
      "2026-05-01 09:00" → den datoen kl 09:00
    """
    now = datetime.now()
    s = at_time.strip().lower()

    # Bare klokkeslett: "07:30"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        t = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t

    # Ukedag + klokkeslett: "fredag 08:00"
    m = re.fullmatch(r"(\w+)\s+(\d{1,2}):(\d{2})", s)
    if m:
        day_name = m.group(1)
        if day_name not in _DAYS_NO:
            return None
        h, mi = int(m.group(2)), int(m.group(3))
        target_wd = _DAYS_NO[day_name]
        days_ahead = (target_wd - now.weekday()) % 7
        if days_ahead == 0:
            candidate = now.replace(hour=h, minute=mi, second=0, microsecond=0)
            if candidate <= now:
                days_ahead = 7
            else:
                return candidate
        return (now + timedelta(days=days_ahead)).replace(hour=h, minute=mi, second=0, microsecond=0)

    # ISO-dato med valgfritt klokkeslett: "2026-05-01" / "2026-05-01 09:00"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        try:
            d = datetime.strptime(m.group(1), "%Y-%m-%d")
            if m.group(2):
                d = d.replace(hour=int(m.group(2)), minute=int(m.group(3)))
            return d
        except ValueError:
            return None

    return None


def _next_occurrence(fires_at: datetime, repeat: str) -> datetime:
    """Beregn neste tidspunkt etter en repeterende timer har fyrt."""
    if repeat == "hourly":
        return fires_at + timedelta(hours=1)
    if repeat == "daily":
        return fires_at + timedelta(days=1)
    if repeat == "weekly":
        return fires_at + timedelta(weeks=1)
    if repeat == "weekdays":
        nxt = fires_at + timedelta(days=1)
        while nxt.weekday() >= 5:  # hopp over lørdag og søndag
            nxt += timedelta(days=1)
        return nxt
    if repeat == "weekend":
        nxt = fires_at + timedelta(days=1)
        while nxt.weekday() < 5:  # hopp over mandag–fredag
            nxt += timedelta(days=1)
        return nxt
    return fires_at + timedelta(days=1)


def _fmt_delay(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}t {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ── Persistens ────────────────────────────────────────────────────────────────

def _persist_repeating():
    """Skriv alle gjentakende timere til disk (uten task-objekt)."""
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {k: v for k, v in info.items() if k != "task"}
            for info in _timers.values()
            if info.get("repeat")
        ]
        _PERSIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _clear_persist(timer_id: str):
    """Fjern én timer fra persisted fil."""
    _persist_repeating()  # enklest: skriv hele filen på nytt


# ── Selve avfyringen ──────────────────────────────────────────────────────────

async def _fire(timer_id: str, prompt: str, notify: bool,
                repeat: Optional[str], fires_at_local: datetime):
    try:
        import httpx
        _log("timer_fired", source="timer", timer_id=timer_id, prompt_preview=prompt[:80])
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_API_BASE}/api/generate",
                json={"prompt": prompt, "source": "self_timer", "user_id": "global"},
            )
            response_text = r.json().get("text", "")[:120]
        _log("timer_response", source="timer", timer_id=timer_id,
             notify=notify, result_preview=response_text, duration_ms=0)
    except Exception as e:
        _log("timer_fire_error", source="timer", timer_id=timer_id, error=str(e))
    finally:
        if repeat and timer_id in _timers:
            # Beregn neste tidspunkt og reschedule
            next_dt    = _next_occurrence(fires_at_local, repeat)
            delay_secs = (next_dt - datetime.now()).total_seconds()
            delay_secs = max(1.0, delay_secs)

            async def _next_delayed():
                await asyncio.sleep(delay_secs)
                await _fire(timer_id, prompt, notify, repeat, next_dt)

            task = asyncio.create_task(_next_delayed())
            fires_at_ts = next_dt.timestamp()
            _timers[timer_id].update({
                "fires_at_ts": fires_at_ts,
                "fires_at":    datetime.fromtimestamp(fires_at_ts, tz=timezone.utc).isoformat(),
                "task":        task,
            })
            _persist_repeating()
        else:
            _timers.pop(timer_id, None)
            _persist_repeating()


# ── Offentlig API ─────────────────────────────────────────────────────────────

def sett_timer(
    prompt: str,
    in_seconds: int = 0,
    notify: bool = True,
    repeat: Optional[str] = None,
    at_time: Optional[str] = None,
) -> str:
    """
    Sett en timer.

    Parametere:
      prompt     — meldingen Kåre vekkes med
      in_seconds — forsinkelse i sekunder (brukes hvis at_time ikke er satt)
      at_time    — norsk klokkeslett/dato: "07:30", "fredag 08:00", "2026-05-01 09:00"
      repeat     — "hourly" | "daily" | "weekdays" | "weekend" | "weekly"
      notify     — om brukeren skal varsles (standard: ja)
    """
    if not prompt.strip():
        return "Feil: prompt kan ikke være tom."

    if repeat and repeat not in VALID_REPEATS:
        return f"Feil: ugyldig repeat-verdi '{repeat}'. Gyldige: {', '.join(VALID_REPEATS)}."

    # Bestem tidspunkt
    if at_time:
        fire_dt = _parse_at_time(at_time)
        if fire_dt is None:
            return (
                f"Feil: kunne ikke tolke '{at_time}'. "
                "Bruk f.eks. '07:30', 'fredag 08:00' eller '2026-05-01 09:00'."
            )
        delay_secs = max(1.0, (fire_dt - datetime.now()).total_seconds())
    else:
        if in_seconds < 5:
            return "Feil: minimum 5 sekunder."
        if in_seconds > 86400 * 365 and not repeat:
            return "Feil: engangs-timer kan ikke settes mer enn ett år frem."
        delay_secs = float(in_seconds)
        fire_dt    = datetime.now() + timedelta(seconds=delay_secs)

    timer_id    = str(uuid.uuid4())[:8]
    fires_at_ts = fire_dt.timestamp()

    async def _delayed():
        await asyncio.sleep(delay_secs)
        await _fire(timer_id, prompt, notify, repeat, fire_dt)

    task = asyncio.create_task(_delayed())

    _timers[timer_id] = {
        "id":          timer_id,
        "prompt":      prompt,
        "fires_at_ts": fires_at_ts,
        "fires_at":    datetime.fromtimestamp(fires_at_ts, tz=timezone.utc).isoformat(),
        "in_seconds":  int(delay_secs),
        "notify":      notify,
        "repeat":      repeat,
        "at_time":     at_time,
        "task":        task,
    }

    if repeat:
        _persist_repeating()

    _log("timer_set", source="kare", tool="sett_timer", timer_id=timer_id,
         in_seconds=int(delay_secs), prompt_preview=prompt[:80],
         notify=notify, repeat=repeat, at_time=at_time,
         result_preview=f"Timer {timer_id} satt", duration_ms=0)

    # Bygg lesbar tilbakemelding
    local_str = fire_dt.strftime("%d.%m.%Y %H:%M")
    delay_str = _fmt_delay(delay_secs)
    repeat_str = f" — gjentar {_REPEAT_LABELS[repeat]}" if repeat else ""
    return (
        f"Timer satt [{timer_id}]: om {delay_str} ({local_str}){repeat_str}. "
        f"Prompt: «{prompt[:60]}{'…' if len(prompt) > 60 else ''}»"
    )


def avbryt_timer(timer_id: str) -> str:
    if timer_id not in _timers:
        return f"Ingen aktiv timer med ID '{timer_id}'."
    info = _timers.pop(timer_id)
    info["task"].cancel()
    _persist_repeating()
    _log("timer_cancelled", source="kare", tool="avbryt_timer", timer_id=timer_id,
         result_preview=f"Timer {timer_id} avbrutt", duration_ms=0)
    repeat_str = f" (var {_REPEAT_LABELS[info['repeat']]})" if info.get("repeat") else ""
    return f"Timer {timer_id} avbrutt{repeat_str}."


def liste_timere() -> str:
    if not _timers:
        return "Ingen aktive timere."
    now = datetime.now(timezone.utc).timestamp()
    engang   = [(k, v) for k, v in _timers.items() if not v.get("repeat")]
    gjentatt = [(k, v) for k, v in _timers.items() if v.get("repeat")]

    lines = [f"Aktive timere ({len(_timers)}):"]
    if gjentatt:
        lines.append("  [Gjentakende]")
        for _, info in gjentatt:
            remaining = max(0, int(info["fires_at_ts"] - now))
            p_short = info["prompt"][:50] + ("…" if len(info["prompt"]) > 50 else "")
            fire_local = datetime.fromtimestamp(info["fires_at_ts"]).strftime("%d.%m %H:%M")
            lines.append(
                f"    [{info['id']}] {_REPEAT_LABELS[info['repeat']]} — "
                f"neste om {_fmt_delay(remaining)} ({fire_local}): «{p_short}»"
            )
    if engang:
        lines.append("  [Engang]")
        for _, info in engang:
            remaining = max(0, int(info["fires_at_ts"] - now))
            p_short = info["prompt"][:50] + ("…" if len(info["prompt"]) > 50 else "")
            lines.append(f"    [{info['id']}] om {_fmt_delay(remaining)}: «{p_short}»")

    return "\n".join(lines)


def get_active_timers() -> List[Dict]:
    now = datetime.now(timezone.utc).timestamp()
    return [
        {
            "id":               info["id"],
            "prompt":           info["prompt"],
            "fires_at":         info["fires_at"],
            "remaining_seconds": max(0, int(info["fires_at_ts"] - now)),
            "notify":           info["notify"],
            "repeat":           info.get("repeat"),
            "at_time":          info.get("at_time"),
        }
        for info in _timers.values()
    ]


# ── Gjenopprett ved oppstart ──────────────────────────────────────────────────

def restore_timers():
    """
    Kalles fra kaare_api.py ved oppstart.
    Laster gjentakende timere fra disk og re-scheduler dem.
    """
    if not _PERSIST_PATH.exists():
        return
    try:
        data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return

    now = datetime.now()
    restored = 0
    for info in data:
        timer_id = info.get("id")
        prompt   = info.get("prompt", "")
        notify   = info.get("notify", True)
        repeat   = info.get("repeat")
        at_time  = info.get("at_time")

        if not (timer_id and prompt and repeat):
            continue

        # Finn neste gyldige tidspunkt (kan ha gått mange iterasjoner siden restart)
        fires_at_ts = info.get("fires_at_ts", 0.0)
        fire_dt = datetime.fromtimestamp(fires_at_ts)
        while fire_dt <= now:
            fire_dt = _next_occurrence(fire_dt, repeat)

        delay_secs = max(1.0, (fire_dt - now).total_seconds())

        async def _make_delayed(tid=timer_id, p=prompt, n=notify, r=repeat, fdt=fire_dt, d=delay_secs):
            await asyncio.sleep(d)
            await _fire(tid, p, n, r, fdt)

        task = asyncio.create_task(_make_delayed())
        fires_at_ts_new = fire_dt.timestamp()
        _timers[timer_id] = {
            "id":          timer_id,
            "prompt":      prompt,
            "fires_at_ts": fires_at_ts_new,
            "fires_at":    datetime.fromtimestamp(fires_at_ts_new, tz=timezone.utc).isoformat(),
            "in_seconds":  int(delay_secs),
            "notify":      notify,
            "repeat":      repeat,
            "at_time":     at_time,
            "task":        task,
        }
        restored += 1

    if restored:
        import logging
        logging.getLogger("kaare_api").info(
            "Timer-gjenoppretting: %d gjentakende timer(e) lastet fra disk.", restored
        )
