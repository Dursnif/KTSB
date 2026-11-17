"""The 27B's conclusion per closed episode (P144-s06b): «9B sees, code checks, 27B concludes».

An *episode* is the whole situation across cameras until quiet (s04). Once it closes, KTSB asks
the `frigate_conclude` model role (shares Kåre's `default` model unless the user picks another in
Integrations → Frigate) ONE text question about it: what it meant, whether it was unusual against
the household's own baseline (`kaare_core/normalcy.py`), whether someone should be told, and one
memory line in first person. The answer is structured JSON, stored with
`store.conclude_episode()`.

  - **Idle gate:** a background 27B call evicts the users' vLLM prefix cache (T22), so the call
    waits until no user has prompted Kåre for `conclude.idle_gate_s`. Waiting episodes stay in
    frigate.db as «closed, not concluded» — nothing lives only in RAM, nothing is dropped.
  - **Failures:** malformed output is stored as `status: failed` with the reason and retried on
    the next rounds, at most `conclude.max_attempts` calls per episode.
  - **Urgent bypass (L4):** `conclude_urgent(event, image)` — s06a calls it for every stored
    event; a not-welcome face, a person while the household is away, or fire/smoke goes to the
    model at once, *with the image*, and the result is added to the episode's `urgent` list.
  - **Scene step (s06d):** before the conclusion, ~9 frames picked by code (`frames.py`) go as separate numbered
    images in ONE call to the `frigate` role, with the code route (`route.py`) in the same prompt; the JSON scene and
    the route are stored on the episode and the conclusion reads them instead of re-deriving movement. Runs once per
    episode (a failure is stored and not retried); one call at a time, since this loop is sequential.
  - **Shadow day 1 fixes (s06e):** a vehicle-only episode gets no model call (no scene, no 27B) — a code summary
    line (`route.vehicle_summary`, `by: code`), outside the idle gate. «Unusual» on a baseline under
    `normalcy.MIN_TRUST_DAYS` (per camera) is turned off by code and the model's view kept as `unusual_suppressed`
    (not for urgent classes / not welcome / a person while away / fire). `raise_at_dev_meeting` = `{topic, text}`,
    counted in frigate.db's topic register and raised once until resolved. The household's camera facts
    (knowledge.db world, P133 cap) go into the prompt, opted-out names scrubbed.
  - **Label claim gate (s14):** Frigate's *label* is a claim, like a face. After the scene step, an episode whose
    Frigate person/animal labels the 9B did not see (no person, no animal in the scene's lists) is concluded by code as
    «likely false detection» (`by: code`, `false_detection` kept for the phase-4b review list) — no 27B call. Never
    for urgent cases (`_ungated`), never on a failed scene, never from Frigate's score; the scene's `persons` count
    can only block the gate. The weather at the episode (`weather_adapter.current_conditions`, the user's one weather
    config) is code context on the route, the false-detection line and the 27B prompt — not in the 9B's see call.
  - **Blind closed gate (s16, measured in s15):** the gate is ONE closed, non-leading question to the `frigate` role
    with the episode's frames — «what is in these images: person / animal (which) / vehicle / nothing?» (form B, short
    JSON, no route, no Frigate label, no name). Code compares it with Frigate's labels; B saw neither a person nor an
    animal → likely false detection, and the description call is skipped (on empty frames its `animals` field fills
    with junk — s15). The description (C) runs blind for «what happened» and is no longer the gate. A blind L3 person
    count keeps the gate open; a primed one (label in its prompt, before s16) does not.
  - **Count table + STM (s16 part B):** the 27B gets a small table of numbers made by code (`counts.py`, RAM counters)
    instead of the recent-episodes text: one row per named person (as the gate kept the name), «unknown person», and
    each label ticked in the camera settings; columns this episode · today · 7 days · usual now · last seen before.
    Every concluded episode's memory line (and every urgent result) goes into the Frigate user's STM (`stm.py`).
  - **Truer episode text (s20, each measured first):** a person below the face/head px limit carries no face/head
    fields and the 27B reads «far away (N px)» (`checks.limit_by_size`, set in the pipe); with a done scene the 27B's
    event lines carry no L3 single-image text and a rule says where/what comes from the scene + route (ep 93); the
    scene's `at_door` is shown as «at a door or window»; an animal only the blind look saw (no Frigate animal label
    in the episode window) is «uncertain» and never keeps the gate open alone (garden ornaments); `nothing` beside a
    being = `some_empty`; frames on an even grid clipped to the episode (`frames.py`); the STM gets only what
    `stm.counts_as_memory()` counts.
  - **The 27B looks on doubt (s28, measured):** `doubt.py` raises a flag in code (IR frames + burnt-out light inside
    Frigate's box); only then, after the frame run and only when idle, the conclusion role gets ~4 separate frames + a crop
    and a closed list with no label, name or example. Its answer is one more vote, decided by code in `doubt_gate`: at the
    lens / nothing with no other person → likely false detection; person / animal → normal; unclear → normal, certainty
    lowered and said in the stored line. Never closes an urgent case. A person right at the lens is a known weak spot (log).
  - **Shadow:** `notify_suggestion` and `raise_at_dev_meeting` are stored, never sent. Kåre does
    not read any of this yet (I60).

Everything is off while the Frigate user's switch (`frigate_user.enabled`, s06a) is off.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
import threading
import time
from datetime import UTC, datetime

from adapters import frigate_adapter, weather_adapter
from adapters.llm_adapter import ask_llm_role, main_model_background_work
from kaare_core import app_state, model_enforcement, normalcy
from kaare_core.config import get_local_tz, get_memory_prompt_config
from kaare_core.domain import frigate_store
from kaare_core.frigate_user import (
    change_detect,
    chapters,
    checks,
    counts,
    doubt,
    frames,
    labels,
    modules,
    pipe,
    places_learned,
    query,
    route,
    stm,
    store,
    thing_changes,
    thing_touch,
)
from kaare_core.frigate_user import episode_state as es
from kaare_core.frigate_user import settings as fu_settings
from kaare_core.memory import agenda_themes
from kaare_core.memory import knowledge_store as ks
from kaare_core.model_enforcement import PROFILES, ModelEnforcer, strip_language_tag
from kaare_core.tools.household_state import is_away
from kaare_core.tools.i18n import get_lang
from kaare_core.tools.i18n import t as _t_i18n
from kaare_core.users import frigate_consent
from kaare_core.users import store as user_store

logger = logging.getLogger(__name__)

ROLE = "frigate_conclude"
THIN_SCENE = "frigate_thin_scene"  # P142 detector (frigate profile): the scene said almost nothing (s14)
SCENE_ROLE = "frigate"  # the «see» role (9B by choice in the GUI): one call per episode with ~9 frames (s06d)
# P142 profile: s06a's `frigate` profile (LANG tag, empty/truncated text, JSON failure) fits the
# 27B's JSON too — shadow (profile cap), so it only logs; the JSON check below decides.
ENFORCER_PROFILE = "frigate"
FRAMES_PROFILE = "frigate_frames"  # P144-s25: the frame-by-frame calls have their own P142 profile (shadow)
CHAPTERS_ON = True  # P144-s27: long episodes get chapters and a frame budget from code; False = today's grid everywhere
# J15: the window for «what stood here» (before) and «what stands here now» (after); after frames stored from this file no.
THING_BEFORE_S = 6 * 3600.0
THING_AFTER_S = 30 * 60.0
AFTER_FRAME_FILE0 = 100
CHANGES_ON = True  # P144-s27: before/after change regions as a position fact for the 27B
TOUCH_ON = True  # J17: «a person was at <thing>: handled / moved» as a code fact for the 27B
THING_LOOKBACK_S = 6 * 3600.0  # J17: a parked thing's event may have started hours before (only its last sighting must be fresh)
FRAMES_ON = True  # P144-s25: the scene is the frame-by-frame run; the one-call scene is the fallback when it fails
_TIMELINE_TIMEOUT_S = 5
BEFORE_WINDOW_S = 3 * 3600  # P144-s25 cross-episode memory: the previous episode on the same camera within 3 h

# Fallbacks for the `conclude:` section of configs/frigate_user.yaml (s06a owns the file and its
# defaults; these only fill a key the settings module did not return).
DEFAULT_CONCLUDE = {
    "idle_gate_s": 30,
    "urgent_classes": ["not_welcome"],
    "urgent_when_away_person": True,
    "urgent_labels": ["fire", "smoke"],
    "max_attempts": 3,
    "poll_s": 30,
}

# Episodes older than this are not picked up as pending (events keep their description 7 days,
# s07) — bounds the scan; anything older that never concluded stays visible as such in the DB.
_PENDING_LOOKBACK_S = 7 * 86400
_STARTUP_DELAY_S = 90

# P144-s24 item 3: the image model could not be reached (every agent-pool instance down) — the episode waits for its
# analysis instead of being concluded on nothing; retried on the kept frames, one per round, with a growing pause.
WAITING = "waiting"
UNREACHABLE = "model_unreachable"
_WAIT_RETRY_S = (60, 900)  # first pause, longest pause (doubles per try)

CONFIDENCE_LEVELS = ("low", "medium", "high")
# Never gated by the thin-baseline rule (s06e), whatever the urgent settings say: the household must hear about these.
FIRE_LABELS = ("fire", "smoke")
# World categories (Kåre's own free-text categories in knowledge.db) that are about cameras, nb/en/de.
CAMERA_CATEGORIES = ("cameras", "camera", "kameraer", "kamera", "kameras")
_WORLD_ROWS = 200
_OPEN_TOPICS = 20
_TOPIC_MAX_CHARS = 48
VERDICTS = ("yes", "no", "unsure")

# Read-modify-write of an episode's conclusion (urgent results and the episode conclusion can
# land at the same time); the store calls run in worker threads, so a thread lock.
_merge_lock = threading.Lock()

# P144-s24: one episode analysis at a time (the loop, a retry, the GUI «Analyser» button) — per event loop, made lazily.
_analysis_locks: dict[int, asyncio.Lock] = {}
_current: dict = {}  # N40: the episode being analysed now and since when (RAM) — {"episode_id", "started_ts"}
_background: set[asyncio.Task] = set()  # keeps the backfill task referenced


def _analysis_lock() -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    lock = _analysis_locks.get(loop_id)
    if lock is None:
        _analysis_locks.clear()  # a new loop (tests, restart) never inherits an old loop's lock
        lock = _analysis_locks[loop_id] = asyncio.Lock()
    return lock


@contextlib.asynccontextmanager
async def _analysing():
    """One whole episode analysis (look → frames → doubt → conclusion): the analysis lock, plus a mark the reflection
    waits for — it is last in the queue and starts only when the camera is fully done (2026-11-05)."""
    async with _analysis_lock():
        with main_model_background_work():
            yield


# ── settings ──────────────────────────────────────────────────────────────────


def _settings() -> dict:
    """`enabled`, `episode_gap_s` and the `conclude:` section, with code defaults filled in."""
    try:
        raw = dict(fu_settings.get_settings() or {})
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] settings unreadable, switch treated as off: %s", e)
        raw = {}
    conclude = {**DEFAULT_CONCLUDE, **(raw.get("conclude") or {})}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "episode_gap_s": raw.get("episode_gap_s", store.EPISODE_GAP_S),
        "conclude": conclude,
    }


def _is_away() -> bool:
    try:
        return is_away()
    except Exception:
        return False


def _idle(conclude_cfg: dict) -> bool:
    gate = float(conclude_cfg.get("idle_gate_s") or 0)
    return time.time() - app_state._last_user_prompt_time >= gate


# ── urgency ───────────────────────────────────────────────────────────────────


def is_urgent(ev: store.FrigateUserEvent) -> str:
    """Why this event cannot wait for its episode to close ('' = it can)."""
    c = _settings()["conclude"]
    if ev.person_kind in (c.get("urgent_classes") or []):
        return f"class:{ev.person_kind}"
    if ev.label in (c.get("urgent_labels") or []):
        return f"label:{ev.label}"
    if ev.label == "person" and c.get("urgent_when_away_person") and _is_away():
        return "person_while_away"
    return ""


pipe.set_urgent_rule(is_urgent)  # P144-s28 item 0: the pipe's queue asks this rule, so urgent has one definition


# ── prompt text (nb/en/de, rule 8) ────────────────────────────────────────────

_TEXT: dict[str, dict[str, str]] = {
    "lang_name": {"nb": "norsk (bokmål)", "en": "English", "de": "Deutsch"},
    "intro": {
        "nb": "Kameraene mine så en episode (alt som skjedde på tvers av kameraene til det ble stille). "
        "Bildemodellen beskrev hvert bilde, og koden min sjekket Frigates påstander. Nå skal du, "
        "som meg (KTSB), avgjøre hva episoden betydde.",
        "en": "My cameras saw an episode (everything that happened across the cameras until it went "
        "quiet). The vision model described each image and my code checked Frigate's claims. Now you, "
        "as me (KTSB), decide what the episode meant.",
        "de": "Meine Kameras haben eine Episode gesehen (alles, was kameraübergreifend geschah, bis "
        "es ruhig wurde). Das Bildmodell hat jedes Bild beschrieben, mein Code hat Frigates "
        "Behauptungen geprüft. Jetzt entscheidest du, als ich (KTSB), was die Episode bedeutete.",
    },
    "intro_urgent": {
        "nb": "Kameraene mine så nettopp noe som ikke kan vente til episoden er over. Du ser bildet "
        "selv. Frigates navn er en påstand, ikke en fasit — sjekk den mot bildet.",
        "en": "My cameras just saw something that cannot wait until the episode is over. You see "
        "the image yourself. Frigate's name is a claim, not a fact — check it against the image.",
        "de": "Meine Kameras haben gerade etwas gesehen, das nicht bis zum Ende der Episode warten "
        "kann. Du siehst das Bild selbst. Frigates Name ist eine Behauptung, keine Tatsache — "
        "prüfe sie am Bild.",
    },
    "h_episode": {"nb": "Episode", "en": "Episode", "de": "Episode"},
    "episode_line": {
        "nb": "{weekday} {start}–{end} ({minutes} min), {part}. Kameraer: {cameras}.",
        "en": "{weekday} {start}–{end} ({minutes} min), {part}. Cameras: {cameras}.",
        "de": "{weekday} {start}–{end} ({minutes} Min.), {part}. Kameras: {cameras}.",
    },
    "away": {
        "nb": "Husstanden er borte (bortemodus på) nå.",
        "en": "The household is away (away mode on) right now.",
        "de": "Der Haushalt ist gerade abwesend (Abwesenheitsmodus an).",
    },
    "home": {
        "nb": "Husstanden er hjemme (bortemodus av) nå.",
        "en": "The household is at home (away mode off) right now.",
        "de": "Der Haushalt ist gerade zu Hause (Abwesenheitsmodus aus).",
    },
    "h_events": {"nb": "Hendelser i rekkefølge", "en": "Events in order", "de": "Ereignisse der Reihe nach"},
    "who_confirmed": {
        "nb": "{name} (Frigate-påstand, bekreftet av KTSB)",
        "en": "{name} (Frigate claim, confirmed by KTSB)",
        "de": "{name} (Frigate-Behauptung, von KTSB bestätigt)",
    },
    "who_unverified": {
        "nb": "{name} (Frigate-påstand, ubekreftet)",
        "en": "{name} (Frigate claim, unverified)",
        "de": "{name} (Frigate-Behauptung, unbestätigt)",
    },
    "who_untracked": {
        "nb": "en person som ikke vil spores (ikke navngitt)",
        "en": "a person who opted out of tracking (not named)",
        "de": "eine Person, die nicht verfolgt werden möchte (nicht benannt)",
    },
    "who_unknown": {"nb": "ukjent person", "en": "unknown person", "de": "unbekannte Person"},
    # P144-s19 — the second face sensor (Double Take first); the 27B may see claims, the 9B never does.
    "who_two_sensors": {
        "nb": "{name} (to ansiktssensorer er enige — sannsynlig, bare maskiner, ikke fakta)",
        "en": "{name} (two face sensors agree — likely, machines only, not a fact)",
        "de": "{name} (zwei Gesichtssensoren stimmen überein — wahrscheinlich, nur Maschinen, keine Tatsache)",
    },
    # J14b (ep 266): a weak name while a stronger sensor named someone else in the same episode
    "who_stronger_other": {
        "nb": "{name} (bare svake sensorer; en sterkere sensor så {other} i samme episode — usikkert, ikke fakta)",
        "en": "{name} (only weak sensors; a stronger sensor saw {other} in the same episode — uncertain, not a fact)",
        "de": "{name} (nur schwache Sensoren; ein stärkerer Sensor sah {other} in derselben Episode — unsicher, keine Tatsache)",
    },
    "who_sensors_disagree": {
        "nb": "{name} (ansiktssensorene er uenige: {sensors} — {word})",
        "en": "{name} (the face sensors disagree: {sensors} — {word})",
        "de": "{name} (die Gesichtssensoren sind uneins: {sensors} — {word})",
    },
    "who_second_sensor": {
        "nb": "{name} (påstand fra ekstra ansiktsgjenkjenning, ubekreftet)",
        "en": "{name} (extra face recognition's claim, unverified)",
        "de": "{name} (Behauptung der zusätzlichen Gesichtserkennung, unbestätigt)",
    },
    # P144-s20 item 1: the person's size from Frigate's box, as a code fact (the 9B never sees it)
    "far_face": {
        "nb": "langt unna (kode): personen er {px} px høy i et bilde {h} px høyt — ansikt, briller og skjegg kan ikke ses",
        "en": "far away (code): the person is {px} px high in a frame {h} px high — face, glasses and beard cannot be "
        "seen",
        "de": "weit weg (Code): die Person ist {px} px hoch in einem {h} px hohen Bild — Gesicht, Brille und Bart "
        "sind nicht zu erkennen",
    },
    "far_head": {
        "nb": "langt unna (kode): personen er {px} px høy i et bilde {h} px høyt — ansikt, lue, alder og klær kan "
        "ikke ses",
        "en": "far away (code): the person is {px} px high in a frame {h} px high — face, headwear, age and clothes "
        "cannot be seen",
        "de": "weit weg (Code): die Person ist {px} px hoch in einem {h} px hohen Bild — Gesicht, Kopfbedeckung, "
        "Alter und Kleidung sind nicht zu erkennen",
    },
    "kind": {"nb": "klasse", "en": "class", "de": "Klasse"},
    "desc": {"nb": "beskrivelse", "en": "description", "de": "Beschreibung"},
    "attrs": {"nb": "trekk", "en": "attributes", "de": "Merkmale"},
    "scene": {"nb": "scene", "en": "scene", "de": "Situation"},
    "claim": {"nb": "påstandssjekk", "en": "claim check", "de": "Behauptungsprüfung"},
    # P144-s12: Frigate's plate reading is a claim, not a fact; an unknown plate's text never reaches the prompt.
    "plate_known": {
        "nb": "Frigate leste skilt: {name} (kjent)",
        "en": "Frigate read plate: {name} (known)",
        "de": "Frigate las Kennzeichen: {name} (bekannt)",
    },
    "plate_unknown": {
        "nb": "Frigate leste et ukjent skilt",
        "en": "Frigate read an unknown plate",
        "de": "Frigate las ein unbekanntes Kennzeichen",
    },
    "h_urgent": {
        "nb": "Hastevurderinger tidligere i episoden",
        "en": "Urgent assessments earlier in the episode",
        "de": "Dringende Einschätzungen früher in der Episode",
    },
    "h_baseline": {
        "nb": "Hva som er vanlig her (husets egen statistikk, ukedag × time)",
        "en": "What is normal here (the household's own statistics, weekday × hour)",
        "de": "Was hier normal ist (eigene Statistik des Haushalts, Wochentag × Stunde)",
    },
    "baseline_line": {
        "nb": "{camera}/{label}: skjedde {seen}/{observed} av de samme ukedagene denne timen ({pct} %), grunnlag {days} dager",
        "en": "{camera}/{label}: happened on {seen}/{observed} of the same weekdays in this hour ({pct} %), based on {days} days",
        "de": "{camera}/{label}: kam an {seen}/{observed} der gleichen Wochentage in dieser Stunde vor ({pct} %), Grundlage {days} Tage",
    },
    "baseline_bucket_none": {
        "nb": "{camera}/{label}: aldri sett denne ukedagen og timen (grunnlag {days} dager)",
        "en": "{camera}/{label}: never seen on this weekday and hour (based on {days} days)",
        "de": "{camera}/{label}: an diesem Wochentag zu dieser Stunde nie gesehen (Grundlage {days} Tage)",
    },
    "baseline_none": {
        "nb": "Ingen baseline ennå — ikke gjett hva som er vanlig.",
        "en": "No baseline yet — do not guess what is normal.",
        "de": "Noch keine Baseline — rate nicht, was normal ist.",
    },
    "unusual_thin": {
        "nb": "Grunnlaget er for tynt ({days} av {need} dager) — «aldri sett» alene gjør ikke en episode uvanlig.",
        "en": "The baseline is too thin ({days} of {need} days) — «never seen» alone does not make an episode unusual.",
        "de": "Die Grundlage ist zu dünn ({days} von {need} Tagen) — «nie gesehen» allein macht eine Episode nicht "
        "ungewöhnlich.",
    },
    "code_unusual": {
        "nb": "Bare kjøretøy — ikke vurdert av modellen.",
        "en": "Vehicles only — not assessed by the model.",
        "de": "Nur Fahrzeuge — nicht vom Modell bewertet.",
    },
    "code_reason": {
        "nb": "skrevet av koden: {n} kjøretøy-hendelser, {k} kameraer, ingen modell",
        "en": "written by code: {n} vehicle events, {k} cameras, no model",
        "de": "vom Code geschrieben: {n} Fahrzeug-Ereignisse, {k} Kameras, kein Modell",
    },
    "modules_off_summary": {  # DT04 R0.6b: module 5 (the conclusion) is off
        "nb": "Kamerahendelse: {n} hendelser på {k} kameraer — ikke oppsummert (modulen «Konklusjon og minne» er av).",
        "en": "Camera episode: {n} events on {k} cameras — not summarised (the «Conclusion and memory» module is off).",
        "de": "Kamera-Episode: {n} Ereignisse auf {k} Kameras — nicht zusammengefasst (Modul «Schluss und Gedächtnis» ist aus).",
    },
    "modules_off_unusual": {
        "nb": "skrevet av koden: modulen «Konklusjon og minne» er av, ingen modell",
        "en": "written by code: the «Conclusion and memory» module is off, no model",
        "de": "vom Code geschrieben: das Modul «Schluss und Gedächtnis» ist aus, kein Modell",
    },
    "h_facts": {
        "nb": "Det jeg vet om kameraene og stedene (husets egne fakta)",
        "en": "What I know about the cameras and places (the household's own facts)",
        "de": "Was ich über die Kameras und Orte weiß (eigene Fakten des Haushalts)",
    },
    "h_topics": {
        "nb": "Åpne emner til utviklingsmøtet (allerede tatt opp — bruk samme nøkkel hvis det er det samme)",
        "en": "Open dev-meeting topics (already raised — reuse the key if it is the same thing)",
        "de": "Offene Themen für das Entwicklungstreffen (bereits angesprochen — denselben Schlüssel verwenden, wenn "
        "es dasselbe ist)",
    },
    "topic_line": {
        "nb": "- {topic} (sett {count} ganger): {text}",
        "en": "- {topic} (seen {count} times): {text}",
        "de": "- {topic} ({count}-mal gesehen): {text}",
    },
    "baseline_thin": {
        "nb": "(Under 14 dager med data: tallene er tynne.)",
        "en": "(Under 14 days of data: the numbers are thin.)",
        "de": "(Unter 14 Tagen Daten: die Zahlen sind dünn.)",
    },
    # the count table (s16): numbers made by code, every number labelled in its column head
    "h_counts": {
        "nb": "Tall fra minnet mitt (laget av koden)",
        "en": "Numbers from my memory (made by code)",
        "de": "Zahlen aus meinem Gedächtnis (vom Code erstellt)",
    },
    "c_head": {
        "nb": "| | denne episoden (hendelser) | i dag før denne (episoder) | siste 7 dager (episoder) | vanlig nå | "
        "sist sett før |",
        "en": "| | this episode (events) | today before this (episodes) | last 7 days (episodes) | usual now | "
        "last seen before |",
        "de": "| | diese Episode (Ereignisse) | heute vorher (Episoden) | letzte 7 Tage (Episoden) | üblich jetzt | "
        "zuletzt gesehen |",
    },
    "c_label_row": {"nb": "{label} (Frigate-etikett)", "en": "{label} (Frigate label)", "de": "{label} (Frigate-Label)"},
    "c_usual": {"nb": "{pct} % ({seen}/{observed}, {camera})", "en": "{pct} % ({seen}/{observed}, {camera})",
                "de": "{pct} % ({seen}/{observed}, {camera})"},
    "c_never": {"nb": "aldri ({camera})", "en": "never ({camera})", "de": "nie ({camera})"},
    "c_not_seen": {"nb": "ingen", "en": "none", "de": "keine"},
    "c_legend": {
        "nb": "«Denne episoden» teller Frigate-hendelser (ett objekt på tre kameraer = tre); de andre kolonnene teller "
        "episoder før denne. «Vanlig nå» = andel av de samme ukedagene denne timen med etiketten på kameraet (husets "
        "egen statistikk, grunnlag {days} dager); «–» = telles ikke per person. «ingen» = ikke sett i minnet mitt "
        "(de siste {horizon} dagene). Navn er Frigate-påstander slik lagringsporten tillot dem.",
        "en": "«This episode» counts Frigate events (one object on three cameras = three); the other columns count "
        "episodes before this one. «Usual now» = share of the same weekdays in this hour with the label on the camera "
        "(the household's own statistics, based on {days} days); «–» = not counted per person. «none» = not seen in "
        "my memory (the last {horizon} days). Names are Frigate claims as the storage gate allowed them.",
        "de": "«Diese Episode» zählt Frigate-Ereignisse (ein Objekt auf drei Kameras = drei); die anderen Spalten "
        "zählen Episoden vor dieser. «Üblich jetzt» = Anteil der gleichen Wochentage in dieser Stunde mit dem Label "
        "auf der Kamera (eigene Statistik des Haushalts, Grundlage {days} Tage); «–» = nicht pro Person gezählt. "
        "«keine» = nicht in meinem Gedächtnis gesehen (die letzten {horizon} Tage). Namen sind Frigate-Behauptungen, "
        "so wie das Speichertor sie erlaubt hat.",
    },
    "rules": {
        "nb": "Regler:\n"
        "- `summary` er MITT eget minne, én linje i førsteperson («Jeg så …»), på {language}. Aldri «Frigate så» eller «brukeren så».\n"
        "- Navn bare slik de står over. Står det «ubekreftet», skal det stå slik i teksten også. Finn aldri på navn.\n"
        "- `unusual` bygger på tallene for hva som er vanlig over, ikke på gjetning. Uten baseline: si det i begrunnelsen.\n"
        "- Nøytral og ærlig tone. Ved flere ting: ikke led med det verste med mindre det haster.\n"
        "- `notify_suggestion` bare når noen i huset faktisk bør vite det nå; ellers null.\n"
        "- `raise_at_dev_meeting`: noe KTSB bør lære eller rette i oppsettet (f.eks. et kamera som ofte tar feil), med `topic` (kort nøkkel på engelsk i snake_case, f.eks. `trailer_false_car`) og `text` (én setning); står det samme blant de åpne emnene over, bruk den nøkkelen; ellers null.\n"
        "- Det jeg vet om kameraene over er husets egne fakta og veier tyngre enn Frigates etikett (noe kjent som vårt eller fast parkert er ikke uvanlig).\n"
        "- `confidence.reasons`: målbare grunner (antall kameraer, lys, ansiktsstørrelse, påstandssjekk), ikke følelser.\n"
        "- Det som står i tidslinjen, ruten og tabellen over, skriver du rett ut uten forbehold («jeg så …»). Bare det som "
        "ikke står der og som du selv slutter deg til, får et sannsynlighetsord foran seg (høyst sannsynlig / sannsynlig / "
        "usikkert / lite sannsynlig) eller utelates. Sannsynlighetsord brukes aldri om hvem noen er: navn står slik "
        "de står over.",
        "en": "Rules:\n"
        "- `summary` is MY own memory, one line in first person («I saw …»), in {language}. Never «Frigate saw» or «the user saw».\n"
        "- Names only as written above. If it says «unverified», say so in the text too. Never invent names.\n"
        "- `unusual` builds on the numbers for what is normal above, not on guessing. Without a baseline: say so in the reason.\n"
        "- Neutral and honest tone. With several things: do not lead with the worst unless it is urgent.\n"
        "- `notify_suggestion` only when someone in the household really should know now; otherwise null.\n"
        "- `raise_at_dev_meeting`: something KTSB should learn or fix in the setup (e.g. a camera that is often wrong), with `topic` (a short English snake_case key, e.g. `trailer_false_car`) and `text` (one sentence); if it is among the open topics above, reuse that key; otherwise null.\n"
        "- What I know about the cameras above is the household's own facts and weighs more than Frigate's label (something known to be ours or parked for good is not unusual).\n"
        "- `confidence.reasons`: measurable reasons (number of cameras, light, face size, claim check), not feelings.\n"
        "- What stands in the timeline, the route and the table above you state plainly, without hedging («I saw …»). Only "
        "what does not stand there and you infer yourself gets a probability word in front of it (highly likely / likely / "
        "uncertain / unlikely) or is left out. Probability words are never used about who someone is: names stay as "
        "written above.",
        "de": "Regeln:\n"
        "- `summary` ist MEINE eigene Erinnerung, eine Zeile in der ersten Person («Ich sah …»), auf {language}. Nie «Frigate sah» oder «der Benutzer sah».\n"
        "- Namen nur so, wie sie oben stehen. Steht «unbestätigt» dort, sag es auch im Text. Erfinde nie Namen.\n"
        "- `unusual` stützt sich auf die Zahlen zum Normalen oben, nicht auf Raten. Ohne Baseline: sag es in der Begründung.\n"
        "- Neutraler, ehrlicher Ton. Bei mehreren Dingen: nicht mit dem Schlimmsten anfangen, außer es ist dringend.\n"
        "- `notify_suggestion` nur, wenn jemand im Haushalt es jetzt wirklich wissen sollte; sonst null.\n"
        "- `raise_at_dev_meeting`: etwas, das KTSB lernen oder im Setup korrigieren sollte (z. B. eine Kamera, die oft falsch liegt), mit `topic` (kurzer englischer snake_case-Schlüssel, z. B. `trailer_false_car`) und `text` (ein Satz); steht es schon bei den offenen Themen oben, denselben Schlüssel verwenden; sonst null.\n"
        "- Was ich oben über die Kameras weiß, sind eigene Fakten des Haushalts und wiegen mehr als Frigates Label (etwas, das als unseres bekannt ist oder fest parkt, ist nicht ungewöhnlich).\n"
        "- `confidence.reasons`: messbare Gründe (Anzahl Kameras, Licht, Gesichtsgröße, Behauptungsprüfung), keine Gefühle.\n"
        "- Was in der Zeitleiste, dem Weg und der Tabelle oben steht, schreibst du ohne Vorbehalt («ich sah …»). Nur was dort "
        "nicht steht und du selbst schlussfolgerst, bekommt ein Wahrscheinlichkeitswort davor (höchstwahrscheinlich / "
        "wahrscheinlich / unsicher / unwahrscheinlich) oder wird weggelassen. Wahrscheinlichkeitswörter nie darüber, wer "
        "jemand ist: Namen bleiben, wie sie oben stehen.",
    },
    # P144-s20 item 2 (nb measured on the 27B, en/de translations): where/what = the scene, not L3's single image
    "rule_scene": {
        "nb": "- Hvor folk var og hva de gjorde, tar du fra Scene-blokken (bildemodellen så alle bildene samlet) og ruten.",
        "en": "- Where people were and what they did comes from the Scene block (the vision model saw all frames "
        "together) and the route.",
        "de": "- Wo Personen waren und was sie taten, nimmst du aus dem Gesamtbild (das Bildmodell sah alle Bilder "
        "zusammen) und dem Weg.",
    },
    "rule_verdict": {
        "nb": "- `claim_verdict`: støtter bildet Frigates påstand? `yes` / `no` / `unsure`, med grunn (ansiktsstørrelse, lys, vinkel). Er det ingen navnepåstand, vurder om bildet viser det koden flagget.",
        "en": "- `claim_verdict`: does the image support Frigate's claim? `yes` / `no` / `unsure`, with a reason (face size, light, angle). With no name claim, judge whether the image shows what the code flagged.",
        "de": "- `claim_verdict`: stützt das Bild Frigates Behauptung? `yes` / `no` / `unsure`, mit Grund (Gesichtsgröße, Licht, Winkel). Ohne Namensbehauptung: beurteile, ob das Bild zeigt, was der Code markiert hat.",
    },
    "answer": {
        "nb": "Svar med KUN ett JSON-objekt, ingen annen tekst:",
        "en": "Answer with ONLY one JSON object, no other text:",
        "de": "Antworte NUR mit einem JSON-Objekt, ohne weiteren Text:",
    },
    "h_route": {
        "nb": "Rute og tid (fra koden, eksakt)",
        "en": "Route and time (from code, exact)",
        "de": "Weg und Zeit (aus dem Code, exakt)",
    },
    "h_scene": {
        "nb": "Scene (bildemodellen så alle bildene samlet; der den er uenig med ruten og tiden over, har koden rett)",
        "en": "Scene (the vision model saw all frames together; where it disagrees with the route and time above, the "
        "code is right)",
        "de": "Gesamtbild (das Bildmodell sah alle Bilder zusammen; wo es Weg und Zeit oben widerspricht, hat der "
        "Code recht)",
    },
    # P144-s25: frame by frame with memory in code (nb written for the 27B, en/de translations — not measured)
    "h_frames": {
        "nb": "Bilde for bilde (bildemodellen svarte per bilde mot tilstanden koden holdt; om noen er i bildet, avgjør "
        "Frigates spor; tidslinjen er skrevet av koden)",
        "en": "Frame by frame (the vision model answered per frame against the state code held; whether anyone is in "
        "the image is decided by Frigate's tracks; the timeline is written by code)",
        "de": "Bild für Bild (das Bildmodell antwortete pro Bild gegen den Zustand, den der Code hielt; ob jemand im "
        "Bild ist, entscheiden Frigates Spuren; die Zeitleiste schreibt der Code)",
    },
    "frames_person": {"nb": "{id}: {who}; klær: {clothing}", "en": "{id}: {who}; clothing: {clothing}",
                      "de": "{id}: {who}; Kleidung: {clothing}"},
    "frames_persons": {"nb": "Personer: {people}", "en": "Persons: {people}", "de": "Personen: {people}"},
    "frames_corrections": {"nb": "Rettelser underveis: {items}", "en": "Corrections on the way: {items}",
                           "de": "Korrekturen unterwegs: {items}"},
    "rule_frames": {
        "nb": "- Hvor folk var og hva de gjorde, og i hvilken rekkefølge, tar du fra tidslinjen bilde for bilde og "
        "ruten. Står det ikke der, skriv det ikke. Bildemodellens egne ord står på engelsk: oversett dem til {language}, kopier dem aldri.",
        "en": "- Where people were, what they did and in which order comes from the frame-by-frame timeline and the "
        "route. If it is not there, do not write it. The image model's own words are in English: put them in "
        "{language}, never copy foreign words.",
        "de": "- Wo Personen waren, was sie taten und in welcher Reihenfolge, nimmst du aus der Bild-für-Bild-"
        "Zeitleiste und dem Weg. Steht es dort nicht, schreib es nicht. Die eigenen Worte des Bildmodells sind "
        "Englisch: übersetze sie auf {language}, kopiere sie nie.",
    },
    "h_before": {
        "nb": "Før denne episoden (kode: forrige episode på samme kamera og det Frigate ser som står stille)",
        "en": "Before this episode (code: the previous episode on the same camera and what Frigate sees standing "
        "still)",
        "de": "Vor dieser Episode (Code: die vorige Episode auf derselben Kamera und was Frigate stillstehen sieht)",
    },
    "before_prev": {
        "nb": "{camera}: forrige episode endte kl. {time} ({minutes} min før) — {state}",
        "en": "{camera}: the previous episode ended at {time} ({minutes} min before) — {state}",
        "de": "{camera}: die vorige Episode endete um {time} ({minutes} Min. vorher) — {state}",
    },
    "before_end_nobody": {"nb": "ingen i bildet til slutt", "en": "nobody in the image at the end",
                          "de": "am Ende niemand im Bild"},
    "before_end_persons": {"nb": "{n} person(er) i bildet til slutt", "en": "{n} person(s) in the image at the end",
                           "de": "am Ende {n} Person(en) im Bild"},
    "before_things": {"nb": "løse ting: {things}", "en": "loose things: {things}", "de": "lose Dinge: {things}"},
    "before_still": {"nb": "{camera}: Frigate ser {label} som står stille ({where})",
                     "en": "{camera}: Frigate sees a {label} standing still ({where})",
                     "de": "{camera}: Frigate sieht {label}, das stillsteht ({where})"},
    # P144-s27b: expression and gesture are what one frame showed, in the image model's words — no label, no mood
    "rule_looks": {
        "nb": "- Uttrykk og gest i tidslinjen er det ett enkelt bilde viste, med bildemodellens egne ord. Skriv dem som det "
              "personen gjorde da, ikke som en stemning gjennom hele episoden.",
        "en": "- Expression and gesture in the timeline are what one single frame showed, in the image model's own words. "
              "Write them as what the person did then, not as a mood through the whole episode.",
        "de": "- Ausdruck und Geste in der Zeitleiste zeigen, was ein einzelnes Bild zeigte, in den eigenen Worten des "
              "Bildmodells. Schreib sie als das, was die Person da tat, nicht als Stimmung über die ganze Episode.",
    },
    # P144-s27: chapters and change regions — code's words for the 27B, no label, no interpretation
    "chapter_line": {"nb": "— Kapittel {n}, {start}–{end}: {kind} ({cameras})", "en": "— Chapter {n}, {start}–{end}: {kind} ({cameras})",
                     "de": "— Kapitel {n}, {start}–{end}: {kind} ({cameras})"},
    "chapter_active": {"nb": "noe skjer", "en": "something happens", "de": "es passiert etwas"},
    "chapter_calm": {"nb": "rolig, bildet endrer seg ikke", "en": "calm, the image does not change",
                     "de": "ruhig, das Bild ändert sich nicht"},
    "chapter_empty": {"nb": "ingen i bildet", "en": "nobody in the image", "de": "niemand im Bild"},
    "h_changes": {
        # J10 / R64 (measured ep 102: 0/3 → 3/3 conclusions name the place): the line asks for the place in the summary
        "nb": "Ser annerledes ut etter episoden enn før (kode sammenligner bilder før og etter; sier ikke hva det er). "
              "Ta med i oppsummeringen at området under ser annerledes ut etter episoden, med stedet",
        "en": "Looks different after the episode than before (code compares frames before and after; it does not say what it "
              "is). Include in the summary that the area below looks different after the episode, with the place",
        "de": "Sieht nach der Episode anders aus als davor (Code vergleicht Bilder davor und danach; sagt nicht, was es ist). "
              "Nimm in die Zusammenfassung auf, dass der Bereich unten nach der Episode anders aussieht, mit dem Ort",
    },
    "change_line": {"nb": "{camera}: {where} i bildet", "en": "{camera}: {where} of the image", "de": "{camera}: {where} im Bild"},
    "change_thing_before": {"nb": "{camera}: området der {label} sto ({where}) er endret",
                            "en": "{camera}: the area where the {label} stood ({where}) has changed",
                            "de": "{camera}: der Bereich, wo {label} stand ({where}), hat sich verändert"},
    "change_thing_after": {"nb": "{camera}: {label} står nå i dette området ({where})",
                           "en": "{camera}: a {label} now stands in this area ({where})",
                           "de": "{camera}: {label} steht jetzt in diesem Bereich ({where})"},
    "change_thing_cameras": {"nb": " — sett på {n} kameraer", "en": " — seen on {n} cameras", "de": " — auf {n} Kameras gesehen"},
    "change_thing_elsewhere": {"nb": " — står nå et annet sted ({camera})", "en": " — now stands somewhere else ({camera})",
                               "de": " — steht jetzt woanders ({camera})"},
    # J17 (from P144-s31): a still thing a person was at — code facts for the 27B only (the 9B never sees a label)
    "touch_handled": {"nb": "{camera}: en person var ved {label} i {secs} s ({start}–{end}); omrisset av {label} endret seg "
                            "mens personen var der og var som før etterpå",
                      "en": "{camera}: a person was at the {label} for {secs} s ({start}–{end}); the {label}'s outline "
                            "changed while the person was there and was as before afterwards",
                      "de": "{camera}: eine Person war {secs} s am {label} ({start}–{end}); der Umriss von {label} "
                            "veränderte sich, solange die Person dort war, und war danach wie vorher"},
    "touch_moved": {"nb": "{camera}: {label} ble flyttet eller tatt bort, endringen begynte {at}; en person var ved den i "
                          "{secs} s ({start}–{end})",
                    "en": "{camera}: the {label} was moved or taken away, the change began at {at}; a person was at it "
                          "for {secs} s ({start}–{end})",
                    "de": "{camera}: {label} wurde verschoben oder weggenommen, die Veränderung begann um {at}; eine "
                          "Person war {secs} s daran ({start}–{end})"},
    "touch_near": {"nb": "{camera}: en person var ved {label} i {secs} s ({start}–{end}); ingen endring på {label}",
                   "en": "{camera}: a person was at the {label} for {secs} s ({start}–{end}); no change to the {label}",
                   "de": "{camera}: eine Person war {secs} s am {label} ({start}–{end}); keine Veränderung an {label}"},
    # J18: spots admin named («Hva er her?») — code facts for the 27B; never a person's name
    "spot_from": {"nb": "{camera}: en person kom fra {place} kl. {time}", "en": "{camera}: a person came from {place} at {time}",
                  "de": "{camera}: eine Person kam um {time} von {place}"},
    "spot_to": {"nb": "{camera}: en person gikk mot {place} kl. {time}", "en": "{camera}: a person went towards {place} at {time}",
                "de": "{camera}: eine Person ging um {time} in Richtung {place}"},
    "spot_handover": {"nb": "{a} → {b}: et spor sluttet og et nytt begynte på samme vei som {n} ganger før (trolig samme person)",
                      "en": "{a} → {b}: one track ended and a new one began along the same way as {n} times before "
                            "(probably the same person)",
                      "de": "{a} → {b}: eine Spur endete und eine neue begann auf demselben Weg wie {n}-mal zuvor "
                            "(wahrscheinlich dieselbe Person)"},
    "h_touched": {"nb": "Ting en person var ved (kode måler pikslene i tingens felt, ikke modell)",
                  "en": "Things a person was at (code measures the pixels in the thing's box, not a model)",
                  "de": "Dinge, an denen eine Person war (Code misst die Pixel im Feld des Dings, kein Modell)"},
    "change_words": {"nb": "bildemodellens ord: {words}", "en": "the image model's words: {words}",
                     "de": "Worte des Bildmodells: {words}"},
    "pos_left": {"nb": "til venstre", "en": "on the left", "de": "links"},
    "pos_mid": {"nb": "midt i bildet", "en": "in the middle", "de": "in der Mitte"},
    "pos_right": {"nb": "til høyre", "en": "on the right", "de": "rechts"},
    "pos_top": {"nb": "øverst", "en": "at the top", "de": "oben"},
    "pos_bottom": {"nb": "nederst", "en": "at the bottom", "de": "unten"},
    "scene_failed": {
        "nb": "Ingen scene ({reason}) — bygg på hendelsene og ruten.",
        "en": "No scene ({reason}) — build on the events and the route.",
        "de": "Kein Gesamtbild ({reason}) — stütze dich auf die Ereignisse und den Weg.",
    },
    # P144-s24 item 3: a conclusion without any image check says so (code fact) — never «Jeg så …»
    "no_image_check": {
        "nb": "- Ingen bildesjekk: bildemodellen så ingen bilder fra denne episoden. Skriv aldri «jeg så» — bare hva "
              "Frigate meldte og ruten.",
        "en": "- No image check: the vision model saw no frames of this episode. Never write «I saw» — only what "
              "Frigate reported and the route.",
        "de": "- Keine Bildprüfung: das Bildmodell sah keine Bilder dieser Episode. Schreib nie «ich sah» — nur, was "
              "Frigate meldete, und den Weg.",
    },
    "not_checked_suffix": {"nb": "(Ikke sjekket med bilder.)", "en": "(Not checked with images.)",
                           "de": "(Nicht mit Bildern geprüft.)"},
    "scene_people": {"nb": "personer", "en": "people", "de": "Personen"},
    "scene_vehicles": {"nb": "kjøretøy", "en": "vehicles", "de": "Fahrzeuge"},
    "scene_animals": {"nb": "dyr", "en": "animals", "de": "Tiere"},
    # the label gate (s14): a code conclusion when the 9B saw no person/animal behind Frigate's label
    "false_summary": {
        "nb": "Trolig feildeteksjon: Frigate sa {labels}, bildet viste {seen} ({context}).",
        "en": "Likely false detection: Frigate said {labels}, the image showed {seen} ({context}).",
        "de": "Wahrscheinlich Fehlerkennung: Frigate sagte {labels}, das Bild zeigte {seen} ({context}).",
    },
    "false_unusual": {
        "nb": "Trolig feildeteksjon — bildemodellen så ingen person eller dyr.",
        "en": "Likely false detection — the vision model saw no person or animal.",
        "de": "Wahrscheinlich Fehlerkennung — das Bildmodell sah keine Person und kein Tier.",
    },
    "false_reason": {
        "nb": "skrevet av koden: Frigate-etikett {labels}, det blinde blikket så ingen personer eller dyr, ingen "
        "hovedmodell",
        "en": "written by code: Frigate label {labels}, the blind look saw no people or animals, no main model",
        "de": "vom Code geschrieben: Frigate-Label {labels}, der blinde Blick sah keine Personen und Tiere, kein "
        "Hauptmodell",
    },
    # the closed look (s16 = s15 form B; nb is the measured text, en/de are translations — not measured)
    "look_question": {
        "nb": "Hva er i bildene? Velg alt som passer: person, dyr (hvilken art), kjøretøy, ingenting. Svar bare ut fra "
        "det du faktisk ser. Svar med KUN ett JSON-objekt, ingen annen tekst:\n"
        '{"person": true|false, "animal": "art eller none", "vehicle": true|false, "nothing": true|false}',
        "en": "What is in the images? Choose everything that fits: person, animal (which species), vehicle, nothing. "
        "Answer only from what you actually see. Answer with ONLY one JSON object, no other text:\n"
        '{"person": true|false, "animal": "species or none", "vehicle": true|false, "nothing": true|false}',
        "de": "Was ist auf den Bildern? Wähle alles, was zutrifft: Person, Tier (welche Art), Fahrzeug, nichts. "
        "Antworte nur nach dem, was du wirklich siehst. Antworte NUR mit einem JSON-Objekt, ohne weiteren Text:\n"
        '{"person": true|false, "animal": "Art oder none", "vehicle": true|false, "nothing": true|false}',
    },
    "look_line": {
        "nb": "Blindt blikk (bildemodellen, lukket spørsmål uten Frigates påstander): person {person}, dyr: {animal}, "
        "kjøretøy {vehicle}.",
        "en": "Blind look (the vision model, a closed question without Frigate's claims): person {person}, animal: "
        "{animal}, vehicle {vehicle}.",
        "de": "Blinder Blick (das Bildmodell, geschlossene Frage ohne Frigates Behauptungen): Person {person}, Tier: "
        "{animal}, Fahrzeug {vehicle}.",
    },
    "yes": {"nb": "ja", "en": "yes", "de": "ja"},
    "no": {"nb": "nei", "en": "no", "de": "nein"},
    "none": {"nb": "ingen", "en": "none", "de": "keins"},
    "seen_vehicle": {"nb": "ingen person eller dyr, bare kjøretøy", "en": "no person or animal, only vehicles",
                     "de": "keine Person und kein Tier, nur Fahrzeuge"},
    "from_look": {"nb": "fra bildemodellens lukkede svar", "en": "from the vision model's closed answer",
                  "de": "aus der geschlossenen Antwort des Bildmodells"},
    "and": {"nb": "og", "en": "and", "de": "und"},
    "seen_nothing": {"nb": "ingen person eller dyr", "en": "no person or animal", "de": "keine Person und kein Tier"},
    # P144-s28: what the 27B's look on doubt said, in code's words (the false-detection line and the 27B's rule)
    "seen_at_lens": {"nb": "noe helt inntil kameralinsen", "en": "something right at the camera lens",
                     "de": "etwas direkt an der Kameralinse"},
    "from_doubt_look": {"nb": "hovedmodellen så på bildene", "en": "the main model looked at the images",
                        "de": "das Hauptmodell sah sich die Bilder an"},
    "doubt_unusual": {
        "nb": "Trolig feildeteksjon — hovedmodellen så på bildene og fant ingen person eller dyr i scenen.",
        "en": "Likely false detection — the main model looked at the images and found no person or animal in the scene.",
        "de": "Wahrscheinlich Fehlerkennung — das Hauptmodell sah sich die Bilder an und fand keine Person und kein Tier "
        "in der Szene.",
    },
    "doubt_reason": {
        "nb": "avgjort av koden: Frigate-etikett {labels}, utbrent lys i boksen på IR-bilder, hovedmodellens svar «{answer}», "
        "ingen annen person i episoden",
        "en": "decided by code: Frigate label {labels}, burnt-out light in the box on IR frames, the main model's answer "
        "«{answer}», no other person in the episode",
        "de": "vom Code entschieden: Frigate-Label {labels}, ausgebranntes Licht in der Box auf IR-Bildern, Antwort des "
        "Hauptmodells «{answer}», keine andere Person in der Episode",
    },
    "doubt_rule_unclear": {
        "nb": "- Hovedmodellen så på bildene fordi lyset i Frigates boks var utbrent, og kunne ikke avgjøre hva det var. "
        "Skriv at det er usikkert om det var en person; beskriv ingen klær eller utseende.",
        "en": "- The main model looked at the images because the light in Frigate's box was burnt out, and could not "
        "decide what it was. Write that it is uncertain whether it was a person; describe no clothes or looks.",
        "de": "- Das Hauptmodell sah sich die Bilder an, weil das Licht in Frigates Box ausgebrannt war, und konnte nicht "
        "entscheiden, was es war. Schreib, dass unsicher ist, ob es eine Person war; beschreib keine Kleidung und kein "
        "Aussehen.",
    },
    "doubt_rule_false": {
        "nb": "- Hovedmodellen så på bildene fordi lyset i Frigates boks var utbrent, og svarte at det trolig ikke er noen "
        "person eller dyr i scenen. Skriv det; beskriv ingen klær eller utseende.",
        "en": "- The main model looked at the images because the light in Frigate's box was burnt out, and answered that "
        "there is likely no person or animal in the scene. Write that; describe no clothes or looks.",
        "de": "- Das Hauptmodell sah sich die Bilder an, weil das Licht in Frigates Box ausgebrannt war, und antwortete, "
        "dass wahrscheinlich keine Person und kein Tier in der Szene ist. Schreib das; beschreib keine Kleidung und kein "
        "Aussehen.",
    },
    "doubt_unclear_suffix": {"nb": "(Usikkert om dette var en person: hovedmodellen kunne ikke avgjøre det ut fra bildene.)",
                             "en": "(Uncertain whether this was a person: the main model could not tell from the images.)",
                             "de": "(Unsicher, ob dies eine Person war: das Hauptmodell konnte es anhand der Bilder nicht "
                             "entscheiden.)"},
    "doubt_low_reason": {
        "nb": "avgjort av koden: utbrent lys i boksen på IR-bilder, hovedmodellen så på bildene og svarte uklart",
        "en": "decided by code: burnt-out light in the box on IR frames, the main model looked and answered unclear",
        "de": "vom Code entschieden: ausgebranntes Licht in der Box auf IR-Bildern, das Hauptmodell antwortete unklar",
    },
    # P144-s20 item 4: an animal only the blind look reported (no Frigate animal label in the episode window)
    "uncertain_animal": {
        "nb": "(usikkert: bare bildemodellen så det, Frigate meldte ikke noe dyr)",
        "en": "(uncertain: only the vision model saw it, Frigate reported no animal)",
        "de": "(unsicher: nur das Bildmodell sah es, Frigate meldete kein Tier)",
    },
    "seen_uncertain_animal": {
        "nb": "ingen person, bare et usikkert dyr («{animal}» — Frigate meldte ikke noe dyr)",
        "en": "no person, only an uncertain animal («{animal}» — Frigate reported no animal)",
        "de": "keine Person, nur ein unsicheres Tier («{animal}» — Frigate meldete kein Tier)",
    },
    "h_weather": {"nb": "Vær", "en": "Weather", "de": "Wetter"},
    "w_line": {"nb": "Vær ({source}): {parts}", "en": "Weather ({source}): {parts}",
               "de": "Wetter ({source}): {parts}"},
    "w_precip": {"nb": "nedbør {mm} mm/t", "en": "precipitation {mm} mm/h", "de": "Niederschlag {mm} mm/h"},
    "w_precip_hour": {"nb": "{mm} mm siste time", "en": "{mm} mm last hour", "de": "{mm} mm letzte Stunde"},
    "w_dry": {"nb": "opphold", "en": "dry", "de": "trocken"},
    "w_wind": {"nb": "vind {v} {unit}", "en": "wind {v} {unit}", "de": "Wind {v} {unit}"},
    "w_gust": {"nb": "kast {v} {unit}", "en": "gusts {v} {unit}", "de": "Böen {v} {unit}"},
    "w_cloud": {"nb": "skydekke {pct} %", "en": "cloud cover {pct} %", "de": "Bewölkung {pct} %"},
    "w_none": {"nb": "Vær: ukjent ({reason})", "en": "Weather: unknown ({reason})",
               "de": "Wetter: unbekannt ({reason})"},
    # P144-s20 item 2: the 9B's «at the door» answer, shown neutrally — ep 93 described a window on a camera without a
    # front door, and «ved inngangsdøra» made the 27B drop it
    "scene_at_door": {"nb": "ved en dør eller et vindu (bildemodellen)", "en": "at a door or window (the vision model)",
                      "de": "an einer Tür oder einem Fenster (das Bildmodell)"},
    "scene_intro": {
        "nb": "Dette er {n} bilder i tidsrekkefølge fra kameraene mine, alle fra én situasjon. De er separate bilder, "
        "nummerert: {labels}.",
        "en": "These are {n} frames in time order from my home security cameras, all from one situation. They are "
        "separate images, numbered: {labels}.",
        "de": "Das sind {n} Bilder in zeitlicher Reihenfolge von meinen Kameras, alle aus einer Situation. Es sind "
        "einzelne Bilder, nummeriert: {labels}.",
    },
    "scene_image": {"nb": "bilde {i} = {camera} {time}", "en": "image {i} = {camera} {time}",
                    "de": "Bild {i} = {camera} {time}"},
    "scene_route": {
        "nb": "Rute og tid er gitt av koden fra kamerasystemet (eksakt):",
        "en": "Route and time are given by code from the camera system (exact):",
        "de": "Weg und Zeit kommen aus dem Code des Kamerasystems (exakt):",
    },
    "scene_task": {
        "nb": "Ikke utled ruten eller tiden på døgnet selv; bruk kodedataene for dem. Beskriv ut fra bildene hvem "
        "(hvor mange, voksen/barn, mann/kvinne), klær, hva som bæres, dyr (art), kjøretøy, og hva som skjer ved "
        "inngangsdøra. List bare personer og dyr du faktisk ser; ellers tomme lister. Bare det som synes; skriv «unclear» når du ikke kan se det. Gjett aldri navn. Tekstverdier på {language}.",
        "en": "Do not re-derive the route or the time of day; use the code data for them. From the images, describe "
        "who (how many, adult/child, man/woman), clothing, what is carried, animals (kind), vehicles, and what "
        "happens at the front door. List only people and animals you actually see; otherwise empty lists. Only what is visible; write «unclear» when you cannot tell. Never guess names. Text values in "
        "{language}.",
        "de": "Leite Weg und Tageszeit nicht selbst ab; nutze dafür die Code-Daten. Beschreibe aus den Bildern, wer "
        "(wie viele, Erwachsener/Kind, Mann/Frau), Kleidung, was getragen wird, Tiere (Art), Fahrzeuge und was an "
        "der Haustür geschieht. Liste nur Personen und Tiere, die du wirklich siehst; sonst leere Listen. Nur, was sichtbar ist; schreibe «unclear», wenn du es nicht erkennst. Rate nie Namen. "
        "Textwerte auf {language}.",
    },
    "part_night": {"nb": "natt", "en": "night", "de": "Nacht"},
    "part_morning": {"nb": "morgen", "en": "morning", "de": "Morgen"},
    "part_day": {"nb": "dag", "en": "daytime", "de": "Tag"},
    "part_evening": {"nb": "kveld", "en": "evening", "de": "Abend"},
    "weekdays": {
        "nb": "mandag,tirsdag,onsdag,torsdag,fredag,lørdag,søndag",
        "en": "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
        "de": "Montag,Dienstag,Mittwoch,Donnerstag,Freitag,Samstag,Sonntag",
    },
}

# Frigate's English labels in the household language (the false-detection line); unknown labels stay as Frigate wrote.
_LABEL_NAMES: dict[str, dict[str, str]] = {
    "person": {"nb": "person", "en": "person", "de": "Person"},
    "deer": {"nb": "hjort", "en": "deer", "de": "Hirsch"},
    "dog": {"nb": "hund", "en": "dog", "de": "Hund"},
    "cat": {"nb": "katt", "en": "cat", "de": "Katze"},
    "bird": {"nb": "fugl", "en": "bird", "de": "Vogel"},
    "horse": {"nb": "hest", "en": "horse", "de": "Pferd"},
    "sheep": {"nb": "sau", "en": "sheep", "de": "Schaf"},
    "cow": {"nb": "ku", "en": "cow", "de": "Kuh"},
    "bear": {"nb": "bjørn", "en": "bear", "de": "Bär"},
    "fox": {"nb": "rev", "en": "fox", "de": "Fuchs"},
    "squirrel": {"nb": "ekorn", "en": "squirrel", "de": "Eichhörnchen"},
    "rabbit": {"nb": "kanin", "en": "rabbit", "de": "Kaninchen"},
    "moose": {"nb": "elg", "en": "moose", "de": "Elch"},
    "elk": {"nb": "elg", "en": "elk", "de": "Elch"},
    "badger": {"nb": "grevling", "en": "badger", "de": "Dachs"},
    "hedgehog": {"nb": "pinnsvin", "en": "hedgehog", "de": "Igel"},
    "car": {"nb": "bil", "en": "car", "de": "Auto"},
    "truck": {"nb": "lastebil", "en": "truck", "de": "Lkw"},
    "bus": {"nb": "buss", "en": "bus", "de": "Bus"},  # codespell:ignore buss
    "motorcycle": {"nb": "motorsykkel", "en": "motorcycle", "de": "Motorrad"},
    "bicycle": {"nb": "sykkel", "en": "bicycle", "de": "Fahrrad"},
    "boat": {"nb": "båt", "en": "boat", "de": "Boot"},
    "license_plate": {"nb": "skilt", "en": "licence plate", "de": "Kennzeichen"},
}

_SCHEMA = (
    '{"summary": "...", "unusual": {"value": true|false, "reason": "..."}, '
    '"notify_suggestion": null | {"who": "...", "why": "..."}, '
    '"raise_at_dev_meeting": null | {"topic": "short_snake_case_key", "text": "..."}, '
    '"confidence": {"level": "low|medium|high", "reasons": ["..."]}'
)
_SCHEMA_URGENT = _SCHEMA + ', "claim_verdict": {"verdict": "yes|no|unsure", "why": "..."}'
_SCHEMA_SCENE = (
    '{"persons": 0, "people": [{"age": "adult|child|unclear", "gender": "man|woman|unclear", '
    '"clothing": "...", "carried": "..."}], "animals": [{"kind": "...", "count": 1}], "vehicles": "...", '
    '"at_door": "...", "scene": "..."}'
)
# Frigate labels that claim a living being (the label gate, s14). Vehicles have their own code path (s06e); fire and
# smoke are urgent and never gated.
BEING_LABELS = ("person", *store.ANIMAL_LABELS)
_WEATHER_MAX_AGE_S = 3 * 3600  # the weather service gives «now», not history: older episodes get no weather
SCENE_AGES = ("adult", "child", "unclear")
SCENE_GENDERS = ("man", "woman", "unclear")


def _t(key: str, lang: str, **kw) -> str:
    s = _TEXT[key].get(lang) or _TEXT[key]["en"]
    return s.format(**kw) if kw else s


def _local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, get_local_tz())


def _part_of_day(dt: datetime, lang: str) -> str:
    h = dt.hour
    key = "part_night" if h < 6 else "part_morning" if h < 10 else "part_day" if h < 18 else "part_evening"
    return _t(key, lang)


def _who(ev: store.FrigateUserEvent, lang: str, episode_events: list[store.FrigateUserEvent] | None = None) -> str:
    if ev.person_ref:
        other = query.stronger_other(ev, episode_events or [])
        if other:
            return _t("who_stronger_other", lang, name=ev.person_ref, other=other)
        relation = ((ev.check or {}).get("sensors") or {}).get("relation") if isinstance(ev.check, dict) else None
        if relation == "agree" and ev.claim_status == "confirmed":
            return _t("who_two_sensors", lang, name=ev.person_ref)
        if relation in ("disagree", "split"):
            both = query.sensors_text(ev, {}, lang) or ""
            word = query.certainty_word(query.certainty(ev.check, ev.claim_status), lang)
            return _t("who_sensors_disagree", lang, name=ev.person_ref, sensors=both, word=word)
        if relation == "double_take_only":
            return _t("who_second_sensor", lang, name=ev.person_ref)
        key = "who_confirmed" if ev.claim_status == "confirmed" else "who_unverified"
        return _t(key, lang, name=ev.person_ref)
    if ev.label != "person":
        return ""
    return _t("who_untracked" if ev.person_kind == "untracked" else "who_unknown", lang)


def _claim_text(ev: store.FrigateUserEvent) -> str:
    why = ""
    check = ev.check or {}
    if isinstance(check, dict):
        why = str(check.get("why") or check.get("reason") or "")
        if not why:
            why = _check_reasons(check)
    return f"{ev.claim_status}" + (f" — {why}" if why else "")


def _check_reasons(check: dict) -> str:
    """R56: the layers' own reasons in code's words («l0 unsure: face_px=…; l3 yes: match: …; sensors disagree») —
    never a raw JSON dump of the check."""
    parts = []
    for key in ("l0", "l1", "l3"):
        layer = check.get(key)
        parts.extend(f"{key} {item.get('verdict') or ''}: {item.get('reason') or ''}".strip(": ").strip()
                     for item in (layer if isinstance(layer, list) else [layer])
                     if isinstance(item, dict) and (item.get("verdict") or item.get("reason")))
    relation = (check.get("sensors") or {}).get("relation") if isinstance(check.get("sensors"), dict) else None
    if relation:
        parts.append(f"sensors {relation}")
    doubt = check.get("doubt")
    if isinstance(doubt, dict) and doubt.get("reason"):
        parts.append(f"doubt: {doubt['reason']}")
    return "; ".join(parts)


def _far_away(ev: store.FrigateUserEvent, lang: str) -> str:
    """«far away (code): N px …» when the pipe measured a person below the face limit (P144-s20 item 1), else ''."""
    check = ev.check if isinstance(ev.check, dict) else {}
    px, h = check.get("person_px"), check.get("frame_h")
    if ev.label != "person" or not isinstance(px, int) or isinstance(px, bool) or px >= checks.FACE_ATTRS_MIN_PX:
        return ""
    return _t("far_head" if px < checks.HEAD_ATTRS_MIN_PX else "far_face", lang, px=px, h=h or "?")


def _event_line(ev: store.FrigateUserEvent, lang: str, l3_text: bool = True,
                episode_events: list[store.FrigateUserEvent] | None = None) -> str:
    """One event for the 27B. `l3_text=False` (an episode with a done scene, P144-s20 item 2): L3's single-image
    description/analysis are left out — the scene is the one description; the attribute fields stay (claim check)."""
    parts = [_local(ev.start_ts).strftime("%H:%M:%S"), ev.camera + (f" ({ev.place})" if ev.place else ""), ev.label]
    who = _who(ev, lang, episode_events)
    if who:
        parts.append(who)
    if ev.person_kind not in ("unknown", "untracked"):
        parts.append(f"{_t('kind', lang)}: {ev.person_kind}")
    far = _far_away(ev, lang)
    if far:
        parts.append(far)
    if ev.description and l3_text:
        parts.append(f"{_t('desc', lang)}: {ev.description}")
    if ev.attributes:
        parts.append(f"{_t('attrs', lang)}: {json.dumps(ev.attributes, ensure_ascii=False)}")
    if ev.analysis and l3_text:
        parts.append(f"{_t('scene', lang)}: {ev.analysis}")
    if ev.label == "person":
        parts.append(f"{_t('claim', lang)}: {_claim_text(ev)}")
    if ev.plate_known is True and ev.plate_claim:
        parts.append(_t("plate_known", lang, name=ev.plate_claim))
    elif ev.plate_known is False:
        parts.append(_t("plate_unknown", lang))
    return "- " + " · ".join(parts)


def _baseline_lines(events: list[store.FrigateUserEvent], ref_ts: float, lang: str) -> list[str]:
    """The household's own numbers for each camera/label in the episode, at its weekday × hour
    (UTC buckets, as normalcy.py stores them)."""
    try:
        baseline = normalcy._load_baseline() or {}
    except Exception:
        baseline = {}
    days = int(baseline.get("days_of_data", 0) or 0)
    if not baseline or days < 1:
        return [_t("baseline_none", lang)]
    ref = datetime.fromtimestamp(ref_ts, UTC)
    buckets = baseline.get("buckets", {}) or {}
    lines = []
    for camera, label in sorted({(e.camera, e.label) for e in events}):
        days = normalcy.baseline_days([camera], baseline)
        b = buckets.get(f"{camera}|{label}|{ref.hour}|{ref.weekday()}")
        if b is None:
            lines.append(_t("baseline_bucket_none", lang, camera=camera, label=label, days=days))
            continue
        lines.append(_t(
            "baseline_line", lang, camera=camera, label=label,
            seen=b.get("days_with_event", "?"), observed=b.get("days_observed", "?"),
            pct=round(float(b.get("p_normal", 0)) * 100), days=days,
        ))
    if normalcy.baseline_days(sorted({e.camera for e in events}), baseline) < normalcy.MIN_TRUST_DAYS:
        lines.append(_t("baseline_thin", lang))
    return lines


def _ticked_labels(cameras: dict) -> set[str]:
    """Every label ticked («analyse») on any camera card — what the household chose to track."""
    out: set[str] = set()
    for card in cameras.values():
        for label, cfg in ((card or {}).get("labels") or {}).items() if isinstance(card, dict) else ():
            if isinstance(cfg, dict) and cfg.get("analyze"):
                out.add(label)
    return out


def _usual_now(label: str, ep: store.Episode, cameras: dict, baseline: dict, lang: str) -> str:
    """The household's own number for this label at the episode's weekday × hour on each of its cameras (UTC
    buckets, as normalcy.py stores them)."""
    if not baseline or int(baseline.get("days_of_data", 0) or 0) < 1:
        return "–"
    ref = datetime.fromtimestamp(ep.start_ts, UTC)
    buckets = baseline.get("buckets", {}) or {}
    cells = []
    for cam in sorted(set(ep.cameras)):
        shown = str((cameras.get(cam) or {}).get("display_name") or cam)
        b = buckets.get(f"{cam}|{label}|{ref.hour}|{ref.weekday()}")
        if b is None:
            cells.append(_t("c_never", lang, camera=shown))
        else:
            cells.append(_t("c_usual", lang, pct=round(float(b.get("p_normal", 0)) * 100), camera=shown,
                            seen=b.get("days_with_event", "?"), observed=b.get("days_observed", "?")))
    return "; ".join(cells) or "–"


def _last_cell(s: counts.Sighting | None, cameras: dict, lang: str) -> str:
    if s is None:
        return _t("c_not_seen", lang)
    # the camera's name first: a P137 place can be as coarse as «ute» for every outdoor camera (seen on real data)
    where = (cameras.get(s.camera) or {}).get("display_name") or s.place or s.camera
    return f"{_local(s.ts).strftime('%d.%m %H:%M')}, {where}"


def count_table(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str, cameras: dict,
                hidden: set[str] | None) -> list[str]:
    """The 27B's count table (s16, 2026-10-28): numbers made by code from the RAM counters (`counts.py`) and
    this episode's events — no DB query. Rows: each named person (as the storage gate kept the name; `hidden` = the
    opted-out names, merged into «unknown person»; None = consent lookup failed → every name merged, fail closed),
    «unknown person», each ticked label (+ any label in this episode). Every number labelled by its column head."""
    named = {e.person_ref for e in events if e.label == "person" and e.person_ref and e.person_kind in store.NAMED_KINDS}
    names = sorted(set(counts.known_names()) | named)
    shown = [] if hidden is None else [n for n in names if n not in hidden]
    merged = [n for n in names if n not in shown]
    day_start = _local(ep.start_ts).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        baseline = normalcy._load_baseline() or {}
    except Exception:
        baseline = {}

    def row(title: str, keys: list[str], this: int, usual: str) -> str:
        c = counts.count(keys, ep.start_ts, day_start, ep.episode_id)
        return f"| {title} | {this} | {c.today} | {c.week} | {usual} | {_last_cell(c.last, cameras, lang)} |"

    lines = [_t("c_head", lang), "|---|---|---|---|---|---|"]
    lines.extend(row(n, [counts.name_key(n)], sum(1 for e in events if e.label == "person" and e.person_ref == n), "–")
                 for n in shown)
    unknown_this = sum(1 for e in events if e.label == "person" and e.person_ref not in shown)
    lines.append(row(_t("who_unknown", lang), [counts.UNKNOWN, *(counts.name_key(n) for n in merged)], unknown_this,
                     "–"))
    labels = _ticked_labels(cameras) | {e.label for e in events}
    lines.extend(row(_t("c_label_row", lang, label=label_name(label, lang)), [counts.label_key(label)],
                     sum(1 for e in events if e.label == label), _usual_now(label, ep, cameras, baseline, lang))
                 for label in sorted(labels, key=lambda lab: (lab != "person", lab)))
    days = normalcy.baseline_days(sorted(set(ep.cameras)), baseline) if baseline else 0
    lines.append(_t("c_legend", lang, days=days, horizon=counts.REBUILD_S // 86400))
    if not baseline or int(baseline.get("days_of_data", 0) or 0) < 1:
        lines.append(_t("baseline_none", lang))
    elif days < normalcy.MIN_TRUST_DAYS:
        lines.append(_t("baseline_thin", lang))
    return lines


def _header(ep_start: float, ep_end: float, cameras: list[str], lang: str, away: bool) -> list[str]:
    s, e = _local(ep_start), _local(ep_end)
    weekday = _t("weekdays", lang).split(",")[s.weekday()]
    return [
        f"## {_t('h_episode', lang)}",
        _t("episode_line", lang, weekday=weekday, start=s.strftime("%Y-%m-%d %H:%M"),
           end=e.strftime("%H:%M"), minutes=max(1, round((ep_end - ep_start) / 60)),
           part=_part_of_day(s, lang), cameras=", ".join(cameras)),
        _t("away" if away else "home", lang),
    ]


def build_episode_prompt(
    ep: store.Episode,
    events: list[store.FrigateUserEvent],
    lang: str,
    *,
    away: bool,
    cameras: dict | None = None,
    hidden: set[str] | None = None,
    facts: list[str] | None = None,
    topics: list[dict] | None = None,
) -> str:
    """The 27B's one text question per episode. `hidden` = opted-out names for the count table (None = lookup failed:
    the table names nobody)."""
    lines = [_t("intro", lang), ""]
    lines += _header(ep.start_ts, ep.end_ts, ep.cameras, lang, away)
    if facts:
        lines += ["", f"## {_t('h_facts', lang)}", *facts]
    if ep.route:
        lines += ["", f"## {_t('h_route', lang)}", ep.route]
    by_frames = isinstance(ep.scene, dict) and ep.scene.get("by") == "frames" and ep.scene.get("status") == "done"
    if by_frames and ep.scene.get("before"):
        lines += ["", f"## {_t('h_before', lang)}", *ep.scene["before"]]
    regions = ((ep.scene or {}).get("changes") or {}).get("regions") if by_frames else None
    if ep.scene:
        lines += ["", f"## {_t('h_frames' if by_frames else 'h_scene', lang)}", *_scene_lines(ep.scene, lang)]
    if regions:
        lines += ["", f"## {_t('h_changes', lang)}", *(_change_line(r, cameras or {}, lang) for r in regions)]
    touched = _touch_lines(((ep.scene or {}).get("changes") or {}).get("touched"), cameras or {}, lang) if by_frames else []
    if touched:  # J17
        lines += ["", f"## {_t('h_touched', lang)}", *touched]
    scene_done = isinstance(ep.scene, dict) and ep.scene.get("status") == "done"
    lines += ["", f"## {_t('h_events', lang)}", *(_event_line(e, lang, l3_text=not scene_done, episode_events=events) for e in events)]
    urgent = (ep.conclusion or {}).get("urgent") or []
    if urgent:
        lines += ["", f"## {_t('h_urgent', lang)}"]
        lines += [
            f"- {u.get('event_id')} ({u.get('reason')}): {u.get('summary') or u.get('status')}"
            + (f" · claim_verdict={u['claim_verdict'].get('verdict')}" if isinstance(u.get("claim_verdict"), dict) else "")
            for u in urgent
        ]
    lines += ["", f"## {_t('h_counts', lang)}", *count_table(ep, events, lang, cameras or {}, hidden)]
    if topics:
        lines += ["", f"## {_t('h_topics', lang)}"]
        lines += [_t("topic_line", lang, topic=tp["topic"], count=tp["count"], text=_clip(tp["text"] or "", 140))
                  for tp in topics]
    rules = [_t("rules", lang, language=_t("lang_name", lang))] + (
        [_t("rule_frames", lang, language=_t("lang_name", lang)) if by_frames else _t("rule_scene", lang)]
        if scene_done else [])
    if not images_checked(ep.scene):
        rules.append(_t("no_image_check", lang))
    if by_frames and any(r.get("looks") for r in ((ep.scene or {}).get("frames_state") or {}).get("rows") or []):
        rules.append(_t("rule_looks", lang))
    answer = doubt_answer(ep.scene)
    if answer == "unclear":
        rules.append(_t("doubt_rule_unclear", lang))
    elif answer in doubt.FALSE_ANSWERS:  # the code gate did not close (another person, or an urgent case)
        rules.append(_t("doubt_rule_false", lang))
    lines += ["", *rules, "", _t("answer", lang), _SCHEMA + "}"]
    return "\n".join(lines)


def episode_story(scene: dict | None, cameras: dict, lang: str) -> dict:
    """What the admin's episode detail shows (P144-s27): the chapters with their frames and per-frame lines, the code's
    change positions. Lines hold the image model's own words (English, s25) — the GUI says so. {chapters, lines, changes};
    a short episode: `chapters` empty, flat `lines`; an old scene: all empty. Never raises."""
    empty: dict = {"chapters": [], "lines": [], "changes": [], "after_frames": []}
    if not isinstance(scene, dict):
        return empty
    try:
        name = lambda c: route.camera_name(c, cameras.get(c), None, lang)  # noqa: E731
        state = es.EpisodeState.from_dict(scene.get("frames_state"))
        view = scene.get("chapters") or []
        rows = state.rows
        out_chapters = []
        for i, c in enumerate(view):
            part = es.EpisodeState.from_dict(scene.get("frames_state"))
            part.rows = [r for r in rows if chapters.chapter_of(view, r["ts"]) == i or (i == 0 and r["ts"] < c["start"])]
            frames_in = [pos for pos, f in enumerate(scene.get("frames") or [])
                         if chapters.chapter_of(view, float(f["ts"])) == i or (i == 0 and float(f["ts"]) < c["start"])]
            out_chapters.append({"n": i + 1, "kind": c["kind"], "signal": c.get("signal") or "",
                                 "start": route.clock(c["start"]), "end": route.clock(c["end"]),
                                 "cameras": [name(x) for x in c.get("cameras") or []], "frames": frames_in,
                                 "lines": es.timeline_lines(part, lang, camera_name=name)})
        changes = [{"camera": name(r.get("camera", "?")), "where": _t(r.get("where") or "pos_mid", lang),
                    "words": r.get("words") or "", "fact": _change_line(r, cameras, lang) if (r.get("thing_before") or
                                                                                             r.get("thing_after")) else ""}
                   for r in (scene.get("changes") or {}).get("regions") or []]
        changes += [{"camera": name(x.get("camera", "?")), "where": "", "words": "", "fact": line}  # J17
                    for x in (scene.get("changes") or {}).get("touched") or []
                    if isinstance(x, dict) and (line := _touch_line(x, cameras, lang))]
        after = [{"camera": name(a.get("camera", "?")), "n": i}
                 for i, a in enumerate((scene.get("changes") or {}).get("after_frames") or [])]
        lines = [] if view else es.timeline_lines(state, lang, camera_name=name)
        return {"chapters": out_chapters, "lines": lines, "changes": changes, "after_frames": after}
    except Exception as e:
        logger.info("[FRIGATE_CONCLUDE] episode story skipped: %s", type(e).__name__)
        return empty


def _change_line(region: dict, cameras: dict, lang: str) -> str:
    camera = region.get("camera", "?")
    cam = route.camera_name(camera, cameras.get(camera), None, lang)
    where = _t(region.get("where") or "pos_mid", lang)
    thing = region.get("thing_before") or region.get("thing_after")
    if thing:  # J15: code facts — the 27B may see the label (the 9B never does)
        key = "change_thing_before" if region.get("thing_before") else "change_thing_after"
        line = _t(key, lang, camera=cam, where=where, label=labels.display(thing, lang))
        if (region.get("thing_cameras") or 0) > 1:
            line += _t("change_thing_cameras", lang, n=region["thing_cameras"])
        if region.get("thing_elsewhere"):
            other = region["thing_elsewhere"]
            line += _t("change_thing_elsewhere", lang, camera=route.camera_name(other, cameras.get(other), None, lang))
        return line
    line = _t("change_line", lang, camera=cam, where=where)
    return f"{line} ({_t('change_words', lang, words=region['words'])})" if region.get("words") else line


def _touch_line(t: dict, cameras: dict, lang: str) -> str:
    """J17: one code fact about a still thing a person was at; «unknown» says nothing (never a guess)."""
    outcome = t.get("outcome")
    if outcome not in ("handled", "moved", "near"):
        return ""
    camera = t.get("camera", "?")
    kw = {"camera": route.camera_name(camera, cameras.get(camera), None, lang),
          "label": labels.display(t.get("label") or "", lang), "secs": round(float(t.get("near_s") or 0)) or 1,
          "start": route.clock(float(t["start"])), "end": route.clock(float(t["end"]))}
    if outcome == "moved":
        return _t("touch_moved", lang, at=route.clock(float(t.get("first_changed") or t["start"])), **kw)
    return _t(f"touch_{outcome}", lang, **kw)


def _touch_lines(touched: list[dict] | None, cameras: dict, lang: str) -> list[str]:
    return [line for t in touched or [] if isinstance(t, dict) and (line := _touch_line(t, cameras, lang))]


async def _touch_step(ep: store.Episode, events: list[store.FrigateUserEvent]) -> list[dict]:
    """J17: still things a person of the episode was at, handled or moved, with the time span (thing_touch). Nothing on
    any failure: one more sensor, the analysis never depends on it."""
    if not TOUCH_ON or not any(e.label == "person" for e in events):
        return []
    try:
        before = await asyncio.to_thread(store.events_between, ep.start_ts - thing_touch.FRESH_S - THING_LOOKBACK_S,
                                         ep.start_ts)
        return await thing_touch.for_episode(ep, events, before)
    except Exception as e:
        logger.info("[FRIGATE_TOUCH] episode %s skipped: %s", ep.episode_id, type(e).__name__)
        return []


def place_fact_lines(ep: store.Episode, events: list[store.FrigateUserEvent], cameras: dict, lang: str) -> list[str]:
    """J18: «kom fra / gikk mot <named spot>» and known hand-overs (places_learned) — code facts for the 27B. Never raises."""
    try:
        answered = store.spot_answers()
        history = store.events_between(ep.start_ts - places_learned.WINDOW_DAYS * 86400, ep.start_ts - 1)
        return places_learned.fact_lines(events, history, answered, cameras or {}, lang, _t)
    except Exception as e:
        logger.info("[FRIGATE_PLACES] episode %s: no place facts (%s)", ep.episode_id, type(e).__name__)
        return []


def images_checked(scene: dict | None) -> bool:
    """Did any model look at this episode's frames (the closed look or the description answered)? P144-s24."""
    if not isinstance(scene, dict):
        return False
    return scene.get("status") == "done" or (scene.get("look") or {}).get("status") == "done"


