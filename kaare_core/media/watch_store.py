"""
Watch index per Plex account (P140) — KTSB's own record of what was watched, next to what Plex claims.

state/media/watch.db holds one row per *play* (one item watched once, from start until the session ends) plus the
user's own overrides. Status is derived at read time, never written back (P140 D4, 2026-10-18):

- finished  = reached the start of the credits marker (Plex's watched flag sometimes misses after «skip credits»),
              else ≥ FINISHED_FRACTION of the duration. Plex history rows count as finished (Plex scrobbled them).
- skipped   = started, not finished, and a *later* episode of the same show was started afterwards.
- paused    = started, not finished, nothing later started since → the resume point.
- Going back to an earlier episode is a rewatch: it marks nothing skipped. The newest play of an episode decides
  its status, and the user's own override (skipped / watched / resume) beats every rule.

A play also carries the screen it ran on (P140 bug A, 2026-10-26): the KTSB node id, from Plex's Player.address
against nodes.yaml or from plex_play, plus Plex's machineIdentifier (player_id) so the screen is still known after
its IP changes. Rows from before that have only Plex's player name, which still matches a node named the same way.

Sources: live (Plex websocket), ktsb (KTSB started it), plex_history (catch-up). Sync API — call from async code
through asyncio.to_thread (rule 10).
"""

import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kaare_core.storage import sqlite_files

DB_PATH = Path("/kaare/state/media/watch.db")

FINISHED_FRACTION = 0.90  # Plex's own watched threshold, used when an item has no credits marker
CREDITS_TOLERANCE_MS = 2_000  # «skip credits» pressed right as the marker starts still counts
SESSION_GAP_S = 30 * 60  # a session silent this long is a new play when it shows up again
HISTORY_MATCH_S = (
    6 * 3600
)  # a history entry this close to a live play of the same item is that play

OVERRIDES = ("skipped", "watched", "resume")
SOURCES = ("live", "ktsb", "plex_history")

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL,
    rating_key    TEXT    NOT NULL,
    item_type     TEXT    NOT NULL DEFAULT '',
    show_key      TEXT    NOT NULL DEFAULT '',
    show_title    TEXT    NOT NULL DEFAULT '',
    season        INTEGER,
    episode       INTEGER,
    title         TEXT    NOT NULL DEFAULT '',
    duration_ms   INTEGER,
    credits_ms    INTEGER,
    started_at    REAL    NOT NULL,
    last_seen_at  REAL    NOT NULL,
    last_offset_ms INTEGER NOT NULL DEFAULT 0,
    max_offset_ms INTEGER NOT NULL DEFAULT 0,
    state         TEXT    NOT NULL DEFAULT '',
    finished      INTEGER NOT NULL DEFAULT 0,
    player        TEXT    NOT NULL DEFAULT '',
    ktsb_user     TEXT    NOT NULL DEFAULT '',
    room          TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL DEFAULT 'live',
    session_key   TEXT    NOT NULL DEFAULT '',
    screen        TEXT    NOT NULL DEFAULT '',
    player_id     TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS plays_account_show ON plays(account_id, show_key, started_at);
