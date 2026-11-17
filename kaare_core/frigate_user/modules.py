"""The camera pipe as modules 0–7 with one switch each (P144 phase 5, run-queue J21 step 5b).

| # | module | needs |
|---|---|---|
| 0 | base — events → frigate.db, episodes, route, counts, stationary gate, questions (always on) | — |
| 1 | still-image analysis per event (today: the old responder) | 0 |
| 2 | extra face recognition (Double Take) | 0 |
| 3 | episode gate (blind look) | 0 |
| 4 | frame by frame | 0, 3 |
| 5 | conclusion + memory (27B) | 0 and (4 or 1) |
| 6 | 27B on doubt | 5 |
| 7 | alerts / announcements (always on — security; works without any model) | — |

Each of m1–m6 is «auto», «on» or «off» in `frigate_user.yaml: modules`. «auto» derives from what the install already has
configured — m1 = the old responder's `enabled` (frigate_cameras.yaml), m2 = Double Take set up (`face_votes.enabled`
with the Frigate user on), m3–m6 = `frigate_user.enabled` — so an existing install keeps today's behaviour and a fresh one
(both masters off in the templates) gets 0 + 7 (decision 3, rule 24). A module whose needs are off is shown off with the
reason; 1 and 4 both on is allowed and only warned about («dobbelt arbeid», decision 1). Step 5b: nothing reads this yet
to change behaviour — the panel (5e) shows it, module 1 (5c) will ask it. Pure; the live wrapper reads the configs.
"""

from __future__ import annotations

import logging

from kaare_core.domain.frigate_store import camera_config
from kaare_core.frigate_user import settings

log = logging.getLogger(__name__)

SWITCHABLE = (1, 2, 3, 4, 5, 6)
# The switches that act: m2 (J21 5e — off = Double Take's votes are not used) and, since DT04 R0.6b, m4–m6 (conclude.py:
# m4 off = no frame-by-frame run and no one-call description after the gate's look; m5 off = no conclusion call, the
# episode is closed by code; m6 off = no 27B look on doubt). m3 (the gate's look) follows the Frigate user — it is the
# cheap call that keeps false detections out. m1 (the old still-image call) is gone (P144 phase 6, 2026-11-13): replaced
# by L3 per event + module 4 — always off, shown as «replaced».
ACTING = (2, 4, 5, 6)
VALUES = ("auto", "on", "off")
NEEDS: dict[int, list] = {0: [], 1: [0], 2: [0], 3: [0], 4: [0, 3], 5: [0, (4, 1)], 6: [5], 7: []}


def _auto(m: int, user: dict, cams: dict) -> tuple[bool, str]:
    base = bool(user.get("enabled"))
    if m == 1:
        return False, "replaced"
    if m == 2:
        on = base and bool((user.get("face_votes") or {}).get("enabled"))
        return on, "auto_double_take_on" if on else "auto_double_take_off"
    return base, "auto_pipe_on" if base else "auto_pipe_off"


def resolve(user: dict, cams: dict) -> dict:
    """{0..7: {setting, wanted, on, why, needs_missing}, warnings: [...]} from the two config dicts. Never raises."""
    sw = user.get("modules") if isinstance(user.get("modules"), dict) else {}
    out: dict = {}
    for m in range(8):
        if m in (0, 7):
            out[m] = {"setting": "always", "wanted": True, "why": "always"}
            continue
        setting = sw.get(f"m{m}", "auto")
        setting = setting if setting in VALUES else "auto"
        if m not in ACTING:
            setting = "auto"  # not switchable: a stored on/off would not act, so it is shown as what it follows
        if setting == "auto":
            wanted, why = _auto(m, user, cams)
        elif setting == "on":
            # «on» cannot switch on what is not set up here: the old path's own `enabled`, Double Take's own settings
            source_on, _ = _auto(m, user, cams)
            wanted, why = (True, "on") if source_on else (False, "not_set_up")
        else:
            wanted, why = False, "off"
        out[m] = {"setting": setting, "wanted": wanted, "why": why}
    for m in range(8):  # needs in order: a module is on only when it is wanted and its needs are on
        missing = []
        for need in NEEDS[m]:
            if isinstance(need, tuple):
                if not any(out[n].get("on") for n in need):
                    missing.append("|".join(str(n) for n in need))
            elif not out[need].get("on"):
                missing.append(need)
        out[m]["needs_missing"] = missing
        out[m]["on"] = bool(out[m]["wanted"]) and not missing
        if out[m]["wanted"] and missing:
            out[m]["why"] = "needs"
    out["warnings"] = ["double_work"] if out[1]["on"] and out[4]["on"] else []
    return out


def is_on(m: int) -> bool:
    """Live: is module `m` on? Never raises (unreadable → today's behaviour: on)."""
    try:
        return bool(status()[m]["on"])
    except Exception:
        return True


def status() -> dict:
    """The live install's modules (frigate_user.yaml through settings, frigate_cameras.yaml). Never raises."""
    try:
        return resolve(settings.get_settings(), camera_config() or {})
    except Exception as e:
        log.info("[FRIGATE_MODULES] status unreadable: %s", type(e).__name__)
        return resolve({}, {})
