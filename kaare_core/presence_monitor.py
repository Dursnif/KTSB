"""
Background task: auto-detects household return from away mode.

Polls 3 signals every 5 minutes when in away mode:
  1. Date — expected_return date reached
  2. HA geo tracker — person.* entity state == "home"
  3. HA local network — device_tracker.* source_type == "router" or state == "home"

Confirmed home = 2+ signals agree on 2 consecutive polls.
Switches household_state to home and fires trigger cleanup in profiles.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("presence_monitor")

_POLL_INTERVAL_AWAY = 5 * 60   # 5 minutes when away
_CONFIRM_POLLS = 2              # require this many consecutive positive polls before switching
_HA_TIMEOUT = 5.0

_consecutive_positive = 0


def _check_date_signal(expected_return: str | None) -> bool:
    """Signal 1: expected return date has arrived."""
    if not expected_return:
        return False
    try:
        return datetime.now().strftime("%Y-%m-%d") >= expected_return
    except Exception:
        return False


def _ha_state(entity_id: str) -> str | None:
    """Fetch HA entity state synchronously. Returns state string or None on error."""
    try:
        from kaare_ha_gateway import ha_get, HA_API_URL, HA_TOKEN
        data = ha_get(f"/api/states/{entity_id}", timeout=_HA_TIMEOUT)
        return (data or {}).get("state")
    except Exception:
        return None


def _ha_attributes(entity_id: str) -> dict:
    """Fetch HA entity attributes. Returns empty dict on error."""
    try:
        from kaare_ha_gateway import ha_get
        data = ha_get(f"/api/states/{entity_id}", timeout=_HA_TIMEOUT)
        return (data or {}).get("attributes", {})
    except Exception:
        return {}


def _collect_signals_for_user(user_profile: dict) -> list[bool]:
    """Return list of signal booleans for one user based on their ha_entities + presence_consent."""
    signals = []
    ha_entities = user_profile.get("ha_entities") or {}
    consent = user_profile.get("presence_consent") or {}

    geo_entity = ha_entities.get("geo")
    local_entity = ha_entities.get("local")

    if geo_entity and consent.get("geo_tracking", False):
        state = _ha_state(geo_entity)
        signals.append(state == "home")

    if local_entity and consent.get("local_presence", True):
        state = _ha_state(local_entity)
        attrs = _ha_attributes(local_entity)
        is_home = state == "home" or attrs.get("source_type") == "router"
        signals.append(is_home)

    return signals


def _count_positive_signals(state: dict) -> int:
    """Count how many unique signals indicate 'home' across all monitored users."""
    from kaare_core.users.store import list_users
    from kaare_core.users.profile_manager import load_profile

    positive = 0

    date_signal = _check_date_signal(state.get("expected_return"))
    if date_signal:
        positive += 1
        logger.debug("[presence] date signal: home (expected_return reached)")

    try:
        for u in list_users():
            if u.get("role") not in ("admin", "adult"):
                continue
            profile = load_profile(u["username"])
            user_signals = _collect_signals_for_user(profile)
            if any(user_signals):
                positive += 1
                logger.debug("[presence] HA signal: home for user %s", u["username"])
                break  # one confirmed HA user is enough
    except Exception as e:
        logger.debug("[presence] HA signal check failed: %s", e)

    return positive


async def _poll_once() -> bool:
    """Run one poll cycle. Returns True if confirmed home (2+ signals, 2 consecutive)."""
    global _consecutive_positive
    from kaare_core.tools.household_state import read_household_state, set_home

    state = read_household_state()
    if state.get("mode") != "away":
        _consecutive_positive = 0
        return False

    positive = await asyncio.get_event_loop().run_in_executor(
        None, _count_positive_signals, state
    )

    if positive >= 2:
        _consecutive_positive += 1
        logger.info("[presence] %d/2 positive signals (%d/%d consecutive)",
                    positive, _consecutive_positive, _CONFIRM_POLLS)
    else:
        if _consecutive_positive > 0:
            logger.debug("[presence] signal lost — resetting consecutive counter")
        _consecutive_positive = 0

    if _consecutive_positive >= _CONFIRM_POLLS:
        logger.info("[presence] Confirmed home — switching household mode to home")
        set_home()
        _consecutive_positive = 0
        return True

    return False


async def start_presence_monitor() -> None:
    """Long-running background task. No-ops when household is home."""
    logger.info("[presence] Presence monitor started (poll interval: %ds when away)", _POLL_INTERVAL_AWAY)
    while True:
        try:
            from kaare_core.tools.household_state import is_away
            if is_away():
                confirmed = await _poll_once()
                if confirmed:
                    logger.info("[presence] Household returned home — monitor idling")
        except Exception as e:
            logger.warning("[presence] Poll error: %s", e)
        await asyncio.sleep(_POLL_INTERVAL_AWAY)