def _look_line(look: dict | None, lang: str) -> list[str]:
    if not isinstance(look, dict) or look.get("status") != "done":
        return []
    animal = look.get("animal") or _t("none", lang)
    if look.get("animal") and look.get("animal_uncertain") is True:
        animal = f"{animal} {_t('uncertain_animal', lang)}"
    return [_t("look_line", lang, person=_t("yes" if look.get("person") else "no", lang),
               animal=animal, vehicle=_t("yes" if look.get("vehicle") else "no", lang))]


def _scene_lines(scene: dict, lang: str) -> list[str]:
    look = _look_line(scene.get("look"), lang)
    if scene.get("status") != "done":
        return [_t("scene_failed", lang, reason=scene.get("reason") or "?"), *look]
    if scene.get("by") == "frames":  # P144-s25: the code-written timeline replaces the one-call scene
        return [*look, *_frames_lines(scene, lang)]
    lines = [*look, str(scene.get("scene") or "")]
    people = scene.get("people") or []
    if people or scene.get("persons") is not None:
        desc = "; ".join(
            ", ".join(str(p.get(k)) for k in ("age", "gender", "clothing", "carried") if p.get(k)) for p in people)
        lines.append(f"{_t('scene_people', lang)} ({scene.get('persons')}): {desc}")
    if scene.get("animals"):
        uncertain = scene.get("frigate_animals") == [] or (scene.get("look") or {}).get("animal_uncertain") is True
        lines.append(f"{_t('scene_animals', lang)}: " + ", ".join(
            f"{a.get('count', 1)} × {a.get('kind')}" for a in scene["animals"] if isinstance(a, dict))
            + (f" {_t('uncertain_animal', lang)}" if uncertain else ""))
    if scene.get("vehicles"):
        lines.append(f"{_t('scene_vehicles', lang)}: {scene['vehicles']}")
    if scene.get("at_door"):
        lines.append(f"{_t('scene_at_door', lang)}: {scene['at_door']}")
    return lines


