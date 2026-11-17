"""The Frigate user's memory → Kåre's long-term memory (P144-s17): one LTM episode per finished household day.

The nightjob's per-user loop compresses `interactions` rows; the cameras have none. Their memory is frigate.db's
concluded episodes (the one truth — the Frigate STM is a 40-line RAM view of it), so this step reads those, nightly:

  - **What counts:** `stm.memory_lines()` — the same rule as the Frigate STM (model memory + done urgent results, never
    a likely false detection or a vehicle-only code line).
  - **Grouping:** one LTM episode per finished household day (`get_local_tz()`); today waits. A day with more than
    `MAX_LINES_PER_EPISODE` lines is split into equal parts in time order (measured 2026-10-30: ~74 tokens per line).
  - **Numbers from code:** a labelled table per day (episodes, urgent results, false detections and vehicle-only
    episodes left out, each named person with the s08 probability word, unnamed persons, animals). The model writes the
    narrative, never counts.
  - **Names:** only as the storage gate allowed, and the current consent is applied again here (fail closed: a failed
    lookup writes nothing tonight).
  - **Progress:** frigate.db `meta` (`store.ltm_marker()`): the last settled day + the episode ids already in LTM for
    the days after it. Moves only after the LTM episode is saved; a late-concluded episode becomes a small extra
    episode for its day, never skipped. A day settles once nothing in it can still conclude.
  - **Logs:** ids and counts only, never memory text.

The caller (kaare_nightjob) supplies the model call and the save — the LTM save path stays the nightjob's own.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from kaare_core.config import get_local_tz
from kaare_core.frigate_user import conclude, query, stm, store
from kaare_core.retention import engine

logger = logging.getLogger(__name__)

USER_ID = "frigate"
MAX_LINES_PER_EPISODE = 40  # ≈ 3 000 tokens of lines (74 tok/line measured) — far under the 27B's context
# conclude only looks for pending episodes 7 days back (`_PENDING_LOOKBACK_S`); one day more and nothing in a day can
# still conclude — it settles even if an episode never got a conclusion.
SETTLE_S = 8 * 86400
_CONFIDENCE = {"low": 0.4, "medium": 0.6, "high": 0.8}

_TEXT = {
    "header": {
        "nb": "Dette er kameraminnet mitt for {day} ({weekday}){part}.",
        "en": "This is my camera memory for {day} ({weekday}){part}.",
        "de": "Das ist meine Kamera-Erinnerung für den {day} ({weekday}){part}.",
    },
    "part": {"nb": ", del {n} av {of}", "en": ", part {n} of {of}", "de": ", Teil {n} von {of}"},
    "addition": {
        "nb": " — et tillegg: episoder som ble ferdig vurdert etter at resten av dagen var lagret",
        "en": " — an addition: episodes concluded after the rest of the day was stored",
        "de": " — ein Nachtrag: Episoden, die erst nach dem Rest des Tages fertig bewertet wurden",
    },
    "instructions": {
        "nb": "Skriv et kort sammendrag i første person og fortid, med min egen stemme, av hva kameraene mine så denne "
              "dagen. Bruk bare tabellen og minnelinjene under. Tallene er telt av kode — gjengi dem som de står, tell "
              "aldri selv, og nevn ikke rader som er 0. Behold navn og sannsynlighetsord (sannsynlig, usikkert …) nøyaktig som de står, og gjør "
              "aldri en usikker gjenkjenning sikrere. Ikke finn på personer, navn, kjøretøy eller hendelser. Høyst 8 "
              "setninger, ingen overskrift.",
        "en": "Write a short summary in the first person and past tense, in my own voice, of what my cameras saw that "
              "day. Use only the table and the memory lines below. The numbers were counted by code — repeat them as "
              "they stand, never count yourself, and leave out rows that are 0. Keep names and probability words (likely, uncertain …) exactly as "
              "written, and never make an uncertain recognition more certain. Do not invent people, names, vehicles "
              "or events. At most 8 sentences, no heading.",
        "de": "Schreib eine kurze Zusammenfassung in der ersten Person und Vergangenheit, mit meiner eigenen Stimme, "
              "von dem, was meine Kameras an diesem Tag gesehen haben. Verwende nur die Tabelle und die "
              "Erinnerungszeilen unten. Die Zahlen hat Code gezählt — gib sie so wieder, wie sie stehen, zähle nie "
              "selbst, und lass Zeilen mit 0 weg. Behalte Namen und Wahrscheinlichkeitswörter (wahrscheinlich, unsicher …) genau so bei, und "
              "mach eine unsichere Erkennung nie sicherer. Erfinde keine Personen, Namen, Fahrzeuge oder Ereignisse. "
              "Höchstens 8 Sätze, keine Überschrift.",
    },
    # P144-s24 («noen dager, eller en time senere så kan noe i minnet gi oppklaring, annerledes konklusjon»)
    "correction": {
        "nb": "Rettelse: episode {ids} ble vurdert på nytt etter at dagen var lagret. Den nye minnelinja erstatter det "
              "som ble skrevet om den før — si at det er en rettelse.",
        "en": "Correction: episode {ids} was assessed again after the day was stored. The new memory line replaces what "
              "was written about it before — say that it is a correction.",
        "de": "Korrektur: Episode {ids} wurde neu bewertet, nachdem der Tag gespeichert war. Die neue "
              "Erinnerungszeile ersetzt, was vorher darüber stand — sag, dass es eine Korrektur ist.",
    },
    "part_rule": {
        "nb": "Dagen er delt: beskriv bare denne delen og bruk kolonnen «denne delen» — «hele dagen» er bare "
              "bakgrunn.",
        "en": "The day is split: describe only this part and use the column «this part» — «whole day» is only "
              "background.",
        "de": "Der Tag ist geteilt: beschreib nur diesen Teil und verwende die Spalte «dieser Teil» — «ganzer Tag» "
              "ist nur Hintergrund.",
    },
    "sep_table": {"nb": "--- Tall (telt av kode) ---", "en": "--- Numbers (counted by code) ---",
                  "de": "--- Zahlen (von Code gezählt) ---"},
    "sep_lines": {"nb": "--- Minnelinjer ---", "en": "--- Memory lines ---", "de": "--- Erinnerungszeilen ---"},
    "col_what": {"nb": "", "en": "", "de": ""},
    "col_day": {"nb": "hele dagen", "en": "whole day", "de": "ganzer Tag"},
    "col_part": {"nb": "denne delen", "en": "this part", "de": "dieser Teil"},
    "row_episodes": {"nb": "episoder i minnet", "en": "episodes in memory", "de": "Episoden in der Erinnerung"},
    "row_urgent": {"nb": "hasteresultater", "en": "urgent results", "de": "dringende Ergebnisse"},
    "row_false": {"nb": "trolige feildeteksjoner (ikke i minnelinjene)",
                  "en": "likely false detections (not in the memory lines)",
                  "de": "wahrscheinliche Fehlerkennungen (nicht in den Erinnerungszeilen)"},
    "row_vehicles": {"nb": "episoder med bare kjøretøy (ikke i minnelinjene)",
                     "en": "vehicle-only episodes (not in the memory lines)",
                     "de": "Episoden nur mit Fahrzeugen (nicht in den Erinnerungszeilen)"},
    "row_person": {"nb": "{name} ({word}) — episoder", "en": "{name} ({word}) — episodes",
                   "de": "{name} ({word}) — Episoden"},
    "row_unnamed": {"nb": "ukjent person — episoder", "en": "unknown person — episodes",
                    "de": "unbekannte Person — Episoden"},
    "row_animal": {"nb": "{label} (Frigate-etikett) — episoder", "en": "{label} (Frigate label) — episodes",
                   "de": "{label} (Frigate-Label) — Episoden"},
}
_WEEKDAYS = {
    "nb": ("mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
}


def _t(key: str, lang: str, **kw) -> str:
    table = _TEXT[key]
    return (table.get(lang) or table["en"]).format(**kw)


@dataclass
class Batch:
    day: str
    part: int
    parts: int
    episode_ids: list[int]
    lines: list[str]  # scrubbed, oldest first
    table: str
    labels: list[str]
    confidence: float
    addition: bool = False  # the day already has LTM episodes — these concluded later
    corrected: list[int] = field(default_factory=list)  # P144-s24: analysed again after the day went into LTM
    hidden: set[str] = field(default_factory=set)

    @property
    def topics(self) -> str:
        return ", ".join([USER_ID, self.day, *self.labels])


# ── days ────────────────────────────────────────────────────────────────────────


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts, get_local_tz()).date().isoformat()


def days_to_scan(marker: dict, now: float) -> list[str]:
    """Finished household days not yet settled, oldest first (today never)."""
    yesterday = date.fromisoformat(_day_of(now)) - timedelta(days=1)
    if marker.get("settled_through"):
        first = date.fromisoformat(marker["settled_through"]) + timedelta(days=1)
    else:
        ts = store.first_episode_ts()
        if ts is None:
            return []
        first = date.fromisoformat(_day_of(ts))
    out = []
    d = first
    while d <= yesterday:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _final(ep: store.Episode, now: float) -> bool:
    """Nothing about this episode's memory can change any more: concluded, or too old for conclude to pick up."""
    return (ep.conclusion or {}).get("status") == "done" or now - ep.start_ts >= SETTLE_S


