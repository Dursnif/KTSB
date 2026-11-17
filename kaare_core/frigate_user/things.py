"""What stands where now — the stationary gate's knowledge as code facts for Kåre (run-queue J8 d, 2026-11-06:
«den koden som ser pikselendringer og som vet dunken er der, burde si det til KTSB et sted»).

Every event the gate judged carries `check.gate = {verdict, key: camera|label|col,row, thing_id}`. Grouped by key, the
newest verdicts say whether a thing still stands unchanged (and since when) or changed last. Facts only — no model, no
image; for how something looks right now the camera tool's snapshot is the source.
"""

from __future__ import annotations

import time
from datetime import datetime

from kaare_core.config import get_local_tz
from kaare_core.frigate_user import labels, query, store
from kaare_core.tools.i18n import t

WINDOW_H = 48


def _where(col: int, row: int, lang: str) -> str:
    h = "left" if col <= 3 else ("right" if col >= 6 else "mid")
    v = "top" if row <= 3 else ("bottom" if row >= 7 else "mid")
    return t(f"ft_pos_{v}_{h}", lang)


def things(now: float | None = None) -> list[dict]:
    """[{key, camera, label, col, row, verdict, last_ts, since_ts}] newest first. `since_ts` = start of the current run
    of «unchanged» verdicts (None when the newest verdict is not «unchanged»)."""
    now = time.time() if now is None else now
    try:
        evs = store.events_between(now - WINDOW_H * 3600, now)
    except Exception:
        return []
    by_key: dict[str, list[tuple[float, str]]] = {}
    for ev in evs:
        gate = (ev.check or {}).get("gate")
        if isinstance(gate, dict) and gate.get("key") and gate.get("verdict") in ("unchanged", "changed"):
            by_key.setdefault(gate["key"], []).append((ev.start_ts, gate["verdict"]))
    out = []
    for key, rows in by_key.items():
        rows.sort()
        camera, label, place = key.split("|")
        col, row = (int(x) for x in place.split(","))
        last_ts, verdict = rows[-1]
        since = None
        if verdict == "unchanged":
            since = last_ts
            for ts, v in reversed(rows):
                if v != "unchanged":
                    break
                since = ts
        out.append({"key": key, "camera": camera, "label": label, "col": col, "row": row, "verdict": verdict,
                    "last_ts": last_ts, "since_ts": since})
    out.sort(key=lambda x: -x["last_ts"])
    return out


def text(lang: str, now: float | None = None) -> str:
    rows = things(now)
    if not rows:
        return t("ft_none", lang, hours=WINDOW_H)
    cards = query._camera_cards()
    tz = get_local_tz()

    def stamp(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz).strftime("%d.%m %H:%M")

    lines = [t("ft_header", lang, hours=WINDOW_H)]
    for r in rows:
        cam = query._camera_text(r["camera"], None, cards, lang)
        what = labels.display(r["label"], lang)
        where = _where(r["col"], r["row"], lang)
        if r["verdict"] == "unchanged":
            lines.append(t("ft_unchanged", lang, what=what, camera=cam, where=where, since=stamp(r["since_ts"]),
                           last=stamp(r["last_ts"])))
        else:
            lines.append(t("ft_changed", lang, what=what, camera=cam, where=where, last=stamp(r["last_ts"])))
    lines.append(t("ft_source", lang))
    return "\n".join(lines)
