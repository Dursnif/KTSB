"""
Contextual Normalcy Baseline (P72 Phase 1).

Builds a statistical frequency table from Frigate detection history:
  {camera|label|hour|weekday: {p_normal, days_with_event, days_observed}}

p_normal = fraction of days in the 28-day window where at least one event
           of this type occurred in this (camera, label, hour, weekday) bucket.

deviation_score = 1 - p_normal
  → 0.0: happens every time (fully normal)
  → 1.0: never happens at this time (highly unusual)

Confidence: min(days_of_data / 30, 1.0)
  → < 0.5 (< 15 days): scores present but not used in alert routing
  → 0.5–1.0: used for graduated alert urgency

Baseline is computed nightly by kaare_nightjob.py and written to
state/normalcy_baseline.json. get_deviation_score() reads from an
in-memory cache — zero blocking in the hot path.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

logger = logging.getLogger("normalcy")

_BASELINE_PATH     = Path("/kaare/state/normalcy_baseline.json")
_FRIGATE_LOG_PATH  = Path("/kaare/logs/frigate_mqtt.log")
_CORRECTIONS_PATH  = Path("/kaare/state/normalcy_corrections.json")
_SERVICES_PATH     = Path("/kaare/configs/services.yaml")
_WINDOW_DAYS       = 28         # rolling window for baseline computation
_MIN_TRUST_DAYS    = 14         # deviation_score not used in alert routing below this
_CHUNK_BYTES       = 5_000_000  # read last 5 MB of log (~35k events)
_ANOMALY_CHUNK     = 10_000_000 # read last 10 MB for get_recent_anomalies (7+ days)

_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday",
                  "friday", "saturday", "sunday"]

# In-memory cache: loaded once, refreshed after nightly compute
_cache: dict = {}
_cache_mtime: float = 0.0


def _load_baseline() -> dict:
    """Load baseline from disk into cache if file has changed."""
    global _cache, _cache_mtime
    try:
        if not _BASELINE_PATH.exists():
            return {}
        mtime = _BASELINE_PATH.stat().st_mtime
        if mtime != _cache_mtime:
            _cache = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
            _cache_mtime = mtime
        return _cache
    except Exception as e:
        logger.debug("[normalcy] load error: %s", e)
        return {}


def get_deviation_score(
    camera: str, label: str, dt: datetime | None = None
) -> tuple[float, float]:
    """
    Return (deviation_score, confidence) for a Frigate event.

    dt: timestamp of the event (defaults to now). Pass event timestamp when
        scoring historical events so hour/weekday match the baseline bucket.

    deviation_score: 0.0 (very normal) to 1.0 (very unusual)
    confidence:      0.0 (no data) to 1.0 (30+ days of data)

    Never raises.
    """
    try:
        baseline = _load_baseline()
        if not baseline:
            return 0.5, 0.0

        confidence = float(baseline.get("confidence", 0.0))
        days_of_data = int(baseline.get("days_of_data", 0))

        if days_of_data < 1:
            return 0.5, 0.0

        ref = dt or datetime.now()
        hour = ref.hour
        weekday = ref.weekday()  # 0=Mon, 6=Sun
        key = f"{camera}|{label}|{hour}|{weekday}"

        buckets = baseline.get("buckets", {})
        bucket = buckets.get(key)

        if bucket is None:
            # No data for this bucket — assume unusual if we have enough general data
            score = 0.7 if confidence >= 0.5 else 0.5
            return score, confidence

        p_normal = float(bucket.get("p_normal", 0.5))
        return 1.0 - p_normal, confidence

    except Exception as e:
        logger.debug("[normalcy] get_deviation_score error: %s", e)
        return 0.5, 0.0


def _load_corrections() -> list[dict]:
    """Load admin corrections from state/normalcy_corrections.json."""
    try:
        if not _CORRECTIONS_PATH.exists():
            return []
        return json.loads(_CORRECTIONS_PATH.read_text(encoding="utf-8")).get("corrections", [])
    except Exception:
        return []


def compute_baseline() -> dict:
    """
    Build the normalcy baseline from the last _WINDOW_DAYS of frigate_mqtt.log.
    Writes state/normalcy_baseline.json and returns the baseline dict.
    """
    if not _FRIGATE_LOG_PATH.exists():
        logger.info("[normalcy] frigate_mqtt.log not found — skipping baseline")
        return {}

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=_WINDOW_DAYS)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    # Read last _CHUNK_BYTES — covers ~28 days at typical traffic
    try:
        with _FRIGATE_LOG_PATH.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _CHUNK_BYTES))
            raw = fh.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("[normalcy] log read error: %s", e)
        return {}

    # bucket_days[key] = set of "YYYY-MM-DD" days that had at least one event
    bucket_days: dict[str, set] = defaultdict(set)
    # all_days: set of (weekday, hour) days we observed any traffic in the window
    observed_days: set = set()  # set of "YYYY-MM-DD"

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue

        if ev.get("stage") != "detection_end":
            continue

        ts_str = ev.get("ts", "")
        if ts_str < cutoff_iso:
            continue

        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue

        camera  = ev.get("camera", "")
        label   = ev.get("label", "")
        hour    = ts_dt.hour
        weekday = ts_dt.weekday()
        day_str = ts_dt.strftime("%Y-%m-%d")

        key = f"{camera}|{label}|{hour}|{weekday}"
        bucket_days[key].add(day_str)
        observed_days.add(day_str)

    days_of_data = len(observed_days)
    confidence = min(days_of_data / 30.0, 1.0)

    # Apply admin corrections — 'normal' verdicts inject synthetic days
    corrections = _load_corrections()
    for corr in corrections:
        if corr.get("verdict") != "normal":
            continue
        cam  = corr.get("source_key", "").replace("frigate:", "").split(":")[0]
        lbl  = corr.get("source_key", "").replace("frigate:", "").split(":")[-1] if corr.get("source_key") else ""
        hour_s = corr.get("hour_bucket", "")
        wd_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                  "friday": 4, "saturday": 5, "sunday": 6}
        wd = wd_map.get(corr.get("weekday", "").lower(), -1)
        if cam and lbl and hour_s.isdigit() and wd >= 0:
            key = f"{cam}|{lbl}|{int(hour_s)}|{wd}"
            # Inject synthetic days equivalent to 14 days of confirmed normal
            for i in range(14):
                bucket_days[key].add(f"correction-{i}")

    # Build buckets
    # Days observed per (weekday, hour) combination — denominator for p_normal
    wday_hour_days: dict[tuple, set] = defaultdict(set)
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("stage") != "detection_end":
            continue
        ts_str = ev.get("ts", "")
        if ts_str < cutoff_iso:
            continue
        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        day_str = ts_dt.strftime("%Y-%m-%d")
        wday_hour_days[(ts_dt.weekday(), ts_dt.hour)].add(day_str)

    buckets: dict[str, dict] = {}
    for key, days_with_event in bucket_days.items():
        parts = key.split("|")
        if len(parts) != 4:
            continue
        _cam, _lbl, hour_s, wd_s = parts
        try:
            wh_key = (int(wd_s), int(hour_s))
        except Exception:
            continue
        days_obs = len(wday_hour_days.get(wh_key, set()))
        if days_obs == 0:
            days_obs = days_of_data or 1
        n_with = len(days_with_event)
        p_normal = min(1.0, n_with / days_obs)
        buckets[key] = {
            "p_normal":        round(p_normal, 3),
            "days_with_event": n_with,
            "days_observed":   days_obs,
        }

    result = {
        "version":      1,
        "updated_at":   now_utc.isoformat(timespec="seconds"),
        "window_start": cutoff.strftime("%Y-%m-%d"),
        "days_of_data": days_of_data,
        "confidence":   round(confidence, 3),
        "bucket_count": len(buckets),
        "buckets":      buckets,
    }

    try:
        _BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BASELINE_PATH.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "[normalcy] Baseline computed: %d days, %d buckets, confidence=%.2f",
            days_of_data, len(buckets), confidence,
        )
    except Exception as e:
        logger.warning("[normalcy] write error: %s", e)

    # Invalidate cache
    global _cache_mtime
    _cache_mtime = 0.0

    return result


def get_recent_anomalies(
    days: int = 7,
    min_score: float = 0.5,
    min_confidence: float = 0.0,
    limit: int = 100,
    cameras: list[str] | None = None,
    labels: list[str] | None = None,
) -> list[dict]:
    """
    Return recent Frigate detection events with deviation_score >= min_score.

    Each result dict contains: ts, camera, camera_friendly, label, score,
    deviation_score, baseline_confidence, source_key, hour_bucket, weekday,
    correction (None or the matching correction dict).

    Results are sorted by ts descending, capped at limit.
    """
    if not _FRIGATE_LOG_PATH.exists():
        return []

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=days)
    cutoff_iso = cutoff.isoformat(timespec="seconds")

    try:
        with _FRIGATE_LOG_PATH.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _ANOMALY_CHUNK))
            raw = fh.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("[normalcy] get_recent_anomalies read error: %s", e)
        return []

    # Load camera friendly names
    cam_names: dict[str, str] = {}
    try:
        svc = yaml.safe_load(_SERVICES_PATH.read_text(encoding="utf-8")) or {}
        cam_names = svc.get("frigate", {}).get("camera_names", {}) or {}
    except Exception:
        pass

    # Build correction lookup: (camera, label, hour_bucket, weekday) → correction dict
    corr_lookup: dict[tuple, dict] = {}
    for c in _load_corrections():
        sk = c.get("source_key", "").replace("frigate:", "")
        parts = sk.split(":", 1)
        if len(parts) == 2:
            cam, lbl = parts
            corr_lookup[(cam, lbl, c.get("hour_bucket", ""), c.get("weekday", ""))] = c

    results: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue

        if ev.get("stage") != "detection_end":
            continue

        ts_str = ev.get("ts", "")
        if ts_str < cutoff_iso:
            continue

        try:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue

        camera = ev.get("camera", "")
        label  = ev.get("label", "")

        if cameras and camera not in cameras:
            continue
        if labels and label not in labels:
            continue

        deviation_score, confidence = get_deviation_score(camera, label, dt=ts_dt)

        if deviation_score < min_score:
            continue
        if confidence < min_confidence:
            continue

        hour_bucket = str(ts_dt.hour).zfill(2)
        weekday     = _WEEKDAY_NAMES[ts_dt.weekday()]
        source_key  = f"frigate:{camera}:{label}"
        correction  = corr_lookup.get((camera, label, hour_bucket, weekday))

        results.append({
            "ts":                  ts_str,
            "camera":              camera,
            "camera_friendly":     cam_names.get(camera, camera),
            "label":               label,
            "score":               round(float(ev.get("score", 0)), 2),
            "deviation_score":     round(deviation_score, 2),
            "baseline_confidence": round(confidence, 2),
            "source_key":          source_key,
            "hour_bucket":         hour_bucket,
            "weekday":             weekday,
            "correction":          correction,
            "event_id":            ev.get("event_id", ""),
        })

    results.sort(key=lambda x: x["ts"], reverse=True)
    return results[:limit]


def add_correction(
    source_key: str,
    hour_bucket: str,
    weekday: str,
    verdict: str,
    comment: str = "",
    by: str = "admin",
) -> None:
    """
    Append an admin correction to state/normalcy_corrections.json.
    Replaces any existing correction for the same (source_key, hour_bucket, weekday).
    Invalidates the baseline cache so the next get_deviation_score call re-reads the file.
    """
    try:
        corrections = _load_corrections()
        corrections = [
            c for c in corrections
            if not (
                c.get("source_key") == source_key
                and c.get("hour_bucket") == hour_bucket
                and c.get("weekday") == weekday
            )
        ]
        corrections.append({
            "source_key":  source_key,
            "hour_bucket": hour_bucket,
            "weekday":     weekday,
            "verdict":     verdict,
            "comment":     comment,
            "by":          by,
            "ts":          datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        _CORRECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CORRECTIONS_PATH.write_text(
            json.dumps({"corrections": corrections}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Invalidate cache so the correction is reflected at next lookup
        global _cache_mtime
        _cache_mtime = 0.0
        logger.info("[normalcy] correction saved: %s %s -> %s", source_key, weekday, verdict)
    except Exception as e:
        logger.warning("[normalcy] add_correction failed: %s", e)