# ── the per-day table (code-built) ──────────────────────────────────────────────


def _facts(ep: store.Episode, events: list[store.FrigateUserEvent], hidden: set[str]) -> tuple[set[str], bool]:
    """(named persons the gate allowed and nobody has hidden since, an unnamed person was there)."""
    named = {r for r in ep.person_refs if r and r.casefold() not in hidden}
    persons = [e for e in events if e.label == "person"]
    if persons:  # events are kept for the detail tiers; an older episode answers from its own fields
        return named, any(not e.person_ref or e.person_ref.casefold() in hidden for e in persons)
    return named, "person" in ep.labels and not named


def _table(day_eps: list[tuple[store.Episode, list[store.FrigateUserEvent]]], part_ids: set[int] | None,
           hidden: set[str], lang: str) -> str:
    users = query._users_by_name()
    counting = [(ep, evs) for ep, evs in day_eps if stm.memory_lines(ep, lang)]

    def count(pred, eps) -> int:
        return sum(1 for ep, evs in eps if pred(ep, evs))

    def cols(pred, eps=None) -> list[int]:
        eps = counting if eps is None else eps
        whole = count(pred, eps)
        return [whole] if part_ids is None else [whole, count(pred, [x for x in eps if x[0].episode_id in part_ids])]

    rows: list[tuple[str, list[int]]] = [
        (_t("row_episodes", lang), cols(lambda ep, evs: True)),
        (_t("row_urgent", lang), cols(lambda ep, evs: any(u for _, _, u in stm.memory_lines(ep, lang)))),
        (_t("row_false", lang), cols(lambda ep, evs: stm.kind(ep.conclusion) == "false_detection", day_eps)),
        (_t("row_vehicles", lang), cols(lambda ep, evs: stm.kind(ep.conclusion) == "vehicles_only", day_eps)),
    ]
    refs = sorted({r for ep, evs in counting for r in _facts(ep, evs, hidden)[0]}, key=str.casefold)
    for ref in refs:
        mine = [e for _, evs in counting for e in evs if e.label == "person" and e.person_ref == ref]
        word = query.certainty_word(query._combined(mine), lang)
        rows.append((_t("row_person", lang, name=query._display(ref, users), word=word),
                     cols(lambda ep, evs, ref=ref: ref in _facts(ep, evs, hidden)[0])))
    rows.append((_t("row_unnamed", lang), cols(lambda ep, evs: _facts(ep, evs, hidden)[1])))
    animals = sorted({lb for ep, _ in counting for lb in ep.labels if lb in store.ANIMAL_LABELS})
    rows.extend((_t("row_animal", lang, label=conclude.label_name(label, lang)),
                 cols(lambda ep, evs, label=label: label in ep.labels)) for label in animals)
    head = [_t("col_what", lang), _t("col_day", lang)] + ([] if part_ids is None else [_t("col_part", lang)])
    out = ["| " + " | ".join(head) + " |"]
    out += ["| " + " | ".join([name, *(str(n) for n in values)]) + " |" for name, values in rows]
    return "\n".join(out)


