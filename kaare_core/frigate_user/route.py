"""The code route (K) for one episode (P144-s06d; ported from s09 `k_text.py`): what Frigate's data says exactly,
with no model.

Camera order with local clock times, how far each camera's object moved in the image, the direction relative to the
front door (the only camera role that says where a camera is — set on the camera cards in Integrations → Frigate;
`road_facing` says what a camera *sees*: this house's hangs on the house wall), objects that only stood still, and the time of day from the clock — never from a model (s09:
no model said «morning» at 07:16; «dusk/dawn» made the 9B write «at dusk»). Face claims are never in this text: who
someone is stays the code's claim check + storage gate (s06d Part 0: the 9B repeated names it was given).
s09 measured K alone: 0 invented, camera order 10/10, direction 10.5/13; it cannot tell «out on the porch and back in».
`blind=True` (the 9B's see call): every Frigate label is written as «object» — a label is a claim like a name. s14:
the 9B repeated «deer» on a false detection 3/3 when it was in this text; s16 (s15 measured): person and vehicle labels
too, with no per-label count — the route without labels scored 82 % against 76 % without a route (s15 Q2), with them
88 % but the 9B echoed Frigate's names. The stored route (27B, GUI) keeps the labels.
"""

from datetime import datetime

from adapters import frigate_adapter
from kaare_core.config import get_local_tz
from kaare_core.frigate_user import store

# At the front door (0) or somewhere else outside (1). A finer order (door → driveway → road) needs each camera's real
# position — a P137 place question, not the role (2026-10-27: ranking road_facing as «at the road» turned an arrival
# into «leaving» on this house). Indoor cameras give no direction.
ROLE_RANK = {"front_door": 0}
NO_RANK_ROLES = ("indoor",)

