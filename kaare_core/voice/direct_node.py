"""KTSB Direct — the machine KTSB runs on, as a node (P138 Decision 6).

Every install has one: the microphone and speakers of the machine the voice bridge runs on. Before this node existed
the local mic recorded for any `/button/<name>`, with no place, default user, trigger or audio out of its own.

Exactly one direct node, id `direct`, always present: readers add it when nodes.yaml lacks it, a GUI save cannot
delete it or change its type (it can be disabled). Its speaker is LOCAL in output routing. Pure functions, no I/O.
"""

import copy

DIRECT_ID = "direct"
DIRECT_TYPE = "direct"

_DEFAULT = {
    "type": DIRECT_TYPE,
    "description": "",
    "room": "",
    "enabled": True,
    "mic_enabled": True,
    "has_audio": True,
    "has_display": False,
    "default_user": "",
    "trust": "indoor",
}


def is_direct(node) -> bool:
    return isinstance(node, dict) and node.get("type") == DIRECT_TYPE


def direct_id(nodes: dict | None) -> str:
    """The id of the install's direct node: the first node of type `direct`, else DIRECT_ID."""
    for nid, node in (nodes or {}).items():
        if is_direct(node):
            return nid
    return DIRECT_ID


def with_direct(nodes: dict | None) -> dict:
    """`nodes` with the direct node added when it has none (a new dict; the input is not changed)."""
    out = dict(nodes or {})
    if any(is_direct(n) for n in out.values()):
        return out
    if isinstance(out.get(DIRECT_ID), dict):
        # A user node already called «direct» becomes the direct node rather than being shadowed.
        out[DIRECT_ID] = {**copy.deepcopy(_DEFAULT), **out[DIRECT_ID], "type": DIRECT_TYPE}
    else:
        out[DIRECT_ID] = copy.deepcopy(_DEFAULT)
    return out


def keep_direct(old: dict | None, new: dict | None) -> dict:
    """A GUI save of the nodes: the direct node is kept (deleting it restores the saved one) and stays type direct;
    a second node set to type direct is refused by returning it unchanged from `old` (or dropped when new)."""
    old = with_direct(old)
    out = dict(new or {})
    old_id = direct_id(old)
    for nid in [n for n, cfg in out.items() if is_direct(cfg) and n != old_id]:
        if nid in old:
            out[nid] = old[nid]
        else:
            del out[nid]
    if not isinstance(out.get(old_id), dict):
        out[old_id] = copy.deepcopy(old[old_id])
    else:
        out[old_id] = {**out[old_id], "type": DIRECT_TYPE}
    return out
