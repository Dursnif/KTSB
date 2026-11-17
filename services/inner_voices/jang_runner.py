"""
Jang – filter and curator (4B, think mode).
Runs every N minutes (from settings.yaml). Reads last N Jing blocks.
Distills each category to 1-2 lines. Writes categorized output to inner_thoughts.txt.
Size-limited, oldest first (P150, storage_limits.inner_thoughts_mb). Keeps last turns_back blocks in jing_thoughts.txt as context.
"""

import re
import sys
import time

from common import (
    INNER_THOUGHTS,
    JING_THOUGHTS,
    PERSONALITY,
    get_pipe_lang,
    inner_thoughts_max_bytes,
    load_behavior_config,
    load_service_config,
    push_token,
    strip_think,
    t,
)
from provider import load_provider
from push_client import push_thought

_JING_BLOCK_RE = re.compile(r"^\[Jing \d{2}:\d{2}\]")
_JANG_ENTRY_RE = re.compile(r"^\[Jang (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")


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


def _trim_jing_buffer(keep: int) -> None:
    """Keep only the last `keep` blocks in jing_thoughts.txt as context for next run.

    Re-reads the file fresh instead of reusing a list parsed before the (slow) LLM
    call — Jing may have appended a new block while Jang was generating, and trimming
    from a stale in-memory list would silently drop it on write-back.
    """
    if not JING_THOUGHTS.exists():
        return
    fresh_text = JING_THOUGHTS.read_text(errors="replace").strip()
    if not fresh_text:
        return
    fresh_blocks = _parse_jing_blocks(fresh_text)
    to_keep = fresh_blocks[-keep:] if len(fresh_blocks) > keep else fresh_blocks
    JING_THOUGHTS.write_text("\n\n".join(to_keep) + "\n")


def _roll_inner(new_thought: str, max_bytes: int) -> None:
    """Append new_thought with timestamp; over the size limit the oldest entries go first (P150; 0 = off)."""
    # Last-resort guard for think content that survived upstream filtering
    if "<think>" in new_thought:
        new_thought = new_thought[: new_thought.index("<think>")].strip()
    if not new_thought:
        return
    INNER_THOUGHTS.parent.mkdir(parents=True, exist_ok=True)
    entry = f"[Jang {time.strftime('%Y-%m-%d %H:%M')}]\n{new_thought}"
    size = INNER_THOUGHTS.stat().st_size if INNER_THOUGHTS.exists() else 0
    if max_bytes <= 0 or size + len(entry.encode("utf-8")) + 2 <= max_bytes:
        with INNER_THOUGHTS.open("a", encoding="utf-8") as f:  # under the limit: append only
            f.write(("\n" if size else "") + entry + "\n")
        return
    blocks: list[str] = []
    current: list[str] = []
    for line in INNER_THOUGHTS.read_text(errors="replace").splitlines():
        if _JANG_ENTRY_RE.match(line):
            if current:
                blocks.append("\n".join(current).strip())
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    blocks.append(entry)
    budget, kept, total = int(max_bytes * 0.9), [], 0
    for b in reversed([b for b in blocks if b]):
        size_b = len(b.encode("utf-8")) + 2
        if kept and total + size_b > budget:
            break
        kept.append(b)
        total += size_b
    INNER_THOUGHTS.write_text("\n\n".join(reversed(kept)) + "\n")


def run_once(provider, behavior_cfg: dict, service_cfg: dict) -> None:
    max_tokens = int(behavior_cfg.get("max_tokens", 1000))
    turns_back = int(behavior_cfg.get("turns_back", 3))

    if not JING_THOUGHTS.exists() or not JING_THOUGHTS.stat().st_size:
        print("[Jang] No Jing thoughts yet, skipping.", flush=True)
        return

    raw_text = JING_THOUGHTS.read_text(errors="replace").strip()
    if not raw_text:
        return

    all_blocks = _parse_jing_blocks(raw_text)
    if not all_blocks:
        return

    lang = get_pipe_lang()
    recent_blocks = all_blocks[-turns_back:]
    combined = "\n\n".join(recent_blocks)

    personality = PERSONALITY.read_text(errors="replace").strip() if PERSONALITY.exists() else ""
    context = f"{t('jang_context_jing_header', lang)}\n{combined}"
    if personality:
        context += f"\n\n{t('jang_context_personality_header', lang)}\n{personality}"

    prompt = (
        f"<|im_start|>system\n{t('jang_system', lang)}<|im_end|>\n"
        f"<|im_start|>user\n{context}<|im_end|>\n"
        f"<|im_start|>assistant\n/no_think\n"
    )

    raw = provider.generate(prompt, max_tokens)
    distilled = strip_think(raw).strip()

    if distilled:
        push_url = (service_cfg.get("push_url") or "").strip()
        token = push_token(service_cfg)
        if push_url:
            push_thought(push_url, token, "jang", distilled)
        else:
            _roll_inner(distilled, inner_thoughts_max_bytes())
        print(f"[Jang] Distilled: {distilled[:80]}...", flush=True)

    # Always trim, even when distilled is empty — otherwise jing_thoughts.txt
    # grows unbounded through any stretch where Jang consistently has nothing
    # to distill (e.g. a quiet household with all categories empty).
    _trim_jing_buffer(turns_back)


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
