"""
Jing – event scanner, lowest layer of Kare's inner processing.
Runs every N minutes (from settings.yaml). Reads categorized recent events.
Outputs 3 categorized sections: MENNESKER, ANDRE, ARGUS.
Only data from the last INTERVAL+10 seconds is included (time filter).
"""

import json
import re
import sys
import time
import urllib.parse
import urllib.request

from common import (
    DIGEST,
    JING_THOUGHTS,
    get_pipe_lang,
    load_behavior_config,
    load_service_config,
    push_token,
    strip_think,
    t,
)
from provider import load_provider
from push_client import push_thought

_DIGEST_TS_RE = re.compile(r"^(\d{2}-\d{2} \d{2}:\d{2})")


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


def _context_url(service_cfg: dict) -> str:
    """GET /api/inner-voices/context next to the push endpoint (or `context_url` when set)."""
    url = (service_cfg.get("context_url") or "").strip()
    if url:
        return url
    push = (service_cfg.get("push_url") or "").strip()
    return push[: -len("/push")] + "/context" if push.endswith("/push") else ""


def _people_from_api(cutoff: float, lang: str, service_cfg: dict) -> str | None:
    """People from KTSB's camera memory: pseudonym ids, kind, camera and place — never names. None when the API cannot
    be asked or has no camera memory (then the nameless file fallback is used)."""
    url = _context_url(service_cfg)
    token = push_token(service_cfg)
    if not url or not token:
        return None
    query = urllib.parse.urlencode({"source": "jing", "since": f"{cutoff:.0f}", "lang": lang})
    req = urllib.request.Request(f"{url}?{query}", headers={"Authorization": f"Bearer {token}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[Jing] camera context unavailable: {type(e).__name__}", file=sys.stderr, flush=True)
        return None
    if not isinstance(data, dict) or not data.get("available"):
        return None
    people = [str(x) for x in data.get("people") or [] if str(x).strip()]
    return "\n".join(people) if people else t("inner_none", lang)


def _read_digest_category(cutoff: float, category: str, lang: str) -> str:
    """
    category='andre': frigate-mqtt lines where label is not person
    category='argus':  kaare-metrics, ha-events, and error messages
    """
    if not DIGEST.exists():
        return t("inner_none", lang)
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
        elif category == "argus" and (
            source in ("kaare-metrics", "ha-events")
            or "error" in message.lower()
            or "feil" in message.lower()
        ):
            lines.append(line)
    return "\n".join(lines) if lines else t("inner_none", lang)


def build_context(interval: int, lang: str, service_cfg: dict | None = None) -> str:
    cutoff = time.time() - interval - 10
    mennesker = _people_from_api(cutoff, lang, service_cfg or {})
    if mennesker is None:  # P144 phase 6: no file fallback any more (Argus' face_events.txt is gone)
        mennesker = t("inner_none", lang)
    andre = _read_digest_category(cutoff, "andre", lang)
    argus = _read_digest_category(cutoff, "argus", lang)
    return (
        f"[{t('inner_label_mennesker', lang)}]\n{mennesker}\n\n"
        f"[{t('inner_label_andre', lang)}]\n{andre}\n\n"
        f"[{t('inner_label_argus', lang)}]\n{argus}"
    )


def run_once(provider, behavior_cfg: dict, service_cfg: dict) -> None:
    interval = int(behavior_cfg.get("interval_seconds", 180))
    max_tokens = int(behavior_cfg.get("max_tokens", 300))
    lang = get_pipe_lang()

    context = build_context(interval, lang, service_cfg)
    prompt = (
        f"<|im_start|>system\n{t('jing_system', lang)}<|im_end|>\n"
        f"<|im_start|>user\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n/no_think\n"
    )

    raw = provider.generate(prompt, max_tokens)
    thoughts = strip_think(raw).strip()
    if not thoughts:
        return

    push_url = (service_cfg.get("push_url") or "").strip()
    token = push_token(service_cfg)

    if push_url:
        push_thought(push_url, token, "jing", thoughts)
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
