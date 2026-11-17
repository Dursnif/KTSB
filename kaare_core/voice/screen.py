"""P151: KTSB's screen page — what each screen shows, live sessions, pairing and stream choice.

The voice bridge serves one small web page per screen node (`/screen/<node>`). The page asks the bridge every couple
of seconds what to show and reports back what it is really doing (playing, buffer, dropped frames) — that report is
the truth for «it is showing» (N26); a Cast device only says that DashCast runs.

Two ways a page is allowed to see content:
- **Session key** — a one-time random key in the address KTSB opens on a Cast screen (DashCast). Valid for that
  session only.
- **Paired device** — a screen without Cast (a Samsung browser) is opened once by hand. The page makes its own random
  device key, shows a 4-digit code, and the user types the code in the node dialog (D9). Until then the page sees
  nothing — anyone on the LAN who opens the address would otherwise see a camera while it runs.

Everything here is pure (no network, no subprocess) so it can be tested; the bridge does the I/O.
"""

import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

STATE_DIR = Path("/kaare/state/screens")
SETTINGS_PATH = Path("/kaare/configs/settings.yaml")

DEFAULT_LIVE_MINUTES = 2.0  # D4 (owner decision 2026-11-16): 2 minutes by default
MAX_LIVE_MINUTES = 240.0
DEFAULT_MAX_SIZE = (1280, 720)  # D8: lightest size that looked good on every screen tested (T34)
SIZES = {"640x360": (640, 360), "1280x720": (1280, 720), "1920x1080": (1920, 1080)}
PAIR_CODE_TTL_S = 600.0
SEEN_FRESH_S = 6.0  # a page that polled within this many seconds is on screen (it polls every 2 s)
STALE_S = 60.0  # a session that was showing and whose page has been silent this long is over (the screen moved on)


def _now() -> float:
    return time.time()


# ── settings (read live, so a GUI change needs no restart — rule 25) ───────────────────────────────────────────────

_settings_cache: dict = {"mtime": None, "data": {}}


def _screen_settings(path: Path = SETTINGS_PATH) -> dict:
    try:
        m = path.stat().st_mtime
    except OSError:
        return {}
    if _settings_cache["mtime"] != m:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            log.warning("screen settings unreadable: %s", e)
            data = {}
        _settings_cache.update(mtime=m, data=data.get("screens") or {})
    return _settings_cache["data"]


def enabled(settings: dict | None = None) -> bool:
    """D11 (owner decision 2026-11-16): one GUI switch turns the whole screen feature off — then every /screen route answers
    «off» and nothing can start. On by default: it costs nothing while idle (feedback_default_to_lightest_tier)."""
    s = _screen_settings() if settings is None else settings
    v = s.get("enabled", True)
    return v if isinstance(v, bool) else str(v).strip().lower() not in ("0", "false", "off", "no", "")


def live_minutes(override: float | None = None, settings: dict | None = None) -> float:
    """Minutes a live stream runs before it stops by itself: Kåre's per-action value, else the GUI value, else 2."""
    s = _screen_settings() if settings is None else settings
    for v in (override, s.get("live_minutes"), DEFAULT_LIVE_MINUTES):
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if f > 0:
            return min(f, MAX_LIVE_MINUTES)
    return DEFAULT_LIVE_MINUTES


def max_size(node_cfg: dict | None) -> tuple[int, int]:
    """The screen's max video size from its node (`screen_max: 1280x720`), default 1280×720."""
    raw = str((node_cfg or {}).get("screen_max") or "").strip().lower()
    if raw in SIZES:
        return SIZES[raw]
    m = re.fullmatch(r"(\d{3,4})x(\d{3,4})", raw)
    if m:
        return int(m[1]), int(m[2])
    return DEFAULT_MAX_SIZE


# ── stream choice (D7) ────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class StreamInfo:
    name: str
    codec: str  # "h264", "hevc", …
    width: int
    height: int


def camera_streams(frigate_config: dict, camera: str, go2rtc_streams: list[str]) -> list[str]:
    """Every go2rtc stream that belongs to a Frigate camera: its ffmpeg input names plus siblings that only differ in a
    trailing `_<n>` (Frigate's main/sub stream convention, e.g. `x_1` record + `x_2` sub)."""
    cam = (frigate_config.get("cameras") or {}).get(camera) or {}
    inputs = [str(i.get("path") or "").rstrip("/").split("/")[-1].split("?")[0]
              for i in ((cam.get("ffmpeg") or {}).get("inputs") or [])]
    bases = {re.sub(r"_\d+$", "", n) for n in inputs if n}
    return [s for s in go2rtc_streams
            if s in inputs or any(s == b or re.fullmatch(re.escape(b) + r"_\d+", s) for b in bases)]


@dataclass
class StreamChoice:
    stream: str
    mode: str  # "mse" (straight from Frigate's go2rtc) | "fmp4" (re-encoded here)
    width: int
    height: int
    scale_width: int = 0  # re-encode target width (0 = keep)
    reason: str = ""


