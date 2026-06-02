"""
Jang – filter and curator (4B, think mode).
Runs every N minutes (from settings.yaml). Reads last N Jing blocks.
Distills each category to 1-2 lines. Writes categorized output to inner_thoughts.txt.
24-hour rolling retention. Keeps last turns_back blocks in jing_thoughts.txt as context.
"""
import re
import sys
import time
from pathlib import Path

from common import (
    INNER_THOUGHTS,
    JING_THOUGHTS,
    PERSONALITY,
    load_behavior_config,
    load_service_config,
    strip_think,
)
from provider import load_provider
from push_client import push_thought

_JING_BLOCK_RE = re.compile(r"^\[Jing \d{2}:\d{2}\]")
_JANG_ENTRY_RE = re.compile(r"^\[Jang (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")

SYSTEM = """Du er Jang. Du mottar de siste Jing-blokkene — råobservasjoner fra Kares sansesystem.
Destiller til maksimalt 1-2 linjer per kategori som har faktisk innhold.
Utelat en kategori helt hvis alle Jing-blokkene viser (ingen) for den kategorien.
Prioriter den nyeste Jing-blokken (siste [Jing]-oppføring).
Skriv output slik:
[MENNESKER] <1-2 linjer, eller utelat hele linjen>
[ANDRE] <1-2 linjer, eller utelat hele linjen>
[STM] <1-2 linjer, eller utelat hele linjen>
[VAKTMESTER] <1-2 linjer, eller utelat hele linjen>
Skriv på norsk. Ingen forklaringer — kun de destillerte linjene."""


def _parse_jing_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _JING_BLOCK_RE.match(line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _trim_jing_buffer(all_blocks: list[str], keep: int) -> None:
    """Keep only the last `keep` blocks in jing_thoughts.txt as context for next run."""
    to_keep = all_blocks[-keep:] if len(all_blocks) > keep else all_blocks
    JING_THOUGHTS.write_text("\n\n".join(to_keep) + "\n")


def _roll_inner(new_thought: str, retention_hours: int) -> None:
    """Append new_thought with timestamp; drop entries older than retention_hours."""
    # Last-resort guard for think content that survived upstream filtering
    if "<think>" in new_thought:
        new_thought = new_thought[: new_thought.index("<think>")].strip()
    if not new_thought:
        return
    INNER_THOUGHTS.parent.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - retention_hours * 3600
    existing_text = INNER_THOUGHTS.read_text(errors="replace") if INNER_THOUGHTS.exists() else ""

    kept: list[str] = []
    current_lines: list[str] = []
    current_ts: float | None = None

    for line in existing_text.splitlines():
        m = _JANG_ENTRY_RE.match(line)
        if m:
            if current_lines and current_ts is not None and current_ts > cutoff:
                kept.append("\n".join(current_lines))
            try:
                current_ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M"))
            except ValueError:
                current_ts = None
            current_lines = [line]
        elif current_lines:
            current_lines.append(line)

    if current_lines and current_ts is not None and current_ts > cutoff:
        kept.append("\n".join(current_lines))

    timestamp = f"[Jang {time.strftime('%Y-%m-%d %H:%M')}]"
    kept.append(f"{timestamp}\n{new_thought}")
    INNER_THOUGHTS.write_text("\n\n".join(kept) + "\n")


def run_once(provider, behavior_cfg: dict, service_cfg: dict) -> None:
    max_tokens = int(behavior_cfg.get("max_tokens", 1000))
    turns_back = int(behavior_cfg.get("turns_back", 3))
    retention_hours = int(behavior_cfg.get("inner_thoughts_retention_hours", 24))

    if not JING_THOUGHTS.exists() or not JING_THOUGHTS.stat().st_size:
        print("[Jang] No Jing thoughts yet, skipping.", flush=True)
        return

    raw_text = JING_THOUGHTS.read_text(errors="replace").strip()
    if not raw_text:
        return

    all_blocks = _parse_jing_blocks(raw_text)
    if not all_blocks:
        return

    recent_blocks = all_blocks[-turns_back:]
    combined = "\n\n".join(recent_blocks)

    personality = PERSONALITY.read_text(errors="replace").strip() if PERSONALITY.exists() else ""
    context = f"=== Jing-blokker (nyeste sist) ===\n{combined}"
    if personality:
        context += f"\n\n=== Kares personlighet ===\n{personality}"

    prompt = (
        f"<|im_start|>system\n{SYSTEM}<|im_end|>\n"
        f"<|im_start|>user\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

    raw = provider.generate(prompt, max_tokens)
    distilled = strip_think(raw).strip()
    if not distilled:
        return

    push_url = (service_cfg.get("push_url") or "").strip()
    push_token = (service_cfg.get("push_token") or "").strip()

    if push_url:
        push_thought(push_url, push_token, "jang", distilled)
        _trim_jing_buffer(all_blocks, turns_back)
    else:
        _roll_inner(distilled, retention_hours)
        _trim_jing_buffer(all_blocks, turns_back)

    print(f"[Jang] Distilled: {distilled[:80]}...", flush=True)


def main() -> None:
    service_cfg = load_service_config("jang")

    if service_cfg.get("provider") == "remote":
        print("[Jang] Provider is remote — not running locally.", flush=True)
        return

    behavior_cfg = load_behavior_config("jang")
    print("[Jang] Loading model...", flush=True)
    provider = load_provider("jang", service_cfg)
    print("[Jang] Ready.", flush=True)

    while True:
        behavior_cfg = load_behavior_config("jang")
        service_cfg = load_service_config("jang")
        interval = int(behavior_cfg.get("interval_seconds", 600))
        try:
            run_once(provider, behavior_cfg, service_cfg)
        except Exception as e:
            print(f"[Jang] Error: {e}", file=sys.stderr, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