_TEXT: dict[str, dict[str, str]] = {
    "episode": {
        "nb": "{weekday} {date} kl. {start}–{end} lokal tid ({secs} s), {part}.",
        "en": "{weekday} {date} {start}–{end} local time ({secs} s), {part}.",
        "de": "{weekday} {date} {start}–{end} Ortszeit ({secs} s), {part}.",
    },
    "objects": {
        "nb": "{n} Frigate-hendelser ({objects}) — {one} sett av flere kameraer gir flere hendelser; høyst {k} "
        "{what} samtidig på ett kamera.",
        "en": "{n} Frigate events ({objects}) — {one} seen by several cameras gives several events; at most {k} "
        "{what} at the same time on one camera.",
        "de": "{n} Frigate-Ereignisse ({objects}) — {one} auf mehreren Kameras ergibt mehrere Ereignisse; "
        "höchstens {k} {what} gleichzeitig auf einer Kamera.",
    },
    # blind (the 9B): no per-label count, no «person» (s15 point 6: the template said «one person» even for a deer)
    "objects_blind": {
        "nb": "{n} Frigate-hendelser — ett objekt sett av flere kameraer gir flere hendelser; høyst {k} objekt "
        "samtidig på ett kamera.",
        "en": "{n} Frigate events — one object seen by several cameras gives several events; at most {k} at the same "
        "time on one camera.",
        "de": "{n} Frigate-Ereignisse — ein Objekt auf mehreren Kameras ergibt mehrere Ereignisse; höchstens {k} "
        "gleichzeitig auf einer Kamera.",
    },
    "one_person": {"nb": "én person", "en": "one person", "de": "eine Person"},
    "one_object": {"nb": "ett objekt", "en": "one object", "de": "ein Objekt"},
    "what_person": {"nb": "person", "en": "person", "de": "Person"},
    "what_object": {"nb": "objekt", "en": "object", "de": "Objekt"},
    "order": {"nb": "Kamerarekkefølge: {order}.", "en": "Camera order: {order}.", "de": "Kamerareihenfolge: {order}."},
    "in_image": {"nb": "I bildet: {moves}.", "en": "In the image: {moves}.", "de": "Im Bild: {moves}."},
    "moved": {
        "nb": "{camera} flyttet seg {pct} % av bildet, {dir}",
        "en": "{camera} moved {pct} % of the frame, {dir}",
        "de": "{camera} bewegte sich {pct} % des Bildes, {dir}",
    },
    "dir_lr": {"nb": "fra venstre mot høyre", "en": "from left to right", "de": "von links nach rechts"},
    "dir_rl": {"nb": "fra høyre mot venstre", "en": "from right to left", "de": "von rechts nach links"},
    "dir_down": {"nb": "nedover i bildet", "en": "down the image", "de": "im Bild nach unten"},
    "dir_up": {"nb": "oppover i bildet", "en": "up the image", "de": "im Bild nach oben"},
    "vehicles": {
        "nb": "Kjøretøy som kjørte: {vehicles}.",
        "en": "Vehicles that moved: {vehicles}.",
        "de": "Fahrzeuge, die fuhren: {vehicles}.",
    },
    "vehicles_blind": {
        "nb": "Andre objekter som beveget seg: {vehicles}.",
        "en": "Other objects that moved: {vehicles}.",
        "de": "Andere Objekte, die sich bewegten: {vehicles}.",
    },
    "still": {
        "nb": "Sto stille (ikke regnet som aktivitet): {still}.",
        "en": "Standing still (not counted as activity): {still}.",
        "de": "Stand still (nicht als Aktivität gezählt): {still}.",
    },
    "leaving": {
        "nb": "Retning etter kamerarollene: fra inngangsdøra og ut på eiendommen (drar).",
        "en": "Direction from the camera roles: from the front door out onto the property (leaving).",
        "de": "Richtung laut Kamerarollen: von der Haustür hinaus aufs Grundstück (geht weg).",
    },
    "arriving": {
        "nb": "Retning etter kamerarollene: fra eiendommen inn til inngangsdøra (kommer).",
        "en": "Direction from the camera roles: from the property to the front door (arriving).",
        "de": "Richtung laut Kamerarollen: vom Grundstück zur Haustür (kommt an).",
    },
    "out_back": {
        "nb": "Retning etter kamerarollene: ut fra inngangsdøra og tilbake dit igjen.",
        "en": "Direction from the camera roles: out from the front door and back to it.",
        "de": "Richtung laut Kamerarollen: von der Haustür hinaus und wieder zurück.",
    },
    "door_only": {
        "nb": "Retning etter kamerarollene: bare ved inngangsdøra — koden kan ikke se om noen kom ut eller gikk inn.",
        "en": "Direction from the camera roles: only at the front door — code cannot tell whether someone came out "
        "or went in.",
        "de": "Richtung laut Kamerarollen: nur an der Haustür — der Code erkennt nicht, ob jemand heraus- oder "
        "hineinging.",
    },
    "no_door": {
        "nb": "Retning: inngangsdøra er ikke med — koden kan ikke se om noen kom eller dro.",
        "en": "Direction: the front door is not in it — code cannot tell whether someone came or went.",
        "de": "Richtung: die Haustür ist nicht dabei — der Code erkennt nicht, ob jemand kam oder ging.",
    },
    "role_front_door": {"nb": "inngangskamera", "en": "front door camera", "de": "Haustürkamera"},
    "role_driveway": {"nb": "innkjørselkamera", "en": "driveway camera", "de": "Einfahrtkamera"},
    "role_road_facing": {"nb": "kamera mot veien", "en": "road-facing camera", "de": "Kamera zur Straße"},
    "role_garden": {"nb": "hagekamera", "en": "garden camera", "de": "Gartenkamera"},
    "role_garage": {"nb": "garasjekamera", "en": "garage camera", "de": "Garagenkamera"},
    "role_indoor": {"nb": "innendørskamera", "en": "indoor camera", "de": "Innenkamera"},
    "part_night": {"nb": "natt", "en": "night", "de": "Nacht"},
    "part_morning": {"nb": "morgen", "en": "morning", "de": "Morgen"},
    "part_day": {"nb": "dag", "en": "daytime", "de": "Tag"},
    "part_evening": {"nb": "kveld", "en": "evening", "de": "Abend"},
    # the code summary for a vehicle-only episode (s06e: no model sees these — 30 of 40 episodes on shadow day 1)
    "veh_summary": {
        "nb": "Jeg så bare kjøretøy kl. {start}–{end} ({cameras}): {parts} (Frigate-hendelser — ett kjøretøy kan gi "
        "flere). Ingen personer eller dyr.",
        "en": "I saw only vehicles {start}–{end} ({cameras}): {parts} (Frigate events — one vehicle can give "
        "several). No people or animals.",
        "de": "Ich sah nur Fahrzeuge {start}–{end} Uhr ({cameras}): {parts} (Frigate-Ereignisse — ein Fahrzeug kann "
        "mehrere ergeben). Keine Personen oder Tiere.",
    },
    "veh_moving": {"nb": "{n} × {what} i bevegelse", "en": "{n} × {what} moving", "de": "{n} × {what} in Bewegung"},
    "veh_still": {"nb": "{n} × {what} som sto stille", "en": "{n} × {what} standing still",
                  "de": "{n} × {what} stehend"},
    "veh_car": {"nb": "bil", "en": "car", "de": "Auto"},
    "veh_truck": {"nb": "lastebil", "en": "truck", "de": "Lkw"},
    "veh_bus": {"nb": "buss", "en": "bus", "de": "Bus"},  # codespell:ignore buss
    "veh_motorcycle": {"nb": "motorsykkel", "en": "motorcycle", "de": "Motorrad"},
    "veh_bicycle": {"nb": "sykkel", "en": "bicycle", "de": "Fahrrad"},
    "veh_boat": {"nb": "båt", "en": "boat", "de": "Boot"},
    "veh_license_plate": {"nb": "skilt", "en": "licence plate", "de": "Kennzeichen"},
    "weekdays": {
        "nb": "mandag,tirsdag,onsdag,torsdag,fredag,lørdag,søndag",
        "en": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "de": "Montag,Dienstag,Mittwoch,Donnerstag,Freitag,Samstag,Sonntag",
    },
}