def choose_stream(streams: list[StreamInfo], max_w: int, max_h: int, force_reencode: bool = False) -> StreamChoice | None:
    """Direct (no CPU here) when an H.264 stream fits the screen — the largest that fits. Otherwise re-encode the
    smallest stream that still covers the screen (less to decode), scaled down to fit."""
    usable = [s for s in streams if s.width > 0 and s.height > 0]
    if not usable:
        return None
    fits = [s for s in usable if s.codec == "h264" and s.width <= max_w and s.height <= max_h]
    if fits and not force_reencode:
        best = max(fits, key=lambda s: s.width * s.height)
        return StreamChoice(best.name, "mse", best.width, best.height, reason="h264 fits")
    covering = [s for s in usable if s.width >= max_w or s.height >= max_h]
    src = min(covering, key=lambda s: s.width * s.height) if covering else max(usable, key=lambda s: s.width * s.height)
    scale = min(1.0, max_w / src.width, max_h / src.height)
    w = int(src.width * scale) // 2 * 2
    h = int(src.height * scale) // 2 * 2
    why = "stutter" if force_reencode else (f"{src.codec} not playable direct" if src.codec != "h264" else "too large")
    return StreamChoice(src.name, "fmp4", w, h, scale_width=w, reason=why)


# ── sessions, content, pairing ────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    token: str
    node: str
    kind: str  # "camera" | "card"
    expires: float  # epoch seconds; 0 = until replaced or stopped
    camera: str = ""
    choice: StreamChoice | None = None
    card_file: str = ""
    title: str = ""
    created: float = field(default_factory=_now)
    rev: int = 1
    playing_since: float = 0.0
    last_report: dict = field(default_factory=dict)
    switched: bool = False  # already moved to re-encode once because of stutter
    restore: dict = field(default_factory=dict)  # how to put the screen back when this ends (phase 2/3)
    stale: bool = False  # ended because the page went away — something else owns the screen, do not restore

    def public(self) -> dict:
        c = self.choice
        return {"token": self.token, "kind": self.kind, "camera": self.camera, "title": self.title,
                "rev": self.rev, "mode": c.mode if c else "", "stream": c.stream if c else "",
                "size": f"{c.width}x{c.height}" if c else "", "reason": c.reason if c else "",
                "expires_in": max(0, int(self.expires - _now())) if self.expires else 0,
                "playing": bool(self.playing_since)}


