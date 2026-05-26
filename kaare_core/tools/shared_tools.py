"""
Shared tool implementations used by both executor.py (Kåre) and
pettersmart/tools.py (Pettersmart).

Each function takes an `arguments` dict matching the tool's JSON schema
and returns a plain-text string result.
"""

import subprocess as _sp
from pathlib import Path

_SENSITIVE_NAMES = {".env", "auth.env", "token", "secret", "password", "apikey", "api_key", "nvidia"}

_KAARE_ROOT = Path("/kaare").resolve()


def _valider_sti(sti: str, tillatt_rot: Path = _KAARE_ROOT) -> Path | None:
    """Resolve path and verify it stays inside tillatt_rot. Returns resolved Path or None."""
    try:
        resolved = Path(sti).resolve()
        resolved.relative_to(tillatt_rot)
        return resolved
    except (ValueError, RuntimeError):
        return None


def les_fil(arguments: dict, default_chunk: int = 500, max_chunk: int = 500) -> str:
    sti = arguments.get("sti", "").strip()
    resolved = _valider_sti(sti)
    if not sti.startswith("/kaare/") or resolved is None:
        return (
            "Mangler eller ugyldig filsti. Oppgi absolutt sti under /kaare/, "
            "f.eks. les_fil(sti='/kaare/kaare_api.py'). "
            "Bruk liste_filer() for å se hva som finnes."
        )
    if any(s in resolved.name.lower() for s in _SENSITIVE_NAMES):
        return "[Feil: filen er sensitiv og kan ikke leses]"
    p = resolved
    if not p.exists():
        return f"[Finner ikke: {sti}]"
    alle = p.read_text(encoding="utf-8", errors="replace").splitlines()
    totalt = len(alle)
    fra = max(1, int(arguments.get("fra_linje", 1))) - 1
    til = min(totalt, int(arguments.get("til_linje", fra + default_chunk)))
    til = min(til, fra + max_chunk)
    utsnitt = alle[fra:til]
    header = f"[{sti} — linjer {fra+1}–{til} av {totalt}]\n"
    mer = f"\n... ({totalt - til} linjer gjenstår — bruk fra_linje={til+1})" if til < totalt else ""
    return header + "\n".join(utsnitt) + mer


def liste_filer(arguments: dict) -> str:
    mappe = arguments.get("mappe", "/kaare/kaare_core").strip() or "/kaare/kaare_core"
    rekursiv = bool(arguments.get("rekursiv", False))
    p = _valider_sti(mappe)
    if p is None:
        return "[Feil: kun mapper under /kaare er tillatt]"
    if not p.exists():
        return f"[Finner ikke: {mappe}]"
    if rekursiv:
        items = sorted(p.rglob("*"), key=lambda x: str(x))[:200]
        lines = [f"{'[mappe]' if i.is_dir() else '[fil]  '} {i.relative_to(p)}" for i in items]
        suffix = "\n[Maks 200 filer — bruk mappe-parameter for å snevre inn søket]" if len(items) == 200 else ""
        return f"{mappe}/\n" + "\n".join(lines) + suffix
    items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
    lines = [f"{'[mappe]' if i.is_dir() else '[fil]  '} {i.name}" for i in items[:100]]
    return f"{p}/\n" + "\n".join(lines)


def søk_kode(arguments: dict) -> str:
    mønster = arguments.get("mønster", "").strip()
    mappe   = arguments.get("mappe", "/kaare").strip() or "/kaare"
    if not mappe.startswith("/kaare"):
        mappe = "/kaare"
    if not mønster:
        return "[Tomt søk]"
    result = _sp.run(
        ["grep", "-rn",
         "--include=*.py", "--include=*.yaml", "--include=*.md",
         "--include=*.json", "--include=*.sh", "--include=*.toml",
         "-m", "50", mønster, mappe],
        capture_output=True, text=True, timeout=15,
    )
    out = result.stdout.strip()
    return out[:3000] if out else f"[Ingen treff på '{mønster}' i {mappe}]"


def les_logg(arguments: dict) -> str:
    """Read log files, with optional grep via mønster parameter."""
    fil      = arguments.get("fil", "").strip()
    linjer   = max(20, min(int(arguments.get("linjer", 20)), 200))
    mønster  = arguments.get("mønster", "").strip()
    maks     = min(int(arguments.get("maks_treff", 50)), 200)
    logg_dir = Path("/kaare/logs")
    filer    = sorted(f.name for f in logg_dir.iterdir() if f.is_file()) if logg_dir.exists() else []

    if mønster:
        if fil:
            logg_p = logg_dir / fil
            if not logg_p.exists():
                return f"[Finner ikke: /kaare/logs/{fil}]\nTilgjengelige: {', '.join(filer)}"
            grep_args = ["grep", "-n", "-m", str(maks), mønster, str(logg_p)]
        else:
            grep_args = ["grep", "-rn", "-m", str(maks), "--include=*.log", mønster, str(logg_dir)]
        result = _sp.run(grep_args, capture_output=True, text=True, timeout=15)
        out = result.stdout.strip()
        return out[:4000] if out else f"[Ingen treff på '{mønster}' i {fil or '/kaare/logs/'}]"

    if not fil or "/" in fil:
        _KEY_LOGS = ["kaare_ha_gateway.log", "llm_calls.log", "route_decisions.log", "tool_calls.log"]
        andre = [f for f in filer if f not in _KEY_LOGS]
        deler = [f"[Generell logg-oversikt — {linjer} linjer per fil. Andre filer: {', '.join(andre)}]\n"]
        for _fn in _KEY_LOGS:
            _p = logg_dir / _fn
            if not _p.exists():
                continue
            _r = _sp.run(["tail", "-n", str(linjer), str(_p)], capture_output=True, text=True)
            innhold = _r.stdout.strip()
            if innhold:
                deler.append(f"\n--- {_fn} ---\n{innhold}")
        return "\n".join(deler)[:6000]

    p = _valider_sti(str(logg_dir / fil), logg_dir.resolve())
    if p is None or not p.exists():
        return (
            f"Finner ikke: /kaare/logs/{fil}\n"
            "Tilgjengelige filer:\n" + "\n".join(f"  {f}" for f in filer)
        )
    if "fra_linje" in arguments:
        alle = p.read_text(encoding="utf-8", errors="replace").splitlines()
        totalt = len(alle)
        fra = max(1, int(arguments["fra_linje"])) - 1
        til = min(totalt, int(arguments.get("til_linje", fra + linjer)))
        til = min(til, fra + 300)
        utsnitt = alle[fra:til]
        header = f"[{fil} — linjer {fra+1}–{til} av {totalt}]\n"
        mer = f"\n... ({totalt - til} linjer gjenstår — bruk fra_linje={til+1})" if til < totalt else ""
        return header + "\n".join(utsnitt) + mer
    result = _sp.run(["tail", "-n", str(linjer), str(p)], capture_output=True, text=True)
    return result.stdout.strip() or "[Tom logg]"