def build_scene_prompt(picked: list[frames.Frame], route_text: str, lang: str, cameras: dict,
                       places: dict[str, str]) -> str:
    """The see call (s06d; s09 B9 + s06d Part 0's K-in-prompt): numbered separate images with camera + clock, the
    code route in the same prompt, fixed JSON out. Never a face claim (the model never sees Frigate's names)."""
    labels = "; ".join(
        _t("scene_image", lang, i=i + 1, camera=route.camera_name(f.camera, cameras.get(f.camera),
                                                                   places.get(f.camera), lang),
           time=route.clock(f.ts))
        for i, f in enumerate(picked))
    return "\n\n".join([
        _t("scene_intro", lang, n=len(picked), labels=labels),
        _t("scene_route", lang) + "\n" + route_text,
        _t("scene_task", lang, language=_t("lang_name", lang)),
        _t("answer", lang) + "\n" + _SCHEMA_SCENE,
    ])


def build_look_prompt(picked: list[frames.Frame], lang: str, cameras: dict) -> str:
    """The gate's closed look (s16 = s15 form B): the numbered frames and one non-leading question with every option.
    No route, no Frigate label, no name — and no place either (s15 measured it without)."""
    labels = "; ".join(
        _t("scene_image", lang, i=i + 1, camera=route.camera_name(f.camera, cameras.get(f.camera), None, lang),
           time=route.clock(f.ts))
        for i, f in enumerate(picked))
    return _t("scene_intro", lang, n=len(picked), labels=labels) + "\n\n" + _t("look_question", lang)


