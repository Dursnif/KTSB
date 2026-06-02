"""
Jing – event scanner, lowest layer of Kare's inner processing.
Runs every N minutes (from settings.yaml). Reads categorized recent events.
Outputs 4 categorized sections: MENNESKER, ANDRE, STM, ARGUS.
Only data from the last INTERVAL+10 seconds is included (time filter).
"""
import re
import sys
import time
from pathlib import Path

from common import (
    DIGEST,
    FACE_EVENTS,
    JING_THOUGHTS,
    load_behavior_config,
    load_service_config,
    read_stm_recent,
    strip_think,
)
from provider import load_provider
from push_client import push_thought

_FACE_TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")
_DIGEST_TS_RE = re.compile(r"^(\d{2}-\d{2} \d{2}:\d{2})")

SYSTEM = """Du er Jing, et sanse-lag i Kares kognitive system.
Du mottar fire forhåndssorterte kategorier med ferske hendelser.
Skriv 1-3 korte, faktabaserte observasjoner per kategori som har innhold.
Utelat hele kategorien hvis den er tom (ingen data). Maks 2 setninger per kategori.
Skriv på norsk. Ingen refleksjon, ingen meninger — kun hva du faktisk ser i dataene."""


def _parse_face_ts(line: str) -> float | None:
    m = _FACE_TS_RE.match(line)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M"))
    except ValueError:
        return None


def _parse_digest_ts(line: str) -> float | None:
    m = _DIGEST_TS_RE.match(line)
    if not m:
        return None
    try:
        year = time.localtime().tm_year
        ts = time.mktime(time.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d %H:%M"))
        # Guard against new year edge case
        if ts > time.time() + 3600:
            ts = time.mktime(time.strptime(f"{year - 1}-{m.group(1)}", "%Y-%m-%d %H:%M"))
        return ts
    except ValueError:
        return None


def _read_face_events_recent(cutoff: float) -> str:
    if not FACE_EVENTS.exists():
        return "(ingen)"
    lines = []
    for line in FACE_EVENTS.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ts = _parse_face_ts(line)
        if ts is not None and ts >= cutoff:
            lines.append(line)
    return "\n".join(lines) if lines else "(ingen)"


def _read_digest_category(cutoff: float, category: str) -> str:
    """
    category='andre': frigate-mqtt lines where label is not person
    category='argus':  kaare-metrics, ha-events, and error messages
    """
    if not DIGEST.exists():
        return "(ingen)"
    lines = []
    for line in DIGEST.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        ts = _parse_digest_ts(line)
        if ts is None or ts < cutoff:
            continue
        parts = line.split("|", 2)
        source = parts[1].strip() if len(parts) > 1 else ""
        message = parts[2].strip() if len(parts) > 2 else line

        if category == "andre":
            if source == "frigate-mqtt" and "person" not in message.lower():
                lines.append(line)
        elif category == "argus":
            if source in ("kaare-metrics", "ha-events"):
                lines.append(line)
            elif "error" in message.lower() or "feil" in message.lower():
                lines.append(line)
    return "\n".join(lines) if lines else "(ingen)"


def build_context(interval: int) -> str:
    cutoff = time.time() - interval - 10
    mennesker = _read_face_events_recent(cutoff)
    andre = _read_digest_category(cutoff, "andre")
    stm = read_stm_recent(cutoff)
    argus = _read_digest_category(cutoff, "argus")
    return (
        f"[MENNESKER]\n{mennesker}\n\n"
        f"[ANDRE]\n{andre}\n\n"
        f"[STM]\n{stm}\n\n"
        f"[ARGUS]\n{argus}"
    )


def run_once(provider, behavior_cfg: dict, service_cfg: dict) -> None:
    interval = int(behavior_cfg.get("interval_seconds", 180))
    max_tokens = int(behavior_cfg.get("max_tokens", 300))

    context = build_context(interval)
    prompt = (
        f"<|im_start|>system\n{SYSTEM}<|im_end|>\n"
        f"<|im_start|>user\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n/no_think\n"
    )

    raw = provider.generate(prompt, max_tokens)
    thoughts = strip_think(raw).strip()
    if not thoughts:
        return

    push_url = (service_cfg.get("push_url") or "").strip()
    push_token = (service_cfg.get("push_token") or "").strip()

    if push_url:
        push_thought(push_url, push_token, "jing", thoughts)
    else:
        JING_THOUGHTS.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%H:%M")
        block = f"[Jing {timestamp}]\n{thoughts}"
        with JING_THOUGHTS.open("a") as f:
            f.write(f"\n\n{block}")

    print(f"[Jing] {len(thoughts)} chars written", flush=True)


def main() -> None:
    service_cfg = load_service_config("jing")

    if service_cfg.get("provider") == "remote":
        print("[Jing] Provider is remote — not running locally.", flush=True)
        return

    behavior_cfg = load_behavior_config("jing")
    print("[Jing] Loading model...", flush=True)
    provider = load_provider("jing", service_cfg)
    print("[Jing] Ready.", flush=True)

    while True:
        behavior_cfg = load_behavior_config("jing")
        service_cfg = load_service_config("jing")
        interval = int(behavior_cfg.get("interval_seconds", 180))
        try:
            run_once(provider, behavior_cfg, service_cfg)
        except Exception as e:
            print(f"[Jing] Error: {e}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