_ALLOWED_SERVICES = {
    "kaare", "kaare_ha_gateway", "kaare-semantic-embed", "kaare-embedding",
    "kaare-agents", "kaare-qdrant", "kaare-argus", "kaare-voice-bridge",
    "kaare-frontend", "kaare-nightjob", "kaare-reflection", "kaare-dev-meeting",
    "kaare-nvidia-init", "kaare-backup",
}


def sjekk_tjenester(arguments: dict) -> str:
    tjeneste = arguments.get("tjeneste", "").strip()
    if tjeneste:
        if tjeneste not in _ALLOWED_SERVICES:
            return (
                f"[Feil: '{tjeneste}' er ikke en kjent Kåre-tjeneste. "
                f"Tillatte: {', '.join(sorted(_ALLOWED_SERVICES))}]"
            )
        logglinjer = min(int(arguments.get("logglinjer", 20)), 50)
        status_r = _sp.run(
            ["systemctl", "status", f"{tjeneste}.service", "--no-pager", "-l"],
            capture_output=True, text=True,
        )
        journal_r = _sp.run(
            ["journalctl", "-u", f"{tjeneste}.service",
             "-n", str(logglinjer), "--no-pager", "--output=short"],
            capture_output=True, text=True,
        )
        return (
            f"=== {tjeneste}.service ===\n{status_r.stdout.strip()}\n\n"
            f"=== Siste {logglinjer} logglinjer ===\n{journal_r.stdout.strip()}"
        )[:5000]
    tjenester = [
        "kaare", "kaare_ha_gateway", "kaare-semantic-embed",
        "kaare-agents", "kaare-qdrant",
        "kaare-argus", "kaare-voice-bridge", "kaare-frontend",
    ]
    linjer = []
    for t in tjenester:
        r = _sp.run(
            ["systemctl", "is-active", f"{t}.service"],
            capture_output=True, text=True,
        )
        linjer.append(f"{t}: {r.stdout.strip()}")
    return "\n".join(linjer)


def sjekk_ressurser(arguments: dict) -> str:  # noqa: ARG001
    deler = []
    load_r = _sp.run(["cat", "/proc/loadavg"], capture_output=True, text=True)
    if load_r.returncode == 0:
        parts = load_r.stdout.strip().split()
        deler.append(f"CPU last (1m/5m/15m): {parts[0]} / {parts[1]} / {parts[2]}")
    free_r = _sp.run(["free", "-h"], capture_output=True, text=True)
    if free_r.returncode == 0:
        deler.append(f"\nRAM:\n{free_r.stdout.strip()}")
    df_r = _sp.run(["df", "-h", "/kaare"], capture_output=True, text=True)
    if df_r.returncode == 0:
        deler.append(f"\nDisk (/kaare):\n{df_r.stdout.strip()}")
    gpu_r = _sp.run(
        ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader"],
        capture_output=True, text=True, timeout=10,
    )
    if gpu_r.returncode == 0 and gpu_r.stdout.strip():
        deler.append(f"\nGPU:\n{gpu_r.stdout.strip()}")
    else:
        deler.append("\nGPU: [nvidia-smi ikke tilgjengelig]")
    return "\n".join(deler)


def git_diff(arguments: dict) -> str:
    sti = arguments.get("sti", "").strip()
    cmd = ["git", "-C", "/kaare", "diff"]
    if sti and sti.startswith("/kaare/"):
        cmd.append(sti)
    result = _sp.run(cmd, capture_output=True, text=True, timeout=10)
    out = result.stdout.strip()
    return out[:4000] if out else "[Ingen ukommitterte endringer]"


def git_log(arguments: dict) -> str:
    antall = min(int(arguments.get("antall", 10)), 50)
    sti = arguments.get("sti", "").strip()
    cmd = ["git", "-C", "/kaare", "log", f"-{antall}",
           "--pretty=format:%h %as %s", "--no-color"]
    if sti and sti.startswith("/kaare/"):
        cmd += ["--", sti]
    result = _sp.run(cmd, capture_output=True, text=True, timeout=10)
    out = result.stdout.strip()
    return out[:3000] if out else "[Ingen commits funnet]"