def _t(key: str, lang: str, **kw) -> str:
    s = _TEXT[key].get(lang) or _TEXT[key]["en"]
    return s.format(**kw) if kw else s


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, get_local_tz())


def clock(ts: float) -> str:
    """HH:MM:SS in the household's time zone."""
    return _local(ts).strftime("%H:%M:%S")


def part_of_day(ts: float, lang: str) -> str:
    """Morning/day/evening/night from the household's clock — unambiguous (never «dusk/dawn»)."""
    h = _local(ts).hour
    return _t("part_night" if h < 6 else "part_morning" if h < 10 else "part_day" if h < 18 else "part_evening", lang)


def camera_name(camera: str, card: dict | None, place: str | None, lang: str) -> str:
    """«<role meaning> «<display name>» (place)» — the meaning the model needs, from the camera card + P137 place."""
    card = card or {}
    role = card.get("role") or ""
    shown = frigate_adapter.camera_display_name(camera, {camera: card}, fetch=False)  # J24: never a raw id when a name exists
    meaning = _t(f"role_{role}", lang) if f"role_{role}" in _TEXT else ""
    name = f"{meaning} «{shown}»" if meaning else f"«{shown}»"
    return name + (f" ({place})" if place else "")


def _window(e: store.FrigateUserEvent) -> tuple[float, float]:
    a0 = e.active_start if e.active_start is not None else e.start_ts
    a1 = e.active_end if e.active_end is not None else (e.end_ts if e.end_ts is not None else a0)
    return a0, max(a0, a1)


def _max_concurrent(events: list[store.FrigateUserEvent]) -> int:
    best = 0
    for cam in {e.camera for e in events}:
        marks = sorted([(_window(e)[0], 1) for e in events if e.camera == cam]
                       + [(_window(e)[1], -1) for e in events if e.camera == cam])
        n = 0
        for _, d in marks:
            n += d
            best = max(best, n)
    return best


def _image_move(e: store.FrigateUserEvent, lang: str) -> tuple[float, str] | None:
    pts = e.path or []
    if len(pts) < 2:
        return None
    dx, dy = pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    spread = max(max(xs) - min(xs), max(ys) - min(ys))
    if abs(dx) >= abs(dy):
        way = _t("dir_lr" if dx > 0 else "dir_rl", lang)
    else:
        way = _t("dir_down" if dy > 0 else "dir_up", lang)
    return spread, way


