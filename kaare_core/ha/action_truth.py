"""What an HA action really did (N26) — pure rules, no I/O, shared by the gateway and its callers.

Home Assistant answers `POST /api/services/<domain>/<service>` with 200 and the list of states that changed during
the call — also for an entity that does not exist (it only logs «Referenced entities … are missing») and also when
the device reports its new state a moment after the reply (Tuya, Zigbee). So 200 proves nothing and an empty list
disproves nothing. The truth comes from the entity's state: looked up before (exists? available?) and after (did it
reach the state the action asks for?).
"""

CONFIRMED = "confirmed"  # the entity reached the asked-for state
ALREADY = "already"  # it was in that state before the call
ACCEPTED = "accepted"  # HA took the call; this action has no state we can check (e.g. a thermostat setpoint)
NOT_CONFIRMED = "not_confirmed"  # HA took the call, the new state was not reported within the wait
NOT_FOUND = "not_found"  # HA has no such entity — nothing was sent
UNAVAILABLE = "unavailable"  # the entity exists but is unavailable — nothing was sent
ERROR = "error"  # HA refused or failed the call
UNSUPPORTED = "unsupported"  # the entity does not offer this action (wrong domain or feature flag) — nothing was sent
SOURCE_UNKNOWN = "source_unknown"  # select_source named a source the player does not list — nothing was sent

# Outcomes that count as done (memory records them as a success).
DONE = frozenset({CONFIRMED, ALREADY, ACCEPTED})

_ON_OFF_DOMAINS = frozenset(
    {"light", "switch", "input_boolean", "fan", "automation", "siren", "humidifier", "remote"}
)


# Every action the gateway's explicit path knows (P139 phase 1 added the media-player ones). The GUI's «allowed
# actions» list is built from this.
LIGHT_ACTIONS = ("turn_on", "turn_off", "toggle", "set_level", "set_color_temp", "set_color")
MEDIA_ACTIONS = (
    "set_volume",
    "volume_up",
    "volume_down",
    "mute",
    "unmute",
    "select_source",
    "media_pause",
    "media_play",
    "media_stop",
)
GATEWAY_ACTIONS = (
    *LIGHT_ACTIONS,
    "open",
    "close",
    "set_temperature",
    "get_temperature",
    "get_state",
    *MEDIA_ACTIONS,
)
# What the GUI offered before it recorded what it offered: an allowed-actions list saved then cannot have left out an
# action that did not exist yet, so actions outside this set stay allowed for such a list.
LEGACY_OFFERED_ACTIONS = frozenset(
    {*LIGHT_ACTIONS, "open", "close", "set_temperature", "get_temperature", "get_state"}
)

# media_player action → (HA service, MediaPlayerEntityFeature flags of which one is needed). Flags verified against
# homeassistant/components/media_player/{const,services}.py (dev, 2026-10-19).
_PAUSE, _VOLUME_SET, _VOLUME_MUTE, _VOLUME_STEP, _SELECT_SOURCE, _STOP, _PLAY = 1, 4, 8, 1024, 2048, 4096, 16384
MEDIA_SERVICES: dict[str, tuple[str, tuple[int, ...]]] = {
    "set_volume": ("volume_set", (_VOLUME_SET,)),
    "volume_up": ("volume_up", (_VOLUME_SET, _VOLUME_STEP)),
    "volume_down": ("volume_down", (_VOLUME_SET, _VOLUME_STEP)),
    "mute": ("volume_mute", (_VOLUME_MUTE,)),
    "unmute": ("volume_mute", (_VOLUME_MUTE,)),
    "select_source": ("select_source", (_SELECT_SOURCE,)),
    "media_pause": ("media_pause", (_PAUSE,)),
    "media_play": ("media_play", (_PLAY,)),
    "media_stop": ("media_stop", (_STOP,)),
}
_PLAYER_ON = frozenset({"on", "idle", "playing", "paused", "buffering"})
_PLAYER_OFF = frozenset({"off", "standby"})
_VOLUME_TOLERANCE = 0.015  # players round the level (a Cast reports 0.3 as 0.30000001)
_STOPPED = frozenset({"idle", "off", "standby", "on"})


def allowed(action: str, allowed_list: set[str], offered: set[str] | None) -> bool:
    """The GUI's allow-list: empty = everything; otherwise an action is allowed when ticked, or when the GUI never
    offered it to the user who saved the list (a newer action they could not have left out)."""
    if not allowed_list or action in allowed_list:
        return True
    return action not in (offered if offered else LEGACY_OFFERED_ACTIONS)


def media_unsupported(action: str, entity_id: str, attributes: dict | None) -> bool:
    """True when the entity cannot do this media action: not a media_player, or its feature flags lack every flag the
    HA service accepts. A player that reports no flags at all is given the benefit of the doubt."""
    if domain_of(entity_id) != "media_player":
        return True
    flags = (attributes or {}).get("supported_features")
    if not isinstance(flags, int):
        return False
    return not any(flags & f for f in MEDIA_SERVICES[action][1])


