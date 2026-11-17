"""One question from the user's list in chat, now and then (run-queue J11; P149 D12) — as stored question cards (N39).

D12 (2026-11-06): verification is a bad fit for voice; in chat a question may slip in, but only one at a time; how
many depends on how much the user uses KTSB, and if the user says no every time, KTSB stops asking. So:
  - offers only in chat (`source == "gui"`), never voice (`stt`) or internal callers; only when the house switch is on
    (Integrations → Frigate → «Frigate-brukeren» → «Spør i chatten», `frigate_user.yaml: questions.chat`);
  - only after a light turn: the turn used no tool at all (no command, no alarm, no film, no lookup) and the user's text
    is short; never while a card is still open; at least MIN_GAP_S since the last one;
  - how many per day comes from the same willingness weight as the GUI list, channel «chat»: answered → asked a little
    more, skipped or ignored → less, until zero;
  - the question text is code's (i18n), appended after Kåre's own answer;
  - J29 (2026-11-13): a camera question shows the event's stored picture (the pipe's snapshot store) as a chat image
    — the user must see it to answer; no model call, no description; access as J24's `may_see_snapshot`.
N39 (2026-11-16, after Kåre picked the wrong event of an episode): every question is a **card** with its own question
ID (`q-` + 6 hex), stored in a small file so a restart no longer loses it. The note to Kåre lists each open card (id +
camera + time), never an event_id; Kåre stores the user's answer with `answer_question(question_id=…)`, one call per
card, and code binds it to that card's event. An episode with several open events becomes one card per event — the LLM
never chooses among events. Voice (owner decision 2026-11-16, option A): cards are read aloud as camera + time; no pictures, and
`scrub_for_voice` keeps a raw id out of the spoken reply. The file holds only open cards (answered, ignored, out of the
question window or over MAX_OPEN_CARDS are dropped) — small and self-cleaning; the answers live in weights.db.
Same lists, same rules as the GUI (J2/J9): own questions only, opted-out users never about the camera, never a theme that
names someone else.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from kaare_core import weights
from kaare_core.config import get_local_tz
from kaare_core.domain import frigate_store as domain_store
from kaare_core.frigate_user import query, questions, sealing, settings
from kaare_core.image_store import save_image
from kaare_core.memory import theme_questions
from kaare_core.tools.i18n import t

logger = logging.getLogger(__name__)

CHAT_SOURCES = ("gui",)
CARD_SOURCES = ("gui", "stt")  # where a card can be shown and answered (offers stay chat-only, D12)
MIN_GAP_S = 3600.0
MAX_TEXT_CHARS = 200  # a longer user message is not «light»
PER_DAY = {"very_likely": 3, "likely": 2, "uncertain": 1, "unlikely": 0}  # model constants (Forklaringer says so)
MAX_OPEN_CARDS = 10  # per user; oldest dropped beyond it (N39 R2: the file stays a few KB)
CARDS_PATH = Path("/kaare/state/memory/question_cards.json")
QID_RE = re.compile(r"\bq-[0-9a-f]{6}\b")

_lock = threading.RLock()
_cards: dict[str, list[dict]] | None = None  # user → open cards (RAM copy of CARDS_PATH)
_noted: dict[str, set[str]] = {}  # user → qids in this turn's note (RAM; settle() closes the ignored ones)
_asked: dict[str, list[float]] = {}  # user → offer times (RAM)


# ── the card store ──

def _load() -> dict[str, list[dict]]:
    global _cards
    with _lock:
        if _cards is None:
            try:
                data = json.loads(CARDS_PATH.read_text()) if CARDS_PATH.is_file() else {}
                _cards = {str(u): [c for c in v if isinstance(c, dict) and c.get("qid")]
                          for u, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}
            except Exception as e:
                logger.warning("[CHAT_Q] card file unreadable, starting empty: %s", type(e).__name__)
                _cards = {}
        return _cards


def _save() -> None:
    """Atomic rewrite of the open cards. Never raises (a failed save keeps RAM; logged)."""
    try:
        CARDS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CARDS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps({u: v for u, v in (_cards or {}).items() if v}, ensure_ascii=False))
        os.replace(tmp, CARDS_PATH)
    except Exception as e:
        logger.warning("[CHAT_Q] card file not saved: %s", type(e).__name__)


def _open_now(user: str, card: dict, now: float) -> bool:
    if card.get("kind") not in ("camera", "correct"):
        return True
    ts = card.get("ts")
    if isinstance(ts, (int, float)) and ts < now - questions.WINDOW_DAYS * 86400:
        return False  # out of the question window: no longer a question
    if card.get("kind") == "correct":  # N39 b: open while the person still has an answer there to correct
        return questions.current_answer(user, str(card.get("target"))) is not None
    return not questions._closed(str(card.get("target")), user)


def open_cards(user: str, now: float | None = None) -> list[dict]:
    """This user's open cards, oldest first (closed ones pruned and saved). Never raises."""
    now = time.time() if now is None else now
    with _lock:
        cards = _load()
        mine = cards.get(user) or []
        try:
            keep = [c for c in mine if _open_now(user, c, now)]
        except Exception as e:
            logger.warning("[CHAT_Q] card check failed: %s", type(e).__name__)
            keep = mine
        if len(keep) != len(mine):
            cards[user] = keep
            _save()
        return [dict(c) for c in keep]