# ── batches ─────────────────────────────────────────────────────────────────────


def _split(items: list[tuple[store.Episode, list[tuple[float, str, bool]]]]) -> list[list]:
    """Equal parts in time order, at most MAX_LINES_PER_EPISODE lines each; an episode's lines stay together."""
    total = sum(len(lines) for _, lines in items)
    parts = max(1, math.ceil(total / MAX_LINES_PER_EPISODE))
    target = math.ceil(total / parts)
    out: list[list] = [[]]
    n = 0
    for item in items:
        if out[-1] and n + len(item[1]) > target and len(out) < parts:
            out.append([])
            n = 0
        out[-1].append(item)
        n += len(item[1])
    return out


def day_batches(day: str, marker: dict, now: float, lang: str, global_hidden: set[str]) -> tuple[list[Batch], bool]:
    """(the batches this day still owes LTM, the day is settled once they are saved)."""
    start, end = engine.day_bounds(day, get_local_tz())
    day_eps = sorted(((ep, evs) for ep, evs in store.episodes_window(start, end) if start <= ep.start_ts < end),
                     key=lambda x: (x[0].start_ts, x[0].episode_id))
    done = set(marker.get("done", {}).get(day, []))
    hidden = {n.casefold() for n in global_hidden | store.hidden_names(r for ep, _ in day_eps for r in ep.person_refs)}
    ready = [(ep, lines) for ep, _ in day_eps
             if ep.episode_id not in done and _final(ep, now) and (lines := stm.memory_lines(ep, lang))]
    settled = all(_final(ep, now) for ep, _ in day_eps)
    if not ready:
        return [], settled
    groups = _split(ready)
    batches = []
    for i, group in enumerate(groups, 1):
        ids = [ep.episode_id for ep, _ in group]
        levels = [_CONFIDENCE.get(((ep.conclusion or {}).get("confidence") or {}).get("level"), 0.6) for ep, _ in group]
        part_ids = set(ids) if len(groups) > 1 or done else None
        batches.append(Batch(
            day=day, part=i, parts=len(groups), episode_ids=ids,
            lines=[store.scrub(line, hidden) for ep, lines in group for _, line, _ in lines],
            table=_table(day_eps, part_ids, hidden, lang),
            labels=sorted({lb for ep, _ in group for lb in ep.labels}),
            confidence=round(sum(levels) / len(levels), 3), addition=bool(done), hidden=hidden,
            corrected=[ep.episode_id for ep, _ in group if (ep.conclusion or {}).get("history")]))
    return batches, settled


