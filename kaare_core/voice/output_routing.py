"""Where a voice reply goes (P138) — one routing rule for every input path.

A node can take the question on one device and give the answer on others: `audio_out` (speech) and `image_out`
(pictures/video, used by tools) in nodes.yaml, each `auto` or a list of node ids. `auto` keeps the behaviour an
install had before P138, so a node without these fields works as it always did.

Pure functions over the nodes dict — no I/O — so the voice bridge and the API process share them and tests can call
them directly. Two sentinels besides node ids: SELF (answer on the input device's own connection — a Wyoming
satellite) and LOCAL (the speakers of the machine running the voice bridge).
"""

from collections.abc import Callable

from kaare_core.voice.direct_node import is_direct

SELF = "self"
LOCAL = "local"
AUTO = "auto"

INPUT_WYOMING = "wyoming"
INPUT_BUTTON = "button"

PlaceOf = Callable[[str], str]


def _ids(value) -> list[str]:
    """`audio_out`/`image_out` as a list of ids; [] for auto, missing or empty."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, list | tuple):
        items = [str(v).strip() for v in value]
    else:
        return []
    items = [v for v in items if v]
    return [] if items in ([], [AUTO]) else items


def _enabled(node: dict) -> bool:
    return bool(node.get("enabled", True))


def has_audio(node: dict) -> bool:
    return bool(node.get("has_audio", True))


def has_display(node: dict) -> bool:
    if "has_display" in node:
        return bool(node["has_display"])
    return bool(node.get("is_tv", False))


def label(node_id: str, nodes: dict) -> str:
    """A name a user recognises: the node's description, else its id with spaces."""
    node = nodes.get(node_id) or {}
    return str(node.get("description") or "").strip() or node_id.replace("_", " ")


def _place_mates(node_id: str, nodes: dict, place_of: PlaceOf | None) -> list[str]:
    """Other enabled nodes in the same place (P137 register), else with the same free-text room."""
    node = nodes.get(node_id) or {}
    place = ""
    if place_of is not None:
        try:
            place = place_of(node_id) or ""
        except Exception:
            place = ""
    mates: list[str] = []
    for nid, ncfg in nodes.items():
        if nid == node_id or not isinstance(ncfg, dict) or not _enabled(ncfg):
            continue
        if place:
            try:
                same = (place_of(nid) if place_of else "") == place
            except Exception:
                same = False
        else:
            room = str(node.get("room") or "").strip().lower()
            same = bool(room) and str(ncfg.get("room") or "").strip().lower() == room
        if same:
            mates.append(nid)
    return mates


def audio_targets(
    node_id: str,
    nodes: dict,
    input_kind: str,
    playback_mode: str = LOCAL,
    place_of: PlaceOf | None = None,
) -> list[str]:
    """Where the spoken reply to a question from `node_id` is played.

    Explicit `audio_out`: those enabled nodes (the input node itself → SELF on a Wyoming satellite; LOCAL or the
    direct node → LOCAL).
    `auto`:
      - `tts_target: local` on the node, or the KTSB Direct node (this machine) → LOCAL;
      - Wyoming satellite with a speaker → SELF (as before); without one (`has_audio: false`) → the best speaker in
        the same place (audio-only before a display), else SELF;
      - button/wake word on the local mic → the global `bridge.playback_mode` (as before): `esp32` → the node,
        anything else → LOCAL.
    """
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return [SELF] if input_kind == INPUT_WYOMING else [LOCAL]

    explicit: list[str] = []
    for nid in _ids(node.get("audio_out")):
        if nid == LOCAL or is_direct(nodes.get(nid)):
            explicit.append(LOCAL)
        elif nid == node_id:
            explicit.append(SELF if input_kind == INPUT_WYOMING else node_id)
        elif isinstance(nodes.get(nid), dict) and _enabled(nodes[nid]):
            explicit.append(nid)
    if explicit:
        return list(dict.fromkeys(explicit))

    if str(node.get("tts_target") or "") == LOCAL or is_direct(node):
        return [LOCAL]
    if input_kind == INPUT_WYOMING:
        if has_audio(node):
            return [SELF]
        speakers = [n for n in _place_mates(node_id, nodes, place_of) if has_audio(nodes[n])]
        audio_only = [n for n in speakers if not has_display(nodes[n])]
        chosen = audio_only or speakers
        if not chosen:
            return [SELF]
        return [LOCAL] if is_direct(nodes[chosen[0]]) else [chosen[0]]
    return [node_id] if playback_mode == "esp32" else [LOCAL]


def image_targets(
    node_id: str,
    nodes: dict,
    display_ids: list[str],
    place_of: PlaceOf | None = None,
) -> list[str]:
    """Display nodes a tool should show something on when Kåre names no target (P138, image out through tools).

    Explicit `image_out`: those of them that are display nodes. `auto`: the input node itself if it is a display,
    else the displays in the same place. [] = nothing node-specific — the tool keeps its own default.
    """
    node = nodes.get(node_id)
    if not isinstance(node, dict):
        return []
    explicit = [nid for nid in _ids(node.get("image_out")) if nid in display_ids]
    if explicit:
        return list(dict.fromkeys(explicit))
    if node_id in display_ids:
        return [node_id]
    return [n for n in _place_mates(node_id, nodes, place_of) if n in display_ids]


def emergency_target(node_id: str, nodes: dict, input_kind: str) -> str | None:
    """Where to say «the output node does not answer»: the input device's own speaker (even when its audio_out
    points elsewhere — a poor speaker is still fine for one sentence), LOCAL for the local mic, None when the input
    device has no speaker (the notice then only reaches the reply text and the satellite's error light)."""
    node = nodes.get(node_id)
    if input_kind == INPUT_WYOMING:
        return SELF if isinstance(node, dict) and has_audio(node) else None
    return LOCAL
