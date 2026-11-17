"""The Frigate user's settings (P144-s06a): `configs/frigate_user.yaml`, set in Integrations → Frigate.

`enabled` is the switch for the shadow pipe and the episode conclusion (s06b) — unfinished work that acts on its own,
so the shipped template has it off (PENDING_SYNC «switch» rule). The other keys are Expert values with safe defaults.
`conclude:` belongs to s06b (read there); this module owns the file and the validation.

Readers never raise: a missing or broken file, or a value of the wrong type, reads as the default; numbers are
clamped to their limits. `update()` (GUI only) raises ValueError on anything outside the limits (fail closed).
"""

import copy
import logging
import re
from pathlib import Path

from kaare_core import config_store

log = logging.getLogger(__name__)

CONFIG_PATH = config_store.CONFIG_DIR / "frigate_user.yaml"

DEFAULTS: dict = {
    "enabled": False,
    "episode_gap_s": 120,  # an event within this long of the open episode's last activity joins it
    # A vehicle or a stationary object keeps an episode open only when its path moves this share of the frame.
    "episode_min_movement": 0.08,
    "visit_gap_s": 1800,  # events of the same person closer than this are one visit
    "visit_fold_interval_s": 300,  # how often the pipe folds events into visits
    "l0_min_face_px": 10000,  # smaller faces: «unverified» without any LLM call (blind test 2026-10-26)
    "l0_min_claim_score": 0.0,  # Frigate's face score below this: «unverified» (0 = Frigate's own threshold only)
    # Episode frames on disk (P144-s11): the frames the scene step showed the model, per episode. Over this cap the
    # oldest episodes keep only their middle frame (0 = store none) …
    "episode_frames_max_mb": 500,
    # … and those kept frames have their own cap (the butler remembers long; over it the oldest go).
    "episode_frames_keep_mb": 500,
    # Size limit of frigate.db in MB (P150, 2026-11-13: size, not time; 0 = off). Over it, the oldest rolled-up
    # detail goes first (slimmed in an episode, deleted outside one), then — once no detail is left — the oldest hourly
    # counts. Daily counts, episodes and visits are kept. Replaces the day tiers of P144-s07.
    "frigate_db_max_mb": 500,
    # Dry run: the nightly job writes the rollups but deletes/slims nothing and logs what it would. On until P144 is
    # done — the numbers are read before anything is deleted.
    "retention_dry_run": True,
    # Unknown licence plates (P144-s12): «7_days» (a stranger's LPR camera may face the road) or «always».
    "keep_unknown_plates": "7_days",
    # Weather at the episode as code context (P144-s14): read from the user's one weather config (Settings → Weather:
    # its HA sensors when on, else the chosen service). Off = no weather lookups for episodes.
    "episode_weather": True,
    # «Uttrykk og gester» (P144-s27b): the image model also says expression and gesture per person, per frame. On by
    # default (2026-11-06: «bedre at det skrues av der det ikke ønskes»); each person can turn it off for
    # themselves (users.db, the s02 who-decides rule) and then nothing is asked in an episode they are named in.
    "expressions": True,
    # Extra face recognition (P144-s19, P130 port): a second, independent face sensor whose vote joins the claim check
    # (never a fact). Off by default; provider «double_take» = Double Take over the household's MQTT broker. A vote
    # below `min_confidence` (0–100) does not count (0 = the provider's own match flag). `rest_url` only feeds the
    # status/test button and the per-detector read (P144-s29: Double Take's MQTT merges the detectors per name, its REST
    # rows do not — GET only). How much each detector counts is `face_sensors` below (P144-s29b).
    "face_votes": {
        "enabled": False,
        "provider": "double_take",
        "topic_prefix": "double-take",
        "rest_url": "",
        "min_confidence": 0.0,
    },
    # Face sensors (P144-s29b, 2026-11-05): every face sensor — Frigate's own face recognition and each detector a
    # provider reports (by the provider's own name) — is «auto» (KTSB learns; until its weight system exists auto counts
    # as «uncertain» in the vote and leaves the word Kåre says as before) or a word admin sets: «When this sensor alone
    # says a name, it is …» very_likely / likely / uncertain / unlikely, or off (shown, never counts). `trust` keeps
    # admin's last word even while the mode is auto. Read only through face_votes.sensor_weight().
    # P149 phase 2 / J2b (2026-11-06): Auto uses what KTSB learned from «Var det deg?» answers once a sensor has
    # about five of them (model constant, not a setting); `auto_learning` false (all sensors, or one) keeps Auto at
    # «uncertain» whatever was learned. Admin's fixed word always wins.
    "face_sensors": {
        "auto_learning": True,
        "frigate": {"mode": "auto", "trust": "uncertain", "label": "", "auto_learning": True},
        "detectors": {},
    },
    # J11 (P149 D12): one question from the user's list now and then in chat (never voice) — GUI «Spør i chatten»
    "questions": {"chat": True, "wrong_rules": False},  # J20: wrong_rules False = confirmed-wrong rules in shadow
    # J21 5b (phase 5): one switch per module m1–m6 (auto | on | off); 0 and 7 always on — see frigate_user/modules.py
    "modules": {f"m{i}": "auto" for i in range(1, 7)},
    "conclude": {
        "idle_gate_s": 30,
        "urgent_classes": ["not_welcome"],
        "urgent_when_away_person": True,
        "urgent_labels": ["fire", "smoke"],
        "max_attempts": 3,
        "poll_s": 30,
        # «Analyser» / «Analyser på nytt» in the episode view (P144-s24): starts per 10 minutes for the whole install.
        "analyse_per_10min": 2,
        # N40: an episode is «stuck» (henger) when it is still open, or still has no conclusion, this many minutes after
        # its last activity — the episode view then says what code knows and offers «Analyser på nytt».
        "stuck_after_min": 30,
    },
}

