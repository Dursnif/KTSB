import json
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends

from kaare_core.users.auth import require_admin as _require_admin

router = APIRouter()


# /api/argus_delta removed (P120 Phase 3): no caller — anyone on the LAN could append lines to
# argus_delta.log, which argus_status/brief/advice then presented as Argus events.


@router.get("/api/argus_status")
def api_argus_status(limit: int = 20, _u=Depends(_require_admin)):
    p = Path("/kaare/logs/argus_delta.log")
    events = []
    if p.exists():
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            if "ARGUS_DELTA " in line:
                try:
                    payload = json.loads(line.split("ARGUS_DELTA ", 1)[1])
                    events.append(payload.get("summary", payload))
                    if len(events) >= limit:
                        break
                except Exception:
                    continue
    return {"count": len(events), "events": events}


@router.get("/api/argus_brief")
def api_argus_brief(limit: int = 1, _u=Depends(_require_admin)):
    p = Path("/kaare/logs/argus_delta.log")
    events = []
    if p.exists():
        for line in reversed(p.read_text(encoding="utf-8").splitlines()):
            if "ARGUS_DELTA " in line:
                try:
                    payload = json.loads(line.split("ARGUS_DELTA ", 1)[1])
                    ev = payload.get("summary", payload)
                    events.append(ev)
                    if len(events) >= limit:
                        break
                except Exception:
                    continue

    if not events:
        return {"brief": "Ingen Argus-rapporter enda.", "events": []}

    last = events[0]
    sev = last.get("severity", "ok")
    ts = last.get("timestamp", "?")
    ne = last.get("new_error_total", 0)
    nw = last.get("new_warning_total", 0)

    def fmt_top(items):
        if not items:
            return "ingen"
        return ", ".join(f"{k.split('.')[-1]}:+{v}" for k, v in items[:3])

    top_err = fmt_top(last.get("top_error_components", []))
    top_warn = fmt_top(last.get("top_warning_components", []))
    tot_e = last.get("totals", {}).get("errors", 0)
    tot_w = last.get("totals", {}).get("warnings", 0)

    brief = (
        f"[{ts}] Status: {sev.upper()}. "
        f"Nye feil: {ne}, nye varsler: {nw}. "
        f"Topp feilkomponenter: {top_err}. "
        f"Topp varselkomponenter: {top_warn}. "
        f"Totalt (akkumulert): errors={tot_e}, warnings={tot_w}."
    )
    return {"brief": brief, "events": events}


@router.get("/api/argus_advice")
def api_argus_advice(limit: int = 1, _u=Depends(_require_admin)):
    rules_path = Path("/kaare/kaare_advice_rules.yaml")
    log_path = Path("/kaare/logs/argus_delta.log")

    rules = []
    if rules_path.exists():
        try:
            rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or []
        except Exception:
            rules = []

    payload = None
    if log_path.exists():
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            if "ARGUS_DELTA " in line:
                try:
                    payload = json.loads(line.split("ARGUS_DELTA ", 1)[1])
                    break
                except Exception:
                    continue
    if not payload:
        return {"advice": [], "note": "Ingen deltarapporter enda."}

    delta_err = payload.get("delta_errors", {}) or {}
    delta_wrn = payload.get("delta_warnings", {}) or {}
    summary = payload.get("summary", {})

    out = []
    for r in rules:
        patt = re.compile(r.get("match", ".*"))
        hits_err = sum(v for k, v in delta_err.items() if patt.search(k))
        hits_wrn = sum(v for k, v in delta_wrn.items() if patt.search(k))
        trig = False
        if r.get("threshold_errors") and hits_err >= int(r["threshold_errors"]):
            trig = True
        if r.get("threshold_warnings") and hits_wrn >= int(r["threshold_warnings"]):
            trig = True
        if trig:
            out.append(
                {
                    "id": r.get("id"),
                    "advice": r.get("advice"),
                    "hits": {"errors": hits_err, "warnings": hits_wrn},
                }
            )
    out.sort(key=lambda x: (x["hits"]["errors"], x["hits"]["warnings"]), reverse=True)
    return {
        "timestamp": summary.get("timestamp"),
        "severity": summary.get("severity"),
        "advice": out,
    }


@router.get("/api/argus_types")
def api_argus_types(_u=Depends(_require_admin)):
    log_path = Path("/kaare/logs/argus_delta.log")

    payload = None
    if log_path.exists():
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            if "ARGUS_DELTA " in line:
                try:
                    payload = json.loads(line.split("ARGUS_DELTA ", 1)[1])
                    break
                except Exception:
                    continue

    if not payload:
        return {"delta_warnings": {}, "delta_errors": {}, "note": "Ingen delta funnet"}

    summary = payload.get("summary", {}) or {}
    return {
        "timestamp": summary.get("timestamp"),
        "severity": summary.get("severity"),
        "delta_warnings": payload.get("delta_warnings", {}) or {},
        "delta_errors": payload.get("delta_errors", {}) or {},
        "top_warning_components": summary.get("top_warning_components", []) or [],
        "top_error_components": summary.get("top_error_components", []) or [],
    }
