"""One receipt for everything that is saved (J22 part 4, 2026-11-07: «de må ha lik tekst, så vi senere kan koble inn
Model Enforcer der hvis det blir behov»).

A tool that writes something returns `build(...)` — built by code from what was actually written, never by a model:

    ⟦saved kind=<kind> ref=<id>⟧ <What>: … | <Where>: … | <Who>: … | <Channel>: … | <When>: …

The marker `⟦saved kind=… ref=…⟧` is language-independent and stable; the five fields are labelled in the user's language
(nb/en/de). `instruction(lang)` is the sentence that follows it in a tool result: Kåre repeats the receipt, he does not
write his own version. `parse(text)` finds every receipt in a text — the hook a later Model Enforcer detector uses to compare
«Kåre says he saved X» with the receipts of the same turn (the P118 tool-promise pattern). That detector is not built yet.
Nothing is stored here; no IO except the household's clock (rule 24).
"""

from __future__ import annotations

import atexit
import hashlib
import re
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime

from kaare_core import log_buffer
from kaare_core.config import get_local_tz
from kaare_core.tools.i18n import t

_WHO: ContextVar[str | None] = ContextVar("receipt_who", default=None)  # set per tool call by the executor
MARKER_RE = re.compile(r"⟦saved kind=([a-z_]{1,40}) ref=([^\s⟧]{1,200})⟧")
CHANNELS = ("chat", "gui", "voice", "night")


def _clean(v) -> str:
    return " ".join(str(v if v is not None else "—").split()).replace("|", "/") or "—"


def build(kind: str, *, ref: str, what: str, where: str, who: str, channel: str, when: float | None = None,
          lang: str = "nb") -> str:
    """The one receipt line. `kind` = a short machine word (camera_answer, place_answer, note, …); `ref` = the stored
    thing's id. Never raises."""
    try:
        ts = time.time() if when is None else float(when)
        stamp = datetime.fromtimestamp(ts, get_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        stamp = "—"
    ch = t(f"rcpt_channel_{channel}", lang) if channel in CHANNELS else _clean(channel)
    kind = re.sub(r"[^a-z_]", "_", (kind or "saved").lower())[:40] or "saved"
    ref = re.sub(r"[\s⟧]", "_", str(ref or "-"))[:200]
    return (f"⟦saved kind={kind} ref={ref}⟧ {t('rcpt_what', lang)}: {_clean(what)} | {t('rcpt_where', lang)}: "
            f"{_clean(where)} | {t('rcpt_who', lang)}: {_clean(who)} | {t('rcpt_channel', lang)}: {ch} | "
            f"{t('rcpt_when', lang)}: {stamp}")


def suffix(kind: str, *, ref: str, what: str, where: str, who: str | None, channel: str = "chat",
           lang: str = "nb") -> str:
    """The receipt + the repeat instruction as a block to append to a tool reply — "" when `who` is None (the caller did
    not ask for a receipt: night jobs, tests, internal writers)."""
    if who is None:
        return ""
    return "\n" + build(kind, ref=ref, what=what, where=where, who=who, channel=channel, lang=lang) + "\n" + \
        instruction(lang)


def instruction(lang: str = "nb") -> str:
    return t("rcpt_repeat", lang)


def parse(text: str) -> list[dict]:
    """Every receipt marker in `text`, in order: [{kind, ref}]."""
    return [{"kind": m.group(1), "ref": m.group(2)} for m in MARKER_RE.finditer(text or "")]


def acting_as(who: str | None):
    """Set who a tool call acts for (the executor, around one call); returns the token for `done()`."""
    return _WHO.set(who)


def done(token) -> None:
    _WHO.reset(token)


def change(kind: str, *, ref: str, action: str, where: str, lang: str = "nb") -> str:
    """A receipt for a change or removal — never the content (it may be personal): only the type, the id and the action.
    "" outside a tool call (night jobs, internal callers)."""
    who = _WHO.get()
    if who is None:
        return ""
    what = t("rcpt_change_what", lang, action=t(f"rcpt_act_{action}", lang), ref=ref)
    return suffix(kind, ref=ref, what=what, where=where, who=who, lang=lang)


def fragment_ref(prefix: str, fragment: str, count: int) -> str:
    """A stable, content-free ref for a change made by text fragment (profile / world / self image)."""
    h = hashlib.sha1((fragment or "").encode("utf-8")).hexdigest()[:8]
    return f"{prefix}:{count}x:{h}"


# ── background saves (no chat): one line per batch in logs/receipts.log under the job's rid ──────────────────────────

RECEIPTS_LOG = "/kaare/logs/receipts.log"
BACKGROUND_ON = True  # one receipt line per background batch
_REFS_MAX = 50


def _job_rid() -> str:
    """The rid of the job that is running (llm_adapter's ContextVar: chat, night steps, meetings) — "" when none.
    Read without importing llm_adapter (a store must not pull in the LLM layer)."""
    mod = sys.modules.get("adapters.llm_adapter")
    try:
        return mod._current_rid.get() if mod is not None else ""
    except Exception:
        return ""


# Kinds saved one row at a time in a steady stream (a camera event, an Argus poll): their receipts are coalesced in RAM —
# one line per COALESCE_MAX ids or COALESCE_S seconds per (kind, where, rid), the rest at exit.
COALESCE_KINDS: frozenset[str] = frozenset({"frigate_event", "argus_index"})
COALESCE_MAX = 50
COALESCE_S = 60.0
_pending: dict[tuple[str, str, str], dict] = {}
_pending_lock = threading.Lock()


def _write_line(kind: str, where: str, rid: str, refs: list[str], n: int, ts: datetime | None = None) -> None:
    log_buffer.write(RECEIPTS_LOG, {"ts": (ts or datetime.now(get_local_tz())).isoformat(timespec="seconds"), "rid": rid,
                                    "kind": kind, "where": where, "n": n, "refs": refs[:_REFS_MAX]})


def flush_pending(force: bool = True) -> int:
    """Write coalesced receipts — all of them (force), or only those that are full or old. Returns lines written."""
    out = []
    now = time.monotonic()
    with _pending_lock:
        for key in list(_pending):
            b = _pending[key]
            if force or b["n"] >= COALESCE_MAX or now - b["t0"] >= COALESCE_S:
                out.append((key, _pending.pop(key)))
    for (kind, where, rid), b in out:
        try:
            _write_line(kind, where, rid, b["refs"], b["n"], b["ts"])
        except Exception:
            pass
    return len(out)


atexit.register(flush_pending)


def log_saved(kind: str, *, where: str, refs=(), n: int | None = None, rid: str | None = None) -> None:
    """One receipt line for a background save — type, place, ids (capped) and count, never content. Never raises; the
    write is buffered (log_buffer), not on the caller's hot path; a streamed kind is coalesced (COALESCE_KINDS)."""
    if not BACKGROUND_ON:
        return
    try:
        refs = [str(r) for r in refs if r is not None and str(r) != ""]
        kind = re.sub(r"[^a-z_]", "_", (kind or "saved").lower())[:40]
        where, rid = str(where)[:80], rid if rid is not None else _job_rid()
        count = n if n is not None else len(refs)
        if kind not in COALESCE_KINDS:
            _write_line(kind, where, rid, refs, count)
            return
        with _pending_lock:
            b = _pending.setdefault((kind, where, rid), {"refs": [], "n": 0, "t0": time.monotonic(),
                                                         "ts": datetime.now(get_local_tz())})
            b["refs"].extend(refs)
            b["n"] += count
        flush_pending(force=False)
    except Exception:
        pass
