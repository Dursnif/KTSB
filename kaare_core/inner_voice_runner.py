"""Jing and Jang as part of KTSB (run-queue J27): the code runs in KTSB, a model server only serves the models.

The two inner voices run on the KTSB machine; their models are ordinary model roles `jing` / `jang` in llm.yaml (base_url,
model, API key from the GUI — Settings → LLM/Models), so the model can sit on this machine or on another one in the house
(the same path that lets KTSB borrow a model from another PC). One thought = one rid (`rid-jing-<ms>` / `rid-jang-<ms>`): the
model call lands in llm_calls.log with that rid, a prompt preview and its latency.
  - Jing: what happened in the last interval — people from the camera memory as pseudonym ids, never names
    (`inner_context.people`), other camera labels and Argus lines from Argus' digest — → a short block in jing_thoughts.txt.
  - Jang: the last Jing blocks + Kåre's self-image → one distilled thought in inner_thoughts.txt (rolling window).
The prompts and texts are the existing ones (`services/inner_voices/common.py`). Nothing here starts on its own: the entry
script `kaare_inner_voices.py` runs it (a systemd unit, scripts/systemd/kaare-{jing,jang}.service).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from adapters.llm_adapter import CFG as LLM_CFG
from adapters.llm_adapter import call_llm_chat
from kaare_core.config import get_local_tz
from kaare_core.frigate_user import inner_context, query
from kaare_core.storage import limits, rotation
from kaare_core.tools.i18n import get_lang
from services.inner_voices import common

log = logging.getLogger("inner_voices")

STATE = Path("/kaare/state")
DIGEST = STATE / "argus" / "digest.txt"
JING_THOUGHTS = STATE / "jing_thoughts.txt"
INNER_THOUGHTS = STATE / "inner_thoughts.txt"
PERSONALITY = STATE / "personality_self.md"

_DIGEST_TS_RE = re.compile(r"^(\d{2}-\d{2} \d{2}:\d{2})")
_JING_BLOCK_RE = re.compile(r"^\[Jing \d{2}:\d{2}\]")
_JANG_ENTRY_RE = re.compile(r"^\[Jang (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\]")


def new_rid(voice: str) -> str:
    return f"rid-{voice}-{int(time.time() * 1000)}"


def _settings(voice: str) -> dict:
    return common.load_behavior_config(voice) or {}


def _digest_ts(line: str, now: float) -> float | None:
    m = _DIGEST_TS_RE.match(line)
    if not m:
        return None
    tz = get_local_tz()
    year = datetime.fromtimestamp(now, tz).year
    try:
        ts = datetime.strptime(f"{year}-{m.group(1)}", "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp()
    except ValueError:
        return None
    return ts if ts <= now + 3600 else datetime.strptime(f"{year - 1}-{m.group(1)}", "%Y-%m-%d %H:%M").replace(
        tzinfo=tz).timestamp()


def digest_lines(cutoff: float, category: str, now: float | None = None) -> list[str]:
    """«andre»: Frigate MQTT lines that are not persons; «argus»: metrics, HA events and error lines. Argus' digest."""
    now = time.time() if now is None else now
    if not DIGEST.exists():
        return []
    out = []
    for line in DIGEST.read_text(errors="replace").splitlines():
        line = line.strip()
        ts = _digest_ts(line, now) if line else None
        if ts is None or ts < cutoff:
            continue
        parts = line.split("|", 2)
        source = parts[1].strip() if len(parts) > 1 else ""
        message = parts[2].strip() if len(parts) > 2 else line
        if (category == "andre" and source == "frigate-mqtt" and "person" not in message.lower()) or (category == "argus" and (source in ("kaare-metrics", "ha-events") or "error" in message.lower()
                                      or "feil" in message.lower())):
            out.append(line)
    return out