def build_doubt_prompt(n: int, crop_of: int, lang: str) -> str:
    """The 27B's look on doubt (P144-s28, measured): `n` images, the last one an enlarged crop of image `crop_of` (0 = no
    crop). A closed list of categories: no Frigate label, no name, no claim, no example (verifier + prompt rules)."""
    head = (_t_i18n("frigate_doubt_question", lang, n=n, k=crop_of) if crop_of
            else _t_i18n("frigate_doubt_question_no_crop", lang, n=n))
    options = [f'- "{a}": {_t_i18n(f"frigate_doubt_option_{a}", lang)}' for a in doubt.ANSWERS]
    return "\n".join([head, *options, _t_i18n("frigate_doubt_answer", lang)])


def copied_phrases(summary: str, scene: dict | None, lang: str) -> list[str]:
    """Two or more words in a row from the 9B's English free text that stand in the memory line (P144-s28 item 4: «potted
    plants» in a Norwegian line). Single words are left out: too many are shared between the languages. Code's check for the
    P142 language detector, nothing more."""
    if lang == es.MODEL_LANG or not isinstance(scene, dict):
        return []
    state = scene.get("frames_state") if isinstance(scene.get("frames_state"), dict) else {}
    values = [p.get(k) for p in scene.get("people") or [] if isinstance(p, dict) for k in ("clothing", "carried")]
    for cam in (state.get("cams") or {}).values():
        values += [p.get(k) for p in (cam.get("persons") or {}).values() for k in ("place", "carried")]
        values += list(cam.get("doors") or {})
    low, found = (summary or "").lower(), set()
    for value in values:
        for part in re.split(r"[;,]", value if isinstance(value, str) else ""):
            words = part.lower().split()
            found.update(" ".join(words[i:i + 2]) for i in range(len(words) - 1) if " ".join(words[i:i + 2]) in low)
    return sorted(found)


def doubt_answer(scene: dict | None) -> str | None:
    """What the 27B answered on doubt (None = not flagged, not asked, or no usable answer)."""
    look = ((scene or {}).get("doubt") or {}).get("look") if isinstance(scene, dict) else None
    return look.get("answer") if isinstance(look, dict) and look.get("status") == "done" else None