# (min, max) per number key; nested keys as «section.key».
LIMITS: dict[str, tuple[float, float]] = {
    "episode_gap_s": (10, 3600),
    "episode_min_movement": (0.01, 1.0),
    "visit_gap_s": (60, 86400),
    "visit_fold_interval_s": (30, 3600),
    "l0_min_face_px": (0, 1_000_000),
    "l0_min_claim_score": (0.0, 1.0),
    "episode_frames_max_mb": (0, 100_000),
    "episode_frames_keep_mb": (0, 100_000),
    "frigate_db_max_mb": (0, 1_000_000),
    "conclude.idle_gate_s": (0, 3600),
    "conclude.max_attempts": (1, 10),
    "conclude.poll_s": (5, 3600),
    "conclude.analyse_per_10min": (1, 60),
    "conclude.stuck_after_min": (5, 1440),
    "face_votes.min_confidence": (0.0, 100.0),
}

URGENT_CLASSES = ("household", "safe_guest", "not_welcome", "unclassified", "unknown")
PLATE_KEEP = ("7_days", "always")
PLATE_KEEP_DAYS = 7
FACE_VOTE_PROVIDERS = ("double_take",)
TRUST_WORDS = ("very_likely", "likely", "uncertain", "unlikely", "off")  # «verified» is a person's answer, never here
SENSOR_MODES = ("auto", "admin")
DETECTOR_NAME_MAX = 64
DETECTOR_LABEL_MAX = 40
_TOPIC_PREFIX_RE = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$")  # an MQTT topic level path, no wildcards


def _path() -> Path:
    return Path(CONFIG_PATH)


def _raw() -> dict:
    try:
        data = config_store.read_yaml_cached(_path(), copy_result=False)
    except Exception as e:
        log.warning("frigate user settings: could not read %s: %s", _path().name, e)
        return {}
    return data if isinstance(data, dict) else {}