CREATE INDEX IF NOT EXISTS plays_session ON plays(session_key, rating_key, last_seen_at);
CREATE TABLE IF NOT EXISTS overrides (
    account_id  INTEGER NOT NULL,
    rating_key  TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    set_at      REAL    NOT NULL,
    PRIMARY KEY (account_id, rating_key)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


_ADDED_COLUMNS = ("screen", "player_id")  # columns newer than the first schema — added to existing databases

_conns: dict[str, sqlite3.Connection] = {}  # one long-lived connection per database per process


@contextmanager
def _conn(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """The process's connection to the database, as one transaction: commit on success, roll back on error.

    Callers hold `_lock`, so the shared connection is used by one thread at a time. Long-lived on purpose: the
    WAL/shm side files then stay open with this process's owner instead of being re-created by whichever process
    opens the database next (a test run as another user once left them unwritable for the API — P140 phase 7).
    """
    db = str(path or DB_PATH)
    conn = _conns.get(db)
    if conn is None:
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite_files.connect(db, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        have = {r[1] for r in conn.execute("PRAGMA table_info(plays)")}
        for col in _ADDED_COLUMNS:
            if col not in have:
                conn.execute(f"ALTER TABLE plays ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        conn.commit()
        _conns[db] = conn
    try:
        with conn:
            yield conn
    except sqlite3.OperationalError as e:
        if "readonly" in str(e):
            # The side files were unwritable when this connection opened; SQLite keeps it read-only for good.
            # Drop it so the next call opens a fresh one (works as soon as the permissions are right again).
            _conns.pop(db, None)
            conn.close()
        raise


def close_all() -> None:
    """Close the cached connections (tests, shutdown)."""
    for conn in _conns.values():
        try:
            conn.close()
        except Exception:
            pass
    _conns.clear()


def is_finished(max_offset_ms: int, duration_ms: int | None, credits_ms: int | None) -> bool:
    # 2026-10-18: the credits marker decides when there is one; 90 % only for items without a marker.
    if credits_ms:
        return max_offset_ms >= credits_ms - CREDITS_TOLERANCE_MS
    return bool(duration_ms) and max_offset_ms >= FINISHED_FRACTION * duration_ms


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _learned_screen(conn: sqlite3.Connection, player_id: str) -> str:
    if not player_id:
        return ""
    row = conn.execute(
        "SELECT screen FROM plays WHERE player_id=? AND screen<>'' ORDER BY last_seen_at DESC LIMIT 1", (player_id,)
    ).fetchone()
    return row["screen"] if row else ""


def screen_for_player_id(player_id: str, path: Path | None = None) -> str:
    """The node a Plex client (machineIdentifier) was last tied to, "" if never."""
    with _lock, _conn(path) as conn:
        return _learned_screen(conn, player_id)


def _ordinal(season: int | None, episode: int | None) -> int | None:
    if season is None or episode is None:
        return None
    return int(season) * 10_000 + int(episode)


def record_progress(
    *,
    account_id: int,
    session_key: str,
    item: dict,
    state: str,
    offset_ms: int,
    player: str = "",
    ktsb_user: str = "",
    room: str = "",
    source: str = "live",
    screen: str = "",
    player_id: str = "",
    now: float | None = None,
    path: Path | None = None,
) -> tuple[int, str]:
    """Record one progress tick of a playing session. Returns (play id, event).

    event: "started" (new play), "paused" / "resumed" / "stopped" (state changed), "finished" (crossed the finished
    line this tick) or "" (nothing worth logging). `item` carries rating_key, type, show_key, show_title, season,
    episode, title, duration_ms, credits_ms. `screen` is the KTSB node; without it a `player_id` seen on a node
    before gives that node.
    """
    now = time.time() if now is None else now
    rk = str(item.get("rating_key") or "")
    with _lock, _conn(path) as conn:
        screen = screen or _learned_screen(conn, player_id)
        row = conn.execute(
            "SELECT * FROM plays WHERE session_key=? AND rating_key=? AND account_id=? AND source IN ('live','ktsb')"
            " AND last_seen_at>=? ORDER BY id DESC LIMIT 1",
            (session_key, rk, account_id, now - SESSION_GAP_S),
        ).fetchone()
        if row is None:
            finished = is_finished(offset_ms, item.get("duration_ms"), item.get("credits_ms"))
            cur = conn.execute(
                "INSERT INTO plays (account_id, rating_key, item_type, show_key, show_title, season, episode, title,"
                " duration_ms, credits_ms, started_at, last_seen_at, last_offset_ms, max_offset_ms, state, finished,"
                " player, ktsb_user, room, source, session_key, screen, player_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    account_id,
                    rk,
                    item.get("type") or "",
                    str(item.get("show_key") or ""),
                    item.get("show_title") or "",
                    item.get("season"),
                    item.get("episode"),
                    item.get("title") or "",
                    item.get("duration_ms"),
                    item.get("credits_ms"),
                    now,
                    now,
                    offset_ms,
                    offset_ms,
                    state,
                    int(finished),
                    player,
                    ktsb_user,
                    room,
                    source if source in SOURCES else "live",
                    session_key,
                    screen,
                    player_id,
                ),
            )
            return int(cur.lastrowid or 0), "started"

        max_offset = max(int(row["max_offset_ms"]), offset_ms)
        was_finished = bool(row["finished"])
        finished = was_finished or is_finished(max_offset, row["duration_ms"], row["credits_ms"])
        conn.execute(
            "UPDATE plays SET last_seen_at=?, last_offset_ms=?, max_offset_ms=?, state=?, finished=?,"
            " ktsb_user=CASE WHEN ?<>'' THEN ? ELSE ktsb_user END, room=CASE WHEN ?<>'' THEN ? ELSE room END,"
            " screen=CASE WHEN screen='' THEN ? ELSE screen END,"
            " player_id=CASE WHEN player_id='' THEN ? ELSE player_id END WHERE id=?",
            (
                now,
                offset_ms,
                max_offset,
                state,
                int(finished),
                ktsb_user,
                ktsb_user,
                room,
                room,
                screen,
                player_id,
                row["id"],
            ),
        )
        if finished and not was_finished:
            event = "finished"
        elif state != row["state"]:
            event = {"paused": "paused", "playing": "resumed", "stopped": "stopped"}.get(state, "")
        else:
            event = ""
        return int(row["id"]), event


def mark_source(
    play_id: int, source: str, ktsb_user: str = "", screen: str = "", path: Path | None = None
) -> None:
    """KTSB started this play itself (plex_play) — the strongest source; fills ktsb_user and screen if empty."""
    if source not in SOURCES:
        return
    with _lock, _conn(path) as conn:
        conn.execute(
            "UPDATE plays SET source=?, ktsb_user=CASE WHEN ktsb_user='' THEN ? ELSE ktsb_user END,"
            " screen=CASE WHEN screen='' THEN ? ELSE screen END WHERE id=?",
            (source, ktsb_user, screen, play_id),
        )


def import_history(account_id: int, entries: list[dict], path: Path | None = None) -> int:
    """Catch-up from Plex history. Each entry: rating_key, viewed_at + item fields. Returns rows added.

    An entry matching a live/ktsb play of the same item (same account, within HISTORY_MATCH_S) is that play: it is
    marked finished (Plex counted it as viewed) instead of being added twice.
    """
    added = 0
    with _lock, _conn(path) as conn:
        for e in entries:
            rk = str(e.get("rating_key") or "")
            viewed = float(e.get("viewed_at") or 0)
            if not rk or not viewed:
                continue
            dup = conn.execute(
                "SELECT id, source FROM plays WHERE account_id=? AND rating_key=? AND started_at BETWEEN ? AND ?",
                (account_id, rk, viewed - HISTORY_MATCH_S, viewed + HISTORY_MATCH_S),
            ).fetchone()
            if dup is not None:
                if dup["source"] != "plex_history":
                    conn.execute("UPDATE plays SET finished=1 WHERE id=?", (dup["id"],))
                continue
            conn.execute(
                "INSERT INTO plays (account_id, rating_key, item_type, show_key, show_title, season, episode, title,"
                " duration_ms, started_at, last_seen_at, last_offset_ms, max_offset_ms, state, finished, player,"
                " source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'stopped',1,?,'plex_history')",
                (
                    account_id,
                    rk,
                    e.get("type") or "",
                    str(e.get("show_key") or ""),
                    e.get("show_title") or "",
                    e.get("season"),
                    e.get("episode"),
                    e.get("title") or "",
                    e.get("duration_ms"),
                    viewed,
                    viewed,
                    e.get("duration_ms") or 0,
                    e.get("duration_ms") or 0,
                    e.get("player") or "",
                ),
            )
            added += 1
    return added


def set_override(account_id: int, rating_key: str, status: str, path: Path | None = None) -> bool:
    """The user's own word. status in OVERRIDES, or "clear" to remove it."""
    with _lock, _conn(path) as conn:
        if status == "clear":
            conn.execute(
                "DELETE FROM overrides WHERE account_id=? AND rating_key=?",
                (account_id, rating_key),
            )
            return True
        if status not in OVERRIDES:
            return False
        conn.execute(
            "INSERT INTO overrides (account_id, rating_key, status, set_at) VALUES (?,?,?,?)"
            " ON CONFLICT(account_id, rating_key) DO UPDATE SET status=excluded.status, set_at=excluded.set_at",
            (account_id, rating_key, status, time.time()),
        )
        return True


def get_meta(key: str, default: str = "", path: Path | None = None) -> str:
    with _lock, _conn(path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key: str, value: str, path: Path | None = None) -> None:
    with _lock, _conn(path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _episode_statuses(
    plays: list[dict], overrides: dict[str, tuple[str, float]]
) -> dict[str, dict]:
    """rating_key → {status, play} for one account + show, from its plays in time order (D4)."""
    latest: dict[str, dict] = {}
    for p in plays:  # ordered by started_at → the newest play of an episode wins
        latest[p["rating_key"]] = p
    result: dict[str, dict] = {}
    for rk, p in latest.items():
        status = "watched" if p["finished"] else "paused"
        if status == "paused":
            ordn = _ordinal(p["season"], p["episode"])
            if ordn is not None and any(
                (o := _ordinal(q["season"], q["episode"])) is not None
                and o > ordn
                and q["started_at"] >= p["started_at"]
                for q in plays
                if q is not p
            ):
                status = "skipped"
        ov = overrides.get(rk)
        if ov and ov[1] >= p["last_seen_at"]:
            status = {"resume": "paused"}.get(ov[0], ov[0])
        result[rk] = {"status": status, "play": p}
    return result


def _load(
    conn: sqlite3.Connection, account_id: int, show_key: str | None
) -> tuple[list[dict], dict]:
    if show_key is None:
        rows = conn.execute(
            "SELECT * FROM plays WHERE account_id=? ORDER BY started_at, id", (account_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM plays WHERE account_id=? AND show_key=? ORDER BY started_at, id",
            (account_id, show_key),
        ).fetchall()
    ovs = {
        r["rating_key"]: (r["status"], r["set_at"])
        for r in conn.execute(
            "SELECT * FROM overrides WHERE account_id=?", (account_id,)
        ).fetchall()
    }
    return [dict(r) for r in rows], ovs


def show_status(account_id: int, show_key: str, path: Path | None = None) -> dict[str, dict]:
    with _lock, _conn(path) as conn:
        plays, ovs = _load(conn, account_id, show_key)
    return _episode_statuses(plays, ovs)


def _screen_keys(plays: list[dict], names: dict[str, str] | None = None) -> list[str]:
    """The screen each play ran on: its node; else the node its player_id was tied to; else the node Plex's player
    name belongs to (`names`: node id / HA player id / HA name → node, from nodes.yaml — rows from before screens
    were recorded); else a node id equal to that name; else the player name itself. "" = unknown."""
    by_id = {p["player_id"]: p["screen"] for p in plays if p.get("player_id") and p.get("screen")}
    by_name = {_norm(p["screen"]): p["screen"] for p in plays if p.get("screen")}
    by_name.update({_norm(alias): nid for alias, nid in (names or {}).items() if _norm(alias)})
    keys = []
    for p in plays:
        key = p.get("screen") or by_id.get(p.get("player_id") or "", "")
        if not key and p.get("player"):
            key = by_name.get(_norm(p["player"]), p["player"])
        keys.append(key)
    return keys


def _on_screen(plays: list[dict], screen: str, names: dict[str, str] | None = None) -> list[dict]:
    want = _norm(screen)
    return [p for p, key in zip(plays, _screen_keys(plays, names), strict=True) if key and _norm(key) == want]


def continue_point(
    account_id: int,
    show_key: str | None = None,
    path: Path | None = None,
    screen: str | None = None,
    names: dict[str, str] | None = None,
) -> dict | None:
    """Where this account is, by KTSB's own record. None if nothing is known.

    {"kind": "resume", "play": …}  → the newest play is paused: continue it at last_offset_ms.
    {"kind": "next", "play": …}    → the newest play is finished/skipped: the episode after it (season/episode of
                                      the play; the caller resolves «next» against the library).
    {"kind": "done", "play": …}    → a finished movie (nothing to continue).
    Without show_key: the account's newest play of any show or movie. With screen (a KTSB node id): only what ran
    on that screen.
    """
    with _lock, _conn(path) as conn:
        plays, ovs = _load(conn, account_id, show_key)
    if screen:
        plays = _on_screen(plays, screen, names)
    if not plays:
        return None
    newest = max(plays, key=lambda p: (p["last_seen_at"], p["id"]))
    key = newest["show_key"]
    group = [p for p in plays if p["show_key"] == key] if key else [newest]
    status = _episode_statuses(group, ovs)[newest["rating_key"]]["status"]
    if status == "paused":
        return {"kind": "resume", "status": status, "play": newest}
    if newest["item_type"] == "episode":
        return {"kind": "next", "status": status, "play": newest}
    return {"kind": "done", "status": status, "play": newest}


def latest_per_screen(
    account_id: int, show_key: str, path: Path | None = None, names: dict[str, str] | None = None
) -> list[tuple[str, dict, str]]:
    """(screen, newest play there, its status) for one show, newest first. Plays with no known screen (Plex
    history) are left out."""
    with _lock, _conn(path) as conn:
        plays, ovs = _load(conn, account_id, show_key)
    groups: dict[str, list[dict]] = {}
    for p, key in zip(plays, _screen_keys(plays, names), strict=True):
        if key:
            groups.setdefault(key, []).append(p)
    out = []
    for key, group in groups.items():
        newest = max(group, key=lambda p: (p["last_seen_at"], p["id"]))
        out.append((key, newest, _episode_statuses(group, ovs)[newest["rating_key"]]["status"]))
    out.sort(key=lambda r: -r[1]["last_seen_at"])
    return out


def skipped(account_id: int, show_key: str | None = None, path: Path | None = None) -> list[dict]:
    """Episodes KTSB holds as skipped, oldest show order first."""
    with _lock, _conn(path) as conn:
        plays, ovs = _load(conn, account_id, show_key)
    out: list[dict] = []
    for key in sorted({p["show_key"] for p in plays if p["show_key"]}):
        group = [p for p in plays if p["show_key"] == key]
        out.extend(
            i["play"] for i in _episode_statuses(group, ovs).values() if i["status"] == "skipped"
        )
    out.sort(key=lambda p: (p["show_title"], _ordinal(p["season"], p["episode"]) or 0))
    return out