def build_urgent_prompt(ev: store.FrigateUserEvent, reason: str, lang: str, *, away: bool) -> str:
    lines = [_t("intro_urgent", lang), "", f"urgent: {reason}", ""]
    lines += _header(ev.start_ts, ev.end_ts or ev.start_ts, [ev.camera], lang, away)
    lines += ["", f"## {_t('h_events', lang)}", _event_line(ev, lang)]
    lines += ["", f"## {_t('h_baseline', lang)}", *_baseline_lines([ev], ev.start_ts, lang)]
    lines += ["", _t("rules", lang, language=_t("lang_name", lang)), _t("rule_verdict", lang), "",
              _t("answer", lang), _SCHEMA_URGENT + "}"]
    return "\n".join(lines)


# ── output ────────────────────────────────────────────────────────────────────

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def normalize_topic(value) -> str:
    """A topic key: lowercase ASCII snake_case, at most _TOPIC_MAX_CHARS («Trailer False-Car!» → trailer_false_car)."""
    key = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return key[:_TOPIC_MAX_CHARS].rstrip("_")


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def parse_conclusion(text: str, urgent: bool = False) -> tuple[dict | None, str]:
    """(conclusion, '') or (None, why it was refused)."""
    raw = _FENCE_RE.sub("", strip_language_tag(text or "").strip()).strip()
    if not raw:
        return None, "empty"
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "no_json_object"
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        return None, f"json_error: {e.msg}"
    if not isinstance(data, dict):
        return None, "not_an_object"

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "summary_missing"
    unusual = data.get("unusual")
    if isinstance(unusual, bool):
        unusual = {"value": unusual, "reason": ""}
    if not isinstance(unusual, dict) or not isinstance(unusual.get("value"), bool):
        return None, "unusual_missing"
    conf = data.get("confidence")
    if not isinstance(conf, dict) or conf.get("level") not in CONFIDENCE_LEVELS:
        return None, "confidence_invalid"
    notify = data.get("notify_suggestion")
    if notify is not None and not isinstance(notify, dict):
        return None, "notify_suggestion_invalid"
    raise_dm = data.get("raise_at_dev_meeting")
    if raise_dm is None:
        dev = None
    elif isinstance(raise_dm, str):  # the old shape (before s06e) — the topic key is set by code on registering
        dev = {"topic": "", "text": raise_dm.strip()} if raise_dm.strip() else None
    elif isinstance(raise_dm, dict):
        text = str(raise_dm.get("text") or "").strip()
        dev = {"topic": normalize_topic(raise_dm.get("topic")), "text": text} if text else None
    else:
        return None, "raise_at_dev_meeting_invalid"

    out = {
        "summary": summary.strip(),
        "unusual": {"value": unusual["value"], "reason": str(unusual.get("reason") or "")},
        "notify_suggestion": notify or None,
        "raise_at_dev_meeting": dev,
        "confidence": {
            "level": conf["level"],
            "reasons": [str(r) for r in (conf.get("reasons") or []) if r],
        },
    }
    if urgent:
        verdict = data.get("claim_verdict")
        if not isinstance(verdict, dict) or verdict.get("verdict") not in VERDICTS:
            return None, "claim_verdict_invalid"
        out["claim_verdict"] = {"verdict": verdict["verdict"], "why": str(verdict.get("why") or "")}
    return out, ""


def _as_text(value) -> str:
    """A model field that should be text but came as a list/dict (seen live: vehicles as a list of objects)."""
    if isinstance(value, list):
        return "; ".join(t for t in (_as_text(v) for v in value) if t)
    if isinstance(value, dict):
        main = str(value.get("type") or value.get("what") or "").strip()
        rest = [str(v) for k, v in value.items() if k not in ("type", "what") and v]
        return f"{main} ({', '.join(rest)})" if main and rest else main or ", ".join(rest)
    text = str(value or "").strip()
    return "" if text.strip(".… ") == "" else text  # the schema's "..." placeholder copied back (seen live, ep97)


def parse_scene(text: str) -> tuple[dict | None, str]:
    """(scene, '') or (None, why it was refused). Only `scene` is required; the rest is normalised."""
    raw = _FENCE_RE.sub("", strip_language_tag(text or "").strip()).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "no_json_object" if raw else "empty"
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        return None, f"json_error: {e.msg}"
    if not isinstance(data, dict):
        return None, "not_an_object"
    scene = _as_text(data.get("scene")) if isinstance(data.get("scene"), str) else ""
    if not scene:
        return None, "scene_missing"
    persons = data.get("persons")
    people = []
    for p in data.get("people") if isinstance(data.get("people"), list) else []:
        if not isinstance(p, dict):
            continue
        people.append({
            "age": p.get("age") if p.get("age") in SCENE_AGES else "unclear",
            "gender": p.get("gender") if p.get("gender") in SCENE_GENDERS else "unclear",
            "clothing": _as_text(p.get("clothing")),
            "carried": _as_text(p.get("carried")),
        })
    animals = []
    for a in data.get("animals") if isinstance(data.get("animals"), list) else []:
        kind = _as_text(a.get("kind") if isinstance(a, dict) else a)
        if not kind or kind.lower() in ("unclear", "none", "ingen"):
            continue
        count = a.get("count") if isinstance(a, dict) else None
        animals.append({"kind": kind,
                        "count": count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1})
    return {
        "persons": persons if isinstance(persons, int) and not isinstance(persons, bool) else None,
        "people": people,
        "animals": animals,
        "vehicles": _as_text(data.get("vehicles")),
        "at_door": _as_text(data.get("at_door")),
        "scene": scene,
    }, ""


_TRUE_WORDS = ("true", "yes", "ja", "1")
_FALSE_WORDS = ("false", "no", "nei", "nein", "0", "none", "null", "")
# «no animal» as the 9B writes it (s15: none / ingen / null / nei …), nb/en/de
_NO_ANIMAL = ("none", "ingen", "null", "nei", "no", "nothing", "ingenting", "keins", "kein", "keine", "nichts", "-")


def _flag(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    return True if text in _TRUE_WORDS else False if text in _FALSE_WORDS else None


def parse_look(text: str) -> tuple[dict | None, str]:
    """(look, '') or (None, why it was refused). `person` and `animal` are required — they are what the gate reads;
    `animal` is the species text or None. An unreadable `person` refuses the answer (the gate then stays open)."""
    raw = _FENCE_RE.sub("", strip_language_tag(text or "").strip()).strip()
    if not raw:
        return None, "empty"
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, "no_json_object"
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        return None, f"json_error: {e.msg}"
    if not isinstance(data, dict):
        return None, "not_an_object"
    if "person" not in data or "animal" not in data:
        return None, "look_fields_missing"
    person = _flag(data["person"])
    if person is None:
        return None, "person_invalid"
    animal = data["animal"]
    if isinstance(animal, bool) or animal is None:
        animal = "unclear" if animal else None  # «true» without a species still says an animal is there
    else:
        animal = _as_text(animal)
        animal = None if animal.strip(".… ").lower() in _NO_ANIMAL else _clip(animal, 40)
    look = {"person": person, "animal": animal, "vehicle": bool(_flag(data.get("vehicle"))),
            "nothing": bool(_flag(data.get("nothing")))}
    if look["nothing"] and (person or animal):
        # P144-s20 item 5 (s18 rec. 4, ep 91): «choose all that apply» over several frames — `nothing` beside a being
        # means some frames are empty (the person had gone in), not an unreadable answer.
        look.update(nothing=False, some_empty=True)
    return look, ""


def _enforce_shadow(text: str, lang: str, rid: str, meta: dict) -> tuple[list[str], object | None]:
    """Hand the raw output to the P142 profile in its own mode (shadow = logged only). Returns (the detectors that
    matched, the correction action when one runs «on»)."""
    if ENFORCER_PROFILE not in PROFILES:
        return [], None
    try:
        enf = ModelEnforcer.for_profile(ENFORCER_PROFILE, lang, rid=rid)
        action = enf.check(text_out=text, used_tools=[], meta=meta)
        return list(enf.hits), action
    except Exception as e:
        logger.debug("[FRIGATE_CONCLUDE] enforcer skipped: %s", e)
        return [], None


async def _ask(prompt: str, images: list[str] | None, rid: str, lang: str, urgent: bool) -> tuple[dict | None, str]:
    try:
        result = await ask_llm_role(ROLE, prompt, images=images, rid=rid)
    except Exception as e:
        return None, f"llm_error: {e}"
    result = result or {}
    if result.get("unreachable"):
        return None, UNREACHABLE
    text = result.get("text", "") or ""
    parsed, reason = parse_conclusion(text, urgent=urgent)
    meta = dict(result.get("meta") or {})
    if reason:
        meta["parse_error"] = reason
    # J16 (R143): fire / smoke / weather / sound / smell the summary claims that nothing in its input supports (code fact)
    meta["kind"] = "conclusion"
    meta["unsupported_senses"] = model_enforcement.unsupported_senses(
        (parsed or {}).get("summary") or "" if parsed else text, lang, prompt)
    _hits, action = _enforce_shadow(text, lang, rid, meta)
    if action is not None and parsed is not None:  # only when admin turned a detector «on»: one correction round
        try:
            retry = await ask_llm_role(ROLE, prompt + "\n\n" + action.correction_message, images=images, rid=f"{rid}-r")
            parsed2, reason2 = parse_conclusion((retry or {}).get("text", "") or "", urgent=urgent)
            if parsed2 is not None and not reason2:
                return parsed2, reason2
        except Exception as e:
            logger.info("[FRIGATE_CONCLUDE] correction round skipped: %s", type(e).__name__)
    return parsed, reason


# ── storage (read-modify-write under one lock) ────────────────────────────────


def _merge(episode_id: int, update: dict | None = None, urgent_item: dict | None = None) -> dict | None:
    with _merge_lock:
        ep = store.episode(episode_id)
        if ep is None:
            return None
        c = dict(ep.conclusion or {})
        if urgent_item is not None:
            c["urgent"] = [*(c.get("urgent") or []), urgent_item]
        if update:
            c.update(update)
        return c if store.conclude_episode(episode_id, c) else None


def _pending(now: float, max_attempts: int) -> list[store.Episode]:
    """Closed episodes still owed a conclusion: none yet (or only urgent results), or failed with
    attempts left. Oldest first."""
    out = []
    for ep in store.episodes_between(now - _PENDING_LOOKBACK_S, now):
        if ep.status != "closed":
            continue
        c = ep.conclusion or {}
        status = c.get("status")
        if status == "done":
            continue
        if status == "failed" and int(c.get("attempts", 0)) >= max_attempts:
            continue
        if status in (WAITING, "failed") and float(c.get("next_try_ts") or 0) > now:  # s24: pause between tries
            continue
        out.append(ep)
    # P144-s26: no look/scene/27B on half an episode — one whose events still wait for their analysis waits too
    busy = store.episodes_with_pending([ep.episode_id for ep in out])
    return [ep for ep in out if ep.episode_id not in busy]


def _waiting(ep: store.Episode) -> bool:
    """Waiting for its image analysis (the image model was unreachable) — retried on the kept frames."""
    return isinstance(ep.scene, dict) and ep.scene.get("status") == WAITING


def _next_try(tries: int, now: float) -> float:
    first, longest = _WAIT_RETRY_S
    return now + min(longest, first * 2 ** max(0, tries - 1))


def episode_rid(episode_id, step: str, n: int | None = None) -> str:
    """J23b: one rid per episode, the step under it — `rid-frig-ep<n>:<step>[:<i>]` (trace_reader.trace_key joins them)."""
    return f"rid-frig-ep{episode_id}:{step}" + (f":{n}" if n is not None else "")


def _camera_cards() -> dict:
    """The camera cards (Integrations → Frigate): display name + role per camera, for the route and the labels."""
    try:
        cams = frigate_store.camera_config().get("cameras") or {}
        return cams if isinstance(cams, dict) else {}
    except Exception:
        return {}


def _claims(events: list[store.FrigateUserEvent]) -> list[dict]:
    """Who the episode's people are, from code (Frigate claim → claim check → storage gate) — never from the model."""
    return [
        {"person_ref": e.person_ref, "person_kind": e.person_kind, "claim_status": e.claim_status}
        for e in events if e.label == "person" and (e.person_ref or e.claim_status != "none")
    ]


async def _look_step(ep: store.Episode, picked: list[frames.Frame], images: list[str], lang: str,
                     cameras: dict) -> dict:
    """The gate's closed look (one `frigate` call, all frames). {status: done, person, animal, vehicle, nothing} or
    {status: failed, reason}; never raises. Through the P142 `frigate` profile as `kind: look`."""
    prompt = build_look_prompt(picked, lang, cameras)
    rid = episode_rid(ep.episode_id, "look")
    try:
        result = await ask_llm_role(SCENE_ROLE, prompt, images=images, rid=rid)
    except Exception as e:
        return {"status": "failed", "reason": f"llm_error: {e}"}
    if (result or {}).get("unreachable"):  # no instance answered — not a model answer (s24 item 3)
        logger.warning("[FRIGATE_CONCLUDE] episode %s look: image model unreachable — waiting", ep.episode_id)
        return {"status": WAITING, "reason": UNREACHABLE}
    parsed, reason, _, action = _judge_look(result, lang, rid)
    if parsed is None and action is not None:  # a P142 detector runs «on»: one correction round, as for the scene
        try:
            retry = await ask_llm_role(SCENE_ROLE, f"{prompt}\n\n{action.correction_message}", images=images, rid=rid)
            parsed2, reason2, _, _ = _judge_look(retry, lang, rid)
            if parsed2 is not None:
                parsed, reason = parsed2, reason2
        except Exception as e:
            logger.info("[FRIGATE_CONCLUDE] episode %s look correction failed: %s", ep.episode_id, e)
    if parsed is None:
        logger.warning("[FRIGATE_CONCLUDE] episode %s look failed: %s", ep.episode_id, reason)
        return {"status": "failed", "reason": reason}
    logger.info("[FRIGATE_CONCLUDE] episode %s look (blind, closed): person=%s animal=%s vehicle=%s nothing=%s",
                ep.episode_id, parsed["person"], parsed["animal"] or "none", parsed["vehicle"], parsed["nothing"])
    return {"status": "done", **parsed}


def _judge_look(result: dict | None, lang: str, rid: str) -> tuple[dict | None, str, list[str], object | None]:
    """Parse one look answer and hand it to the P142 `frigate` profile (`kind: look`): empty → frigate_empty_output,
    unreadable / fields missing → cut_or_invalid_output (`parse_error`), counted like the scene's."""
    text = (result or {}).get("text", "") or ""
    parsed, reason = parse_look(text)
    meta = {**dict((result or {}).get("meta") or {}), "kind": "look"}
    if reason:
        meta["parse_error"] = reason
    hits, action = _enforce_shadow(text, lang, rid, meta)
    return parsed, reason, hits, action


async def _plan_step(ep: store.Episode, events: list[store.FrigateUserEvent],
                     kept: list[tuple[int, frames.Frame]] | None) -> tuple[list[tuple[int, frames.Frame]] | None, dict | None]:
    """P144-s27: chapters + frame budget for a long episode: (the planned (file, frame) list, the plan), or (kept, None)
    when it keeps the grid or anything fails. Stored before any model sees them (s24)."""
    try:
        tracks = await _episode_tracks(events)
        started = time.monotonic()
        plan = await frames.plan_chapters(ep.start_ts, ep.end_ts, tracks)
        if plan and plan["picks"]:
            logger.info("[FRIGATE_FRAMES] episode %s: %d chapters, %d frames planned; scan %d frames, %.1f s", ep.episode_id,
                        len(plan["chapters"]), len(plan["picks"]), plan["scan_frames"], time.monotonic() - started)
            got = await frames.fetch_plan(ep, plan["picks"], kept)
            if got and len(got) >= min(len(kept or []), len(plan["picks"])):
                return got, plan
            if kept:  # recordings gone (an old episode, «Analyser»): the kept frames stay, the chapters still hold
                return kept, plan
            if got:
                return got, plan
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] episode %s: chapter plan failed: %s", ep.episode_id, e)
    return kept, None


def _look_subset(picked: list[frames.Frame], images: list[str]) -> tuple[list[frames.Frame], list[str]]:
    """The closed look stays at the measured ~9 frames however many the budget gave the frame run (even spread)."""
    n = len(picked)
    if n <= frames.N_FRAMES:
        return picked, images
    pos = [round(i * (n - 1) / (frames.N_FRAMES - 1)) for i in range(frames.N_FRAMES)]
    return [picked[i] for i in pos], [images[i] for i in pos]


async def _scene_step(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str,
                      kept: list[tuple[int, frames.Frame]] | None = None, replan: bool = False) -> tuple[dict, str]:
    """Route (code) + the closed look (the gate, s16) + the description (one `frigate` call each with the frames).
    Returns (scene, route); never raises. The description is skipped when the look already closes the gate.
    P144-s24: the frames are fetched, stored and listed **before** any model call, and every outcome (done, skipped,
    failed, waiting) carries the list. `kept` = (file, frame) already on disk (a retry / re-analysis): no new fetch."""
    cameras = await asyncio.to_thread(_camera_cards)
    route_text = route.route_text(ep, events, lang, cameras=cameras)
    # The 9B never sees any Frigate label (s14: with «deer: 1» in the route it listed a deer on a lens full of light
    # streaks 3/3; s15/s16: person and vehicle labels too) — the stored route (for the 27B and the GUI) keeps them.
    blind_route = route.route_text(ep, events, lang, cameras=cameras, blind=True)
    plan = None
    if CHAPTERS_ON and (not kept or replan):
        kept, plan = await _plan_step(ep, events, kept)
    chapter_view = plan["chapters"] if plan else (ep.scene or {}).get("chapters") if kept else None
    if kept:
        files = [i for i, _f in kept]
        picked = [f for _i, f in kept]
    else:
        try:
            picked = await frames.fetch_frames(frames.pick(frames.candidates(events, (ep.start_ts, ep.end_ts)),
                                                           events=events))
        except Exception as e:
            logger.warning("[FRIGATE_CONCLUDE] episode %s: frames failed: %s", ep.episode_id, e)
            picked = []
        files = list(range(len(picked)))
        # s11: keep the exact frames the models will see (file = position), through the store's own caps — before any
        # model call, so a model that is down or answers badly never costs the images (s24 item 2).
        if picked:
            await asyncio.to_thread(store.save_episode_frames, ep.episode_id, [f.jpeg for f in picked])
    if not picked:
        return {"status": "failed", "reason": "no_frames"}, route_text
    listed = {"frames": [{"camera": f.camera, "ts": f.ts, "file": i} for i, f in zip(files, picked, strict=True)],
              "claims": _claims(events), "role": SCENE_ROLE, "lang": lang}
    if chapter_view:
        listed["chapters"] = chapter_view
    images = [base64.b64encode(f.jpeg).decode() for f in picked]
    look = await _look_step(ep, *_look_subset(picked, images), lang, cameras)
    if look.get("status") == WAITING:
        return {"status": WAITING, "reason": UNREACHABLE, "look": look, **listed}, route_text
    # P144-s20 item 4: Frigate's own animal labels in the episode window (any camera, also unticked plain events). An
    # animal only the blind look reports is «uncertain» — garden ornaments (s18) — and never keeps the gate open alone.
    frigate_animals = sorted(await asyncio.to_thread(_window_animal_labels, ep) | (set(ep.labels) & set(store.ANIMAL_LABELS)))
    if look.get("status") == "done" and look.get("animal"):
        look["animal_uncertain"] = not checks.same_animal_kind(look["animal"], frigate_animals)  # s28: the same kind
    seen_by = {"look": look, **listed, "frigate_animals": frigate_animals}
    if label_gate(ep, events, {"look": look}, _is_away(), lang) is not None:
        logger.info("[FRIGATE_CONCLUDE] episode %s: the closed look saw no person or animal — description skipped",
                    ep.episode_id)
        return {"status": "skipped", "reason": "likely_false_detection", **seen_by}, route_text
    if not modules.is_on(4):  # DT04 R0.6b: frame by frame (and its one-call fallback) off — the frames stay kept
        logger.info("[FRIGATE_CONCLUDE] episode %s: module 4 off — no description", ep.episode_id)
        return {"status": "skipped", "reason": MODULE_OFF, **seen_by}, route_text
    frames_failed = None
    if FRAMES_ON:  # P144-s25: frame by frame first; the one-call scene below only when it fails
        run = await _frames_step(ep, events, picked, images, lang, cameras, chapter_view)
        if run.get("status") == WAITING:
            return {"status": WAITING, "reason": UNREACHABLE, **seen_by}, route_text
        if run.get("status") == "done":
            return {**run, **seen_by}, route_text
        frames_failed = run.get("reason") or "?"
        seen_by = {**seen_by, "frames_failed": frames_failed}
    places = {e.camera: e.place for e in events if e.place}
    prompt = build_scene_prompt(picked, blind_route, lang, cameras, places)
    rid = episode_rid(ep.episode_id, "scene")
    try:
        result = await ask_llm_role(SCENE_ROLE, prompt, images=images, rid=rid)
    except Exception as e:
        return {"status": "failed", "reason": f"llm_error: {e}", **seen_by}, route_text
    if (result or {}).get("unreachable"):
        logger.warning("[FRIGATE_CONCLUDE] episode %s scene: image model unreachable — waiting", ep.episode_id)
        return {"status": WAITING, "reason": UNREACHABLE, **seen_by}, route_text
    parsed, reason, hits, action = _judge_scene(result, lang, rid)
    if action is not None and action.detector_name == THIN_SCENE:
        # The P142 detector runs «on»: one correction round, like the pipe's L3 (s06a); the second answer is judged
        # again (counted again if it is still thin).
        try:
            retry = await ask_llm_role(SCENE_ROLE, f"{prompt}\n\n{action.correction_message}", images=images, rid=rid)
            parsed2, reason2, hits2, _ = _judge_scene(retry, lang, rid)
            if parsed2 is not None:
                parsed, reason, hits = parsed2, reason2, hits2
        except Exception as e:
            logger.info("[FRIGATE_CONCLUDE] episode %s scene correction failed: %s", ep.episode_id, e)
    if parsed is None:
        logger.warning("[FRIGATE_CONCLUDE] episode %s scene failed: %s", ep.episode_id, reason)
        return {"status": "failed", "reason": reason, **seen_by}, route_text
    logger.info("[FRIGATE_CONCLUDE] episode %s scene: %d frames, persons=%s", ep.episode_id, len(picked),
                parsed["persons"])
    return {"status": "done", **parsed, "scene_thin": THIN_SCENE in hits, **seen_by}, route_text