def add_card(user: str, kind: str, target: str, *, source: str, origin: str = "verify", camera: str | None = None,
             ts: float | None = None, text: str | None = None, created: float | None = None) -> dict:
    """One card for `target` (an event_id, or a theme chain); the same open target gives the same card."""
    with _lock:
        cards = _load()
        mine = cards.setdefault(user, [])
        for c in mine:
            if c.get("kind") == kind and c.get("target") == target:
                return dict(c)
        if kind == "camera" and camera is None:
            try:
                ev = questions.store.event_by_id(target)
            except Exception:
                ev = None
            camera, ts = (ev.camera, ev.start_ts) if ev else (None, ts)
        card = {"qid": f"q-{secrets.token_hex(3)}", "kind": kind, "target": target, "camera": camera, "ts": ts,
                "text": text, "origin": origin, "source": source,
                "created": time.time() if created is None else created}
        mine.append(card)
        del mine[: max(0, len(mine) - MAX_OPEN_CARDS)]  # oldest out
        _save()
        return dict(card)


def _close(user: str, qid: str) -> None:
    with _lock:
        cards = _load()
        if user in cards:
            cards[user] = [c for c in cards[user] if c.get("qid") != qid]
            _save()


def _card(user: str, qid: str) -> dict | None:
    return next((c for c in open_cards(user) if c.get("qid") == qid), None)


# ── texts ──

def _willing(user: str) -> weights.Weight:
    return weights.get(("asking", f"{user}|chat", user))


def per_day(user: str) -> int:
    return PER_DAY.get(_willing(user).word, PER_DAY["uncertain"])


def _first_question(user: str, now: float) -> dict | None:
    for q in questions.candidates(user, "gui", now=now):
        return {"kind": "camera", "id": q["event_id"], "camera": q["camera"], "ts": q["ts"]}
    for th in theme_questions.candidates(user):
        return {"kind": "theme", "id": th["chain"], "text": th["text"]}
    return None


def _text(q: dict, lang: str) -> str:
    """The question as the user sees (or hears) it: camera + time, or the theme text — never an id."""
    if q["kind"] == "correct":  # N39 b: camera + time + the answer that stands now — never an id
        cards = query._camera_cards()
        cam = query._camera_text(q.get("camera") or "", None, cards, lang)
        when = datetime.fromtimestamp(q["ts"], get_local_tz()).strftime("%d.%m %H:%M") if q.get("ts") else "—"
        before = q.get("text") or ""
        word = t(f"fv_answer_{before}", lang) if before in ("yes", "no", "private", "unsure") else \
            t("fv_answer_skip_word", lang)
        return t("cq_correct_line", lang, camera=cam, time=when, answer=word)
    if q["kind"] == "camera":
        cards = query._camera_cards()
        cam = query._camera_text(q.get("camera") or "", None, cards, lang)
        when = datetime.fromtimestamp(q["ts"], get_local_tz()).strftime("%d.%m %H:%M") if q.get("ts") else "—"
        return t("cq_chat_camera", lang, camera=cam, time=when)
    return t("cq_chat_theme", lang, text=q.get("text") or "")


def card_lines(user: str, qids: list[str], lang: str) -> str:
    """«- question_id q-…: <question>» for the given cards (what Kåre gets; the user hears/sees only the question)."""
    by = {c["qid"]: c for c in open_cards(user)}
    return "\n".join(t("cq_card_line", lang, qid=q, question=_text(by[q], lang)) for q in qids if q in by)


