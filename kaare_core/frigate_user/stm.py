"""The Frigate user's short-term memory (P144-s16 part B): the existing `STMRegistry` with `user_id="frigate"`.

2026-10-28: STM «ligger i RAM, ikke på disk — RAM til bruk, til disk ca. hvert 5 sek/etter hendelse», and the 9B
needs no memory at all — this is code that ties the Frigate user to the rest of KTSB. P65's August decision, restored.

  - **Writer = code only:** each concluded episode's first-person memory line (model or code summary) and each urgent
    result at once, as a dialog turn with role `observation` — never `user` (context_builder would show the cameras as
    another user being active) and never `assistant` (never replayed as chat). Text: «<local time> (episode <id>):
    <summary>».
  - **Names only as the storage gate allowed:** the summary was built from gated data; opted-out names are scrubbed
    once more here, and a failed consent lookup writes nothing (fail closed — the line can be rebuilt later).
  - **No parallel truth:** frigate.db holds the facts; STM is the narrative. `rebuild()` refills an empty Frigate STM
    from the last concluded episodes in frigate.db (at start, when the snapshot did not bring any).
  - **What counts (P144-s17):** `counts_as_memory()` / `memory_lines()` — the model memory and done urgent results,
    never a likely false detection or a vehicle-only code line. `rebuild()` and the nightly LTM step
    (`kaare_core/memory/frigate_ltm.py`) use it; the live write in `conclude._remember` does not yet (main session).
  - **Storage:** the registry's own autosave (`state/stm_users/frigate.json`, plain JSON: `frigate` has no keypair —
    the USER_GLOBAL precedent until N19 point 3). The 9B never reads it; readers are wired in phase 5.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from kaare_core import app_state
from kaare_core.config import get_local_tz
from kaare_core.frigate_user import store
from kaare_core.users import frigate_consent
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

USER_ID = "frigate"
ROLE = "observation"
REBUILD_EPISODES = 40  # at most what one STM holds by default (`stm.dialog_max_turns`)
_REBUILD_LOOKBACK_S = 7 * 86400
REFRESH_LOOKBACK_S = 30 * 86400

_TEXT = {
    "line": {"nb": "{when} (episode {id}): {summary}", "en": "{when} (episode {id}): {summary}",
             "de": "{when} (Episode {id}): {summary}"},
    "urgent": {"nb": "{when} (haster, episode {id}): {summary}", "en": "{when} (urgent, episode {id}): {summary}",
               "de": "{when} (dringend, Episode {id}): {summary}"},
}


def _registry():
    return getattr(app_state, "STM_REGISTRY", None)


def _hidden_names() -> set[str] | None:
    """Every name of a person who said «Frigate may not track me». None = lookup failed."""
    try:
        names = set(frigate_consent.untracked_faces())
        for u in user_store.list_users():
            if not u.get("frigate_tracking", True):
                names.update(n for n in (u.get("username"), u.get("display_name"), *(u.get("frigate_faces") or [])) if n)
        return names
    except Exception as e:
        logger.warning("[FRIGATE_STM] consent lookup failed, memory line not written: %s", e)
        return None


def memory_line(episode_id: int, ts: float, summary: str, lang: str, urgent: bool = False) -> str:
    when = datetime.fromtimestamp(ts, get_local_tz()).strftime("%Y-%m-%d %H:%M")
    key = "urgent" if urgent else "line"
    tmpl = _TEXT[key].get(lang) or _TEXT[key]["en"]
    return tmpl.format(when=when, id=episode_id, summary=" ".join(str(summary or "").split()))


def kind(conclusion: dict | None) -> str:
    """What a stored conclusion is: «model» (the 27B's memory), «false_detection» (the closed look saw no being —
    code line), «vehicles_only» (code route + code line), or «pending» (not done / no summary). From the conclusion
    alone: retention slims the scene, so `scene.reason` is gone after a week (store `SLIM_SCENE_KEYS`)."""
    c = conclusion or {}
    if c.get("status") != "done" or not str(c.get("summary") or "").strip():
        return "pending"
    if c.get("false_detection"):
        return "false_detection"
    if c.get("by") == "code":
        return "vehicles_only"
    return "model"


def counts_as_memory(conclusion: dict | None) -> bool:
    """The one rule for what the Frigate user remembers (P144-s17 decision 2) — STM and Kåre's LTM both use it:
    the concluded model memory, never a likely false detection or a vehicle-only code line."""
    return kind(conclusion) == "model"


def memory_lines(ep: store.Episode, lang: str) -> list[tuple[float, str, bool]]:
    """(ts, line, urgent) for every memory line one episode gives, oldest first: its concluded memory (if it counts)
    and each done urgent result. Names not yet scrubbed — the caller applies the current consent."""
    c = ep.conclusion or {}
    out = []
    for item in c.get("urgent") or []:
        if isinstance(item, dict) and item.get("status") == "done" and str(item.get("summary") or "").strip():
            ts = float(item.get("ts") or ep.start_ts)
            out.append((ts, memory_line(ep.episode_id, ts, item["summary"], item.get("lang") or c.get("lang") or lang,
                                        urgent=True), True))
    if counts_as_memory(c):
        out.append((ep.start_ts, memory_line(ep.episode_id, ep.start_ts, c["summary"], c.get("lang") or lang), False))
    return sorted(out, key=lambda x: x[0])


def remember(episode_id: int, ts: float, summary: str, lang: str, urgent: bool = False) -> bool:
    """Write one memory line into the Frigate user's STM (RAM; the registry autosaves). Never raises."""
    try:
        reg = _registry()
        if reg is None or not str(summary or "").strip():
            return False
        names = _hidden_names()
        if names is None:
            return False
        text = store._scrub_text(memory_line(episode_id, ts, summary, lang, urgent), names)
        reg.get(USER_ID).add_dialog(ROLE, text, user_id=USER_ID)
        return True
    except Exception as e:
        logger.warning("[FRIGATE_STM] memory line not written: %s", e)
        return False