def _judge_scene(result: dict | None, lang: str, rid: str) -> tuple[dict | None, str, list[str], object | None]:
    """Parse one scene answer and hand it to the P142 `frigate` profile as the scene call (`kind: scene`)."""
    text = (result or {}).get("text", "") or ""
    parsed, reason = parse_scene(text)
    meta = {**dict((result or {}).get("meta") or {}), "kind": "scene"}
    if reason:
        meta["parse_error"] = reason
    hits, action = _enforce_shadow(text, lang, rid, meta)
    return parsed, reason, hits, action


# ── frame by frame with memory in code (P144-s25) ─────────────────────────────


def _frames_lines(scene: dict, lang: str) -> list[str]:
    """The 27B's view of a frame-by-frame scene: who (registry), the code-written timeline, corrections."""
    who = []
    for p in scene.get("people") or []:
        desc = ", ".join(_t_i18n(f"frigate_frames_{k}_{p.get(k)}", lang) for k in ("age", "gender")
                         if p.get(k) not in (None, "unclear"))
        who.append(_t("frames_person", lang, id=p.get("id", "?"), who=desc or _t_i18n("frigate_frames_unclear", lang),
                      clothing=p.get("clothing") or _t_i18n("frigate_frames_unclear", lang)))
    lines = [_t("frames_persons", lang, people="; ".join(who) or _t("none", lang))]
    lines += list(scene.get("timeline") or [])
    fixes = [c.get("text") for c in ((scene.get("frames_state") or {}).get("corrections") or []) if c.get("text")]
    if fixes:
        lines.append(_t("frames_corrections", lang, items="; ".join(fixes[:4])))
    return lines


async def _timeline(event_id: str) -> list[dict]:
    try:
        return await asyncio.wait_for(frigate_adapter.fetch_timeline(event_id), timeout=_TIMELINE_TIMEOUT_S)
    except Exception as e:  # presence falls back to «end − linger» for this track
        logger.info("[FRIGATE_FRAMES] no timeline for %s (%s)", event_id, type(e).__name__)
        return []


async def _episode_tracks(events: list[store.FrigateUserEvent]) -> list[es.Track]:
    rows = await asyncio.gather(*(_timeline(e.event_id) for e in events))
    return [es.track_from(e.event_id, e.camera, e.label, e.start_ts, e.end_ts, r) for e, r in zip(events, rows,
                                                                                                    strict=True)]


def _position(box) -> str:
    """Code's position words for a Frigate box (relative x, y, w, h) — never a name."""
    try:
        cx, cy = float(box[0]) + float(box[2]) / 2, float(box[1]) + float(box[3]) / 2
    except (TypeError, ValueError, IndexError):
        return "?"
    side = "pos_left" if cx < 0.34 else "pos_right" if cx > 0.66 else "pos_mid"
    return "pos_top" if cy < 0.34 else "pos_bottom" if cy > 0.66 else side


def _before_lines(ep: store.Episode, tracks: list[es.Track], cameras: dict, lang: str) -> list[str]:
    """Cross-episode memory (code → the 27B): per camera of this episode, the previous episode's end state there
    (within BEFORE_WINDOW_S: who was still in the image at the end — a count, never a name — and loose things), and
    what Frigate tracks standing still on that camera now (position words)."""
    out: list[str] = []
    cams = list(dict.fromkeys(ep.cameras))
    try:
        earlier = [e for e in store.episodes_between(ep.start_ts - BEFORE_WINDOW_S, ep.start_ts)
                   if e.episode_id != ep.episode_id and (e.end_ts or e.start_ts) <= ep.start_ts]
    except Exception:
        earlier = []
    for cam in cams:
        name = route.camera_name(cam, cameras.get(cam), None, lang)
        prev = next((e for e in sorted(earlier, key=lambda e: e.end_ts or e.start_ts, reverse=True)
                     if cam in (e.cameras or []) and isinstance(e.scene, dict) and e.scene.get("frames_state")), None)
        if prev is not None:
            st = es.EpisodeState.from_dict(prev.scene["frames_state"])
            cs = st.cam(cam)
            n = len(st.present_ids(cam))
            bits = [_t("before_end_persons", lang, n=n) if n else _t("before_end_nobody", lang)]
            things = [f"{w} ({v})" for w, v in cs["things"].items() if v]
            if things:
                bits.append(_t("before_things", lang, things=", ".join(things[:4])))
            end = prev.end_ts or prev.start_ts
            out.append(_t("before_prev", lang, camera=name, time=route.clock(end),
                          minutes=max(0, round((ep.start_ts - end) / 60)), state="; ".join(bits)))
        for k in tracks:
            if k.camera == cam and k.label in store.VEHICLE_LABELS and any(m[1] == "stationary" for m in k.marks):
                box = next((m[2] for m in reversed(k.marks) if m[2]), None)
                out.append(_t("before_still", lang, camera=name, label=label_name(k.label, lang),
                              where=_t(_position(box), lang) if box else "?"))
    return list(dict.fromkeys(out))


def expressions_allowed(events: list[store.FrigateUserEvent]) -> bool:
    """P144-s27b: ask for expression and gesture in this episode? The house switch is on, and no household member named
    in it has turned it off for themselves (the s02 who-decides rule). An opted-out person is never named, so their
    expression is never tied to a name. Unreadable switches → no (fail closed)."""
    try:
        if not fu_settings.expressions():
            return False
        off = frigate_consent.expressions_off()
    except Exception:
        return False
    if off is None:
        return False
    return not any(e.person_ref and e.person_ref in off for e in events)


def _frame_px(tracks: list[es.Track], camera: str, ts: float) -> int | None:
    """Height in px (of the frame the 9B saw) of the largest person box Frigate has on this camera at this time."""
    heights = [b[3] for k in es.present(tracks, camera, ts) if (b := k.box_at(ts))]
    return round(max(heights) * frames.FRAME_HEIGHT) if heights else None


def _frame_facts(data: dict | None, st: es.EpisodeState, camera: str, step: int, frig_now: int,
                 frig_before: int | None, lang: str) -> dict:
    """Code facts for the `frigate_frames` detectors (P142 `UnsupportedClaimDetector` pattern)."""
    facts: dict = {"kind": "frame"}
    if not isinstance(data, dict):
        return facts
    said_in = [k.get("id") for k in data.get("known") or [] if isinstance(k, dict)
               and str(k.get("in_image", "")).strip().lower() in ("yes", "ja", "true")]
    new = [p for p in data.get("new_persons") or [] if isinstance(p, dict)]
    if step and frig_now == 0 and said_in:
        cur = st.cam(camera)["persons"]

        def real(k: dict) -> bool:  # something newly carried is sight, not a copy (ep 102 #7, the bottle) — s25
            now = es.text_value(k.get("carried"))
            return "carried" in k and bool(now) and now != (cur.get(k.get("id")) or {}).get("carried")

        copied = [k.get("id") for k in data.get("known") or [] if isinstance(k, dict) and k.get("id") in said_in
                  and not real(k)]
        if copied:
            facts["state_carried"] = copied
    if frig_now == 0 and (new or (not step and data.get("persons"))):
        facts["unsupported_changes"] = ["new_person"]
    if step and frig_before is not None and (frig_before > 0) != (frig_now > 0):
        cur = st.cam(camera)["persons"]
        changed = bool(new) or any(es._yes_no(k.get("in_image")) != cur.get(k.get("id"), {}).get("in_image")
                                   for k in data.get("known") or [] if isinstance(k, dict))
        if not changed:
            facts["missed_changes"] = ["appeared" if frig_now else "left"]
    found = es.text_language(es.answer_texts(data))
    if found and found != lang:
        facts["wrong_language"] = [found]
    return facts


def _frames_valid(data: dict | None, full: bool) -> bool:
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("persons"), list) if full else (
        isinstance(data.get("known"), list) or isinstance(data.get("new_persons"), list))


def _chapter_headers(view: list[dict] | None, cameras: dict, lang: str) -> list[tuple[float, str]]:
    """(chapter start, header line) from code, for the timeline the 27B reads."""
    out = []
    for i, c in enumerate(view or []):
        names = ", ".join(dict.fromkeys(route.camera_name(x, cameras.get(x), None, lang) for x in c.get("cameras") or []))
        out.append((c["start"], _t("chapter_line", lang, n=i + 1, start=route.clock(c["start"]), end=route.clock(c["end"]),
                                   kind=_t(f"chapter_{c['kind']}", lang), cameras=names or "—")))
    return out


async def _changes_step(ep: store.Episode, events: list[store.FrigateUserEvent], tracks: list[es.Track]) -> dict:
    """P144-s27 (#5): {regions: [{camera, box, cells, where}], guards, crops} — what looks different after the episode.
    Nothing on any failure: this is one more sensor, the analysis never depends on it. `crops` stays in RAM."""
    if not CHANGES_ON:
        return {"regions": [], "guards": {}}
    try:
        found = await frames.change_regions(ep.start_ts, ep.end_ts, tracks, events)
    except Exception as e:
        logger.info("[FRIGATE_CHANGE] episode %s skipped: %s", ep.episode_id, type(e).__name__)
        return {"regions": [], "guards": {}}
    for r in found["regions"]:
        x0, y0, x1, y1 = r["box"]
        r["where"] = _position([x0, y0, x1 - x0, y1 - y0])
    try:  # J15: what stood (or stands now) in a changed region — code facts from the stationary gate's keys
        before = await asyncio.to_thread(store.events_between, ep.start_ts - THING_BEFORE_S, ep.start_ts)
        after = await asyncio.to_thread(store.events_between, ep.end_ts, ep.end_ts + THING_AFTER_S)
        thing_changes.annotate(found["regions"], before, after)
    except Exception as e:
        logger.info("[FRIGATE_CHANGE] episode %s: no thing facts (%s)", ep.episode_id, type(e).__name__)
    saved = []
    for i, af in enumerate(found.pop("after_frames", None) or []):  # J15: for the GUI only — never a model's input
        file = AFTER_FRAME_FILE0 + i
        if await asyncio.to_thread(store.save_episode_frames, ep.episode_id, [af["jpeg"]], [file]):
            saved.append({"camera": af["camera"], "ts": af["ts"], "file": file})
    found["after_frames"] = saved
    found["touched"] = await _touch_step(ep, events)  # J17
    return found


async def _change_words(ep: store.Episode, found: dict) -> None:
    """The image model's words on a large change region: the neutral crop before and after, no label, no claim (s27). Small
    regions stay a position: on them this 9B invents. Any failure costs only the words."""
    for i, (region, crops) in enumerate(zip(found.get("regions") or [], found.get("crops") or [], strict=False)):
        if region["cells"] < change_detect.WORDS_MIN_CELLS or not crops.get("before") or not crops.get("after"):
            continue
        try:
            images = [base64.b64encode(crops["before"]).decode(), base64.b64encode(crops["after"]).decode()]
            result = await ask_llm_role(SCENE_ROLE, _t_i18n("frigate_change_crop", es.MODEL_LANG), images=images,
                                        rid=episode_rid(ep.episode_id, "change", i + 1))
            data, _reason = es.parse_answer((result or {}).get("text", "") or "")
            words = es.text_value((data or {}).get("different"))
            if words and not (result or {}).get("unreachable"):
                region["words"] = words
        except Exception as e:
            logger.info("[FRIGATE_CHANGE] episode %s region %d: no words (%s)", ep.episode_id, i + 1, type(e).__name__)


def _keep_raw(st, frame: int, text: str) -> None:
    """P144 6e (3): the image model's raw reply on this frame's row (capped like L3's), shown as «Bildemodellens ord (rått)».
    Only on the row of this frame — a skipped frame had no call. Never raises."""
    raw = store.cap_raw(text)
    if raw and st.rows and st.rows[-1].get("frame") == frame:
        st.rows[-1]["raw"] = raw


async def _frames_step(ep: store.Episode, events: list[store.FrigateUserEvent], picked: list[frames.Frame],
                       images: list[str], lang: str, cameras: dict, chapter_view: list[dict] | None = None) -> dict:
    """P144-s25 (Design today #1): the best first frame described in a fixed schema = the episode state held by code;
    every other frame = one image + the state written out by code → only what is different; code applies it, Frigate's
    tracks decide presence (a person the 9B still «sees» where Frigate tracks nobody is set absent by code — measured:
    the 9B copies the state forward on empty frames), code skips frames without Frigate movement. Every call through
    the P142 `frigate_frames` profile (shadow; code acts on its facts itself). Returns a scene dict (status done, by
    frames, timeline, people …) or {status: failed|waiting, reason}; never raises."""
    tz = get_local_tz()
    tracks = await _episode_tracks(events)
    found = await _changes_step(ep, events, tracks)
    pts = [(f.camera, f.ts) for f in picked]
    first = es.best_first(pts, tracks)
    order = [first] + [i for i in range(len(picked)) if i != first]
    looks = await asyncio.to_thread(expressions_allowed, events)  # P144-s27b
    max_persons = es.max_at_once(tracks)
    st = es.EpisodeState()
    last: dict[str, float] = {}
    calls = failed = 0
    hits_seen: dict[str, int] = {}
    t0 = time.monotonic()
    for step, i in enumerate(order):
        f = picked[i]
        cam_name = route.camera_name(f.camera, cameras.get(f.camera), None, es.MODEL_LANG)
        if step and f.camera in last and not es.moved_between(tracks, f.camera, last[f.camera], f.ts):
            st.skip(f.camera, f.ts, i + 1, tz=tz)
            last[f.camera] = f.ts
            continue
        frig_now = len(es.present(tracks, f.camera, f.ts))
        frig_before = len(es.present(tracks, f.camera, last[f.camera])) if f.camera in last else None
        mlang = es.MODEL_LANG  # rule-8 exception (episode_state.MODEL_LANG): the 9B's frame calls are English
        if step == 0:
            task = _t_i18n("frigate_frames_full_ex" if looks else "frigate_frames_full", mlang, camera=cam_name, time=route.clock(f.ts),
                           language=_t("lang_name", mlang))
        else:
            task = _t_i18n("frigate_frames_change_ex" if looks else "frigate_frames_change", mlang, camera=cam_name, time=route.clock(f.ts),
                           state=st.prompt_text(f.camera, mlang), language=_t("lang_name", mlang))
        # s25 (d): Kåre's honesty block in front of the frame prompt made this 9B write more English, more new persons
        # and more things — it is not in the prompt; it closes the P142 correction messages instead.
        prompt = task
        rid = episode_rid(ep.episode_id, "frames", i + 1)
        try:
            result = await ask_llm_role(SCENE_ROLE, prompt, images=[images[i]], rid=rid)
        except Exception as e:
            result = {"text": "", "meta": {"error": str(e)}}
        calls += 1
        if (result or {}).get("unreachable"):
            logger.warning("[FRIGATE_FRAMES] episode %s: image model unreachable — waiting", ep.episode_id)
            return {"status": WAITING, "reason": UNREACHABLE}
        text = (result or {}).get("text", "") or ""
        used_text = text  # P144 6e (3): the reply this frame's row came from, kept raw on the row
        data, reason = es.parse_answer(text)
        if data is not None and not _frames_valid(data, step == 0):
            data, reason = None, "schema"
        meta = {**dict((result or {}).get("meta") or {}),
                **_frame_facts(data, st, f.camera, step, frig_now, frig_before, mlang)}
        if reason:
            meta["parse_error"] = reason
        hits, action = _enforce_frames(text, mlang, rid, meta)
        if action is not None:  # a detector runs «on» in the GUI: one correction round for this frame
            try:
                retry = await ask_llm_role(SCENE_ROLE, f"{prompt}\n\n{action.correction_message}",
                                           images=[images[i]], rid=rid)
                calls += 1
                data2, _reason2 = es.parse_answer((retry or {}).get("text", "") or "")
                if data2 is not None and _frames_valid(data2, step == 0):
                    data, reason = data2, ""
                    used_text = (retry or {}).get("text", "") or ""
            except Exception as e:
                logger.info("[FRIGATE_FRAMES] episode %s frame %d correction failed: %s", ep.episode_id, i + 1, e)
        for h in hits:
            hits_seen[h] = hits_seen.get(h, 0) + 1
        if data is None:
            failed += 1
            if step == 0:
                logger.warning("[FRIGATE_FRAMES] episode %s: first frame unreadable (%s) — one-call scene instead",
                               ep.episode_id, reason)
                return {"status": "failed", "reason": f"first_frame: {reason}"}
            st.skip(f.camera, f.ts, i + 1, tz=tz)  # the state stands; the row says the answer was unreadable
            st.rows[-1]["kind"] = "unreadable"
            _keep_raw(st, i + 1, used_text)
            last[f.camera] = f.ts
            continue
        if frig_now == 0:  # no person where Frigate tracks nobody (s25: a ghost on the empty night porch, ep 106)
            data = {**data, "persons": [], "new_persons": []}
        if step == 0:
            st.apply_full(data, f.camera, f.ts, i + 1, tz=tz, looks=looks)
            changes = []
        else:
            changes = st.apply_change(data, f.camera, f.ts, i + 1, max_persons, tz=tz, looks=looks)
        if frig_now == 0 and st.present_ids(f.camera):
            # s25 measured: the 9B copies the state forward on empty frames (27/36); Frigate alone also loses a person
            # it cannot box (ep 102 #7, the bottle frame). So code sets absent every person without something newly
            # carried in this frame — a reworded place or a flipped posture is no proof of sight (es.changed_ids).
            copied = [pid for pid in st.present_ids(f.camera) if pid not in es.changed_ids(changes)]
            if copied:
                st.set_absent(f.camera, f.ts, "frigate_no_track", tz=tz, ids=copied)
        if looks:
            es.limit_looks(st.rows[-1], _frame_px(tracks, f.camera, f.ts))
        _keep_raw(st, i + 1, used_text)
        last[f.camera] = f.ts
    await _change_words(ep, found)
    before = _before_lines(ep, tracks, cameras, lang)
    logger.info("[FRIGATE_FRAMES] episode %s: %d frames, %d calls, %d unreadable, %d persons, %.1f s, hits=%s",
                ep.episode_id, len(picked), calls, failed, len(st.persons), time.monotonic() - t0, hits_seen or "-")
    return {"status": "done", "by": "frames", **es.scene_fields(st, lang),
            "timeline": es.timeline_lines(st, lang, camera_name=lambda c: route.camera_name(
                c, cameras.get(c), None, lang), headers=_chapter_headers(chapter_view, cameras, lang)),
            "frames_state": st.to_dict(), "before": before,
            "changes": {"regions": found.get("regions") or [], "guards": found.get("guards") or {},
                        "after_frames": found.get("after_frames") or [], "touched": found.get("touched") or []},
            "frames_run": {"calls": calls, "unreadable": failed, "first": first + 1, "max_persons": max_persons,
                           "secs": round(time.monotonic() - t0, 1), "hits": hits_seen}}


def _enforce_frames(text: str, lang: str, rid: str, meta: dict) -> tuple[list[str], object | None]:
    """The `frigate_frames` profile on one frame answer. Its corrections go to the 9B → always in es.MODEL_LANG
    (`lang` is ignored on purpose; rule-8 exception, see episode_state.MODEL_LANG)."""
    if FRAMES_PROFILE not in PROFILES:
        return [], None
    try:
        enf = ModelEnforcer.for_profile(FRAMES_PROFILE, es.MODEL_LANG, rid=rid)
        action = enf.check(text_out=text, used_tools=[], meta=meta)
        return list(enf.hits), action
    except Exception as e:
        logger.debug("[FRIGATE_FRAMES] enforcer skipped: %s", e)
        return [], None


# ── known camera facts (s06e) ─────────────────────────────────────────────────


def _world_rows() -> list[dict]:
    """The household's world facts, ranked (importance, newest) — the same accessor the P133 prompt uses."""
    try:
        return ks.get_top_facts("world", limit=_WORLD_ROWS, exclude_section_prefixes=["var::"])
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] world facts unreadable: %s", e)
        return []


def _untracked_names() -> set[str] | None:
    """Every name of a person who said «Frigate may not track me» — scrubbed from the facts block. None = lookup
    failed (the caller leaves the block out: fail closed)."""
    try:
        names = set(frigate_consent.untracked_faces())
        for u in user_store.list_users():
            if not u.get("frigate_tracking", True):
                names.update(n for n in (u.get("username"), u.get("display_name"), *(u.get("frigate_faces") or [])) if n)
        return names
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] consent lookup failed, camera facts left out: %s", e)
        return None