def match_source(wanted: str, attributes: dict | None) -> str | None:
    """The player's own spelling of a source (case and spaces ignored), None when it lists no such source. A player
    that lists no sources at all gets the text as given."""
    sources = [str(s) for s in (attributes or {}).get("source_list") or []]
    if not sources:
        return wanted or None
    key = "".join(wanted.lower().split())
    for s in sources:
        if "".join(s.lower().split()) == key:
            return s
    return None


def _volume(attributes: dict | None) -> float | None:
    v = (attributes or {}).get("volume_level")
    return float(v) if isinstance(v, int | float) else None


def media_judge(action: str, params: dict, pre: dict | None, seen: dict | None) -> str | None:
    """Outcome of a media-player action from its state rows (`state` + `attributes`) before and after; None = keep
    watching. Volume, mute and source live in attributes, so the state string alone says nothing about them. Cast
    players hide the attributes while «off»: an attribute absent before and after means there is nothing to check
    (ACCEPTED), not that the call failed."""
    pre_a = (pre or {}).get("attributes") or {}
    now_a = (seen or {}).get("attributes") or {}
    now_state = (seen or {}).get("state")
    if action in ("set_volume", "volume_up", "volume_down"):
        before, after = _volume(pre_a), _volume(now_a)
        if action == "set_volume":
            target = float(params.get("volume_level", -1))
            if before is not None and abs(before - target) <= _VOLUME_TOLERANCE and seen is None:
                return ALREADY
            if after is not None and abs(after - target) <= _VOLUME_TOLERANCE:
                return CONFIRMED
        else:
            up = action == "volume_up"
            if before is not None and seen is None and (before >= 1.0 if up else before <= 0.0):
                return ALREADY
            if before is not None and after is not None and (after > before if up else after < before):
                return CONFIRMED
        if seen is not None and before is None and after is None:
            return ACCEPTED
        return None
    if action in ("mute", "unmute"):
        want = action == "mute"
        if seen is None:
            return ALREADY if pre_a.get("is_volume_muted") is want else None
        if now_a.get("is_volume_muted") is want:
            return CONFIRMED
        if "is_volume_muted" not in pre_a and "is_volume_muted" not in now_a:
            return ACCEPTED
        return None
    if action == "select_source":
        want = str(params.get("source") or "")
        if seen is None:
            return ALREADY if pre_a.get("source") == want else None
        return CONFIRMED if now_a.get("source") == want else None
    expected = {"media_pause": {"paused"}, "media_play": {"playing"}, "media_stop": _STOPPED}.get(action)
    if expected is None:
        return ACCEPTED
    if seen is None:
        return ALREADY if (pre or {}).get("state") in expected else None
    return CONFIRMED if now_state in expected else None


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def expected_states(action: str, entity_id: str, pre_state: str | None) -> frozenset[str] | None:
    """States that mean the action took effect, or None when there is nothing to check."""
    domain = domain_of(entity_id)
    if action in ("open", "close"):
        if domain != "cover":
            return None
        return (
            frozenset({"open", "opening"}) if action == "open" else frozenset({"closed", "closing"})
        )
    if domain == "media_player":
        # Any running state counts as on — a Cast device turned on via HA shows HA's own receiver as «paused».
        if action == "toggle":
            action = "turn_off" if pre_state in _PLAYER_ON else "turn_on" if pre_state in _PLAYER_OFF else ""
        if action == "turn_on":
            return _PLAYER_ON
        if action == "turn_off":
            return _PLAYER_OFF
        return None
    if domain not in _ON_OFF_DOMAINS:
        return None
    if action in ("turn_on", "set_level", "set_color_temp", "set_color"):
        return frozenset({"on"})
    if action == "turn_off":
        return frozenset({"off"})
    if action == "toggle" and pre_state in ("on", "off"):
        return frozenset({"off" if pre_state == "on" else "on"})
    return None


def changed_state_of(entity_id: str, changed: object) -> str | None:
    """The entity's new state in HA's reply (a list of states, or {"changed_states": [...]}), if present."""
    rows = changed.get("changed_states") if isinstance(changed, dict) else changed
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("entity_id") == entity_id:
            return str(row.get("state"))
    return None


def judge(
    action: str,
    expected: frozenset[str] | None,
    pre_state: str | None,
    seen_state: str | None,
) -> str | None:
    """Outcome from what is known so far; None = keep watching for the new state.

    A level/colour change on a light that was already on keeps it «on», so «already» is only said for plain
    on/off/open/close — there the call cannot have changed anything that matters."""
    if expected is None:
        return ACCEPTED
    if seen_state in expected:
        return CONFIRMED
    if pre_state in expected and action in ("turn_on", "turn_off", "open", "close"):
        return ALREADY
    if (
        pre_state in expected
    ):  # set_level & co. on a light that is already on: the level itself is not checked
        return ACCEPTED
    return None


def pre_check(status_code: int, state: str | None) -> str | None:
    """Before the call: NOT_FOUND / UNAVAILABLE stop it; None = go ahead."""
    if status_code == 404:
        return NOT_FOUND
    if state == "unavailable":
        return UNAVAILABLE
    return None