def scrub_for_voice(text: str) -> str:
    """N39 (voice, option A): no raw question id is ever spoken — strip «q-xxxxxx» and the word «question_id». Text
    without either is returned unchanged."""
    if not text or not (QID_RE.search(text) or "question_id" in text):
        return text
    out = re.sub(r"\(?\s*\bquestion_id\b\s*[:=]?\s*", " ", text or "")
    out = re.sub(r"\(?\s*" + QID_RE.pattern + r"\s*\)?", "", out)
    return re.sub(r"[ \t]{2,}", " ", out).replace(" ?", "?").replace(" .", ".").replace(" ,", ",").strip()


def picture_line(user: str, event_id: str, lang: str, role: str | None = None) -> str:
    """J29 / J29b: the chat line with the event's stored snapshot as a chat image (`/api/image/<id>` in the user's own
    folder), or «(ingen bilde lagret)» when the pipe stored none, or "" when the user may not see it (J24's
    `may_see_snapshot`) or the event holds an opted-out person (never a question about them — J2/J22; an extra guard,
    admin's picture access elsewhere is unchanged). No model call. Never raises."""
    try:
        ev = questions.store.event_by_id(event_id)
        if ev is None or ev.person_kind == "untracked":
            return ""
        if not questions.may_see_snapshot(user, role, event_id):
            return ""
        snap = domain_store.SNAPSHOT_DIR / f"{event_id}.jpg"  # read per call: the pipe's store
        data = sealing.read_picture(snap) if snap.is_file() else None  # sealed at rest (R0.6a)
        if not data:
            return t("cq_chat_no_picture", lang)
        return f"/api/image/{save_image(data, user, 'frigate', ext='jpg')}"  # the folder decides the kind (camera)
    except Exception as e:
        logger.warning("[CHAT_Q] picture failed: %s", type(e).__name__)
        return ""


def _has_picture(card: dict) -> bool:
    """A camera card whose event has a stored snapshot (what the chat shows under the question)."""
    if card.get("kind") not in ("camera", "correct"):
        return False
    try:
        return (domain_store.SNAPSHOT_DIR / f"{card.get('target')}.jpg").is_file()
    except Exception:
        return False


def card_targets(user: str, text: str) -> list[str]:
    """The camera events of the cards named in a tool text (for their pictures) — only this user's own cards."""
    by = {c["qid"]: c for c in open_cards(user)}
    out = []
    for q in QID_RE.findall(text or ""):
        c = by.get(q)
        if c and c.get("kind") in ("camera", "correct") and c["target"] not in out:
            out.append(c["target"])
    return out


# ── the chat loop (router_generate) ──

def offer(user: str, source: str, user_text: str, used_tools: list[str], lang: str, now: float | None = None) -> str:
    """The line to append after Kåre's answer, or "" (most turns). Never raises."""
    try:
        now = time.time() if now is None else now
        if source not in CHAT_SOURCES or used_tools or len(user_text or "") > MAX_TEXT_CHARS or open_cards(user, now):
            return ""
        if not settings.questions_chat():
            return ""
        asked = [ts for ts in _asked.get(user, []) if now - ts < 86400]
        if (asked and now - asked[-1] < MIN_GAP_S) or len(asked) >= per_day(user):
            return ""
        q = _first_question(user, now)
        if not q:
            return ""
        add_card(user, q["kind"], q["id"], source=source, origin="offer", camera=q.get("camera"), ts=q.get("ts"),
                 text=q.get("text"), created=now)
        _asked[user] = [*asked, now]
        picture = picture_line(user, q["id"], lang) if q["kind"] == "camera" else ""
        return _text(q, lang) + (f"\n{picture}" if picture else "")  # the user sees the question, never the id
    except Exception as e:
        logger.warning("[CHAT_Q] offer failed: %s", type(e).__name__)
        return ""


def pending_note(user: str, lang: str, source: str = "gui") -> str:
    """The note Kåre gets at the start of a turn: every open card (id + question) and how to store answers, or "".
    Voice gets the extra rule: read camera + time aloud, never the id. Remembers which cards were noted (settle)."""
    try:
        if source not in CARD_SOURCES:
            return ""
        cards = open_cards(user)
        if not cards:
            _noted.pop(user, None)
            return ""
        qids = [c["qid"] for c in cards]
        _noted[user] = set(qids)
        note = t("cq_chat_pending_note", lang, cards=card_lines(user, qids, lang))
        if source == "stt":
            return note + " " + t("cq_voice_note", lang)
        if any(_has_picture(c) for c in cards):  # N39: «the picture» in an answer = the card's own (BUG-log 16.11)
            note += " " + t("cq_card_picture_note", lang)
        return note
    except Exception as e:
        logger.warning("[CHAT_Q] note failed: %s", type(e).__name__)
        return ""


