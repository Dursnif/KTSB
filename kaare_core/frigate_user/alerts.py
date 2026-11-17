"""Module 7 — alerts / announcements (P144 phase 5, run-queue J21 step 5a).

Step 5a moves the away-mode alert out of the old responder **unchanged** (2026-11-06, phase-5 answer 2): the same
triggers (Frigate's away/priority hints for the camera's role), the same texts (`notify_frigate_away_person` /
`_unknown`), the same targets (every admin and adult), the same graduated urgency (a known-normal pattern at a confident
baseline → medium) and the same `notifications.log` line. Since phase 6c the MQTT listener starts it directly
(`on_end_event`); the old responder is gone (phase 6, List B, 2026-11-13) — one sender. A test replayed recorded end events
through the old code and this one and required identical calls (run-queue J21 log). One alert per episode is a later,
measured step — not this one.
Works without any model (module 7 needs only module 0). Never raises.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from adapters.frigate_adapter import derive_hints
from kaare_core import log_buffer
from kaare_core.domain.frigate_store import camera_config as _cfg
from kaare_core.notify import Urgency, notify_user
from kaare_core.tools.i18n import get_lang, t
from kaare_core.users.store import list_users

log = logging.getLogger("frigate_responder")  # the old logger name: the same journal lines as before the move

NOTIFY_LOG = Path("/kaare/logs/notifications.log")


def write_notify_log(entry: dict) -> None:
    try:
        log_buffer.write(NOTIFY_LOG, entry)
    except Exception as e:
        log.warning("[notify_log] write failed: %s", e)


async def on_end_event(event: dict) -> None:
    """Module 7's own starter (P144 phase 6c): every Frigate end event from the MQTT listener — the same source and the same
    event dict the old responder got — goes to the away alert. Independent of module 0 (frigate_user.enabled, off on a
    fresh install) and of module 1; module 7 is always on. Never raises."""
    try:
        await maybe_notify_away(event.get("camera", ""), event.get("label", ""), float(event.get("score", 0) or 0))
    except Exception as e:
        log.warning("[frigate_responder] module 7 end-event starter failed: %s", e)


async def maybe_notify_away(camera: str, label: str, score: float) -> None:
    """Fire notify_user() for admin users when household is away and threat is HIGH."""
    try:
        cam_cfg = _cfg().get("cameras", {}).get(camera, {})
        camera_role = cam_cfg.get("role", "")
        # R146: the baseline of THIS camera (it was looked up for camera «» before)
        hints = derive_hints({"camera": camera, "label": label, "confidence": score, "zones": []}, camera_role)

        if not hints.get("away_mode") or hints.get("priority") != "high":
            return

        admins = [u for u in list_users() if u.get("role") in ("admin", "adult")]
        for user in admins:
            uid = user["username"]
            source_key = f"frigate:{camera}:{label or 'unknown'}"
            lang = "en"
            try:
                lang = get_lang(uid)
            except Exception:
                pass

            if label:
                msg = t("notify_frigate_away_person", lang, camera=camera, label=label)
            else:
                msg = t("notify_frigate_away_unknown", lang, camera=camera)

            # Graduated urgency: known-normal pattern at high-confidence baseline → MEDIUM
            deviation_score = hints.get("deviation_score", 0.5)
            baseline_confidence = hints.get("baseline_confidence", 0.0)
            # R146 (2026-11-12, option B): a person never goes below HIGH while away — the baseline is learned
            # while people are home, so «a person at the usual hour» is exactly what an empty house must hear about
            if label != "person" and baseline_confidence >= 0.5 and deviation_score < 0.3:
                urgency = Urgency.MEDIUM
                urgency_str = "medium"
            else:
                urgency = Urgency.HIGH
                urgency_str = "high"

            await notify_user(uid, msg, urgency=urgency, source_key=source_key)
            write_notify_log(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "user_id": uid,
                    "urgency": urgency_str,
                    "source_key": source_key,
                    "channel": "sound_node",
                    "camera": camera,
                    "label": label,
                    "score": score,
                    "deviation_score": deviation_score,
                    "baseline_confidence": baseline_confidence,
                }
            )
    except Exception as e:
        log.warning("[frigate_responder] _maybe_notify_away failed: %s", e)