def _number(value, default, key: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    lo, hi = LIMITS[key]
    value = max(lo, min(hi, value))
    return int(value) if isinstance(default, int) else float(value)


def _str_list(value, default: list, allowed: tuple | None = None) -> list:
    if not isinstance(value, list):
        return list(default)
    out = [v.strip() for v in value if isinstance(v, str) and v.strip()]
    if allowed is not None:
        out = [v for v in out if v in allowed]
    return out


def get_settings() -> dict:
    """The whole file merged over the defaults, validated. Never raises."""
    raw = _raw()
    out = copy.deepcopy(DEFAULTS)
    for key in ("enabled", "retention_dry_run", "episode_weather", "expressions"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    for key in ("episode_gap_s", "episode_min_movement", "visit_gap_s", "visit_fold_interval_s", "l0_min_face_px",
                "l0_min_claim_score", "episode_frames_max_mb", "episode_frames_keep_mb", "frigate_db_max_mb"):
        out[key] = _number(raw.get(key), DEFAULTS[key], key)
    if raw.get("keep_unknown_plates") in PLATE_KEEP:
        out["keep_unknown_plates"] = raw["keep_unknown_plates"]
    c_raw = raw.get("conclude") if isinstance(raw.get("conclude"), dict) else {}
    c = out["conclude"]
    for key in ("idle_gate_s", "max_attempts", "poll_s", "analyse_per_10min", "stuck_after_min"):
        c[key] = _number(c_raw.get(key), DEFAULTS["conclude"][key], f"conclude.{key}")
    if isinstance(c_raw.get("urgent_when_away_person"), bool):
        c["urgent_when_away_person"] = c_raw["urgent_when_away_person"]
    c["urgent_classes"] = _str_list(c_raw.get("urgent_classes"), DEFAULTS["conclude"]["urgent_classes"],
                                    URGENT_CLASSES)
    c["urgent_labels"] = _str_list(c_raw.get("urgent_labels"), DEFAULTS["conclude"]["urgent_labels"])
    out["face_votes"] = _face_votes(raw.get("face_votes"))
    out["face_sensors"] = _face_sensors(raw.get("face_sensors"))
    q_raw = raw.get("questions") if isinstance(raw.get("questions"), dict) else {}
    out["questions"] = {k: q_raw[k] if isinstance(q_raw.get(k), bool) else DEFAULTS["questions"][k]
                        for k in DEFAULTS["questions"]}
    m_raw = raw.get("modules") if isinstance(raw.get("modules"), dict) else {}
    out["modules"] = {k: m_raw[k] if m_raw.get(k) in ("auto", "on", "off") else DEFAULTS["modules"][k]
                      for k in DEFAULTS["modules"]}
    return out


def _sensor(raw, default: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(default)
    if raw.get("mode") in SENSOR_MODES:
        out["mode"] = raw["mode"]
    if raw.get("trust") in TRUST_WORDS:
        out["trust"] = raw["trust"]
    if isinstance(raw.get("label"), str):
        out["label"] = raw["label"].strip()[:DETECTOR_LABEL_MAX]
    if isinstance(raw.get("auto_learning"), bool):
        out["auto_learning"] = raw["auto_learning"]
    return out


def _face_sensors(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    base = DEFAULTS["face_sensors"]["frigate"]
    out = {"auto_learning": raw["auto_learning"] if isinstance(raw.get("auto_learning"), bool)
           else DEFAULTS["face_sensors"]["auto_learning"],
           "frigate": _sensor(raw.get("frigate"), base), "detectors": {}}
    dets = raw.get("detectors") if isinstance(raw.get("detectors"), dict) else {}
    for name, d in dets.items():
        if isinstance(name, str) and name.strip() and len(name) <= DETECTOR_NAME_MAX:
            out["detectors"][name.strip()] = _sensor(d, base)
    return out


def _face_votes(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = copy.deepcopy(DEFAULTS["face_votes"])
    if isinstance(raw.get("enabled"), bool):
        out["enabled"] = raw["enabled"]
    if raw.get("provider") in FACE_VOTE_PROVIDERS:
        out["provider"] = raw["provider"]
    prefix = raw.get("topic_prefix")
    if isinstance(prefix, str) and _TOPIC_PREFIX_RE.match(prefix.strip().strip("/")):
        out["topic_prefix"] = prefix.strip().strip("/")
    url = raw.get("rest_url")
    if isinstance(url, str) and (not url.strip() or url.strip().startswith(("http://", "https://"))):
        out["rest_url"] = url.strip().rstrip("/")
    out["min_confidence"] = _number(raw.get("min_confidence"), 0.0, "face_votes.min_confidence")
    return out


def enabled() -> bool:
    return get_settings()["enabled"]


def episode_gap_s() -> float:
    return get_settings()["episode_gap_s"]


# «Kan lage episoder» on each camera card (P144-s14, «det styres i GUI under Kamera-innstillinger hvilket
# kamera som skal hva»). A card without the key gets its role's default: a road-facing camera sees traffic all day.
# Replaces the Expert list `episode_ignore_roles`, which silently beat the camera ticks (config migration step 9
# moved its values onto the cards).
EPISODES_OFF_ROLES = ("road_facing",)


def camera_episodes_default(role) -> bool:
    return role not in EPISODES_OFF_ROLES


def camera_makes_episodes(card: dict | None) -> bool:
    """Whether a camera's events may open / keep open an episode: the card's switch, else its role's default."""
    card = card if isinstance(card, dict) else {}
    value = card.get("episodes")
    return value if isinstance(value, bool) else camera_episodes_default(card.get("role"))


def episode_min_movement() -> float:
    return get_settings()["episode_min_movement"]


def visit_gap_s() -> float:
    return get_settings()["visit_gap_s"]


def visit_fold_interval_s() -> float:
    return get_settings()["visit_fold_interval_s"]


def l0_min_face_px() -> int:
    return get_settings()["l0_min_face_px"]


def l0_min_claim_score() -> float:
    return get_settings()["l0_min_claim_score"]


def episode_frames_caps() -> tuple[int, int]:
    """(max MB for whole frame sets, MB for the one kept frame per episode)."""
    s = get_settings()
    return s["episode_frames_max_mb"], s["episode_frames_keep_mb"]


def episode_weather() -> bool:
    return get_settings()["episode_weather"]


def expressions() -> bool:
    return get_settings()["expressions"]


def conclude() -> dict:
    return get_settings()["conclude"]


def questions_wrong_rules() -> bool:
    """J20: «Hold tilbake bekreftet feil» — True = a confirmed-wrong rule holds the detection back; False (default) =
    shadow, only written on the event."""
    return bool(get_settings().get("questions", {}).get("wrong_rules", False))


def questions_chat() -> bool:
    """J11: may Kåre put one question from the user's list into a chat now and then (GUI «Spør i chatten»)."""
    return bool(get_settings().get("questions", {}).get("chat", True))


def face_sensors() -> dict:
    """The face sensors' words (P144-s29b). Callers in the pipe read them through face_votes.sensor_weight()."""
    return get_settings()["face_sensors"]


def face_votes() -> dict:
    """The extra face recognition settings (P144-s19): enabled, provider, topic_prefix, rest_url, min_confidence."""
    return get_settings()["face_votes"]


def retention() -> dict:
    """The retention settings (P150): frigate_db_max_mb (0 = off) and dry_run."""
    s = get_settings()
    return {"db_max_mb": s["frigate_db_max_mb"], "dry_run": s["retention_dry_run"]}


def unknown_plate_days() -> int | None:
    """Days an unknown plate's text is kept; None = always."""
    return None if get_settings()["keep_unknown_plates"] == "always" else PLATE_KEEP_DAYS


# ── writer (admin API only) ─────────────────────────────────────────────────


def _check_number(key: str, value) -> None:
    lo, hi = LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
        raise ValueError(f"{key} must be a number {lo}–{hi}")


def _validated(changes: dict) -> dict:
    if not isinstance(changes, dict):
        raise ValueError("settings must be a mapping")
    out: dict = {}
    for key, value in changes.items():
        if key in ("enabled", "retention_dry_run", "episode_weather", "expressions"):
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            out[key] = value
        elif key == "keep_unknown_plates":
            if value not in PLATE_KEEP:
                raise ValueError(f"keep_unknown_plates must be one of {', '.join(PLATE_KEEP)}")
            out[key] = value
        elif key == "conclude":
            if not isinstance(value, dict):
                raise ValueError("conclude must be a mapping")
            sub: dict = {}
            for ck, cv in value.items():
                if f"conclude.{ck}" in LIMITS:
                    _check_number(f"conclude.{ck}", cv)
                elif ck == "urgent_when_away_person":
                    if not isinstance(cv, bool):
                        raise ValueError("urgent_when_away_person must be true or false")
                elif ck == "urgent_classes":
                    if not isinstance(cv, list) or any(v not in URGENT_CLASSES for v in cv):
                        raise ValueError(f"urgent_classes must be a list of {', '.join(URGENT_CLASSES)}")
                elif ck == "urgent_labels":
                    if not isinstance(cv, list) or not all(isinstance(v, str) and v.strip() for v in cv):
                        raise ValueError("urgent_labels must be a list of labels")
                else:
                    raise ValueError(f"unknown setting: conclude.{ck}")
                sub[ck] = cv
            out[key] = sub
        elif key == "face_votes":
            out[key] = _validated_face_votes(value)
        elif key == "face_sensors":
            out[key] = _validated_face_sensors(value)
        elif key == "questions":
            if not isinstance(value, dict) or any(k not in DEFAULTS["questions"] or not isinstance(v, bool)
                                                  for k, v in value.items()):
                raise ValueError("questions must be {chat: true|false, wrong_rules: true|false}")
            out[key] = dict(value)
        elif key == "modules":
            if not isinstance(value, dict) or any(k not in DEFAULTS["modules"] or v not in ("auto", "on", "off")
                                                  for k, v in value.items()):
                raise ValueError("modules must be {m1..m6: auto|on|off} (modules 0 and 7 are always on)")
            out[key] = dict(value)
        elif key in LIMITS:
            _check_number(key, value)
            out[key] = value
        else:
            raise ValueError(f"unknown setting: {key}")
    return out


def _validated_face_votes(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("face_votes must be a mapping")
    for k, v in value.items():
        if k == "enabled":
            if not isinstance(v, bool):
                raise ValueError("face_votes.enabled must be true or false")
        elif k == "provider":
            if v not in FACE_VOTE_PROVIDERS:
                raise ValueError(f"face_votes.provider must be one of {', '.join(FACE_VOTE_PROVIDERS)}")
        elif k == "topic_prefix":
            if not isinstance(v, str) or not _TOPIC_PREFIX_RE.match(v.strip().strip("/")):
                raise ValueError("face_votes.topic_prefix must be an MQTT topic without wildcards")
        elif k == "rest_url":
            if not isinstance(v, str) or (v.strip() and not v.strip().startswith(("http://", "https://"))):
                raise ValueError("face_votes.rest_url must be empty or start with http:// or https://")
        elif k == "min_confidence":
            _check_number("face_votes.min_confidence", v)
        else:
            raise ValueError(f"unknown setting: face_votes.{k}")
    return dict(value)


def _validated_sensor(where: str, d) -> None:
    if not isinstance(d, dict):
        raise ValueError(f"{where} must be {{mode, trust, label}}")
    for k, v in d.items():
        if k == "mode":
            if v not in SENSOR_MODES:
                raise ValueError(f"{where}.mode must be one of {', '.join(SENSOR_MODES)}")
        elif k == "trust":
            if v not in TRUST_WORDS:
                raise ValueError(f"{where}.trust must be one of {', '.join(TRUST_WORDS)}")
        elif k == "label":
            if not isinstance(v, str) or len(v) > DETECTOR_LABEL_MAX:
                raise ValueError(f"{where}.label is text up to {DETECTOR_LABEL_MAX} characters")
        elif k == "auto_learning":
            if not isinstance(v, bool):
                raise ValueError(f"{where}.auto_learning must be true or false")
        else:
            raise ValueError(f"unknown setting: {where}.{k}")


def _validated_face_sensors(value) -> dict:
    """{frigate: {mode, trust, label}, detectors: {<provider's detector name>: {mode, trust, label}}}."""
    if not isinstance(value, dict):
        raise ValueError("face_sensors must be a mapping")
    for k, v in value.items():
        if k == "auto_learning":
            if not isinstance(v, bool):
                raise ValueError("face_sensors.auto_learning must be true or false")
        elif k == "frigate":
            _validated_sensor("face_sensors.frigate", v)
        elif k == "detectors":
            if not isinstance(v, dict):
                raise ValueError("face_sensors.detectors must be a mapping of detector name → {mode, trust, label}")
            for name, d in v.items():
                if not isinstance(name, str) or not name.strip() or len(name) > DETECTOR_NAME_MAX:
                    raise ValueError(f"face_sensors.detectors: a detector name is 1–{DETECTOR_NAME_MAX} characters")
                _validated_sensor(f"face_sensors.detectors.{name}", d)
        else:
            raise ValueError(f"unknown setting: face_sensors.{k}")
    return dict(value)



def update(changes: dict) -> dict:
    """Validate and write a partial change (GUI). Returns the new effective settings."""
    clean = _validated(changes)

    def _apply(data):
        for key, value in clean.items():
            if key in ("conclude", "face_votes", "face_sensors", "questions", "modules"):
                if not isinstance(data.get(key), dict):
                    data[key] = {}
                data[key].update(value)
            else:
                data[key] = value

    config_store.update_yaml(_path(), _apply, "frigate user settings")
    return get_settings()