def build_prompt(batch: Batch, lang: str) -> str:
    d = date.fromisoformat(batch.day)
    weekday = (_WEEKDAYS.get(lang) or _WEEKDAYS["en"])[d.weekday()]
    part = _t("part", lang, n=batch.part, of=batch.parts) if batch.parts > 1 else ""
    if batch.addition:
        part += _t("addition", lang)
    return "\n".join([
        _t("header", lang, day=batch.day, weekday=weekday, part=part),
        _t("instructions", lang),
        *([_t("part_rule", lang)] if batch.parts > 1 or batch.addition else []),
        *([_t("correction", lang, ids=", ".join(str(i) for i in batch.corrected))] if batch.corrected else []),
        "",
        _t("sep_table", lang),
        batch.table,
        "",
        _t("sep_lines", lang),
        *batch.lines,
    ])


# ── progress ────────────────────────────────────────────────────────────────────


def mark_done(marker: dict, batch: Batch) -> bool:
    done = marker.setdefault("done", {})
    done[batch.day] = sorted(set(done.get(batch.day, [])) | set(batch.episode_ids))
    return store.set_ltm_marker(marker)


def mark_settled(marker: dict, day: str, first_day: str) -> bool:
    """Advance «settled through» when `day` is the next one in line; a day after an unsettled one keeps its ids."""
    expected = (date.fromisoformat(marker["settled_through"]) + timedelta(days=1)).isoformat() \
        if marker.get("settled_through") else first_day
    if day != expected:
        return True
    marker["settled_through"] = day
    marker.get("done", {}).pop(day, None)
    return store.set_ltm_marker(marker)