def route_text(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str, *, cameras: dict,
               blind: bool = False) -> str:
    """The episode's route in plain words. `cameras` = the camera cards (`frigate_cameras.yaml` `cameras:`).
    `blind`: every Frigate label hidden (the 9B's see call)."""

    def shown(label: str) -> str:
        return _t("what_object", lang) if blind else label

    events = sorted(events, key=lambda e: _window(e)[0])
    moved = [e for e in events if e.drives or e.path]  # an ignored camera role still shows where someone went
    movers = [e for e in moved if e.label not in store.VEHICLE_LABELS] or moved or events
    place = {e.camera: e.place for e in events if e.place}

    def name(cam: str) -> str:
        return camera_name(cam, cameras.get(cam), place.get(cam), lang)

    start, end = ep.start_ts, ep.end_ts
    s = _local(start)
    lines = [_t("episode", lang, weekday=_t("weekdays", lang).split(",")[s.weekday()], date=s.strftime("%Y-%m-%d"),
                start=s.strftime("%H:%M:%S"), end=_local(end).strftime("%H:%M:%S"), secs=round(end - start),
                part=part_of_day(start, lang))]

    counted = moved or events
    counts: dict[str, int] = {}
    for e in counted:
        counts[shown(e.label)] = counts.get(shown(e.label), 0) + 1
    persons = [e for e in movers if e.label == "person"]
    most = _max_concurrent(persons or movers)
    if blind:
        lines.append(_t("objects_blind", lang, n=len(counted), k=most))
    else:
        lines.append(_t("objects", lang, n=len(counted), objects=", ".join(f"{k}: {v}" for k, v in counts.items()),
                        one=_t("one_person" if persons else "one_object", lang),
                        k=most, what=_t("what_person" if persons else "what_object", lang)))

    seq: list[list] = []
    for e in movers:
        a0, a1 = _window(e)
        if seq and seq[-1][0] == e.camera:
            seq[-1][2] = max(seq[-1][2], a1)
        else:
            seq.append([e.camera, a0, a1])
    lines.append(_t("order", lang, order=" → ".join(
        f"{name(c)} {_local(a).strftime('%H:%M:%S')} ({round(b - a)} s)" for c, a, b in seq)))

    moves = []
    for cam in dict.fromkeys(e.camera for e in movers):
        best = max((m for m in (_image_move(e, lang) for e in movers if e.camera == cam) if m), default=None)
        if best:
            moves.append(_t("moved", lang, camera=name(cam), pct=round(best[0] * 100), dir=best[1]))
    if moves:
        lines.append(_t("in_image", lang, moves="; ".join(moves)))

    roles = [(cameras.get(c) or {}).get("role") for c, _, _ in seq]
    ranks = [ROLE_RANK.get(r, 1) for r in roles if r not in NO_RANK_ROLES]
    if ranks:
        if 0 not in ranks:
            lines.append(_t("no_door", lang))
        elif ranks[-1] > ranks[0]:
            lines.append(_t("leaving", lang))
        elif ranks[-1] < ranks[0]:
            lines.append(_t("arriving", lang))
        elif max(ranks) > ranks[0]:
            lines.append(_t("out_back", lang))
        else:
            lines.append(_t("door_only", lang))

    vehicles = [e for e in moved if e.label in store.VEHICLE_LABELS and e not in movers]
    if vehicles:
        lines.append(_t("vehicles_blind" if blind else "vehicles", lang, vehicles=", ".join(
            (f"{name(e.camera)} " if blind else f"{e.label} {name(e.camera)} ")
            + _local(_window(e)[0]).strftime('%H:%M:%S') for e in vehicles)))
    still: dict[tuple[str, str], int] = {}
    for e in events:
        if not e.drives and not e.path:
            still[(shown(e.label), e.camera)] = still.get((shown(e.label), e.camera), 0) + 1
    if still:
        lines.append(_t("still", lang, still=", ".join(f"{lab} {name(cam)} ×{n}" for (lab, cam), n in still.items())))
    return "\n".join(lines)


def vehicle_summary(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str, *, cameras: dict) -> str:
    """One first-person memory line for an episode that held only vehicles (s06e) — written by code, no model.
    Counts are Frigate events (one car on two cameras is two), «moving» = the store's episode rule saw it drive."""
    place = {e.camera: e.place for e in events if e.place}
    counts: dict[tuple[str, bool], int] = {}
    for e in events:
        counts[(e.label, bool(e.drives))] = counts.get((e.label, bool(e.drives)), 0) + 1
    parts = []
    for (label, moving), n in sorted(counts.items(), key=lambda kv: (not kv[0][1], kv[0][0])):
        what = _t(f"veh_{label}", lang) if f"veh_{label}" in _TEXT else label
        parts.append(_t("veh_moving" if moving else "veh_still", lang, n=n, what=what))
    cams = ", ".join(camera_name(c, cameras.get(c), place.get(c), lang) for c in dict.fromkeys(
        e.camera for e in sorted(events, key=lambda e: _window(e)[0])))
    return _t("veh_summary", lang, start=_local(ep.start_ts).strftime("%H:%M"), end=_local(ep.end_ts).strftime("%H:%M"),
              cameras=cams, parts=", ".join(parts))