def refresh(lang: str) -> int:
    """P144-s24: after a re-analysis the episode's old line must not stay beside the new one — the Frigate STM holds
    only code-written observation lines, so it is emptied and rebuilt from frigate.db (the one truth), up to
    REFRESH_LOOKBACK_S back so a quiet month keeps its older lines. Returns lines written. Never raises."""
    reg = _registry()
    if reg is None:
        return 0
    try:
        reg.get(USER_ID).clear()
    except Exception as e:
        logger.warning("[FRIGATE_STM] refresh failed: %s", e)
        return 0
    return rebuild(lang, lookback_s=REFRESH_LOOKBACK_S)


def rebuild(lang: str, now: float | None = None, lookback_s: float = _REBUILD_LOOKBACK_S) -> int:
    """Refill an empty Frigate STM from frigate.db's concluded episodes (oldest first). Returns lines written; 0 when
    the STM already holds lines (the snapshot brought them back) or there is no registry."""
    reg = _registry()
    if reg is None:
        return 0
    try:
        if reg.get(USER_ID).snapshot_counts().get("dialog_turns", 0) > 0:
            return 0
        now = time.time() if now is None else now
        names = _hidden_names()
        if names is None:
            return 0
        lines = [ln for ep in store.episodes_between(now - lookback_s, now) for ln in memory_lines(ep, lang)]
        n = 0
        for _ts, line, _urgent in lines[-REBUILD_EPISODES:]:
            reg.get(USER_ID).add_dialog(ROLE, store._scrub_text(line, names), user_id=USER_ID)
            n += 1
        if n:
            logger.info("[FRIGATE_STM] rebuilt %d memory lines from frigate.db", n)
        return n
    except Exception as e:
        logger.warning("[FRIGATE_STM] rebuild failed: %s", e)
        return 0