def camera_fact_lines(events: list[store.FrigateUserEvent], cameras: dict) -> list[str]:
    """World facts about this episode's cameras/places first, then the rest of the camera categories, whole lines
    up to P133's `core_facts_max_chars`. Read-only; opted-out names scrubbed."""
    rows = _world_rows()
    if not rows:
        return []
    names = _untracked_names()
    if names is None:
        return []
    terms = set()
    for cam in {e.camera for e in events}:
        card = cameras.get(cam) or {}
        terms.update({cam, cam.replace("_", " "), str(card.get("display_name") or "")})
    terms.update(e.place for e in events if e.place)
    pattern = [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE) for t in terms if len(t.strip()) >= 3]
    mentions, category = [], []
    for r in rows:
        text = f"{r.get('section') or ''} {r.get('content') or ''}"
        if any(p.search(text) for p in pattern):
            mentions.append(r)
        elif (r.get("section") or "").split("::", 1)[0].strip().lower() in CAMERA_CATEGORIES:
            category.append(r)
    try:
        cap = int(get_memory_prompt_config()["core_facts_max_chars"])
    except Exception:
        cap = 1500
    lines, used = [], 0
    for r in mentions + category:
        line = "- " + " ".join(str(store._scrub_text(r.get("content") or "", names)).split())
        if used + len(line) + 1 > cap:
            if not lines:
                lines.append(_clip(line, cap))
            break
        lines.append(line)
        used += len(line) + 1
    return lines


# ── vehicle-only episodes, the thin-baseline gate, dev topics (s06e) ──────────


def vehicles_only(labels) -> bool:
    """An episode of only vehicle labels (s06d's vocabulary) — no person, no animal, nothing else."""
    labels = list(labels or [])
    return bool(labels) and all(lab in store.VEHICLE_LABELS for lab in labels)


def _ungated(ep: store.Episode, events: list[store.FrigateUserEvent], away: bool) -> bool:
    """Episodes the thin-baseline rule never touches: urgent classes, not welcome, a person while away, fire."""
    c = _settings()["conclude"]
    if (ep.conclusion or {}).get("urgent"):
        return True
    for e in events:
        if e.person_kind == "not_welcome" or e.person_kind in (c.get("urgent_classes") or []):
            return True
        if e.label in FIRE_LABELS or e.label in (c.get("urgent_labels") or []):
            return True
        if e.label == "person" and away:
            return True
    return False


def _gate_unusual(parsed: dict, ep: store.Episode, events: list[store.FrigateUserEvent], away: bool,
                  lang: str) -> dict:
    """«Never seen» on a baseline under MIN_TRUST_DAYS cannot make an episode unusual — a code gate, not a prompt
    wish (shadow day 1: 18 of 40 «unusual» on 2 days). The model's view is kept for review (butler principle)."""
    if not parsed["unusual"]["value"] or _ungated(ep, events, away):
        return parsed
    days = normalcy.baseline_days(sorted({e.camera for e in events}))
    if days >= normalcy.MIN_TRUST_DAYS:
        return parsed
    return {
        **parsed,
        "unusual": {"value": False, "reason": _t("unusual_thin", lang, days=days, need=normalcy.MIN_TRUST_DAYS)},
        "unusual_suppressed": {**parsed["unusual"], "baseline_days": days},
    }


def _register_dev_topic(dev: dict | None, episode_id: int | None, ts: float) -> dict | None:
    """Count the suggestion in frigate.db's topic register: raised once until resolved. The P118 theme is set by
    code here (agenda_themes.resolve_theme) so the row is ready for the agenda inbox; shadow: nothing goes there."""
    if not dev:
        return None
    theme = agenda_themes.resolve_theme(text=dev["text"], source="frigate")
    topic = dev["topic"] or f"untagged_{theme}"
    note = store.note_dev_topic(topic, dev["text"], theme, ts, episode_id)
    return {"topic": topic, "text": dev["text"], "theme": theme,
            "raised": note["raised"] if note else None, "count": note["count"] if note else None}


# ── the label claim gate + weather context (s14) ──────────────────────────────


def _num(value, digits: int = 1) -> str:
    """1.2 → «1,2» in nb/de, «1.2» in en is handled by the caller; here only rounding (no locale lib)."""
    return f"{round(float(value), digits):g}"


def weather_line(weather: dict | None, lang: str) -> str:
    """One code line about the weather at the episode, in the household language. '' when no weather was asked."""
    if not weather:
        return ""
    if weather.get("status") != "ok":
        return _t("w_none", lang, reason=weather.get("reason") or weather.get("status") or "?")
    sep = "." if lang == "en" else ","

    def fmt(v) -> str:
        return _num(v).replace(".", sep)

    parts = []
    precip, hour = weather.get("precip_mm_h"), weather.get("precip_last_hour_mm")
    if precip is not None:
        parts.append(_t("w_precip", lang, mm=fmt(precip)) if precip > 0.05 else _t("w_dry", lang))
    if hour is not None and hour > 0.05:
        parts.append(_t("w_precip_hour", lang, mm=fmt(hour)))
    unit = weather.get("wind_unit") or "m/s"
    if weather.get("wind") is not None:
        parts.append(_t("w_wind", lang, v=fmt(weather["wind"]), unit=unit))
    if weather.get("gust") is not None:
        parts.append(_t("w_gust", lang, v=fmt(weather["gust"]), unit=unit))
    if weather.get("cloud_pct") is not None:
        parts.append(_t("w_cloud", lang, pct=round(float(weather["cloud_pct"]))))
    if not parts:
        return _t("w_none", lang, reason="no_values")
    return _t("w_line", lang, source=weather.get("source") or "?", parts=", ".join(parts))


async def _episode_weather(ep: store.Episode) -> dict | None:
    """The weather around the episode from the user's one weather config (Settings → Weather: the HA sensors there
    when they are on, else the chosen service). None = switched off in Integrations → Frigate. Never raises."""
    try:
        if not fu_settings.episode_weather():
            return None
    except Exception:
        return None
    age = time.time() - float(ep.end_ts or ep.start_ts)
    if age > _WEATHER_MAX_AGE_S:
        return {"status": "too_old", "age_s": round(age)}
    try:
        w = await weather_adapter.current_conditions()
    except Exception as e:
        logger.info("[FRIGATE_CONCLUDE] episode %s: weather unavailable (%s)", ep.episode_id, type(e).__name__)
        return {"status": "unavailable", "reason": type(e).__name__}
    return {**w, "age_s": round(age)} if w else {"status": "unavailable", "reason": "no_source"}


def label_name(label: str, lang: str) -> str:
    names = _LABEL_NAMES.get(label)
    return (names.get(lang) or names["en"]) if names else label


def _joined(items: list[str], lang: str) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} {_t('and', lang)} {items[-1]}"


def false_detection_summary(fd: dict, part: str, lang: str) -> str:
    """«Trolig feildeteksjon: Frigate sa «hjort», bildet viste … (natt, nedbør 1,2 mm/t …).»"""
    labels = _joined([f"«{label_name(lab, lang)}»" for lab in fd["frigate_labels"]], lang)
    w = weather_line(fd.get("weather"), lang)
    w = w[:1].lower() + w[1:] if lang != "de" else w  # mid-sentence (German nouns keep their capital)
    context = ", ".join(x for x in (part, w) if x)
    seen = (fd.get("seen") or "").strip().rstrip(".")
    seen = seen[:1].lower() + seen[1:] if seen[:2].isalpha() and not seen[:2].isupper() else seen
    return _t("false_summary", lang, labels=labels, seen=seen, context=context)


def _l3_persons(ev: store.FrigateUserEvent) -> int:
    n = (ev.attributes or {}).get("persons") if isinstance(ev.attributes, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) else 0


def _l3_blind(ev: store.FrigateUserEvent) -> bool:
    """The event's L3 ran without Frigate's label in its prompt (pipe, s16). Earlier L3 counts were primed."""
    return isinstance(ev.check, dict) and ev.check.get("l3_blind") is True


def _window_animal_labels(ep: store.Episode) -> set[str]:
    """Frigate's animal labels on any stored event that overlaps the episode window (also plain events of an unticked
    label, which never join an episode — ep 86's real dog). Never raises."""
    try:
        rows = store.events_between(ep.start_ts - frames.EVENT_CAP_S, ep.end_ts)
    except Exception as e:
        logger.info("[FRIGATE_CONCLUDE] episode %s: window labels unreadable (%s)", ep.episode_id, type(e).__name__)
        return set()
    return {e.label for e in rows if e.label in store.ANIMAL_LABELS
            and (e.end_ts if e.end_ts is not None else e.start_ts) >= ep.start_ts}


def _seen_text(look: dict, lang: str) -> str:
    """What the frames showed, for the false-detection line — built by code from the closed look's fields (the
    description is not run when the gate closes)."""
    if look.get("animal") and look.get("animal_uncertain") is True:
        return f"{_t('seen_uncertain_animal', lang, animal=look['animal'])} ({_t('from_look', lang)})"
    text = _t("seen_vehicle" if look.get("vehicle") else "seen_nothing", lang)
    return f"{text} ({_t('from_look', lang)})"


def label_gate(ep: store.Episode, events: list[store.FrigateUserEvent], scene: dict | None,
               away: bool, lang: str = "en") -> dict | None:
    """The label claim gate: {frigate_labels, seen, look} when Frigate claimed a person/animal and the closed blind
    look (s16, form B) saw neither, else None. Reads only the look — the description (C) is not the gate. Never for
    urgent cases or a failed look; Frigate's score is never read (0.82 false vs 0.89–0.94 real deer, 2026-10-28)."""
    claimed = sorted({e.label for e in events if e.label in BEING_LABELS})
    look = scene.get("look") if isinstance(scene, dict) else None
    if not claimed or not isinstance(look, dict) or look.get("status") != "done":
        return None
    if look.get("person") or (look.get("animal") and look.get("animal_uncertain") is not True):
        return None
    # Item 4 (s16): a *blind* single-image check (L3) that counted a person disagrees with the look — two blind looks
    # disagree, the 27B decides (safe direction; episode 23, 2026-10-27: a far-away person on the road). A primed L3
    # (Frigate's label was in its prompt, before s16) does not block: its count is the yes-bias s15 measured.
    if any(e.label == "person" and _l3_blind(e) and _l3_persons(e) > 0 for e in events):
        return None
    if _ungated(ep, events, away):
        return None
    return {"frigate_labels": claimed, "seen": _seen_text(look, lang),
            "look": {k: look.get(k) for k in ("person", "animal", "vehicle", "nothing", "animal_uncertain")
                     if k in look}}


async def _conclude_false_detection(ep: store.Episode, events: list[store.FrigateUserEvent], fd: dict,
                                    lang: str) -> bool:
    """Code conclusion for a likely false detection: not unusual, no alert, kept for the review list. No 27B."""
    labels = ", ".join(fd["frigate_labels"])
    by_27b = "doubt_look" in fd
    update = {
        "status": "done", "by": "code", "attempts": 0, "reason": "",
        "summary": false_detection_summary(fd, route.part_of_day(ep.start_ts, lang), lang),
        "unusual": {"value": False, "reason": _t("doubt_unusual" if by_27b else "false_unusual", lang)},
        "notify_suggestion": None, "raise_at_dev_meeting": None,
        "confidence": {"level": "medium", "reasons": [
            _t("doubt_reason", lang, labels=labels, answer=fd["doubt_look"]["answer"]) if by_27b
            else _t("false_reason", lang, labels=labels)]},
        "false_detection": fd, "lang": lang, "role": "code",
    }
    logger.info("[FRIGATE_CONCLUDE] episode %s concluded by code: likely false detection (Frigate %s, %s)", ep.episode_id,
                labels, "the 27B's look on doubt" if by_27b else "the closed look saw no people/animals, no 27B call")
    if await asyncio.to_thread(_merge, ep.episode_id, update) is None:
        return False
    _remember(ep.episode_id, ep.start_ts, update, lang)
    return True


async def _conclude_by_code(ep: store.Episode) -> bool:
    """A vehicle-only episode: no 9B scene, no 27B — the code route + one code summary line, stored as done."""
    lang = get_lang()
    events = await asyncio.to_thread(store.episode_events, ep.episode_id)
    if not events:
        return False
    cameras = await asyncio.to_thread(_camera_cards)
    route_text = route.route_text(ep, events, lang, cameras=cameras)
    scene: dict = {"status": "skipped", "reason": "vehicles_only"}
    if ep.scene is None:  # s24 item 2: the code-picked frames are kept and listed here too (no model needs them)
        listing, _got, _missing = await frames.ensure(ep, events, asyncio.Semaphore(frames.N_FRAMES))
        if listing:
            scene["frames"] = listing
    else:
        scene = {**ep.scene, **scene}
    await asyncio.to_thread(store.set_episode_scene, ep.episode_id, scene, route_text)
    update = {
        "status": "done", "by": "code", "attempts": 0, "reason": "",
        "summary": route.vehicle_summary(ep, events, lang, cameras=cameras),
        "unusual": {"value": False, "reason": _t("code_unusual", lang)},
        "notify_suggestion": None, "raise_at_dev_meeting": None,
        "confidence": {"level": "medium", "reasons": [
            _t("code_reason", lang, n=len(events), k=len({e.camera for e in events}))]},
        "lang": lang, "role": "code",
    }
    logger.info("[FRIGATE_CONCLUDE] episode %s concluded by code: vehicles only (%d events, no model call)",
                ep.episode_id, len(events))
    if await asyncio.to_thread(_merge, ep.episode_id, update) is None:
        return False
    _remember(ep.episode_id, ep.start_ts, update, lang)
    return True


MODULE_OFF = "module_off"


async def _conclude_modules_off(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str) -> bool:
    """Module 5 off (DT04 R0.6b): the episode is closed by code — the stored route and frames stay, no model wrote a
    conclusion, so it is not a memory line (`stm.counts_as_memory` takes only model conclusions)."""
    update = {
        "status": "done", "by": "code", "attempts": 0, "reason": MODULE_OFF,
        "summary": _t("modules_off_summary", lang, n=len(events), k=len({e.camera for e in events})),
        "unusual": {"value": False, "reason": _t("modules_off_unusual", lang)},
        "notify_suggestion": None, "raise_at_dev_meeting": None,
        "confidence": {"level": "low", "reasons": [_t("modules_off_unusual", lang)]},
        "lang": lang, "role": "code",
    }
    logger.info("[FRIGATE_CONCLUDE] episode %s closed by code: module 5 off (%d events, no model call)",
                ep.episode_id, len(events))
    return await asyncio.to_thread(_merge, ep.episode_id, update) is not None


def _remember(episode_id: int, ts: float, update: dict, lang: str, urgent: bool = False) -> None:
    """The concluded line into the Frigate user's STM (s16) — only once it is stored in frigate.db, and only what
    counts as memory (P144-s20 item 6: the one rule `stm.rebuild()` and the nightly LTM step use — never a likely false
    detection or a vehicle-only code line; an urgent result is always a memory line, as in `stm.memory_lines`)."""
    if urgent:
        if update.get("status") == "done" and str(update.get("summary") or "").strip():
            stm.remember(episode_id, ts, update["summary"], lang, urgent=True)
    elif stm.counts_as_memory(update):
        stm.remember(episode_id, ts, update["summary"], lang)


async def _conclude_episode(ep: store.Episode, now: float | None = None) -> bool:
    lang = get_lang()
    now = time.time() if now is None else now
    events = await asyncio.to_thread(store.episode_events, ep.episode_id)
    if not counts.loaded():  # normally rebuilt at start (start_conclude_loop); once, never in the everyday path
        await asyncio.to_thread(counts.rebuild)
    weather = None
    if ep.scene is None or _waiting(ep):  # once per episode — a failed scene is stored too, the conclusion goes on
        # with the route; a waiting one (model unreachable, s24) runs again on the frames it already kept
        kept = await asyncio.to_thread(frames.kept, ep) if ep.scene else None
        merged = (ep.scene or {}).get("reason") == MERGED_REASON  # s27: a merged window deserves its own plan
        ep.scene, ep.route = await (_scene_step(ep, events, lang, kept=kept, replan=True) if merged
                                    else _scene_step(ep, events, lang, kept=kept))
        if not _waiting(ep):
            weather = await _episode_weather(ep)  # after the see call: the 9B is never primed with «rain»
            w_line = weather_line(weather, lang)
            if w_line:
                ep.route = f"{ep.route}\n{w_line}" if ep.route else w_line
        await asyncio.to_thread(store.set_episode_scene, ep.episode_id, ep.scene, ep.route)
    if _waiting(ep):
        return await _keep_waiting(ep, now)
    return await _conclude_from_scene(ep, events, lang, weather, now)


async def _keep_waiting(ep: store.Episode, now: float) -> bool:
    """s24 item 3: no 27B conclusion built on nothing — the episode stays «venter på analyse» and is tried again."""
    c = ep.conclusion or {}
    tries = int(c.get("analysis_tries", 0)) + 1 if c.get("status") == WAITING else 1
    update = {"status": WAITING, "reason": UNREACHABLE, "analysis_tries": tries, "next_try_ts": _next_try(tries, now),
              "attempts": int(c.get("attempts", 0))}
    logger.warning("[FRIGATE_CONCLUDE] episode %s waits for its image analysis (model unreachable, try %d)",
                   ep.episode_id, tries)
    stored = await asyncio.to_thread(_merge, ep.episode_id, update)
    if stored is not None:
        ep.conclusion = stored
    return False


async def _doubt_assess(ep: store.Episode, events: list[store.FrigateUserEvent]) -> dict | None:
    """The doubt flag (code, no model) stored on the scene once. IR is checked first: on an ordinary episode no timeline is
    fetched for this. None when the scene has no kept frames to judge. Never raises."""
    scene = ep.scene
    if not isinstance(scene, dict) or not scene.get("frames"):
        return None
    if isinstance(scene.get("doubt"), dict):
        return scene["doubt"]
    try:
        kept = await asyncio.to_thread(frames.kept, ep)
        picked = [f for _i, f in kept]
        has_ir = await asyncio.to_thread(doubt.any_ir, picked)
        tracks = await _episode_tracks(events) if has_ir else []
        assessment = await asyncio.to_thread(doubt.assess, picked, tracks)
    except Exception as e:
        logger.info("[FRIGATE_CONCLUDE] episode %s: doubt flag not computed (%s)", ep.episode_id, type(e).__name__)
        return None
    ep.scene = {**scene, "doubt": assessment}
    await asyncio.to_thread(store.set_episode_scene, ep.episode_id, ep.scene, ep.route)
    if assessment["flag"]:
        logger.info("[FRIGATE_CONCLUDE] episode %s: doubt flag raised (%s)", ep.episode_id, assessment["signals"])
    return assessment


def _judge_doubt(result: dict | None, lang: str, rid: str) -> dict:
    """Parse the 27B's answer and hand it to the P142 `frigate` profile (shadow; `kind: doubt_look`): empty and
    unreadable are counted like the other Frigate calls. A closed list has no free text to check for language."""
    text = (result or {}).get("text", "") or ""
    answer, reason = doubt.parse_answer(text)
    meta = {**dict((result or {}).get("meta") or {}), "kind": "doubt_look"}
    if reason:
        meta["parse_error"] = reason
    _enforce_shadow(text, lang, rid, meta)
    return {"status": "done", "answer": answer} if answer else {"status": "failed", "reason": reason}


async def _doubt_look(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str, gate_idle: bool) -> bool:
    """The 27B's look when the flag is up, once per episode, stored on the scene. False = not now (a user is talking, or
    the 27B cannot be reached): nothing is stored, no attempt is used, the episode is picked up again. A bad answer is
    stored and the episode goes on to its normal conclusion."""
    assessment = await _doubt_assess(ep, events)
    if not assessment or not assessment.get("flag") or isinstance(assessment.get("look"), dict):
        return True
    if gate_idle and not _idle(_settings()["conclude"]):
        logger.debug("[FRIGATE_CONCLUDE] episode %s: doubt look waits for the idle gate", ep.episode_id)
        return False
    kept = await asyncio.to_thread(frames.kept, ep)
    picked = [f for _i, f in kept]
    tracks = await _episode_tracks(events)
    material, crop_of = await asyncio.to_thread(doubt.look_material, picked, tracks, assessment)
    if not material:
        look = {"status": "failed", "reason": "no_material"}
    else:
        rid = episode_rid(ep.episode_id, "doubt")
        started = time.monotonic()
        try:
            result = await ask_llm_role(ROLE, build_doubt_prompt(len(material), crop_of, lang),
                                        images=[base64.b64encode(m).decode() for m in material], rid=rid)
        except Exception as e:
            result = {"unreachable": True}
            logger.info("[FRIGATE_CONCLUDE] episode %s doubt look failed: %s", ep.episode_id, type(e).__name__)
        if (result or {}).get("unreachable"):
            logger.warning("[FRIGATE_CONCLUDE] episode %s doubt look: model unreachable, asked again later", ep.episode_id)
            return True  # the conclusion's own unreachable handling takes the pause; the flag stays unanswered
        look = {**_judge_doubt(result, lang, rid), "seconds": round(time.monotonic() - started, 1),
                "images": len(material)}
        logger.info("[FRIGATE_CONCLUDE] episode %s doubt look: %s (%.1f s, %d images)", ep.episode_id,
                    look.get("answer") or look.get("reason"), look["seconds"], len(material))
    ep.scene = {**ep.scene, "doubt": {**assessment, "look": look}}
    await asyncio.to_thread(store.set_episode_scene, ep.episode_id, ep.scene, ep.route)
    return True


def doubt_gate(ep: store.Episode, events: list[store.FrigateUserEvent], scene: dict | None, away: bool,
               lang: str = "en") -> dict | None:
    """The 27B's answer as one more sensor vote, decided here in code: at the lens / nothing, and no other person in the
    episode → {frigate_labels, seen, doubt_look}, concluded as a likely false detection. Never for urgent cases (the same
    rule as the label gate). A person, an animal, unclear or no answer → None: the normal conclusion."""
    answer = doubt_answer(scene)
    assessment = (scene or {}).get("doubt") if isinstance(scene, dict) else None
    if answer not in doubt.FALSE_ANSWERS or doubt.other_person(assessment, events) or _ungated(ep, events, away):
        return None
    claimed = sorted({e.label for e in events if e.label in BEING_LABELS})
    seen = _t("seen_at_lens" if answer == "at_lens" else "seen_nothing", lang)
    return {"frigate_labels": claimed, "seen": f"{seen} ({_t('from_doubt_look', lang)})",
            "doubt_look": {"answer": answer, "signals": assessment.get("signals"), "tracks": assessment.get("tracks")}}


