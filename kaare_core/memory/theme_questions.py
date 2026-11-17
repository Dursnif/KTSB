"""Night-meeting themes set aside as questions (run-queue J9; P149 D6/D7/D12, P14).

When the three-night stop (repeat_stop, J4) parks a chain, that theme waits for the user — one neutral question in the
same G17 checklist as the camera's «Var det deg?» (code sorts, the user ticks ja / nei / privat / hopp over), within the
same budget and willingness weight (D12). Rules:
  - a theme is only ever asked to the user whose meeting it came from (their own report);
  - never user A about user B: a theme that names another household member (username, display name or face name) is
    not asked — it stays parked (logged), and so is anything naming an opted-out person;
  - the answer is the user's own evidence: «ja»/«privat» → Validated up for that chain, «nei» → down; either way the
    chain is unparked (answered); «hopp over» → asked less (willingness) and not shown again;
  - the text shown is the theme as the meeting wrote it, shortened — never anything from another user's meeting.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from kaare_core import weights
from kaare_core.memory import repeat_stop
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

ANSWERS = ("yes", "no", "private", "skip")
TEXT_CHARS = 220


def _others_names(username: str) -> set[str]:
    names: set[str] = set()
    try:
        for u in user_store.list_users():
            if u.get("username") == username:
                continue
            names |= {n.casefold() for n in (u.get("username"), u.get("display_name"), *(u.get("frigate_faces") or []))
                      if isinstance(n, str) and len(n) >= 3}
    except Exception as e:
        logger.warning("[THEME_Q] user list failed — no theme questions (fail closed): %s", e)
        return {"\0"}
    return names


def _names_someone_else(text: str, others: set[str]) -> bool:
    if "\0" in others:
        return True
    low = text.casefold()
    return any(re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", low) for n in others)


def _theme_text(user: str, chain: str, base: Path) -> str:
    """The newest night's opening of this chain (who it was is not shown)."""
    st = weights.get_store()
    rows = st._rows.get(repeat_stop._subject(user, chain), {}).get("evidence") or []
    for r in sorted(rows, key=lambda r: r.get("context") or "", reverse=True):
        night = r.get("context") or ""
        who = (r.get("source") or "").partition(":")[2]
        for th in repeat_stop.earlier_themes(Path(base) / user, f"{night}~", nights=1):
            if th.night == night and (not who or th.who == who):
                return th.text
    return ""


def _skipped(user: str, chain: str) -> bool:
    return weights.get_store().has_pointer("evidence", f"skip:{user}:theme:{chain}")


def candidates(username: str, base: Path | None = None) -> list[dict]:
    """Parked meeting themes this user can be asked about: [{chain, text, nights}], oldest park first."""
    base = Path(base or repeat_stop.REFLECTIONS_BASE)
    st = weights.get_store()
    others = _others_names(username)
    out = []
    for kind, key, scope in st.subjects("topic"):
        if scope != username or not key.startswith(f"refl:{username}:") or not st.is_parked((kind, key, scope)):
            continue
        chain = key.split(":", 2)[2]
        if _skipped(username, chain):
            continue
        text = _theme_text(username, chain, base)
        if not text:
            continue
        if _names_someone_else(text, others):
            logger.info("[THEME_Q] chain %s names someone else — stays parked, not asked", chain)
            continue
        w = st.get((kind, key, scope))
        out.append({"chain": chain, "text": text[:TEXT_CHARS], "nights": w.angles})
    return out


def answer(username: str, chain: str, verdict: str, base: Path | None = None, channel: str = "gui") -> dict:
    """Store the user's answer to one of their own parked themes. Fail closed: only a theme listed for this user."""
    if verdict not in ANSWERS:
        return {"ok": False, "error": "bad_request"}
    if chain not in {c["chain"] for c in candidates(username, base)}:
        return {"ok": False, "error": "not_a_question"}
    subj = repeat_stop._subject(username, chain)
    asking = ("asking", f"{username}|{channel}", username)
    ctx = f"theme:{chain}"
    if verdict == "skip":
        weights.record(subj, source_class="user", source=channel, context=ctx, pointer=f"skip:{username}:theme:{chain}")
        weights.answer(asking, by_user=username, verdict="no", verbatim=True, context=ctx)
    else:
        weights.answer(subj, by_user=username, verdict=verdict, verbatim=True, context=ctx, pointer=ctx)
        weights.unpark(subj, reason=f"answered:{verdict}")
        weights.answer(asking, by_user=username, verdict="yes", verbatim=True, context=ctx)
    return {"ok": True, "saved": weights.flush_ok()}  # N39: the receipt must know whether it was saved