def reopen_episode(episode_id: int) -> str:
    """P144-s24: an episode re-analysed after its day went into LTM is compressed again the next night (an «addition»
    LTM episode; the old LTM text stays — LTM is never edited here). In the marker's own format, no new key:
      - day not settled, id in its `done` list → the id is taken out;
      - day already settled → `settled_through` moves back to the day before, and every day from it up to the old
        settled day gets `done` = the episodes that carry memory lines today (those were compressed), minus this one —
        so nothing else is compressed twice and the night re-settles them.
    Returns "reopened" | "not_in_ltm" | "failed". Never raises."""
    try:
        ep = store.episode(episode_id)
        if ep is None:
            return "not_in_ltm"
        marker = store.ltm_marker()
        day = _day_of(ep.start_ts)
        done = marker.setdefault("done", {})
        if episode_id in done.get(day, []):
            done[day] = [i for i in done[day] if i != episode_id]
        elif marker.get("settled_through") and day <= marker["settled_through"]:
            last = date.fromisoformat(marker["settled_through"])
            d = date.fromisoformat(day)
            while d <= last:
                start, end = engine.day_bounds(d.isoformat(), get_local_tz())
                ids = [e.episode_id for e in store.episodes_between(start, end - 0.001)
                       if start <= e.start_ts < end and e.episode_id != episode_id and stm.memory_lines(e, "en")]
                done[d.isoformat()] = sorted(set(done.get(d.isoformat(), [])) | set(ids))
                d += timedelta(days=1)
            prev = date.fromisoformat(day) - timedelta(days=1)
            first = store.first_episode_ts()
            marker["settled_through"] = prev.isoformat() if first is not None and _day_of(first) <= prev.isoformat() \
                else None
        else:
            return "not_in_ltm"
        if not store.set_ltm_marker(marker):
            return "failed"
        logger.info("[FRIGATE_LTM] frigate episode %s re-analysed: day %s will be compressed again", episode_id, day)
        return "reopened"
    except Exception as e:
        logger.warning("[FRIGATE_LTM] reopen of frigate episode %s failed: %s", episode_id, e)
        return "failed"


# ── the night step ──────────────────────────────────────────────────────────────


async def run(compress: Callable[[str], Awaitable[str]], save: Callable[[Batch, str], Awaitable[int | None]],
              max_batches: int, lang: str, now: float | None = None) -> dict:
    """Compress every finished day the Frigate memory still owes LTM. `compress(prompt)` → narrative ('' = failed);
    `save(batch, narrative)` → the saved LTM episode id (None = not saved). Stops at the first failure (retried next
    night) or after `max_batches` model calls (the rest waits — nothing is lost). Returns counts only."""
    stats = {"episodes": 0, "lines": 0, "days_settled": 0, "bound_hit": False, "stopped": None}
    if not store.exists():
        stats["stopped"] = "no_frigate_memory"
        return stats
    now = time.time() if now is None else now
    try:
        marker = store.ltm_marker()
    except Exception as e:
        logger.error("[FRIGATE_LTM] progress marker unreadable, step skipped: %s", e)
        stats["stopped"] = "marker_unreadable"
        return stats
    global_hidden = stm._hidden_names()
    if global_hidden is None:
        logger.warning("[FRIGATE_LTM] consent lookup failed — nothing compressed tonight (fail closed)")
        stats["stopped"] = "consent_lookup_failed"
        return stats
    days = days_to_scan(marker, now)
    calls = 0
    for day in days:
        batches, settled = day_batches(day, marker, now, lang, global_hidden)
        for b in batches:
            if calls >= max_batches:
                logger.warning("[FRIGATE_LTM] per-night bound hit (%d batches): stopped before day %s part %d/%d "
                               "(frigate episodes %s) — the rest waits for the next night",
                               max_batches, b.day, b.part, b.parts, b.episode_ids)
                stats.update(bound_hit=True, stopped="bound")
                return stats
            calls += 1
            narrative = store.scrub((await compress(build_prompt(b, lang))).strip(), b.hidden)
            if not narrative:
                logger.warning("[FRIGATE_LTM] empty model reply for day %s part %d/%d (frigate episodes %s) — "
                               "retrying next night", b.day, b.part, b.parts, b.episode_ids)
                stats["stopped"] = "empty_reply"
                return stats
            ep_id = await save(b, narrative)
            if ep_id is None:
                stats["stopped"] = "save_failed"
                return stats
            if not mark_done(marker, b):
                logger.error("[FRIGATE_LTM] LTM episode %s saved but the marker was not — day %s may be compressed "
                             "again next night", ep_id, b.day)
                stats["stopped"] = "marker_write_failed"
                return stats
            logger.info("[FRIGATE_LTM] LTM episode %s saved: day %s part %d/%d, %d lines, frigate episodes %s",
                        ep_id, b.day, b.part, b.parts, len(b.lines), b.episode_ids)
            stats["episodes"] += 1
            stats["lines"] += len(b.lines)
        if settled and mark_settled(marker, day, days[0]) and marker.get("settled_through") == day:
            stats["days_settled"] += 1
    return stats