async def _conclude_from_scene(ep: store.Episode, events: list[store.FrigateUserEvent], lang: str,
                               weather: dict | None, now: float, *, gate_idle: bool = True) -> bool:
    """The label gate, then the 27B's look on doubt, then the 27B's conclusion — on the scene already stored on the
    episode. `gate_idle=False` when a person asked for it (the «Analyser» button)."""
    away = _is_away()
    fd = label_gate(ep, events, ep.scene, away, lang)
    if fd is not None:
        return await _conclude_false_detection(ep, events, {**fd, "weather": weather}, lang)
    if not modules.is_on(5):  # DT04 R0.6b: no conclusion call — closed by code so it never waits forever
        return await _conclude_modules_off(ep, events, lang)
    if modules.is_on(6) and not await _doubt_look(ep, events, lang, gate_idle):
        return False  # a user is talking: the next round picks it up (no attempt used)
    fd = doubt_gate(ep, events, ep.scene, away, lang)
    if fd is not None:
        return await _conclude_false_detection(ep, events, {**fd, "weather": weather}, lang)
    cameras = await asyncio.to_thread(_camera_cards)
    facts = await asyncio.to_thread(camera_fact_lines, events, cameras)
    facts = [*facts, *await asyncio.to_thread(place_fact_lines, ep, events, cameras, lang)]  # J18
    topics = (await asyncio.to_thread(store.dev_topics, "open"))[:_OPEN_TOPICS]
    hidden = await asyncio.to_thread(_untracked_names)
    prompt = build_episode_prompt(ep, events, lang, away=away, cameras=cameras, hidden=hidden, facts=facts,
                                  topics=topics)
    attempts = int((ep.conclusion or {}).get("attempts", 0)) + 1
    rid = episode_rid(ep.episode_id, "conclude", attempts)
    parsed, reason = await _ask(prompt, None, rid, lang, urgent=False)
    checked = images_checked(ep.scene)
    if parsed is None and reason == UNREACHABLE:
        # s24: the 27B was not there — not a bad answer, so no attempt is used up; tried again after a pause
        tries = int((ep.conclusion or {}).get("analysis_tries", 0)) + 1
        logger.warning("[FRIGATE_CONCLUDE] episode %s: conclusion model unreachable (try %d)", ep.episode_id, tries)
        update = {"status": "failed", "attempts": attempts - 1, "reason": reason, "analysis_tries": tries,
                  "next_try_ts": _next_try(tries, now)}
    elif parsed is None:
        logger.warning("[FRIGATE_CONCLUDE] episode %s attempt %s failed: %s", ep.episode_id, attempts, reason)
        update = {"status": "failed", "attempts": attempts, "reason": reason}
    else:
        copied = copied_phrases(parsed["summary"], ep.scene, lang)
        if copied:  # P142 shadow: counted and logged, the line stays as written
            _enforce_frames(parsed["summary"], lang, rid, {"kind": "frame", "wrong_language": copied})
        if not checked:  # code fact: no model looked at a frame — the stored text says so, whatever the 27B wrote
            parsed = {**parsed, "summary": f"{parsed['summary'].rstrip()} {_t('not_checked_suffix', lang)}"}
        parsed = _gate_unusual(parsed, ep, events, away, lang)
        if doubt_answer(ep.scene) == "unclear":  # s28: lowered in code, whatever the 27B wrote
            c = parsed["confidence"]
            parsed = {**parsed, "summary": f"{parsed['summary'].rstrip()} {_t('doubt_unclear_suffix', lang)}",
                      "confidence": {**c, "level": "low",
                                     "reasons": [*c.get("reasons", []), _t("doubt_low_reason", lang)]}}
        dev = await asyncio.to_thread(_register_dev_topic, parsed["raise_at_dev_meeting"], ep.episode_id, ep.end_ts)
        parsed = {**parsed, "raise_at_dev_meeting": dev}
        update = {**parsed, "status": "done", "by": "model", "attempts": attempts, "reason": "", "lang": lang,
                  "role": ROLE, "images_checked": checked, "analysis_tries": 0, "next_try_ts": None}
        # Shadow: suggestions are stored, never sent. The log carries no text (names stay in frigate.db).
        logger.info(
            "[FRIGATE_CONCLUDE] episode %s concluded: unusual=%s%s confidence=%s notify_suggestion=%s "
            "raise_at_dev_meeting=%s prompt_chars=%d (shadow, nothing sent)",
            ep.episode_id, parsed["unusual"]["value"],
            " (suppressed: thin baseline)" if "unusual_suppressed" in parsed else "",
            parsed["confidence"]["level"], bool(parsed["notify_suggestion"]),
            f"{dev['topic']}:{'raised' if dev['raised'] else 'seen ' + str(dev['count'])}" if dev else None,
            len(prompt),
        )
    stored = await asyncio.to_thread(_merge, ep.episode_id, update)
    if stored is not None:
        _remember(ep.episode_id, ep.start_ts, update, lang)
    return parsed is not None and stored is not None


async def close_round(now: float | None = None) -> int:
    """N40: close the quiet episodes — pure code, never waits for a model (it runs on its own loop, so a conclusion
    waiting for the 27B never keeps a finished situation «pågår»). Returns how many were closed. Never raises."""
    try:
        s = _settings()
        if not s["enabled"]:
            return 0
        now = time.time() if now is None else now
        gap = float(s["episode_gap_s"])
        # P144-s26: closing = quiet by the clock AND nothing of the episode's window still on its way (an end event being
        # written, a track Frigate still has open) — Frigate is asked only when an episode is due to close
        hold = await pipe.unstored_activity(now) if await asyncio.to_thread(store.closing_due, now, gap) else []
        return len(await asyncio.to_thread(store.close_episodes, now, gap, hold))
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] close round failed: %s", e)
        return 0


def _still_owed(episode_id: int) -> bool:
    """Re-checked under the analysis lock: the episode still wants a conclusion (another round or «Analyser» may have
    concluded it while this one waited) — so no conclusion runs twice."""
    ep = store.episode(episode_id)
    return ep is not None and ep.status == "closed" and (ep.conclusion or {}).get("status") != "done"


async def conclude_round(now: float | None = None) -> int:
    """Merge split episodes, then conclude the pending ones while users are idle. Returns how many were concluded.
    Never raises."""
    try:
        s = _settings()
        if not s["enabled"]:
            return 0
        c = s["conclude"]
        now = time.time() if now is None else now
        await _merge_split_episodes(now)
        pending = await asyncio.to_thread(_pending, now, int(c.get("max_attempts") or 1))
        done, waiting, retried = 0, 0, False
        for ep in pending:
            if vehicles_only(ep.labels):  # code only: no model, so no idle gate (s06e)
                if await _conclude_by_code(ep):
                    done += 1
                continue
            if waiting or not _idle(c):
                waiting += 1
                continue
            if _waiting(ep):  # s24: one waiting episode per round
                if retried:
                    continue
                retried = True
            async with _analysing():
                if not await asyncio.to_thread(_still_owed, ep.episode_id):
                    continue
                _current.update(episode_id=ep.episode_id, started_ts=time.time())
                try:
                    if await _conclude_episode(ep, now):
                        done += 1
                finally:
                    _current.clear()
            if _waiting(ep):  # the image model is still unreachable: the rest waits for the next round
                break
        if waiting:
            logger.debug("[FRIGATE_CONCLUDE] %d episode(s) wait for the idle gate", waiting)
        return done
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] round failed: %s", e)
        return 0


async def run_once(now: float | None = None) -> int:
    """One round: close quiet episodes, conclude the pending ones while users are idle. Returns how many were concluded.
    (The live loop runs the two steps on separate loops — N40; this keeps the one-call form for tests and tools.)"""
    await close_round(now)
    return await conclude_round(now)


async def _close_loop() -> None:
    """N40: closing on its own loop — never behind a conclusion that waits for the 27B."""
    while True:
        await close_round()
        await asyncio.sleep(_poll_s())


def _stuck_after_s() -> float:
    try:
        return max(60.0, float(_settings()["conclude"].get("stuck_after_min") or 30) * 60)
    except Exception:
        return 1800.0


def stuck_info(ep: store.Episode, now: float | None = None) -> dict | None:
    """N40: is this episode «stuck» — still open, or still without a conclusion, longer than the GUI limit after its last
    activity? Only what code knows: not_closed / running (started_ts) / waiting (attempts, next_try_ts) / failed
    (attempts) / queued. None when it is fresh, done or merged."""
    now = time.time() if now is None else now
    quiet_s = now - float(ep.end_ts or ep.start_ts or now)
    if quiet_s < _stuck_after_s() or ep.status not in ("open", "closed"):
        return None
    out = {"minutes": int(quiet_s // 60)}
    if ep.status == "open":
        return {**out, "kind": "not_closed"}
    c = ep.conclusion or {}
    if c.get("status") == "done":
        return None
    if _current.get("episode_id") == ep.episode_id:
        return {**out, "kind": "running", "started_ts": _current.get("started_ts")}
    if c.get("status") == WAITING:
        return {**out, "kind": "waiting", "attempts": int(c.get("attempts") or 0), "next_try_ts": c.get("next_try_ts")}
    if c.get("status") == "failed":
        return {**out, "kind": "failed", "attempts": int(c.get("attempts") or 0)}
    return {**out, "kind": "queued"}


def _poll_s() -> float:
    """Seconds between rounds (GUI `conclude.poll_s`, at least 5)."""
    try:
        return max(5.0, float(_settings()["conclude"].get("poll_s") or 30))
    except Exception:
        return 30.0


async def start_conclude_loop() -> None:
    """Background task (kaare_api startup): hands the pipe the urgent bypass, then one round
    every `conclude.poll_s`."""
    pipe.set_urgent_handler(conclude_urgent)
    await asyncio.sleep(_STARTUP_DELAY_S)
    if _settings()["enabled"]:
        # s16: the RAM counters and an empty Frigate STM are read back from frigate.db once, here — never per episode
        await asyncio.to_thread(counts.rebuild)
        await asyncio.to_thread(stm.rebuild, get_lang())
    # s24 item 5: missing frames are filled in at start and every few hours (Frigate GET only, no model)
    _background.add(asyncio.create_task(_backfill_loop()))
    _background.add(asyncio.create_task(_close_loop()))  # N40: closing never waits for a conclusion
    while True:
        await conclude_round()
        await asyncio.sleep(_poll_s())


# ── one situation = one episode: merging split episodes (P144-s26 item 4) ──────

MERGED_REASON = "merged"


def _reopen_ltm(episode_id: int) -> str:
    """The merged episode's day is compressed again the next night if it already went into LTM."""
    # PLC0415: import cycle — kaare_core.memory.frigate_ltm imports this module (conclude) at its top
    from kaare_core.memory import frigate_ltm  # noqa: PLC0415
    return frigate_ltm.reopen_episode(episode_id)


async def _merge_split_episodes(now: float) -> int:
    """Closed episodes that were one situation (they overlap or lie within the gap: a late event after a clock close —
    an outage, a burst, a track Frigate held open past HOLD_MAX_S) are merged into one (`store.merge_episodes`: events
    moved, every old conclusion kept as history, nothing deleted). The merged window gets its frames re-picked (kept
    frames reused, the rest fetched from Frigate while it has the recordings), and the episode waits for its analysis:
    the loop runs it like a retry on its kept frames — look → frames → 27B, idle-gated, once. The RAM counts, the
    Frigate STM and the LTM day follow. Code only here, no model. Returns how many merges were made. Never raises."""
    made = 0
    try:
        gap = float(_settings()["episode_gap_s"])
        groups = await asyncio.to_thread(store.split_groups, now - _PENDING_LOOKBACK_S, now, gap)
        for ids in groups:
            async with _analysis_lock():  # never under a running analysis of one of the parts
                merged = await asyncio.to_thread(store.merge_episodes, ids,
                                                 {"status": WAITING, "reason": MERGED_REASON}, now)
                if not merged:
                    continue
                survivor = merged["survivor"]
                ep = await asyncio.to_thread(store.episode, survivor)
                events = await asyncio.to_thread(store.episode_events, survivor)
                listing, reused, fetched, missing = await frames.repick_merged(ep, events, merged["frames"])
                if listing:
                    await asyncio.to_thread(store.set_episode_scene, survivor, {**(ep.scene or {}), "frames": listing},
                                            None)
            made += 1
            ltm = await asyncio.to_thread(_reopen_ltm, survivor)
            logger.info("[FRIGATE_CONCLUDE] episodes %s were one situation → merged into %s (%d events; frames reused "
                        "%d, fetched %d, missing %d; LTM %s) — analysed again when idle", ids, survivor, len(events),
                        reused, fetched, missing, ltm)
        if made:
            await asyncio.to_thread(counts.rebuild)  # the counts are per episode: the absorbed ids are gone
            await asyncio.to_thread(stm.refresh, get_lang())  # the absorbed episodes' STM lines go
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] merging split episodes failed: %s", e)
    return made


# ── «Analyser» / «Analyser på nytt» (P144-s24 item 4) ─────────────────────────


def _history_entry(ep: store.Episode, now: float) -> dict:
    """The conclusion + scene being replaced, kept inside the episode (provenance — never silently overwritten)."""
    old = {k: v for k, v in (ep.conclusion or {}).items() if k not in ("history", "urgent")}
    scene = {k: v for k, v in (ep.scene or {}).items() if k not in ("frames", "claims")}
    return {"replaced_ts": now, "conclusion": old, "scene": scene}


def _restart_conclusion(episode_id: int, entry: dict) -> dict | None:
    """A fresh conclusion that keeps the history (+ this entry) and the urgent results — read under the merge lock, so
    an urgent result landing meanwhile is never lost."""
    with _merge_lock:
        cur = store.episode(episode_id)
        if cur is None:
            return None
        old = cur.conclusion or {}
        base = {"history": [*(old.get("history") or []), entry], "attempts": 0}
        if old.get("urgent"):
            base["urgent"] = old["urgent"]
        return base if store.conclude_episode(episode_id, base) else None


async def reanalyse(episode_id: int, now: float | None = None) -> dict:
    """Look → scene → conclusion again on the frames kept on disk (missing files fetched first while Frigate still has
    them); L3 stays as stored, urgent results stay, the previous conclusion + scene go into `history`, the Frigate STM
    is rebuilt from frigate.db. The image model unreachable → nothing is overwritten. Shadow as always (nothing sent).
    Returns {ok, reason} (+ status/by when it ran). The LTM day is reopened by the caller (frigate_ltm imports this)."""
    if not _settings()["enabled"]:
        return {"ok": False, "reason": "switch_off"}
    lang = get_lang()
    async with _analysing():
        now = time.time() if now is None else now
        ep = await asyncio.to_thread(store.episode, episode_id)
        if ep is None:
            return {"ok": False, "reason": "no_episode"}
        if ep.status == "open" and (stuck_info(ep, now) or {}).get("kind") == "not_closed":
            await asyncio.to_thread(store.close_episode, episode_id)  # N40: a stuck open episode is closed first
            ep = await asyncio.to_thread(store.episode, episode_id)
        if ep is None or ep.status != "closed":
            return {"ok": False, "reason": "episode_open"}
        events = await asyncio.to_thread(store.episode_events, episode_id)
        kept = await asyncio.to_thread(frames.kept, ep)
        if len(kept) < len((ep.scene or {}).get("frames") or []) or not kept:
            listing, _got, _missing = await frames.ensure(ep, events, asyncio.Semaphore(frames.BACKFILL_PARALLEL))
            if listing:
                ep.scene = {**(ep.scene or {}), "frames": listing}
            kept = await asyncio.to_thread(frames.kept, ep)
        if not kept:
            return {"ok": False, "reason": "no_frames"}
        scene, route_text = await _scene_step(ep, events, lang, kept=kept, replan=not (ep.scene or {}).get("chapters"))
        if scene.get("status") == WAITING:
            logger.warning("[FRIGATE_CONCLUDE] episode %s re-analysis: image model unreachable, nothing changed",
                           episode_id)
            return {"ok": False, "reason": UNREACHABLE}
        entry = _history_entry(ep, now)
        ep.scene, ep.route = scene, ep.route or route_text
        await asyncio.to_thread(store.set_episode_scene, episode_id, ep.scene, ep.route)
        base = await asyncio.to_thread(_restart_conclusion, episode_id, entry)
        if base is None:
            return {"ok": False, "reason": "store_failed"}
        ep.conclusion = base
        ok = await _conclude_from_scene(ep, events, lang, None, now, gate_idle=False)
        await asyncio.to_thread(stm.refresh, lang)
        c = (await asyncio.to_thread(store.episode, episode_id)).conclusion or {}
        logger.info("[FRIGATE_CONCLUDE] episode %s re-analysed on %d kept frames: status=%s by=%s (history %d)",
                    episode_id, len(kept), c.get("status"), c.get("by"), len(base["history"]))
        return {"ok": bool(ok), "reason": "" if ok else str(c.get("reason") or "failed"), "status": c.get("status"),
                "by": c.get("by")}


# ── missing frames (P144-s24 item 5) ─────────────────────────────────────────

# Frigate keeps continuous recordings 7 days on this install (alerts 14) — older frames cannot be fetched any more.
_BACKFILL_LOOKBACK_S = 7 * 86400
_BACKFILL_EVERY_S = 6 * 3600


async def backfill_frames(now: float | None = None) -> dict:
    """Fetch/list every closed episode's missing frames within the recordings window — no model call, never
    re-concludes, never touches a waiting episode (the loop owns it). Counts only in the log. Never raises."""
    stats = {"episodes_checked": 0, "episodes_fixed": 0, "listed_from_disk": 0, "frames_fetched": 0,
             "frames_failed": 0}
    try:
        if not _settings()["enabled"]:
            return stats
        now = time.time() if now is None else now
        eps = await asyncio.to_thread(store.episodes_between, now - _BACKFILL_LOOKBACK_S, now)
        sem = asyncio.Semaphore(frames.BACKFILL_PARALLEL)
        fixed: list[int] = []
        for ep in eps:
            if ep.status != "closed" or ep.scene is None or _waiting(ep):
                continue
            stats["episodes_checked"] += 1
            events = await asyncio.to_thread(store.episode_events, ep.episode_id)
            listing, fetched, failed = await frames.ensure(ep, events, sem)
            stats["frames_fetched"] += fetched
            stats["frames_failed"] += failed
            if listing is not None:
                async with _analysis_lock():  # read-modify-write of the scene: never under a running analysis
                    cur = await asyncio.to_thread(store.episode, ep.episode_id)
                    if cur is None or cur.scene is None or _waiting(cur) or (cur.scene.get("frames") or []):
                        continue
                    await asyncio.to_thread(store.set_episode_scene, ep.episode_id,
                                            {**cur.scene, "frames": listing}, cur.route)
                if not fetched:
                    stats["listed_from_disk"] += 1
            if fetched or listing is not None:
                stats["episodes_fixed"] += 1
                fixed.append(ep.episode_id)
        if fixed or stats["frames_failed"]:
            logger.info("[FRIGATE_CONCLUDE] frame backfill: %s (episodes %s)", stats, fixed)
        return stats
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] frame backfill failed: %s", e)
        return stats


async def _backfill_loop() -> None:
    while True:
        await backfill_frames()
        await asyncio.sleep(_BACKFILL_EVERY_S)


# ── urgent bypass (L4) ────────────────────────────────────────────────────────


def _stored_event(ev: store.FrigateUserEvent) -> store.FrigateUserEvent | None:
    """The event as the store kept it — names only as the gate allowed, with its episode."""
    rows = store.events_between(ev.start_ts, ev.start_ts)
    return next((r for r in rows if r.event_id == ev.event_id), None)


async def conclude_urgent(ev: store.FrigateUserEvent, image_b64: str | None) -> dict | None:
    """The pipe calls this for every stored event (fire-and-forget, `pipe.set_urgent_handler`). Urgent → the model at once with
    the image, result added to the episode's `urgent` list (shadow: nothing sent). Returns the
    stored item, or None when the event is not urgent / the switch is off. Never raises."""
    try:
        if not _settings()["enabled"]:
            return None
        reason = is_urgent(ev)
        if not reason:
            return None
        stored = await asyncio.to_thread(_stored_event, ev)
        if stored is None or stored.episode_id is None:
            # not stored, or no episode (an ignored camera role / a parked object never makes one — s06d)
            logger.warning("[FRIGATE_CONCLUDE] urgent event %s has no stored episode, skipped", ev.event_id)
            return None
        lang = get_lang()
        away = _is_away()
        prompt = build_urgent_prompt(stored, reason, lang, away=away)
        images = [image_b64] if image_b64 else None
        parsed, why = await _ask(prompt, images, f"rid-frig-ev{ev.event_id}:urgent", lang, urgent=True)
        item = {"event_id": ev.event_id, "reason": reason, "ts": time.time(), "has_image": bool(images)}
        if parsed is None:
            item.update({"status": "failed", "fail_reason": why})
            logger.warning("[FRIGATE_CONCLUDE] urgent %s failed: %s", ev.event_id, why)
        else:
            dev = await asyncio.to_thread(_register_dev_topic, parsed["raise_at_dev_meeting"], stored.episode_id,
                                          ev.start_ts)
            item.update({**parsed, "raise_at_dev_meeting": dev, "status": "done"})
            logger.info(
                "[FRIGATE_CONCLUDE] urgent %s (%s): verdict=%s unusual=%s (shadow, no alert sent)",
                ev.event_id, reason, parsed["claim_verdict"]["verdict"], parsed["unusual"]["value"],
            )
        if await asyncio.to_thread(_merge, stored.episode_id, None, item) is not None:
            _remember(stored.episode_id, ev.start_ts, item, lang, urgent=True)
        return item
    except Exception as e:
        logger.warning("[FRIGATE_CONCLUDE] urgent %s error: %s", getattr(ev, "event_id", "?"), e)
        return None