def settle(user: str, calls: list[tuple[str, str]]) -> None:
    """After the turn (`calls` = (tool, action) that ran): the cards that were in this turn's note and are still open
    were ignored — closed; an ignored offer lowers the chat willingness once (D12). Cards made during the turn stay."""
    noted = _noted.pop(user, set())
    if not noted:
        return
    still = [c for c in open_cards(user) if c["qid"] in noted]
    for c in still:
        _close(user, c["qid"])
    if any(c.get("origin") == "offer" for c in still) and ("camera", "answer_question") not in calls:
        try:
            c = next(c for c in still if c.get("origin") == "offer")
            weights.answer(("asking", f"{user}|chat", user), by_user=user, verdict="no", verbatim=True,
                           context=f"chat:{c['kind']}:{c['target']}:{int(c.get('created') or 0)}")  # ignored (D12)
            weights.flush()  # no claim to anyone; a failed flush is logged and retried by the store
        except Exception as e:
            logger.warning("[CHAT_Q] settle failed: %s", type(e).__name__)


def answer(user: str, qid: str, verdict: str) -> dict:
    """The camera tool's `answer_question`: the user's answer to ONE card, stored on that card's own target through the
    same paths as the GUI. Unknown or someone else's card → not_a_question. {ok, kind, target, …}."""
    card = _card(user, str(qid or "").strip()) if user else None
    if not card:
        return {"ok": False, "error": "not_a_question"}
    if card["kind"] == "theme":
        res = theme_questions.answer(user, card["target"], verdict, channel="chat")
    elif card["kind"] == "correct":  # N39 b: replace the person's own earlier answer
        res = questions.correct(user, card["target"], verdict, channel="chat")
    else:
        res = questions.answer(user, card["target"], verdict, channel="chat", verbatim=True)
    if res.get("ok"):
        _close(user, card["qid"])
    return {**res, "kind": card["kind"], "target": card["target"]}


def verify(user: str, *, event_id: str | None, episode_id, verdict: str, lang: str, source: str = "gui") -> str:
    """The camera tool's `verify` for the user's own answer. One open event → stored as before (questions.verify). An
    episode — or one event of an episode — with several open events → one card per event, nothing stored: the user picks
    by their words, Kåre stores with answer_question(question_id); never a list of event_ids to choose from (N39)."""
    ep = episode_id
    if event_id and ep is None:
        try:
            ev = questions.store.event_by_id(str(event_id))
            ep = ev.episode_id if ev else None
        except Exception:
            ep = None
    if ep is not None:
        try:
            open_evs = questions.open_events_in_episode(user, int(ep))
        except (TypeError, ValueError):
            open_evs = []
        if len(open_evs) > 1 and (not event_id or any(e.event_id == event_id for e in open_evs)):
            cards = [add_card(user, "camera", e.event_id, source=source, camera=e.camera, ts=e.start_ts)
                     for e in open_evs]
            return t("fv_which_cards", lang, items=card_lines(user, [c["qid"] for c in cards], lang))
    return questions.verify(user, event_id=event_id, episode_id=episode_id, verdict=verdict, lang=lang)


def correction_cards(user: str, lang: str, source: str = "gui", limit: int = 5) -> str:
    """N39 b: the person's own latest answers (last week) as cards to correct — the user picks, never the LLM."""
    now = time.time()
    hist = [h for h in questions.history(user, limit=50)
            if h.get("ts") and h["ts"] >= now - questions.WINDOW_DAYS * 86400][:limit]
    made = [add_card(user, "correct", h["event_id"], source=source, origin="correct", ts=h["ts"],
                     camera=_camera_of(h["event_id"]), text=questions.current_answer(user, h["event_id"]))
            for h in hist if questions.current_answer(user, h["event_id"]) is not None]
    if not made:
        return t("fv_nothing_to_correct", lang)
    return t("fv_correct_cards", lang, items=card_lines(user, [c["qid"] for c in made], lang))


def _camera_of(event_id: str) -> str | None:
    try:
        ev = questions.store.event_by_id(event_id)
    except Exception:
        return None
    return ev.camera if ev else None


def reset_for_tests() -> None:
    """Forget RAM state (as a restart does); the card file stays."""
    global _cards
    with _lock:
        _cards = None
    _noted.clear()
    _asked.clear()
