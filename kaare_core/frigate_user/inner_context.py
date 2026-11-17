"""What the inner voices may know about the people the cameras saw (J21b reader 3: Jing reads frigate.db, not Argus'
face_events.txt).

One line per person event since `since`, oldest first: time, who, kind, camera display name and place. «Who» is a stable
pseudonym id (`pseudonyms.pid`), never a name; a person who opted out (untracked, or hidden since) and anyone unnamed is
only «a person» — no id, and no kind for the opted-out. Read only; [] when module 0 holds nothing in the window.
"""

from __future__ import annotations

import time
from datetime import datetime

from adapters import frigate_adapter
from kaare_core.config import get_local_tz
from kaare_core.domain import frigate_store as camera_cards
from kaare_core.frigate_user import pseudonyms, query, store
from kaare_core.tools.i18n import t

MAX_LINES = 40  # one Jing interval is minutes; a busy yard must not flood a 0.6B prompt


def people(since: float, lang: str, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    evs = [e for e in store.events_between(since, now) if e.label == "person"]
    hidden = {n.casefold() for n in store.hidden_names({e.person_ref for e in evs if e.person_ref})}
    try:
        cards = camera_cards.camera_config().get("cameras") or {}
    except Exception:
        cards = {}
    tz = get_local_tz()
    lines = []
    for e in evs[-MAX_LINES:]:
        opted_out = e.person_kind == "untracked" or bool(e.person_ref and e.person_ref.casefold() in hidden)
        if e.person_ref and not opted_out:
            who, kind = pseudonyms.pid(e.person_ref), f" ({query._kind(e.person_kind, lang)})"
        else:
            who, kind = t("iv_a_person", lang), "" if opted_out else f" ({t('iv_unknown', lang)})"
        where = frigate_adapter.camera_display_name(e.camera, cards, fetch=False) + (f" ({e.place})" if e.place else "")
        lines.append(t("iv_person_line", lang, time=datetime.fromtimestamp(e.start_ts, tz).strftime("%H:%M"),
                       who=who, kind=kind, where=where))
    return lines