class ScreenHub:
    def __init__(self, state_dir: Path = STATE_DIR):
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}  # token → session
        self._by_node: dict[str, str] = {}  # node → token
        self._seen: dict[str, float] = {}  # node → last poll time
        self._codes: dict[str, tuple[str, str, float]] = {}  # node → (code, device_key, expires)
        self._state_dir = state_dir
        self._paired: dict[str, str] = self._load_pairs()  # node → device key

    # pairing store ---------------------------------------------------------------------------------------------
    def _pairs_path(self) -> Path:
        return self._state_dir / "paired.json"

    def _load_pairs(self) -> dict[str, str]:
        try:
            return {str(k): str(v) for k, v in json.loads(self._pairs_path().read_text()).items()}
        except FileNotFoundError:
            return {}
        except Exception as e:
            log.warning("screen pairings unreadable: %s", e)
            return {}

    def _save_pairs(self) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            p = self._pairs_path()
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._paired, indent=1))
            tmp.chmod(0o600)
            tmp.replace(p)
        except Exception as e:
            log.warning("screen pairings not saved: %s", e)

    def is_paired(self, node: str) -> bool:
        return node in self._paired

    def unpair(self, node: str) -> bool:
        with self._lock:
            had = self._paired.pop(node, None) is not None
            self._codes.pop(node, None)
            if had:
                self._save_pairs()
            return had

    def confirm_code(self, node: str, code: str) -> bool:
        """The user typed the code from the TV in the node dialog → that page's device key is bound to the node."""
        code = re.sub(r"\D", "", code or "")
        with self._lock:
            got = self._codes.get(node)
            if not got or got[2] < _now() or not code or not secrets.compare_digest(got[0], code):
                return False
            self._paired[node] = got[1]
            self._codes.pop(node, None)
            self._save_pairs()
            return True

    # sessions --------------------------------------------------------------------------------------------------
    def start(self, node: str, kind: str, minutes: float = 0.0, **fields) -> Session:
        """A new session replaces whatever the node showed (one thing per screen)."""
        with self._lock:
            old = self._by_node.pop(node, None)
            rev = 1
            if old and old in self._sessions:
                rev = self._sessions.pop(old).rev + 1
            s = Session(token=secrets.token_urlsafe(18), node=node, kind=kind,
                        expires=_now() + minutes * 60 if minutes > 0 else 0.0, rev=rev, **fields)
            self._sessions[s.token] = s
            self._by_node[node] = s.token
            return s

    def stop(self, node: str) -> Session | None:
        with self._lock:
            tok = self._by_node.pop(node, None)
            return self._sessions.pop(tok, None) if tok else None

    def active(self) -> list[Session]:
        """Every session that exists (expired ones too, until `expired()` takes them)."""
        with self._lock:
            return list(self._sessions.values())

    def current(self, node: str) -> Session | None:
        with self._lock:
            tok = self._by_node.get(node)
            return self._sessions.get(tok) if tok else None

    def session(self, token: str) -> Session | None:
        """A live (not expired) session by its token — the gate for the stream and card routes."""
        with self._lock:
            s = self._sessions.get(token or "")
            if s is None or (s.expires and s.expires < _now()):
                return None
            return s

    def expired(self) -> list[Session]:
        """Remove and return every session whose time is up (the bridge closes the screen for each) — and every session
        that was showing but whose page has been silent for STALE_S (marked `stale`: the screen moved on, no restore)."""
        now = _now()
        with self._lock:
            for s in self._sessions.values():
                if s.playing_since and now - self._seen.get(s.node, now) > STALE_S:
                    s.stale = True
            gone = [s for s in self._sessions.values() if s.stale or (s.expires and s.expires < now)]
            for s in gone:
                self._sessions.pop(s.token, None)
                if self._by_node.get(s.node) == s.token:
                    self._by_node.pop(s.node, None)
            return gone

    def switch_to_reencode(self, token: str, choice: StreamChoice) -> bool:
        with self._lock:
            s = self._sessions.get(token)
            if not s or s.switched:
                return False
            s.choice, s.switched, s.rev, s.playing_since = choice, True, s.rev + 1, 0.0
            return True

    # what the page sees ----------------------------------------------------------------------------------------
    def _allowed(self, node: str, key: str) -> bool:
        tok = self._by_node.get(node)
        if tok and key and secrets.compare_digest(tok, key):
            return True
        bound = self._paired.get(node)
        return bool(bound and key and secrets.compare_digest(bound, key))

    def state_for(self, node: str, key: str, device_key: str = "") -> dict:
        """The answer to a page's poll. Unknown keys get nothing — or a pairing code, for a page that brought its own
        device key and whose node is not paired yet."""
        with self._lock:
            if self._allowed(node, key) or (device_key and self._allowed(node, device_key)):
                self._seen[node] = _now()
                tok = self._by_node.get(node)
                s = self._sessions.get(tok) if tok else None
                if s and s.expires and s.expires < _now():
                    s = None
                return {"paired": True, "content": s.public() if s else None}
            if device_key and node not in self._paired:
                got = self._codes.get(node)
                if not got or got[2] < _now() or got[1] != device_key:
                    got = (f"{secrets.randbelow(10000):04d}", device_key, _now() + PAIR_CODE_TTL_S)
                    self._codes[node] = got
                return {"paired": False, "pair_code": got[0]}
            return {"paired": False}

    def report(self, token: str, report: dict) -> Session | None:
        """A page says what it is doing. «Playing» = frames moving (currentTime grows) — set once, kept."""
        with self._lock:
            s = self._sessions.get(token or "")
            if not s:
                return None
            self._seen[s.node] = _now()
            if report.get("rev") != s.rev:
                return s  # a page still showing the previous content — its numbers say nothing about this one
            clean = {k: report.get(k) for k in ("t", "buf", "frames", "dropped", "w", "h", "event", "rev")}
            prev = s.last_report
            if (not s.playing_since and report.get("rev") == s.rev and float(report.get("t") or 0) > 0.5
                    and (s.kind == "card" or float(report.get("t") or 0) > float(prev.get("t") or 0))):
                s.playing_since = _now()
            if s.kind == "card" and report.get("event") == "loaded" and report.get("rev") == s.rev:
                s.playing_since = s.playing_since or _now()
            s.last_report = clean
            return s

    def seen_recently(self, node: str, within: float = SEEN_FRESH_S) -> bool:
        return _now() - self._seen.get(node, 0.0) <= within


def browser_open_steps(paired: bool, samsung: bool, power: str | None, browser_in_front: bool,
                       page_seen: bool) -> tuple[list[str], str]:
    """P151 phase 3: what to do to show KTSB's page on a screen without Cast → (steps, error). Steps are
    `power_on` and `browser`; an error ends it (`not_paired` / `page_not_open`)."""
    if not paired:
        return [], "not_paired"
    if samsung:
        steps = [] if power == "on" else ["power_on"]
        if power != "on" or not browser_in_front:
            steps.append("browser")
        return steps, ""
    return ([], "") if page_seen else ([], "page_not_open")


def browser_restore_step(was_off: bool, browser_was_front: bool) -> str:
    """How a Samsung goes back (D12): off if it was off, leave the browser if KTSB brought it up, else nothing."""
    if was_off:
        return "power_off"
    return "" if browser_was_front else "exit"


def stutters(prev: dict, cur: dict) -> bool:
    """A direct stream that drops >10 % of frames, or stands still while it should play, is re-encoded (D7)."""
    try:
        frames = float(cur.get("frames") or 0) - float(prev.get("frames") or 0)
        dropped = float(cur.get("dropped") or 0) - float(prev.get("dropped") or 0)
        if frames >= 20 and dropped / frames > 0.10:
            return True
        t_now, t_prev = float(cur.get("t") or 0), float(prev.get("t") or 0)
        return bool(prev) and t_prev > 0 and t_now <= t_prev and cur.get("event") == "stalled"
    except (TypeError, ValueError):
        return False


hub = ScreenHub()
