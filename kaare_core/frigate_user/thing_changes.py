"""What changed where a still thing stood — the change detector's regions joined with the stationary gate's keys
(P144, run-queue J15; ep 266: the bins were moved, the pixels changed on two cameras, the 27B got only a position).

A change region (before vs after the episode) that overlaps where a non-living stationary object stood before the episode
(its J3 key on that camera) gets `thing_before = <label>`; one that overlaps where such an object stands after the
episode (a key that was not there before) gets `thing_after`. Labels seen this way on several cameras → `thing_cameras`;
a thing that left one place while a new key with the same label appeared on another camera → `thing_elsewhere`. These
are code facts for the 27B (which may see labels); the 9B's crop words are never told the label. Pure; no IO.
"""

from __future__ import annotations

from kaare_core.frigate_user import stationary_gate, store

PAD_CELLS = 1  # a key is a cell on a 10 × 10 grid; one cell of slack each side


def _cell_rect(key: str) -> tuple[str, str, tuple[float, float, float, float]] | None:
    try:
        camera, label, place = key.split("|")
        col, row = (int(x) for x in place.split(","))
    except (ValueError, AttributeError):
        return None
    g = stationary_gate.KEY_GRID
    return camera, label, ((col - PAD_CELLS) / g, (row - PAD_CELLS) / g, (col + 1 + PAD_CELLS) / g, (row + 1 + PAD_CELLS) / g)


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _keys(events: list[store.FrigateUserEvent]) -> set[str]:
    out = set()
    for ev in events:
        g = (ev.check or {}).get("gate") if isinstance(ev.check, dict) else None
        if isinstance(g, dict) and g.get("key") and stationary_gate.hold_unknown(ev.label):
            out.add(g["key"])
    return out


def annotate(regions: list[dict], before: list[store.FrigateUserEvent], after: list[store.FrigateUserEvent]) -> None:
    """Add thing facts to change regions in place. `before`/`after`: events before the episode / after its end."""
    k_before = _keys(before)
    k_after = _keys(after) - k_before
    rects_before = [r for k in k_before if (r := _cell_rect(k))]
    rects_after = [r for k in k_after if (r := _cell_rect(k))]
    for reg in regions:
        box = tuple(reg.get("box") or ())
        if len(box) != 4:
            continue
        for cam, label, rect in rects_before:
            if cam == reg.get("camera") and _overlaps(box, rect):
                reg["thing_before"] = label
                break
        for cam, label, rect in rects_after:
            if cam == reg.get("camera") and _overlaps(box, rect):
                reg["thing_after"] = label
                break
    labels: dict[str, set[str]] = {}
    for reg in regions:
        for key in ("thing_before", "thing_after"):
            if reg.get(key):
                labels.setdefault(reg[key], set()).add(reg.get("camera"))
    for reg in regions:
        label = reg.get("thing_before") or reg.get("thing_after")
        if not label:
            continue
        reg["thing_cameras"] = len(labels.get(label, ()))
        if reg.get("thing_before"):
            other = sorted({cam for cam, lab, _r in rects_after if lab == label and cam != reg.get("camera")})
            if other:
                reg["thing_elsewhere"] = other[0]