def jing_context(interval: int, lang: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    cutoff = now - interval - 10
    none = common.t("inner_none", lang)
    people = inner_context.people(cutoff, lang, now=now) if query.available() else []
    andre, argus = digest_lines(cutoff, "andre", now), digest_lines(cutoff, "argus", now)
    return (f"[{common.t('inner_label_mennesker', lang)}]\n" + ("\n".join(people) or none) + "\n\n"
            f"[{common.t('inner_label_andre', lang)}]\n" + ("\n".join(andre) or none) + "\n\n"
            f"[{common.t('inner_label_argus', lang)}]\n" + ("\n".join(argus) or none))


_local: dict[str, object] = {}


def _local_generate(role: str, system: str, user: str, max_tokens: int) -> str:
    """One machine, no model server (rule 24): the model in-process, as the old runners did (services/inner_voices)."""
    # PLC0415: optional dependency (openvino-genai) — only loaded on installs that run the voices in-process
    from services.inner_voices import provider  # noqa: PLC0415
    if role not in _local:
        _local[role] = provider.load_provider(role, common.load_service_config(role))
    prov = _local[role]
    if prov is None:
        return ""
    prompt = (f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
              f"<|im_start|>assistant\n/no_think\n")
    return prov.generate(prompt, max_tokens)


def uses_model_role(role: str) -> bool:
    """The role has its own model address (Settings → LLM/Models) — else the in-process fallback."""
    cfg = LLM_CFG.get(role) or {}
    return bool(cfg.get("enabled", True)) and bool(str(cfg.get("base_url") or "").strip())


async def _think(role: str, system: str, user: str, max_tokens: int, rid: str) -> str:
    if not uses_model_role(role):
        try:
            text = await asyncio.to_thread(_local_generate, role, system, user, max_tokens)
        except Exception as e:
            log.warning("[%s] %s: local model failed: %s", role, rid, type(e).__name__)
            return ""
        return common.strip_think(text or "").strip()
    res = await call_llm_chat(role, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                              options={"num_predict": max_tokens}, disable_thinking=True, rid=rid, source=role)
    if not res.get("ok", True) and not res.get("text"):
        log.warning("[%s] %s: model call failed: %s", role, rid, res.get("error"))
        return ""
    return common.strip_think(res.get("text") or "").strip()


async def jing_once(now: float | None = None, write: bool = True) -> dict:
    """One Jing thought. Returns {rid, text, written}."""
    cfg = _settings("jing")
    lang = get_lang("global")
    rid = new_rid("jing")
    text = await _think("jing", common.t("jing_system", lang),
                        jing_context(int(cfg.get("interval_seconds", 180)), lang, now),
                        int(cfg.get("max_tokens", 300)), rid)
    written = False
    if text and write:
        JING_THOUGHTS.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(time.time() if now is None else now, get_local_tz()).strftime("%H:%M")
        with JING_THOUGHTS.open("a", encoding="utf-8") as f:
            f.write(f"\n\n[Jing {stamp}]\n{text}")
        written = True
    log.info("[jing] %s: %d chars%s", rid, len(text), " written" if written else "")
    return {"rid": rid, "text": text, "written": written}


def jing_blocks(text: str) -> list[str]:
    blocks, current = [], []
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


def roll_inner(new_thought: str, max_bytes: int, now: float | None = None) -> None:
    """Append one Jang thought; over the size limit the oldest entries go first (P150: size, not hours; 0 = off).
    Settings → Logs/Storage (storage_limits.inner_thoughts_mb)."""
    if "<think>" in new_thought:
        new_thought = new_thought[: new_thought.index("<think>")].strip()
    if not new_thought:
        return
    now = time.time() if now is None else now
    tz = get_local_tz()
    entry = f"[Jang {datetime.fromtimestamp(now, tz).strftime('%Y-%m-%d %H:%M')}]\n{new_thought}"
    INNER_THOUGHTS.parent.mkdir(parents=True, exist_ok=True)
    size = INNER_THOUGHTS.stat().st_size if INNER_THOUGHTS.exists() else 0
    if max_bytes <= 0 or size + len(entry.encode("utf-8")) + 2 <= max_bytes:
        with INNER_THOUGHTS.open("a", encoding="utf-8") as f:  # under the limit: append only, no read (IO, rule 10)
            f.write(("\n" if size else "") + entry + "\n")
        return
    existing = INNER_THOUGHTS.read_text(errors="replace")
    blocks, current = [], []
    for line in existing.splitlines():
        if _JANG_ENTRY_RE.match(line):
            if current:
                blocks.append("\n".join(current).strip())
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    blocks.append(entry)
    kept = rotation.trim_blocks([b for b in blocks if b], int(max_bytes * rotation.TRIM_TO))
    INNER_THOUGHTS.write_text("\n\n".join(kept) + "\n", encoding="utf-8")


def trim_jing(keep: int) -> None:
    """Keep the last `keep` Jing blocks (re-read fresh: Jing may have written while Jang thought)."""
    if not JING_THOUGHTS.exists():
        return
    blocks = jing_blocks(JING_THOUGHTS.read_text(errors="replace").strip())
    if blocks:
        JING_THOUGHTS.write_text("\n\n".join(blocks[-keep:]) + "\n", encoding="utf-8")


async def jang_once(now: float | None = None, write: bool = True) -> dict:
    """One Jang thought from the last Jing blocks + the self-image. Returns {rid, text, written}."""
    cfg = _settings("jang")
    lang = get_lang("global")
    rid = new_rid("jang")
    turns = int(cfg.get("turns_back", 3))
    blocks = jing_blocks(JING_THOUGHTS.read_text(errors="replace").strip()) if JING_THOUGHTS.exists() else []
    if not blocks:
        return {"rid": rid, "text": "", "written": False}
    context = f"{common.t('jang_context_jing_header', lang)}\n" + "\n\n".join(blocks[-turns:])
    personality = PERSONALITY.read_text(errors="replace").strip() if PERSONALITY.exists() else ""
    if personality:
        context += f"\n\n{common.t('jang_context_personality_header', lang)}\n{personality}"
    text = await _think("jang", common.t("jang_system", lang), context, int(cfg.get("max_tokens", 1000)), rid)
    written = False
    if write:
        if text:
            roll_inner(text, limits.get_bytes("inner_thoughts_mb"), now)
            written = True
        trim_jing(turns)
    log.info("[jang] %s: %d chars%s", rid, len(text), " written" if written else "")
    return {"rid": rid, "text": text, "written": written}
