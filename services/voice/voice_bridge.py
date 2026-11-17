"""
Kåre Voice Bridge
FastAPI-server som tar imot wakeword/knapp-trigge fra noder
og kjører lokal voice pipeline (TTS ack → opptak → STT → Kåre API → TTS svar → avspilling).
"""

import asyncio
import base64
import hmac
import logging
import math
import os
import re as _re
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path

import df_compat
import httpx
import numpy as np
import soundfile as sf
import speaker_recognition as sr
import uvicorn
import yaml
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel
from scipy.signal import resample_poly
from tts_lexicon import parse_substitutions

from kaare_core import log_buffer, rooms
from kaare_core.audit import audit_log
from kaare_core.background import spawn
from kaare_core.config import ensure_config_file
from kaare_core.service_auth import is_service_request, service_headers
from kaare_core.tools.i18n import get_lang, get_speech_lang, t
from kaare_core.voice import (
    audio_backend,
    cast_sender,
    channels,
    direct_node,
    lead_in,
    screen_io,
    speak_target,
    tv_control,
)
from kaare_core.voice import output_routing as routing
from kaare_core.voice import screen as screen_core
from kaare_core.voice import volume as voice_volume
from kaare_core.voice.ducking import DuckIO, DuckSession, mpd_quiet_command
from kaare_core.voice.media_host import configured_media_host, in_container, media_host
from kaare_core.voice.providers.chromecast import CastFirstProvider
from kaare_core.voice.providers.dlna import description_url_of as dlna_description_url_of
from kaare_core.voice.providers.dlna import play_uri as dlna_play_uri
from kaare_core.voice.registry import VoiceProviderRegistry
from kaare_core.voice.silence import SilenceDetector
from kaare_core.voice.stt_models import cache_dir as stt_cache_dir
from kaare_core.voice.stt_models import cpu_threads as stt_cpu_threads
from kaare_core.voice.stt_models import resolve_model as resolve_stt_model
from kaare_core.voice.tts_models import DEFAULT_VOICES, download_voice, piper_local_path, tts_lang
from kaare_core.voice.wyoming_server import (
    ERROR_OUTPUT_UNREACHABLE,
    WyomingReply,
    WyomingServer,
)

# ---------------------------------------------------------------------------
# Konfig
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
# configs/ like every other setting (P127) — before, services/voice/configs/ shipped the house's IP and
# mic device and was baked into the image. A missing file is created from configs_default/.
CONFIG_PATH = ensure_config_file(Path("/kaare/configs/voice_settings.yaml"))
SERVICES_PATH = Path("/kaare/configs/services.yaml")

with open(CONFIG_PATH) as f:
    cfg = yaml.safe_load(f)

with open(SERVICES_PATH) as f:
    _svc = yaml.safe_load(f)

_voice_svc = _svc.get("voice", {})
_STT_ENABLED: bool = _voice_svc.get("stt", {}).get("enabled", True)

_VOICE_PROVIDERS_PATH = Path("/kaare/configs/voice_providers.yaml")
_providers_cfg: dict = {}
if _VOICE_PROVIDERS_PATH.exists():
    with open(_VOICE_PROVIDERS_PATH) as f:
        _providers_cfg = yaml.safe_load(f) or {}

MAIN_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
with open(MAIN_SETTINGS_PATH) as f:
    _main_cfg = yaml.safe_load(f)

_enrollment_cfg = _main_cfg.get("voice_enrollment", {})
ENROLLMENT_DIR = Path(_enrollment_cfg.get("dir", "/kaare/state/users"))
SPEAKER_THRESHOLD = float(_enrollment_cfg.get("identification_threshold", 0.75))

# The main API's address comes from services.yaml (internal.kaare_api) — 127.0.0.1 at home,
# http://kaare-api:8000 in Docker. Not from voice_settings.yaml, where a fixed 127.0.0.1 broke
# login checks and voice → main agent in Docker (DT03, 2026-10-07).
_KAARE_API_BASE = str(_svc.get("internal", {}).get("kaare_api") or "http://127.0.0.1:8000").rstrip(
    "/"
)
KAARE_API_URL = f"{_KAARE_API_BASE}/api/generate"
KAARE_TIMEOUT = cfg["kaare"]["timeout"]

STT_BACKEND = _voice_svc["stt"].get("backend", "openvino")
STT_MODEL_DIR = Path(_voice_svc["stt"]["model_dir"])
STT_FW_MODEL = resolve_stt_model(
    _voice_svc["stt"].get("faster_whisper_model", ""), get_speech_lang()
)
STT_FW_COMPUTE = _voice_svc["stt"].get("faster_whisper_compute_type", "int8")
STT_FW_THREADS = stt_cpu_threads(_voice_svc["stt"].get("cpu_threads", 0))
VENV_PYTHON = Path(_voice_svc["venv_python"])
PIPER_BIN = VENV_PYTHON  # called with -m piper.__main__
MAX_RECORD_SEC = cfg["stt"]["max_record_seconds"]
SILENCE_SEC = cfg["stt"]["silence_threshold_seconds"]
SILENCE_AMP = cfg["stt"]["silence_amplitude_threshold"]
# Local mic end of speech (P138): "auto" = adaptive SilenceDetector (same rule as Wyoming), "fixed" = SILENCE_AMP.
END_MODES = ("auto", "fixed")
END_OF_SPEECH = (
    cfg["stt"].get("end_of_speech") if cfg["stt"].get("end_of_speech") in END_MODES else "auto"
)

TTS_RATE = _voice_svc["tts"]["sample_rate"]

# Multi-language TTS model map: lang code → Path. Falls back to nb.
_tts_models_cfg: dict = _voice_svc.get("tts", {}).get("tts_models", {})
_TTS_FALLBACK = Path(_voice_svc["tts"].get("voice") or "")
_TTS_MODELS: dict[str, Path] = {}
for _lang, _path in _tts_models_cfg.items():
    if _path and Path(_path).is_file():
        _TTS_MODELS[_lang] = Path(_path)
    elif _lang == "nb":
        _TTS_MODELS["nb"] = _TTS_FALLBACK


def _ensure_household_voice() -> None:
    """A fresh install ships no Piper voice (rule 24: must work from the defaults). If the household
    language has no voice on disk, use — and if needed download — its default voice into
    <models_dir>/voice/piper. Never raises: without a voice, TTS fails per request as before."""
    global _TTS_FALLBACK
    lang = tts_lang(get_speech_lang())
    current = _TTS_MODELS.get(lang)
    if current is not None and current.is_file():
        return
    name = DEFAULT_VOICES.get(lang)
    if not name:
        return
    path = piper_local_path(name)
    if not path.is_file():
        try:
            log.info("No TTS voice for '%s' — downloading default voice %s", lang, name)
            path = download_voice(name)
        except Exception as exc:
            log.error("Default TTS voice download failed (%s): %s", name, exc)
            return
    _TTS_MODELS[lang] = path
    if not _TTS_FALLBACK.is_file():
        _TTS_FALLBACK = path
    log.info("TTS voice for '%s': %s", lang, path)


# KTSB Direct mic (P138 4b, GUI: Nodes → KTSB Direct): a device name or sounddevice index, null = system default;
# rate null = the device's own default rate.
MIC_DEVICE = (cfg.get("audio") or {}).get("mic_device")
MIC_RATE = (cfg.get("audio") or {}).get("mic_sample_rate")
MIC_CHANNELS = 1
TARGET_RATE = 16000

CACHE_DIR = BASE_DIR / cfg["fast_responses"]["cache_dir"]
WAKE_ACK_WAV = CACHE_DIR / "wake_ack_0.wav"

HTTP_PORT = cfg["bridge"]["http_port"]
_duck_cfg: dict = cfg.get("duck", {})

TMP_AUDIO_DIR = Path("/tmp/kaare_tts")
TMP_AUDIO_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# TTS pronunciation lexicon
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("voice_bridge")

def _voice_profile_on() -> bool:
    """Speech was chosen for this install: bare metal, or Docker with the `full` profile (P139 D5). Without it the
    bridge still runs as KTSB's output hub (Cast, DLNA, pictures), but downloads and loads no speech models until
    someone actually uses speech — the README/privacy table promise «no model downloads without `full`»."""
    if not in_container():
        return True
    return "full" in {p.strip() for p in os.environ.get("COMPOSE_PROFILES", "").split(",")}


if _voice_profile_on():
    _ensure_household_voice()  # needs `log`; before the first TTS request

_ROUTE_LOG = Path("/kaare/logs/route_decisions.log")


def _route_log_voice(rid: str, stage: str, **fields) -> None:
    """Write a voice pipeline event to route_decisions.log (same format as kaare_api)."""
    rec = {
        "ts": datetime.now(UTC).isoformat(),
        "rid": rid,
        "source": "voice_bridge",
        "subsystem": "routing",
        "stage": stage,
        **fields,
    }
    log_buffer.write(_ROUTE_LOG, rec)


_LEXICON_PATHS: dict[str, Path] = {
    "nb": Path("/kaare/configs/tts_lexicon.yaml"),
    "en": Path("/kaare/configs/tts_lexicon_en.yaml"),
    "de": Path("/kaare/configs/tts_lexicon_de.yaml"),
}
_tts_substitutions: dict[str, list[tuple[_re.Pattern, str]]] = {}


def _load_lexicon(lang: str, path: Path) -> None:
    try:
        with open(path) as f:
            lex = yaml.safe_load(f) or {}
        _tts_substitutions[lang] = parse_substitutions(lex)
        log.info(
            "TTS lexicon loaded for lang=%s: %d substitutions", lang, len(_tts_substitutions[lang])
        )
    except FileNotFoundError:
        _tts_substitutions[lang] = []
    except Exception as exc:
        log.warning("TTS lexicon load failed for lang=%s: %s", lang, exc)
        _tts_substitutions[lang] = []


for _lang, _lex_path in _LEXICON_PATHS.items():
    _load_lexicon(_lang, _lex_path)


def _preprocess_tts(text: str, lang: str = "nb") -> str:
    """Apply pronunciation substitutions for the given language before passing text to Piper."""
    for pattern, replacement in _tts_substitutions.get(lang, []):
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# STT backend — openvino (Intel Arc GPU) or faster_whisper (universal CPU)
# ---------------------------------------------------------------------------


def _load_stt_backend():
    if STT_BACKEND == "faster_whisper":
        # PLC0415: optional dependency — only for the faster_whisper STT backend
        from faster_whisper import WhisperModel  # noqa: PLC0415

        log.info(
            "Loading faster-whisper model '%s' (compute_type=%s, cpu_threads=%d)...",
            STT_FW_MODEL,
            STT_FW_COMPUTE,
            STT_FW_THREADS,
        )
        # download_root = where the GUI downloads models (P126); on a volume in Docker, so a
        # model is fetched once, not again on every container recreate.
        model = WhisperModel(
            STT_FW_MODEL,
            device="cpu",
            compute_type=STT_FW_COMPUTE,
            cpu_threads=STT_FW_THREADS,
            download_root=str(stt_cache_dir()),
        )
        log.info("faster-whisper ready.")
        return model
    # Default: openvino
    # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
    import openvino_genai  # noqa: PLC0415

    log.info("Laster OpenVINO GenAI WhisperPipeline på Intel Arc GPU...")
    pipe = openvino_genai.WhisperPipeline(str(STT_MODEL_DIR), device="GPU")
    log.info("WhisperPipeline klar på Intel Arc GPU.")
    return pipe


def _stt_enabled_now() -> bool:
    """The GUI switch (Settings → LLM → Whisper), read fresh so turning it on needs no restart (rule 25)."""
    try:
        with open(SERVICES_PATH) as f:
            return bool(((yaml.safe_load(f) or {}).get("voice") or {}).get("stt", {}).get("enabled", True))
    except Exception:
        return _STT_ENABLED


# P139 D5: the bridge ships in every Docker profile (it is KTSB's output hub — Cast, DLNA, speakers); the speech model
# (GBs of RAM) loads only when speech recognition is on, and on first use if it is switched on later.
_stt_model = _load_stt_backend() if _STT_ENABLED and _voice_profile_on() else None
_stt_load_lock = threading.Lock()


def _ensure_stt() -> bool:
    global _stt_model
    if _stt_model is not None:
        return True
    if not _stt_enabled_now():
        return False
    with _stt_load_lock:
        if _stt_model is None:
            _stt_model = _load_stt_backend()
    return True

# ---------------------------------------------------------------------------
# DeepFilterNet (dereverberation + støyreduksjon) — lazy-loaded
# ---------------------------------------------------------------------------

_df_model = None
_df_state = None
DF_SR: int = 48000


def _ensure_df() -> None:
    global _df_model, _df_state, DF_SR
    if _df_model is not None:
        return
    df_compat.ensure()  # DeepFilterNet 0.5.6 imports a torchaudio module removed in torchaudio 2.3 (P145)
    # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
    from df.enhance import init_df  # noqa: PLC0415

    log.info("Laster DeepFilterNet...")
    _df_model, _df_state, _ = init_df()
    DF_SR = _df_state.sr()
    log.info("DeepFilterNet klar (native SR: %d Hz).", DF_SR)


def _agc(audio: np.ndarray) -> np.ndarray:
    """Enkel AGC: normaliser til målnivå basert på talesegmenter."""
    speech_mask = np.abs(audio) > 0.02
    if speech_mask.any():
        speech_rms = np.sqrt(np.mean(audio[speech_mask] ** 2))
        if speech_rms > 0.001:
            gain = min(0.15 / speech_rms, 5.0)
            audio = np.clip(audio * gain, -1.0, 1.0)
    return audio


def process_audio(audio_16k: np.ndarray) -> np.ndarray:
    """
    Kjør DeepFilterNet på lyddata for dereverberation og støyreduksjon.
    Input/output: 16000 Hz mono float32.
    """
    _ensure_df()
    # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
    from df.enhance import enhance  # noqa: PLC0415

    # Resample 16000 → 48000 (DeepFilterNet sitt format)
    gcd = np.gcd(DF_SR, TARGET_RATE)
    audio_48k = resample_poly(audio_16k, DF_SR // gcd, TARGET_RATE // gcd).astype(np.float32)

    # DeepFilterNet forventer (1, samples) tensor
    # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
    import torch  # noqa: PLC0415

    tensor_in = torch.from_numpy(audio_48k).unsqueeze(0)
    tensor_out = enhance(_df_model, _df_state, tensor_in)
    audio_48k_clean = tensor_out.squeeze(0).numpy()

    # Resample tilbake 48000 → 16000
    audio_16k_clean = resample_poly(audio_48k_clean, TARGET_RATE // gcd, DF_SR // gcd).astype(
        np.float32
    )

    # AGC: normaliser til målnivå basert på talesegmenter (ikke stille)
    speech_mask = np.abs(audio_16k_clean) > 0.02
    if speech_mask.any():
        speech_rms = np.sqrt(np.mean(audio_16k_clean[speech_mask] ** 2))
        target_rms = 0.15
        if speech_rms > 0.001:
            gain = min(target_rms / speech_rms, 5.0)  # maks 5x gain
            audio_16k_clean = np.clip(audio_16k_clean * gain, -1.0, 1.0)

    return audio_16k_clean


# ---------------------------------------------------------------------------
# Pipeline-lås (unngår overlappende samtaler)
# ---------------------------------------------------------------------------
_pipeline_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Lyd-hjelpefunksjoner
# ---------------------------------------------------------------------------


def play_wav(path: Path) -> bool:
    """Play a WAV file via aplay (PipeWire/ALSA, handles resampling automatically). False when it did not play —
    e.g. no aplay / no sound device (a Docker install), so callers can say so instead of claiming it played."""
    try:
        result = subprocess.run(["aplay", str(path)], capture_output=True)
    except FileNotFoundError:
        log.error("Local speaker unavailable: aplay is not installed on this machine")
        return False
    if result.returncode != 0:
        log.error("aplay failed: %s", result.stderr.decode())
        return False
    return True


def _reload_tts_models() -> None:
    """Re-read tts_models from services.yaml and update _TTS_MODELS in place."""
    global _TTS_MODELS
    try:
        with open(SERVICES_PATH) as f:
            svc = yaml.safe_load(f) or {}
        models_cfg = svc.get("voice", {}).get("tts", {}).get("tts_models", {})
        new_map: dict[str, Path] = {}
        for lang, path in models_cfg.items():
            if path and Path(path).is_file():
                new_map[lang] = Path(path)
            elif lang == "nb":
                new_map["nb"] = _TTS_FALLBACK
        if "nb" not in new_map:
            new_map["nb"] = _TTS_FALLBACK
        _TTS_MODELS = new_map
        _ensure_household_voice()
        log.info("TTS models reloaded: %s", {k: str(v) for k, v in _TTS_MODELS.items()})
    except Exception as exc:
        log.error("Failed to reload TTS models: %s", exc)


def _resolve_tts_model(lang: str) -> Path:
    """Return the Piper model path for the given language, falling back to nb."""
    if not (_TTS_MODELS.get(lang) or _TTS_FALLBACK).is_file():
        _ensure_household_voice()  # first speech on an install without the voice profile: fetch the voice now
    model = _TTS_MODELS.get(lang) or _TTS_MODELS.get("nb") or _TTS_FALLBACK
    if not model.is_file():
        log.warning("TTS model for lang=%r not found at %s, using fallback", lang, model)
        return _TTS_FALLBACK
    return model


def speak(text: str, lang: str = "nb") -> None:
    """Generate speech with Piper and play locally (used for wake-ack fallback)."""
    text = _preprocess_tts(text, lang)
    model = _resolve_tts_model(lang)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = [
            str(VENV_PYTHON),
            "-m",
            "piper.__main__",
            "--model",
            str(model),
            "--output_file",
            str(tmp_path),
        ]
        result = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=15)
        if result.returncode != 0:
            log.error("Piper failed: %s", result.stderr.decode())
            return
        play_wav(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def generate_speech_wav(text: str, lang: str = "nb") -> Path:
    """
    Generer tale med Piper og lagre som WAV-fil i TMP_AUDIO_DIR.
    Returnerer Path til filen (ryddes opp av kalleren).
    """
    text = _preprocess_tts(text, lang)
    model = _resolve_tts_model(lang)
    cmd = [str(VENV_PYTHON), "-m", "piper.__main__", "--model", str(model), "--output_raw"]
    result = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=15)
    if result.returncode != 0:
        log.error("Piper feilet (lang=%s): %s", lang, result.stderr.decode())
        raise RuntimeError(f"Piper TTS feilet: {result.stderr.decode()}")

    raw = np.frombuffer(result.stdout, dtype=np.int16)
    filename = f"tts_{uuid.uuid4().hex}.wav"
    wav_path = TMP_AUDIO_DIR / filename
    sf.write(str(wav_path), raw, TTS_RATE, subtype="PCM_16")
    log.info("TTS WAV lagret: %s (lang=%s, model=%s)", wav_path, lang, model.name)
    return wav_path


def _delete_wav(path: Path) -> None:
    try:
        path.unlink()
        log.debug("Slettet midlertidig TTS-fil: %s", path)
    except OSError:
        pass


def _mpc(*args: str, text: bool = False):
    """Run mpc; None when MPD's client is not installed (a Docker install has no MPD — DT04 R5a), never a crash."""
    try:
        return subprocess.run(["mpc", *args], capture_output=True, text=text, timeout=3)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.debug("mpc %s unavailable: %s", " ".join(args), type(exc).__name__)
        return None


def _mpd_get_state() -> str:
    """Returns 'playing', 'paused', or 'stopped' ('stopped' when there is no MPD)."""
    result = _mpc("status", text=True)
    if result is None or result.returncode != 0:
        return "stopped"
    for line in result.stdout.splitlines():
        if "[playing]" in line:
            return "playing"
        if "[paused]" in line:
            return "paused"
    return "stopped"


def _mpd_pause() -> None:
    """Silence MPD while Kåre speaks: a radio stream is stopped (a paused stream spins one CPU core), a file paused."""
    current = _mpc("-f", "%file%", "current", text=True)
    _mpc(mpd_quiet_command(current.stdout if current is not None and current.returncode == 0 else ""))


def _mpd_resume() -> None:
    _mpc("play")


def _wav_duration_seconds(wav_path: Path) -> float:
    try:
        with wave.open(str(wav_path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except Exception:
        return 5.0


_LOCAL_FLOOR: dict[
    str, float
] = {}  # the local mic's noise floor from its last round, per device (P138)
_MIC_HW_SUFFIX = _re.compile(r"\s*\(hw:\d+,\d+\)\s*$")


def _resolve_mic_device(sd, wanted):
    """The configured mic as sounddevice takes it: None = system default, an index, or a name. A name that is gone
    (unplugged USB mic) or an index that now points at another device falls back to the system default with a
    warning instead of failing the press."""
    if wanted in (None, ""):
        return None
    if isinstance(wanted, int) or (isinstance(wanted, str) and wanted.strip().isdigit()):
        device = int(wanted)  # indices move between boots — the GUI stores names
    else:
        device = str(wanted)
    try:
        sd.query_devices(device, "input")  # raises when gone, ambiguous or not an input
        return device
    except Exception:
        pass
    if isinstance(device, str):
        # ALSA card numbers can change between boots: the same name without «(hw:N,M)», matched exactly and only
        # when one input has it (sounddevice's own match is word-wise, so «Analog» also hits «Alt Analog»).
        bare = _MIC_HW_SUFFIX.sub("", device)
        same = [d["index"] for d in _input_devices(sd) if _MIC_HW_SUFFIX.sub("", d["name"]) == bare]
        if len(same) == 1:
            return same[0]
    log.warning("Mic device %r not found as an input — using the system default", wanted)
    return None


def _input_devices(sd) -> list[dict]:
    """Input devices PortAudio sees, for the GUI picker."""
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = -1
    out = []
    for i, d in enumerate(sd.query_devices()):
        if int(d.get("max_input_channels") or 0) > 0:
            out.append(
                {
                    "index": i,
                    "name": str(d.get("name") or ""),
                    "rate": int(d.get("default_samplerate") or 0),
                    "channels": int(d["max_input_channels"]),
                    "default": i == default_in,
                }
            )
    return out


def record_audio(rid: str = "") -> np.ndarray:
    """
    Ta opp fra KTSB Direct-mikrofonen (valgt enhet i GUI, enhetens egen rate).
    Stopper ved SILENCE_SEC stillhet (auto: målt mot mikrofonens egen grunnstøy og stemmenivå; fixed: under
    SILENCE_AMP), ingen tale innen 6 s (auto), eller maks MAX_RECORD_SEC.
    Returnerer mono 16kHz float32-array.
    """

    # PLC0415: optional dependency — sounddevice needs the PortAudio system library (local mic only, absent in Docker)
    import sounddevice as sd  # noqa: PLC0415

    device = _resolve_mic_device(sd, MIC_DEVICE)
    rate = int(MIC_RATE or sd.query_devices(device, "input")["default_samplerate"])
    chunk = 2048  # samples per chunk
    silence_chunks_needed = int(SILENCE_SEC * rate / chunk)
    max_chunks = int(MAX_RECORD_SEC * rate / chunk)

    frames = []
    silent_chunks = 0
    amp_min: float | None = None
    amp_max: float | None = None
    amp_sum = 0.0
    n_chunks = 0
    stopped_by = "max_duration"
    mode = END_OF_SPEECH
    detector = (
        SilenceDetector(
            SILENCE_SEC,
            rate,
            4,
            no_speech_sec=min(6.0, float(MAX_RECORD_SEC)),
            prior_floor=_LOCAL_FLOOR.get(str(device)),
        )
        if mode == "auto"
        else None
    )

    log.info("Starter opptak (slutt på tale: %s) …", mode)
    with sd.InputStream(
        device=device,
        samplerate=rate,
        channels=MIC_CHANNELS,
        dtype="float32",
        blocksize=chunk,
    ) as stream:
        for _ in range(max_chunks):
            data, _ = stream.read(chunk)
            frames.append(data)
            # Bruk venstre kanal for amplitudemåling
            amplitude = float(np.abs(data[:, 0]).mean())
            amp_min = amplitude if amp_min is None else min(amp_min, amplitude)
            amp_max = amplitude if amp_max is None else max(amp_max, amplitude)
            amp_sum += amplitude
            n_chunks += 1
            if detector is not None:
                if detector.feed_level(amplitude, len(data) / rate):
                    stopped_by = detector.reason
                    if detector.floor and stopped_by in SilenceDetector.SPEECH_REASONS:
                        _LOCAL_FLOOR[str(device)] = detector.floor
                    log.info("Slutt på tale (%s), stopper opptak.", stopped_by)
                    break
            elif amplitude < SILENCE_AMP:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_needed:
                    stopped_by = "silence"
                    log.info("Stillhet detektert, stopper opptak.")
                    break
            else:
                silent_chunks = 0

    _route_log_voice(
        rid,
        "stt_record_stats",
        stopped_by=stopped_by,
        end_of_speech=mode,
        silence_threshold=SILENCE_AMP if detector is None else None,
        noise_floor=round(detector.floor, 4)
        if detector is not None and detector.floor is not None
        else None,
        speech_level=round(detector.speech_level, 4) if detector is not None else None,
        amp_min=round(amp_min, 4) if amp_min is not None else None,
        amp_max=round(amp_max, 4) if amp_max is not None else None,
        amp_mean=round(amp_sum / n_chunks, 4) if n_chunks else None,
    )

    if not frames:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(frames, axis=0)  # (N, 2)
    mono = audio[:, 0]  # venstre kanal → mono

    # Resample fra 44100 til 16000 Hz
    gcd = np.gcd(TARGET_RATE, rate)
    up = TARGET_RATE // gcd
    down = rate // gcd
    resampled = resample_poly(mono, up, down).astype(np.float32)

    log.info("Opptak ferdig: %.1f s", len(resampled) / TARGET_RATE)
    return resampled


_HALLUCINATIONS = {
    # Norwegian
    "takk for at du så på.",
    "takk for titten.",
    "takk for meg.",
    # English
    "thank you for watching.",
    "thanks for watching!",
    "thanks for watching.",
    "please subscribe.",
    "see you next time.",
    "bye.",
    # German
    "vielen dank fürs zuschauen.",
    "bis zum nächsten mal.",
    "untertitelung aufgrund der amara.org-community",
}


# kare_language's 16 GUI-selectable values are already valid Whisper language codes except
# "nb" (Bokmål) — Whisper only knows the generic "no" for Norwegian.
_WHISPER_LANG_MAP = {"nb": "no"}


def _stt_language() -> str:
    """Resolve the Whisper STT language code from kare_language (P101), uncollapsed."""
    code = get_speech_lang()
    return _WHISPER_LANG_MAP.get(code, code)


def _transcribe_openvino(audio: np.ndarray) -> str:
    # PLC0415: optional dependency — loaded only when this feature is used (rule 24)
    import openvino_genai  # noqa: PLC0415

    config = openvino_genai.WhisperGenerationConfig(str(STT_MODEL_DIR / "generation_config.json"))
    config.task = "transcribe"
    config.language = f"<|{_stt_language()}|>"
    config.num_beams = 1
    result = _stt_model.generate(np.ascontiguousarray(audio.flatten(), dtype=np.float32), config)
    return result.texts[0].strip() if result.texts else ""


def _transcribe_faster_whisper(audio: np.ndarray) -> str:
    segments, _ = _stt_model.transcribe(
        audio.flatten().astype(np.float32),
        language=_stt_language(),
        beam_size=1,
        vad_filter=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe(audio: np.ndarray) -> str:
    if audio.size == 0:
        return ""
    if not _ensure_stt():
        log.info("Speech recognition is switched off — audio ignored")
        return ""
    log.info("Lyd sendt til Whisper (%s): %d samples", STT_BACKEND, len(audio))
    try:
        if STT_BACKEND == "faster_whisper":
            text = _transcribe_faster_whisper(audio)
        else:
            text = _transcribe_openvino(audio)
    except Exception as exc:
        log.error("Whisper krasj: %s", exc)
        return ""

    if len(text) < 2 or text.lower() in _HALLUCINATIONS:
        return ""

    log.info("Transkripsjon: %s", text)
    return text


async def ask_kaare(
    text: str,
    room: str = "",
    resolved_user: str | None = None,
    best_guess: str | None = None,
    speaker_confidence: float = 0.0,
    confirmed_by: str = "none",
    rid: str = "",
    node_id: str = "",
) -> str:
    """Send tekst til Kåre API og returner svaret."""
    payload: dict = {"prompt": text, "source": "stt"}
    if rid:
        payload["rid"] = rid
    if node_id:
        payload["source_node"] = node_id
    context: dict = {}
    if room:
        context["room"] = room
    if resolved_user:
        payload["user_id"] = resolved_user
    context["speaker_result"] = {
        "confirmed_by": confirmed_by,
        "confirmed_user": resolved_user,
        "best_guess": best_guess,
        "confidence": round(speaker_confidence, 3),
        "threshold": SPEAKER_THRESHOLD,
    }
    payload["context"] = context

    async with httpx.AsyncClient(timeout=KAARE_TIMEOUT) as client:
        try:
            # service token: only KTSB's own services may name the speaker (P120)
            resp = await client.post(KAARE_API_URL, json=payload, headers=service_headers())
            resp.raise_for_status()
            data = resp.json()
            rid_received = data.get("rid", "")
            if rid_received:
                log.info("Kåre RID: %s", rid_received)
            answer = data.get("response") or data.get("message") or data.get("text") or str(data)
            log.info("Kåre svar: %s", answer)
            return answer
        except Exception as exc:
            log.error("Feil mot Kåre API: %s", exc)
            return t("voice_kare_unreachable", get_lang("global"))


# ---------------------------------------------------------------------------
# Hoved voice pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(node_id: str, room: str = "", default_user: str = "") -> None:
    """Komplett voice pipeline: ack → opptak → STT → Kåre → TTS → avspilling. The reply's output devices are
    quieted from the press until the cycle ends (P138)."""
    duck = _new_duck()
    if duck is not None:
        await duck.start(
            routing.audio_targets(
                node_id, _nodes, routing.INPUT_BUTTON, _playback_mode(), place_of=_place_of
            ),
            _nodes,
        )
    try:
        await _run_pipeline(node_id, room, default_user, duck)
    finally:
        if duck is not None:
            await duck.end()


async def _run_pipeline(
    node_id: str, room: str, default_user: str, duck: DuckSession | None
) -> None:
    loop = asyncio.get_running_loop()
    rid = f"rid-stt-{int(time.time() * 1000)}"
    t0 = time.time()
    _route_log_voice(rid, "stt_pipeline_start", node=node_id, room=room)

    # 0. DT04-S05o: no local sound backend (Docker, no PortAudio) → the honest reason, no ack/recording/traceback
    reason = audio_backend.missing_reason()
    if reason:
        log.warning("Voice pipeline for %s not started: %s (no microphone on this machine)", node_id, reason)
        _route_log_voice(rid, "stt_not_started", node=node_id, reason=reason)
        return

    # 1. Spill av "Ja?" lokalt (rask ack mens vi venter på bruker)
    if WAKE_ACK_WAV.exists():
        await loop.run_in_executor(None, play_wav, WAKE_ACK_WAV)
    else:
        await loop.run_in_executor(None, speak, "Ja?")
    _route_log_voice(rid, "stt_ack_done", elapsed_ms=int((time.time() - t0) * 1000))

    # 2. Ta opp brukerens stemme
    audio = await loop.run_in_executor(None, record_audio, rid)
    _route_log_voice(rid, "stt_record_done", elapsed_ms=int((time.time() - t0) * 1000))

    if audio.size < TARGET_RATE * 0.3:
        log.warning("For lite lyd fra %s, avbryter.", node_id)
        return

    # 2b. AGC: normaliser lydnivå for Whisper
    audio = await loop.run_in_executor(None, _agc, audio)
    _route_log_voice(rid, "stt_agc_done", elapsed_ms=int((time.time() - t0) * 1000))

    # 2c. Speaker identification (non-blocking, feiler stille)
    speaker_id: str | None = None
    best_guess: str | None = None
    speaker_confidence = 0.0
    try:
        speaker_id, best_guess, speaker_confidence = await loop.run_in_executor(
            None, sr.identify, audio, ENROLLMENT_DIR, SPEAKER_THRESHOLD
        )
    except Exception as exc:
        log.warning("Speaker identification skipped: %s", exc)

    # Speaker resolution: voice ID wins; single default_user as fallback; else unknown
    single_default = _single_default_user(node_id)
    if speaker_id:
        resolved_user = speaker_id
        confirmed_by = "voice"
    elif single_default:
        resolved_user = single_default
        confirmed_by = "default"
    else:
        resolved_user = _unidentified_identity(node_id)
        confirmed_by = "node_trust"
    log.info(
        "Speaker resolution: confirmed_by=%s user=%s best_guess=%s confidence=%.3f",
        confirmed_by,
        resolved_user,
        best_guess,
        speaker_confidence,
    )
    _route_log_voice(
        rid,
        "stt_speaker",
        confirmed_by=confirmed_by,
        user=resolved_user,
        best_guess=best_guess,
        confidence=round(speaker_confidence, 3),
    )

    # 3. STT
    t_stt = time.time()
    text = await loop.run_in_executor(None, transcribe, audio)
    stt_ms = int((time.time() - t_stt) * 1000)
    if not text:
        log.warning("Tom transkripsjon fra %s, avbryter.", node_id)
        return
    _route_log_voice(rid, "stt_done", text=text, stt_ms=stt_ms)

    # 4. Kåre API
    answer = await ask_kaare(
        text,
        room=room,
        resolved_user=resolved_user,
        best_guess=best_guess,
        speaker_confidence=speaker_confidence,
        confirmed_by=confirmed_by,
        rid=rid,
        node_id=node_id,
    )
    _route_log_voice(rid, "stt_kaare_done", total_ms=int((time.time() - t0) * 1000))

    # 5. TTS → WAV → playback where the node's audio_out says (P138; auto = bridge.playback_mode as before)
    wav_path = await loop.run_in_executor(None, generate_speech_wav, answer, get_speech_lang())
    targets = routing.audio_targets(
        node_id, _nodes, routing.INPUT_BUTTON, _playback_mode(), place_of=_place_of
    )
    _route_log_voice(rid, "stt_reply_targets", targets=targets)
    if duck is not None:
        await duck.restore_volumes()
    remote = [t for t in targets if t != routing.LOCAL]
    failed: list[str] = []
    if remote:
        _ok, failed = await _play_on_nodes(wav_path, remote)
        asyncio.get_running_loop().call_later(60, _delete_wav, wav_path)
    if routing.LOCAL in targets:
        await _play_local(wav_path, delete=not remote)
    if failed:
        _route_log_voice(rid, "stt_reply_unreachable", nodes=failed)
        await _say_unreachable(
            failed, routing.emergency_target(node_id, _nodes, routing.INPUT_BUTTON)
        )


async def trigger_pipeline(node_id: str, room: str = "", default_user: str = "") -> None:
    """Trigger pipeline med lås – avviser overlappende forespørsler."""
    if _pipeline_lock.locked():
        log.info("Pipeline allerede aktiv, ignorerer trigger fra %s.", node_id)
        return
    async with _pipeline_lock:
        try:
            await run_pipeline(node_id, room, default_user)
        except Exception as exc:
            log.exception("Feil i voice pipeline for %s: %s", node_id, exc)


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="Kåre Voice Bridge", version="1.0")

# Allow browser requests from the frontend (port 5173) — LAN-only service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load nodes from central config dir (admin GUI reads/writes this file)
_nodes: dict = {}
nodes_path = Path("/kaare/configs/nodes.yaml")
if nodes_path.exists():
    with open(nodes_path) as f:
        _nodes = (yaml.safe_load(f) or {}).get("nodes") or {}
_nodes = direct_node.with_direct(_nodes)  # P138 4b: this machine is always a node

# Home Assistant credentials (for ha_media_player nodes)
_HA_URL: str = _svc.get("home_assistant", {}).get("url", "")
_HA_TOKEN: str = ""
_ha_token_path = Path("/kaare/configs/ha_token.env")
if _ha_token_path.exists():
    for _line in _ha_token_path.read_text().splitlines():
        if _line.startswith("HA_TOKEN="):
            _HA_TOKEN = _line.split("=", 1)[1].strip()
            break


def _media_base() -> str:
    """Base URL speakers on the LAN fetch our wav files from (GUI: voice.media_host, P127)."""
    return f"http://{media_host()}:{HTTP_PORT}/audio"


def _build_provider_registry() -> VoiceProviderRegistry:
    return VoiceProviderRegistry(
        bridge_host=media_host(),
        bridge_port=HTTP_PORT,
        ha_url=_HA_URL,
        ha_token=_HA_TOKEN,
    )


if in_container() and not configured_media_host():
    log.warning(
        "No LAN address for speakers set — speakers on the LAN cannot fetch audio from inside a "
        "container. Set it in Settings → LLM → Piper (LAN address for speakers)."
    )
_provider_registry = _build_provider_registry()


def _provider_for(node_cfg: dict):
    """The speech provider for a node. With an enabled `cast` channel and an address, KTSB's own Cast goes first and
    the type's provider (HA for a Nest) is the fallback (P139 D7); a TV without its own provider speaks through Cast
    alone. Otherwise by type, as before."""
    provider = _provider_registry.get(node_cfg.get("type", "esp32"))
    cast = _provider_registry.get("chromecast")
    if "cast" in channels.enabled_channels(node_cfg) and str(node_cfg.get("host") or "").strip():
        if provider is None or provider is cast:
            return cast
        return CastFirstProvider(cast, provider)
    return provider


def _room_for_node(node_id: str) -> str:
    """The node's place id from the place register (P137), else its free-text room from nodes.yaml."""
    try:
        place, _note = rooms.node_place(node_id)
        if place:
            return place
    except Exception:
        pass
    return _nodes.get(node_id, {}).get("room", "")


def _default_user_for_node(node_id: str) -> str:
    return _nodes.get(node_id, {}).get("default_user", "") or ""


def _single_default_user(node_id: str) -> str | None:
    """Return default_user only if it's a single username (not comma-separated, not empty)."""
    raw = _nodes.get(node_id, {}).get("default_user", "") or ""
    val = raw.strip()
    if not val or "," in val:
        return None
    return val


def _unidentified_identity(node_id: str) -> str:
    """Who an unidentified speaker is at this node (P120): an indoor node → global_user (most
    likely a household member), an outdoor node (`trust: outdoor`) or a node that is not in
    nodes.yaml → unknown_user (untrusted). Default trust is indoor."""
    node = _nodes.get(node_id)
    if node is None:
        return "unknown_user"
    return (
        "unknown_user" if str(node.get("trust", "indoor")).lower() == "outdoor" else "global_user"
    )


def _node_for_host(host: str) -> str | None:
    """nodes.yaml node whose `host` is this IP (Wyoming satellites are known only by peer IP)."""
    for node_id, node in _nodes.items():
        if str(node.get("host", "")).strip() == host:
            return node_id
    return None


# ── Reply routing (P138) ──────────────────────────────────────────────────────
async def _ha_player_state(entity_id: str) -> tuple[str, float | None]:
    if not _HA_URL or not _HA_TOKEN:
        return ("", None)
    async with httpx.AsyncClient(timeout=3.0) as client:
        resp = await client.get(
            f"{_HA_URL}/api/states/{entity_id}", headers={"Authorization": f"Bearer {_HA_TOKEN}"}
        )
    if resp.status_code != 200:
        return ("", None)
    data = resp.json()
    vol = (data.get("attributes") or {}).get("volume_level")
    return (str(data.get("state") or ""), float(vol) if vol is not None else None)


async def _with_lead_in(wav_path: Path, node_id: str, node_cfg: dict) -> Path:
    """N30: a copy of the reply with silence in front for speakers that clip the start; the original otherwise."""
    try:
        async def _cast_status(host: str) -> dict:
            return await cast_sender.run(cast_sender.status, host, timeout=4)

        async def _ha_state(entity: str) -> str:
            return (await _ha_player_state(entity))[0]

        # P139 D7: own Cast first (also warms the connection the reply then uses), HA as the fallback
        was_playing, source = await lead_in.playing_source(node_cfg, _cast_status, _ha_state)
        ms = lead_in.lead_in_ms(node_cfg, was_playing)
        if ms <= 0:
            return wav_path
        dst = TMP_AUDIO_DIR / f"{wav_path.stem}_{_re.sub(r'[^A-Za-z0-9_-]', '_', node_id)}_lead{ms}.wav"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lead_in.pad_wav, wav_path, dst, ms)
        loop.call_later(90, _delete_wav, dst)
        log.info("Lead-in %d ms on %s (was_playing=%s source=%s)", ms, node_id, was_playing, source)
        return dst
    except Exception as exc:
        log.warning("Lead-in skipped on %s: %s", node_id, exc)
        return wav_path


async def _ha_set_volume(entity_id: str, volume: float) -> None:
    async with httpx.AsyncClient(timeout=3.0) as client:
        resp = await client.post(
            f"{_HA_URL}/api/services/media_player/volume_set",
            headers={"Authorization": f"Bearer {_HA_TOKEN}"},
            json={"entity_id": entity_id, "volume_level": round(max(0.0, min(1.0, volume)), 2)},
        )
        resp.raise_for_status()


async def _in_thread(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


_DUCK_IO = DuckIO(
    ha_state=_ha_player_state,
    ha_set_volume=_ha_set_volume,
    mpd_state=lambda: _in_thread(_mpd_get_state),
    mpd_pause=lambda: _in_thread(_mpd_pause),
    mpd_resume=lambda: _in_thread(_mpd_resume),
)
# Wyoming satellites: one duck session per satellite IP, from AudioStart until the reply cycle ends.
_wyoming_ducks: dict[str, DuckSession] = {}
# The running Wyoming server (None when disabled) — silence setting is reloaded into it from the GUI.
_wyoming_server: WyomingServer | None = None
# Last route-log id per satellite IP, so the reply's routing lands under the same rid as its question.
_wyoming_rids: dict[str, str] = {}


def _new_duck() -> DuckSession | None:
    """A duck session when «quiet the output while listening» is on (voice_settings duck.on_listen)."""
    if not _duck_cfg.get("on_listen", True):
        return None
    # I64 seam: a system choice (no user wish) — unchanged today
    level = voice_volume.target_volume("", {}, float(_duck_cfg.get("volume", 0.2)), voice_volume.PURPOSE_DUCK)
    return DuckSession(io=_DUCK_IO, volume=level)


def _playback_mode() -> str:
    return str(cfg.get("bridge", {}).get("playback_mode", "local"))


def _place_of(node_id: str) -> str:
    try:
        return rooms.node_place(node_id)[0]
    except Exception:
        return ""


async def _play_local(wav_path: Path, delete: bool = True) -> None:
    """Play on this machine's speakers: duck MPD, aplay, resume MPD (try/finally)."""
    loop = asyncio.get_running_loop()
    mpd_was_playing = False
    if _duck_cfg.get("mpd", True):
        state = await loop.run_in_executor(None, _mpd_get_state)
        if state == "playing":
            await loop.run_in_executor(None, _mpd_pause)
            mpd_was_playing = True
            log.info("MPD paused for voice pipeline TTS")
            await asyncio.sleep(0.2)
    try:
        await loop.run_in_executor(None, play_wav, wav_path)
    finally:
        if delete:
            _delete_wav(wav_path)
        if mpd_was_playing:
            await loop.run_in_executor(None, _mpd_resume)
            log.info("MPD resumed after voice pipeline TTS")


async def _play_on_node(wav_path: Path, node_id: str) -> bool:
    """One node through its provider: reachable first (a powered-off Nest Hub is `unavailable`), then delivered."""
    node_cfg = _nodes.get(node_id) or {}
    if direct_node.is_direct(node_cfg):  # KTSB Direct = this machine's speakers
        try:
            await _play_local(wav_path, delete=False)
            return True
        except Exception as exc:
            log.error("Local playback failed: %s", exc)
            return False
    provider = _provider_for(node_cfg)
    if provider is None:
        return False
    try:
        if not await provider.is_available(node_cfg):
            log.warning("Reply target %s is not reachable", node_id)
            return False
        return bool(await provider.speak(await _with_lead_in(wav_path, node_id, node_cfg), node_cfg))
    except Exception as exc:
        log.error("Reply target %s failed: %s", node_id, exc)
        return False


async def _speak_with_lead_in(provider, wav_path: Path, node_id: str, node_cfg: dict):
    """/speak: one remote target through its provider, with the N30 lead-in."""
    return await provider.speak(await _with_lead_in(wav_path, node_id, node_cfg), node_cfg)


async def _play_on_nodes(wav_path: Path, node_ids: list[str]) -> tuple[list[str], list[str]]:
    """Play on several nodes at once → (delivered, failed)."""
    results = await asyncio.gather(*(_play_on_node(wav_path, n) for n in node_ids))
    ok = [n for n, r in zip(node_ids, results, strict=True) if r]
    return ok, [n for n, r in zip(node_ids, results, strict=True) if not r]


def _unreachable_text(failed: list[str]) -> str:
    lang = get_lang("global")
    names = [routing.label(n, _nodes) for n in failed]
    joined = (
        names[0]
        if len(names) == 1
        else f"{', '.join(names[:-1])} {t('voice_list_and', lang)} {names[-1]}"
    )
    return t("voice_output_unreachable", lang, nodes=joined)


async def _say_unreachable(failed: list[str], where: str | None) -> None:
    """Kåre says the output node did not answer (2026-10-17) — on the local speakers here; a Wyoming
    satellite gets it through its WyomingReply instead (see _wyoming_route)."""
    text = _unreachable_text(failed)
    log.warning("Reply not delivered to %s — telling the user (%s)", failed, where)
    if where != routing.LOCAL:
        return
    loop = asyncio.get_running_loop()
    try:
        wav = await loop.run_in_executor(None, generate_speech_wav, text, get_speech_lang())
        await _play_local(wav)
    except Exception as exc:
        log.error("Could not say the unreachable notice: %s", exc)


# ── Access (P120 Phase 3) ─────────────────────────────────────────────────────
# SERVICE: only KTSB's own services (service token) — speakers, voice profiles, reloads.
# USER: a logged-in GUI user (browser mic / TTS) — the token is checked with the main API.
# NODE: a node may start only its own microphone (request from its `host` in nodes.yaml),
#       or a KTSB service may. /audio/{file} and /health stay open (random file names).
_USER_TOKEN_TTL = 60.0
_user_token_cache: dict[str, float] = {}


def _require_service(request: Request) -> None:
    if not is_service_request(request.headers):
        raise HTTPException(status_code=401, detail="Service token required.")


async def _require_user(request: Request) -> None:
    if is_service_request(request.headers):
        return
    auth = request.headers.get("authorization") or ""
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Login required.")
    now = time.monotonic()
    if _user_token_cache.get(auth, 0) > now:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{_KAARE_API_BASE}/api/auth/me", headers={"Authorization": auth})
    except Exception as exc:
        log.warning("Login check against the API failed: %s", exc)
        raise HTTPException(status_code=503, detail="Login check unavailable.") from exc
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Login required.")
    if len(_user_token_cache) > 500:
        _user_token_cache.clear()
    _user_token_cache[auth] = now + _USER_TOKEN_TTL


def _node_or_service(node_id: str, request: Request) -> None:
    if is_service_request(request.headers):
        return
    host = str(_nodes.get(node_id, {}).get("host", "")).strip()
    client = request.client.host if request.client else ""
    if not host or client != host:
        log.warning(
            "Mic trigger for node %s refused from %s (not the node's own host)", node_id, client
        )
        raise HTTPException(
            status_code=403, detail="Only the node itself may start its microphone."
        )


def _node_token_or_service(node_id: str, request: Request) -> None:
    """The node's own webhook token (P138, `X-Kare-Token` or `?token=`), or a KTSB service token. A node without a
    token has no webhook."""
    if is_service_request(request.headers):
        return
    want = str((_nodes.get(node_id) or {}).get("webhook_token") or "")
    got = request.headers.get("x-kare-token") or request.query_params.get("token") or ""
    if not want or not hmac.compare_digest(want.encode(), got.encode()):
        client = request.client.host if request.client else ""
        log.warning(
            "Webhook trigger for node %s refused from %s (wrong or no token)", node_id, client
        )
        raise HTTPException(status_code=403, detail="Wrong or missing node token.")


@app.post("/wakeword/{node_id}", status_code=202)
async def wakeword_trigger(node_id: str, background_tasks: BackgroundTasks, http: Request):
    _node_or_service(node_id, http)
    """Trigger fra wakeword-deteksjon på en node."""
    log.info("Wakeword-trigger fra node: %s", node_id)
    background_tasks.add_task(
        trigger_pipeline, node_id, _room_for_node(node_id), _default_user_for_node(node_id)
    )
    return {"status": "ok", "node": node_id, "trigger": "wakeword"}


@app.post("/button/{node_id}", status_code=202)
async def button_trigger(node_id: str, background_tasks: BackgroundTasks, http: Request):
    _node_or_service(node_id, http)
    """Trigger fra knapptrykk på en node."""
    log.info("Knapp-trigger fra node: %s", node_id)
    background_tasks.add_task(
        trigger_pipeline, node_id, _room_for_node(node_id), _default_user_for_node(node_id)
    )
    return {"status": "ok", "node": node_id, "trigger": "button"}


@app.post("/trigger/{node_id}", status_code=202)
async def webhook_trigger(node_id: str, background_tasks: BackgroundTasks, http: Request):
    """Start a node's microphone from anything that can send an HTTP POST — e.g. a Home Assistant automation with
    `rest_command` (P138 webhook fallback, works with any HA token and any button). Needs the node's own
    `webhook_token` in the `X-Kare-Token` header (or `?token=`); a service token also works."""
    _node_token_or_service(node_id, http)
    log.info("Webhook-trigger for node: %s", node_id)
    background_tasks.add_task(
        trigger_pipeline, node_id, _room_for_node(node_id), _default_user_for_node(node_id)
    )
    return {"status": "ok", "node": node_id, "trigger": "webhook"}


@app.api_route("/audio/{filename}", methods=["GET", "HEAD"])  # DLNA renderers check with HEAD first
async def serve_audio(filename: str):
    """Serve en midlertidig TTS WAV-fil til ESP32."""
    wav_path = TMP_AUDIO_DIR / filename
    if not wav_path.exists() or not wav_path.parent == TMP_AUDIO_DIR:
        raise HTTPException(status_code=404, detail="Fil ikke funnet")
    return FileResponse(str(wav_path), media_type="audio/wav")


# ── P139 phase 3: KTSB's own Cast / DLNA output (pictures, web pages, NRK) ─────────────────────────────────────

MEDIA_DIR = TMP_AUDIO_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)
_MEDIA_KEEP = 30  # pictures kept for screens to fetch (a receiver fetches once; a re-show needs a new upload)
_MEDIA_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


class DisplayShowRequest(BaseModel):
    channel: str  # "cast" | "dlna"
    host: str
    image_b64: str
    mime: str = "image/png"
    title: str = "Kåre"
    duration: int = 0  # seconds on screen (Cast); 0 = until something else replaces it


class CastUrlRequest(BaseModel):
    host: str
    url: str
    force: bool = False


class CastYoutubeRequest(BaseModel):
    host: str
    video_id: str


class CastNrkRequest(BaseModel):
    host: str
    kind: str  # "tv" | "radio"
    media_id: str


class CastHostRequest(BaseModel):
    host: str


def _configured_host(host: str) -> None:
    """Only addresses the user set up as nodes in the GUI may be driven (rule 27) — never one the model invents."""
    host = (host or "").strip()
    if not host or not any(
        isinstance(c, dict) and str(c.get("host") or "").strip() == host for c in _nodes.values()
    ):
        raise HTTPException(status_code=403, detail="host is not a configured node")


def _store_media(image_b64: str, mime: str) -> str:
    ext = _MEDIA_TYPES.get(mime)
    if not ext:
        raise HTTPException(status_code=400, detail=f"unsupported picture type {mime!r}")
    try:
        data = base64.b64decode(image_b64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="image_b64 is not base64") from exc
    name = f"{uuid.uuid4().hex}{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    old = sorted(MEDIA_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
    for p in old[:-_MEDIA_KEEP]:
        p.unlink(missing_ok=True)
    return name


@app.api_route("/media/{filename}", methods=["GET", "HEAD"])  # DLNA renderers check with HEAD first
async def serve_media(filename: str):
    """Pictures for Cast/DLNA screens on the LAN (random names, kept briefly). Fixes «no /media route» (BUG-log
    2026-10-18 DLNA picture path)."""
    path = MEDIA_DIR / filename
    if not path.exists() or path.parent != MEDIA_DIR:
        raise HTTPException(status_code=404, detail="not found")
    mime = next((m for m, e in _MEDIA_TYPES.items() if e == path.suffix), "application/octet-stream")
    return FileResponse(str(path), media_type=mime)


_picture_timers: dict[str, asyncio.Task] = {}


async def _clear_picture_later(host: str, url: str, seconds: int) -> None:
    """Close a Cast picture after its time, if it is still that picture (P139: pictures hung on the TV)."""
    try:
        await asyncio.sleep(min(max(seconds, 1), 3600))
        res = await cast_sender.run(cast_sender.clear_picture, host, url)
        log.info("Picture on %s after %ss: %s", host, seconds, res.get("skipped") or ("closed" if res.get("ok")
                                                                                     else res.get("error")))
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.warning("Picture timer on %s failed: %s", host, e)


@app.post("/display/show")
async def display_show(req: DisplayShowRequest, _s: None = Depends(_require_service)):
    """A full-screen picture on a Cast or DLNA screen. The reply says what the device confirmed (N26)."""
    _configured_host(req.host)
    name = await asyncio.to_thread(_store_media, req.image_b64, req.mime)
    url = f"http://{media_host()}:{HTTP_PORT}/media/{name}"
    if req.channel == "cast":
        res = await cast_sender.run(cast_sender.show_picture, req.host, url, req.mime, req.title)
        old = _picture_timers.pop(req.host, None)
        if old is not None:
            old.cancel()  # a new picture replaces the old one's timer
        if res.get("ok") and req.duration > 0:
            _picture_timers[req.host] = asyncio.create_task(_clear_picture_later(req.host, url, req.duration))
        return res
    if req.channel == "dlna":
        # DT04 R0.1: the description URL set on the node (no SSDP needed — Docker Desktop gets none)
        desc = next((dlna_description_url_of(c) for c in _nodes.values() if isinstance(c, dict)
                     and str(c.get("host") or "").strip() == req.host.strip() and dlna_description_url_of(c)), "")
        ok, err = await dlna_play_uri(req.host, url, req.title, description_url=desc)
        return {"ok": ok, "error": err, "before": {}, "after": {}}
    raise HTTPException(status_code=400, detail="channel must be cast or dlna")


@app.post("/cast/url")
async def cast_url(req: CastUrlRequest, _s: None = Depends(_require_service)):
    """A web page full-screen on a Cast screen (DashCast)."""
    _configured_host(req.host)
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    return await cast_sender.run(cast_sender.show_url, req.host, req.url, req.force)


@app.post("/cast/youtube")
async def cast_youtube(req: CastYoutubeRequest, _s: None = Depends(_require_service)):
    """A YouTube video (11-character id) on a Cast device with a screen (P139)."""
    _configured_host(req.host)
    if not _re.fullmatch(cast_sender.YOUTUBE_ID, req.video_id or ""):
        raise HTTPException(status_code=400, detail="video_id must be an 11-character YouTube id")
    return await cast_sender.run(cast_sender.play_youtube, req.host, req.video_id)


@app.post("/cast/nrk")
async def cast_nrk(req: CastNrkRequest, _s: None = Depends(_require_service)):
    """NRK TV / NRK Radio on a Cast device: live channel slug or programme id."""
    _configured_host(req.host)
    if req.kind not in cast_sender.NRK_APPS or not _re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", req.media_id or ""):
        raise HTTPException(status_code=400, detail="kind must be tv or radio, media_id a channel slug or programme id")
    return await cast_sender.run(cast_sender.play_nrk, req.host, req.kind, req.media_id)


class CastPlexRequest(BaseModel):
    host: str
    session: dict  # plex_adapter.cast_session — server address, delegation token, machine id, play queue


@app.post("/cast/plex")
async def cast_plex(req: CastPlexRequest, _s: None = Depends(_require_service)):
    """Plex on a Cast device through the Plex Cast app (P139 phase 4). The token in `session` is never logged."""
    _configured_host(req.host)
    need = {"protocol", "address", "port", "token", "machine_id", "play_queue_id", "key"}
    if not need <= set(req.session):
        raise HTTPException(status_code=400, detail=f"session lacks {sorted(need - set(req.session))}")
    return await cast_sender.run(cast_sender.play_plex, req.host, req.session, timeout=60)


class TvActRequest(BaseModel):
    node: str
    action: str
    value: str = ""
    origin: str = tv_control.ORIGIN_REQUESTED


class TvPairRequest(BaseModel):
    node: str
    code: str = ""


def _tv_node(node_id: str) -> dict:
    cfg = _nodes.get(node_id)
    if not isinstance(cfg, dict) or not tv_control.remote_channel(cfg):
        raise HTTPException(status_code=404, detail="no such node with a remote-control channel")
    return cfg


@app.post("/tv/act")
async def tv_act(req: TvActRequest, _s: None = Depends(_require_service)):
    """One TV remote action (P139 phase 5, D4) — before/after truth, origin logged."""
    return await tv_control.act(req.node, _tv_node(req.node), req.action, req.value, req.origin)


@app.post("/tv/pair/start")
async def tv_pair_start(req: TvPairRequest, _s: None = Depends(_require_service)):
    """Android TV: shows a code on the screen. Samsung: shows «Allow?» and saves the token when accepted."""
    return await tv_control.pair_start(_tv_node(req.node))


@app.post("/tv/pair/finish")
async def tv_pair_finish(req: TvPairRequest, _s: None = Depends(_require_service)):
    return await tv_control.pair_finish(_tv_node(req.node), req.code)


@app.post("/tv/pair/status")
async def tv_pair_status(req: TvPairRequest, _s: None = Depends(_require_service)):
    """Paired or not — the secrets stay in the bridge; the API only asks."""
    return tv_control.pairing_status(_tv_node(req.node))


@app.post("/cast/stop")
async def cast_stop(req: CastHostRequest, _s: None = Depends(_require_service)):
    _configured_host(req.host)
    return await cast_sender.run(cast_sender.stop, req.host)


@app.post("/cast/pause")
async def cast_pause(req: CastHostRequest, _s: None = Depends(_require_service)):
    """P139 reopened #6: pause on a Cast device without HA; confirmed by the device's PAUSED."""
    _configured_host(req.host)
    return await cast_sender.run(cast_sender.pause, req.host)


@app.post("/cast/play")
async def cast_play(req: CastHostRequest, _s: None = Depends(_require_service)):
    """P139 reopened #6: resume on a Cast device without HA; confirmed by the device's PLAYING."""
    _configured_host(req.host)
    return await cast_sender.run(cast_sender.play, req.host)


@app.post("/cast/status")
async def cast_status(req: CastHostRequest, _s: None = Depends(_require_service)):
    """Is the Cast device on, in standby, on its input — and what runs on it."""
    _configured_host(req.host)
    return await cast_sender.run(cast_sender.status, req.host, timeout=15)


# ---------------------------------------------------------------------------
# P151: KTSB's screen page — live camera and cards on any screen
# ---------------------------------------------------------------------------
# Page-side routes (the page, its poll, its report, the stream and the card) are open on purpose: a TV cannot log in.
# They are gated by keys instead — a one-time session key (Cast) or a paired device key (D9) — and answer «off»
# when the GUI switch is off (D11). Everything that starts, stops or pairs needs the service token.

_SCREEN_PAGE = Path(__file__).with_name("screen_page.html")
_CARD_DIR = screen_core.STATE_DIR / "cards"
_CARD_KEEP = 20


def _screen_node(node: str) -> dict:
    cfg = _nodes.get(node)
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=404, detail="not a configured screen")
    return cfg


def _screen_off_reply() -> dict:
    return {"off": True, "paired": False, "text": t("screen_off_text", get_lang())}


@app.get("/screen/{node}")
async def screen_page(node: str):
    """The page a screen shows (an empty shell — what it shows comes from /state, by key)."""
    if not screen_core.enabled():
        raise HTTPException(status_code=404, detail="screens are switched off")
    _screen_node(node)
    return HTMLResponse(_SCREEN_PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


@app.get("/screen/{node}/state")
async def screen_state(node: str, key: str = "", device: str = ""):
    _screen_node(node)
    if not screen_core.enabled():
        return _screen_off_reply()
    state = screen_core.hub.state_for(node, key, device)
    if state.get("pair_code"):
        state["text"] = t("screen_pair_text", get_lang(), node=node)
    content = state.get("content")
    if content and content.get("mode") == "fmp4":
        w, h = (int(x) for x in content["size"].split("x"))
        content["codec"] = screen_io.h264_codec(w, h)[1]
    return state


class ScreenReport(BaseModel):
    token: str
    rev: int = 0
    t: float = 0.0
    buf: float = 0.0
    frames: float = 0.0
    dropped: float = 0.0
    w: int = 0
    h: int = 0
    event: str = ""


async def _reencode_after_stutter(node: str, token: str, camera: str) -> None:
    """D7: a direct stream that stutters on this screen is re-encoded here (the page restarts by itself)."""
    try:
        mw, mh = screen_core.max_size(_nodes.get(node))
        choice = await screen_io.pick(camera, mw, mh, force_reencode=True)
        if choice and screen_core.hub.switch_to_reencode(token, choice):
            log.info("Screen %s: %s stuttered direct — re-encoding %s at %dx%d", node, camera, choice.stream,
                     choice.width, choice.height)
    except Exception as e:
        log.warning("Screen %s: re-encode after stutter failed: %s", node, e)


@app.post("/screen/{node}/report")
async def screen_report(node: str, rep: ScreenReport):
    if not screen_core.enabled():
        return {"ok": False}
    s = screen_core.hub.session(rep.token)
    if not s or s.node != node:
        return {"ok": False}
    prev = dict(s.last_report)
    screen_core.hub.report(rep.token, rep.model_dump())
    if (s.kind == "camera" and s.choice and s.choice.mode == "mse" and not s.switched
            and rep.rev == s.rev and screen_core.stutters(prev, rep.model_dump())):
        spawn(_reencode_after_stutter(node, s.token, s.camera))
    return {"ok": True}


@app.websocket("/screen/ws/{token}")
async def screen_ws(websocket: WebSocket, token: str):
    """MSE straight from Frigate's go2rtc, through the bridge (D6) — only for a live session of mode «mse»."""
    s = screen_core.hub.session(token) if screen_core.enabled() else None
    if not s or not s.choice or s.choice.mode != "mse":
        await websocket.close(code=4403)
        return
    await websocket.accept()

    def alive() -> bool:
        cur = screen_core.hub.session(token)
        return bool(cur and cur.choice and cur.choice.mode == "mse" and screen_core.enabled())

    try:
        await screen_io.proxy_mse(websocket, s.choice.stream, alive)
    except Exception as e:
        log.info("Screen %s: MSE proxy for %s ended: %s", s.node, s.choice.stream, type(e).__name__)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/screen/mp4/{token}")
async def screen_mp4(token: str):
    """The re-encoded stream (fMP4) for a live session of mode «fmp4»."""
    s = screen_core.hub.session(token) if screen_core.enabled() else None
    if not s or not s.choice or s.choice.mode != "fmp4":
        raise HTTPException(status_code=404, detail="not found")
    choice = s.choice

    def alive() -> bool:
        cur = screen_core.hub.session(token)
        return bool(cur and cur.choice is choice and screen_core.enabled())

    return StreamingResponse(screen_io.fmp4(choice, alive), media_type="video/mp4",
                             headers={"Cache-Control": "no-cache"})


@app.get("/screen/card/{token}")
async def screen_card(token: str):
    s = screen_core.hub.session(token) if screen_core.enabled() else None
    path = _CARD_DIR / (s.card_file if s and s.kind == "card" else "-")
    if not s or not path.is_file() or path.parent != _CARD_DIR:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(path), media_type="text/html", headers={"Cache-Control": "no-cache"})


class ScreenCameraRequest(BaseModel):
    node: str
    camera: str
    minutes: float | None = None


def _screen_page_url(node: str, token: str = "") -> str:
    return f"http://{media_host()}:{HTTP_PORT}/screen/{node}" + (f"?s={token}" if token else "")


SCREEN_SHOW_WAIT_S = 25.0  # DashCast start on a slow TV took up to ~9 s, the page then needs a keyframe (T34)


async def _open_screen(node: str, cfg: dict, s) -> dict:
    """Open the page for this session on the device. Cast: note what the screen showed, then DashCast the page (http →
    fresh DashCast + force). Phase 3 adds browser screens."""
    host = str(cfg.get("host") or "").strip()
    chans = channels.enabled_channels(cfg)
    if "cast" in chans and host:
        st = await cast_sender.run(cast_sender.status, host, timeout=15)
        if not st.get("ok"):
            return {"via": "cast", "ok": False, "error": st.get("error") or "screen does not answer"}
        if not s.restore:  # a screen KTSB already shows keeps the «before» from the first session
            s.restore = {"via": "cast", "host": host, "before": st.get("after") or {}}
        res = await cast_sender.run(cast_sender.show_url, host, _screen_page_url(node, s.token), True, timeout=45)
        return {"via": "cast", "ok": bool(res.get("ok")), "error": res.get("error") or ""}
    return await _open_browser_screen(node, cfg, s, host, chans)


async def _open_browser_screen(node: str, cfg: dict, s, host: str, chans: dict) -> dict:
    """P151 phase 3 — a screen without Cast that shows KTSB's page in its own browser (D3/D9). It must be paired once
    (code on the TV). A Samsung is brought back by KTSB: on if it was off, the browser to the front by REST (it keeps
    our page). Any other paired browser (a wall tablet, a TV KTSB has no driver for) works while the page is in front."""
    samsung = "samsung_ws" in chans and bool(host)
    power: str | None = None
    visible = False
    if samsung and screen_core.hub.is_paired(node):
        power = await asyncio.to_thread(tv_control._samsung_power, host)
        if power == "on":
            visible = bool(await asyncio.to_thread(tv_control.samsung_app_visible, host,
                                                   tv_control.SAMSUNG_APPS["browser"]))
    steps, error = screen_core.browser_open_steps(screen_core.hub.is_paired(node), samsung, power, visible,
                                                  screen_core.hub.seen_recently(node))
    if error:
        return {"via": "browser", "ok": False, "error": error, "setup_url": _screen_page_url(node)}
    if samsung and not s.restore:
        s.restore = {"via": "samsung", "host": host, "was_off": power != "on", "browser_was_front": visible}
    for step in steps:
        res = await (tv_control.act(node, cfg, "power_on") if step == "power_on"
                     else tv_control.act(node, cfg, "app", "browser"))
        if res.get("outcome") not in tv_control.DONE:
            return {"via": "browser", "ok": False,
                    "error": f"{step} failed: {res.get('error') or res.get('outcome')}"}
    return {"via": "browser", "ok": True, "error": ""}


async def _wait_showing(token: str, limit: float = SCREEN_SHOW_WAIT_S) -> bool:
    """N26: «showing» only when the page itself reports frames (or a loaded card) for this content."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        s = screen_core.hub.session(token)
        if not s:
            return False
        if s.playing_since:
            return True
        await asyncio.sleep(0.5)
    return False


async def _restore_screen(s) -> dict:
    """Put the screen back the way it was (P151 D12) — on stop and when the time is up."""
    info = s.restore or {}
    cfg_s = _nodes.get(s.node) or {}
    if info.get("via") == "samsung":
        step = screen_core.browser_restore_step(bool(info.get("was_off")), bool(info.get("browser_was_front")))
        if step == "power_off":
            res = await tv_control.act(s.node, cfg_s, "power_off")
        elif step == "exit":
            res = await tv_control.act(s.node, cfg_s, "key", "exit")
        else:
            res = {"outcome": "already", "skipped": "the browser was in front before"}
        log.info("Screen %s restored (Samsung): %s [%s]", s.node, step or "nothing", res.get("outcome"))
        return {"ok": res.get("outcome") in tv_control.DONE, **res}
    if info.get("via") != "cast":
        return {"ok": True, "skipped": "nothing to restore"}
    res = await cast_sender.run(cast_sender.restore, info["host"], info.get("before") or {}, timeout=40)
    cfg = _nodes.get(s.node) or {}
    if res.get("needs_standby") and tv_control.remote_channel(cfg):
        res["standby"] = await tv_control.act(s.node, cfg, "power_off")
    log.info("Screen %s restored: %s", s.node, res.get("skipped") or res.get("relaunched")
             or ("standby" if res.get("standby") else "closed"))
    return res


async def _show_and_confirm(node: str, cfg: dict, s) -> dict:
    opened = await _open_screen(node, cfg, s)
    showing = await _wait_showing(s.token) if opened.get("ok") else False
    if not showing and opened.get("ok"):
        opened["error"] = "the page did not report a picture in time"
    return {**opened, "showing": showing}


@app.post("/screen/camera")
async def screen_camera(req: ScreenCameraRequest, _s: None = Depends(_require_service)):
    """Start a live camera on a screen node. Phase 1: makes the session and its page address; later phases open the
    page on the device and wait for the page to report frames (N26)."""
    if not screen_core.enabled():
        return {"ok": False, "off": True, "error": "screens are switched off"}
    cfg = _screen_node(req.node)
    mw, mh = screen_core.max_size(cfg)
    try:
        choice = await screen_io.pick(req.camera, mw, mh)
    except Exception as e:
        return {"ok": False, "error": f"Frigate: {e}"}
    if not choice:
        return {"ok": False, "error": f"no playable stream for camera {req.camera!r}"}
    minutes = screen_core.live_minutes(req.minutes)
    old = screen_core.hub.current(req.node)
    s = screen_core.hub.start(req.node, "camera", minutes=minutes, camera=req.camera, choice=choice)
    _ensure_screen_loop()
    if old and old.restore:
        s.restore = old.restore  # a new camera on a screen KTSB already took over: keep the original «before»
    _audit_screen("camera_start", req.node, camera=req.camera, stream=choice.stream, mode=choice.mode,
                  minutes=minutes)
    shown = await _show_and_confirm(req.node, cfg, s)
    if not shown.get("ok"):
        screen_core.hub.stop(req.node)
    # DT04-S05q: what the screen showed before KTSB took it, so Kåre says it instead of guessing «as it was»
    before = cast_sender.before_summary((s.restore or {}).get("before")) if (s.restore or {}).get("via") == "cast" \
        else {"kind": "unknown", "app": None}
    return {"ok": bool(shown.get("ok")), "showing": shown.get("showing", False), "via": shown.get("via"),
            "error": shown.get("error") or "", "session": s.public(), "minutes": minutes, "before": before}


class ScreenCardRequest(BaseModel):
    node: str
    html: str
    title: str = ""


def _store_card(html: str) -> str:
    _CARD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.html"
    (_CARD_DIR / name).write_text(html, encoding="utf-8")
    old = sorted(_CARD_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime)
    for p in old[:-_CARD_KEEP]:
        p.unlink(missing_ok=True)
    return name


@app.post("/screen/card")
async def screen_card_start(req: ScreenCardRequest, _s: None = Depends(_require_service)):
    if not screen_core.enabled():
        return {"ok": False, "off": True, "error": "screens are switched off"}
    cfg = _screen_node(req.node)
    name = await asyncio.to_thread(_store_card, req.html)
    old = screen_core.hub.current(req.node)
    s = screen_core.hub.start(req.node, "card", card_file=name, title=req.title)
    _ensure_screen_loop()
    if old and old.restore:
        s.restore = old.restore
    _audit_screen("card_start", req.node, title=req.title)
    shown = await _show_and_confirm(req.node, cfg, s)
    if not shown.get("ok"):
        screen_core.hub.stop(req.node)
    return {"ok": bool(shown.get("ok")), "showing": shown.get("showing", False), "via": shown.get("via"),
            "error": shown.get("error") or "", "session": s.public()}


class ScreenNodeRequest(BaseModel):
    node: str
    code: str = ""


@app.post("/screen/stop")
async def screen_stop(req: ScreenNodeRequest, _s: None = Depends(_require_service)):
    # Allowed while off on purpose: it only ends and restores — it never shows anything (switch-off mid-stream)
    s = screen_core.hub.stop(req.node)
    restored: dict = {}
    if s:
        _audit_screen("stop", req.node, kind=s.kind)
        restored = await _restore_screen(s)
    return {"ok": True, "stopped": bool(s), "restored": restored}


@app.post("/screen/pair")
async def screen_pair(req: ScreenNodeRequest, _s: None = Depends(_require_service)):
    if not screen_core.enabled():
        return {"ok": False, "off": True, "error": "screens are switched off"}
    _screen_node(req.node)
    ok = screen_core.hub.confirm_code(req.node, req.code)
    _audit_screen("pair", req.node, ok=ok)
    return {"ok": ok}


@app.post("/screen/unpair")
async def screen_unpair(req: ScreenNodeRequest, _s: None = Depends(_require_service)):
    if not screen_core.enabled():
        return {"ok": False, "off": True, "error": "screens are switched off"}
    had = screen_core.hub.unpair(req.node)
    _audit_screen("unpair", req.node, had=had)
    return {"ok": True, "was_paired": had}


@app.post("/screen/active")
async def screen_active(req: ScreenNodeRequest, _s: None = Depends(_require_service)):
    """Which screens show something now (Kåre's «stopp» without naming a screen)."""
    return {"sessions": [{"node": s.node, **s.public()} for s in screen_core.hub.active()]}


@app.post("/screen/status")
async def screen_status(req: ScreenNodeRequest, _s: None = Depends(_require_service)):
    s = screen_core.hub.current(req.node)
    return {"enabled": screen_core.enabled(), "paired": screen_core.hub.is_paired(req.node),
            "seen_recently": screen_core.hub.seen_recently(req.node), "page_url": _screen_page_url(req.node),
            "content": s.public() if s else None}


def _audit_screen(event: str, node: str, **fields) -> None:
    """Every start/stop/pair on a screen goes to logs/audit.log (a camera feed is the most sensitive stream)."""
    detail = " ".join([f"node={node}"] + [f"{k}={v}" for k, v in fields.items()])
    audit_log(f"screen_{event}", "kare", detail)


_screen_loop: asyncio.Task | None = None


def _ensure_screen_loop() -> None:
    """The expiry loop runs only while a session exists (started by the first one) — no background job while the
    feature is off or idle (D11)."""
    global _screen_loop
    if _screen_loop is None or _screen_loop.done():
        _screen_loop = spawn(_screen_expiry_loop(), name="screen-expiry")


async def _screen_expiry_loop() -> None:
    """Close sessions whose time is up (D4) and put the screen back (D12). Switched off in the GUI meanwhile → every
    running session ends the same way. Ends itself when no session is left."""
    while screen_core.hub.active():
        await asyncio.sleep(5)
        try:
            ended = screen_core.hub.expired()
            if not screen_core.enabled():
                ended += [s for s in (screen_core.hub.stop(x.node) for x in screen_core.hub.active()) if s]
            for s in ended:
                why = "page gone" if s.stale else ("time up" if screen_core.enabled() else "screens switched off")
                _audit_screen("ended", s.node, kind=s.kind, why=why.replace(" ", "_"))
                log.info("Screen %s: %s session ended (%s)", s.node, s.kind, why)
                if not s.stale:  # the screen moved on by itself — something else owns it, leave it alone
                    spawn(_restore_screen(s))
        except Exception as e:
            log.warning("screen expiry loop: %s", e)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Announce endpoint — called by Kåre's 'announce' tool
# ---------------------------------------------------------------------------


class SpeakRequest(BaseModel):
    text: str
    target: str = "local"  # "local", node_id, room name, or "all"
    volume: float | None = None  # 0.0–1.0, None = don't change
    lang: str = "nb"  # language for TTS model selection (nb/en/de)
    wait: bool = False  # true: answer after playback started/failed, with the outcome (DT04-S05f)


def _has_audio(node_cfg: dict) -> bool:
    return node_cfg.get("has_audio", True)


def _has_display(node_cfg: dict) -> bool:
    if "has_display" in node_cfg:
        return bool(node_cfg["has_display"])
    return bool(node_cfg.get("is_tv", False))  # backward-compat during migration


def _norm(s: str) -> str:
    """Normalize a room/node string: lowercase, spaces and underscores treated as equivalent."""
    return s.strip().lower().replace(" ", "_")


def _resolve_targets(target: str) -> tuple[list[str], str]:
    """(destinations, reason) — kaare_core/voice/speak_target.py. DT04-S05f: «tv-a» finds `tv_a`, and an
    unknown or disabled target is reported (reason), never silently played on the local speaker."""

    def _place(t: str) -> str | None:
        return rooms.get_register().resolve_place(t)

    def _place_of(nid: str) -> str | None:
        return rooms.node_place(nid)[0]

    return speak_target.resolve(target, _nodes, resolve_place=_place, place_of=_place_of)


_SPEAK_TAILS: set = set()  # background finish tasks of confirmed /speak calls (a reference keeps them alive)


async def _speak_background(
    text: str,
    target: str,
    volume: float | None = None,
    lang: str = "nb",
    *,
    targets: list[str] | None = None,
    confirm: bool = False,
) -> dict:
    """Generate TTS audio and play it on the requested target(s).

    Returns {"status": ok|partial|failed, "results": {target: played|playing|failed}}. With confirm=True it returns once
    every playback has started or failed (up to speak_target.CONFIRM_WINDOW_S) and finishes (unduck, cleanup) in the
    background — the caller's receipt comes after the outcome, not at submission (DT04-S05f)."""
    loop = asyncio.get_event_loop()
    if targets is None:
        targets, reason = _resolve_targets(target)
        if not targets:
            log.warning("Speak target %r is %s — nothing played", target, reason)
            return {"status": "failed", "reason": reason, "results": {}}
    # KTSB Direct is this machine's speakers: one «local», never a second local play for «all» or its room.
    targets = list(dict.fromkeys(routing.LOCAL if direct_node.is_direct(_nodes.get(n)) else n for n in targets))
    log.info("Announce: target=%r -> %s volume=%s", target, targets, volume)

    # Duck MPD before TTS (only needed when local speaker is a target)
    mpd_was_playing = False
    if "local" in targets and _duck_cfg.get("mpd", True):
        state = await loop.run_in_executor(None, _mpd_get_state)
        if state == "playing":
            await loop.run_in_executor(None, _mpd_pause)
            mpd_was_playing = True
            log.info("MPD paused for TTS announce")
            await asyncio.sleep(0.2)

    try:
        wav_path = await loop.run_in_executor(None, generate_speech_wav, text, lang)
    except Exception as exc:
        log.error("TTS generation failed: %s", exc)
        if mpd_was_playing:
            await loop.run_in_executor(None, _mpd_resume)
        return {"status": "failed", "reason": "tts", "results": dict.fromkeys(targets, speak_target.FAILED)}

    # Set volume before playback if requested (provider handles it if supported)
    if volume is not None:
        vol_tasks = []
        for tg in targets:
            if tg != "local":
                node_cfg = _nodes.get(tg, {})
                provider = _provider_for(node_cfg)
                level = voice_volume.target_volume(tg, node_cfg, volume, voice_volume.PURPOSE_ANNOUNCE)  # I64 seam
                if provider and level is not None:
                    vol_tasks.append(provider.set_volume(level, node_cfg))
        if vol_tasks:
            await asyncio.gather(*vol_tasks, return_exceptions=True)

    jobs: dict = {}
    no_provider: dict[str, str] = {}
    for tg in targets:
        if tg == "local":
            jobs[tg] = loop.run_in_executor(None, play_wav, wav_path)
        else:
            node_cfg = _nodes.get(tg, {})
            provider = _provider_for(node_cfg)
            if provider:
                jobs[tg] = _speak_with_lead_in(provider, wav_path, tg, node_cfg)
            else:
                log.warning("No provider for node type '%s', skipping node '%s'", node_cfg.get("type", "esp32"), tg)
                no_provider[tg] = speak_target.FAILED

    t_play_start = time.monotonic()
    outcome, pending = await speak_target.confirm_start(jobs)
    outcome.update(no_provider)
    result = {"status": speak_target.summary(outcome), "results": outcome}
    log.info("Announce outcome: target=%r %s", target, outcome)

    async def _finish() -> None:
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        # Unduck MPD — remote targets return immediately, so wait for WAV duration first
        if mpd_was_playing:
            if any(tg != "local" for tg in targets):
                wav_duration = _wav_duration_seconds(wav_path)
                elapsed = time.monotonic() - t_play_start
                extra_wait = max(0.0, wav_duration + 1.5 - elapsed)
                if extra_wait > 0:
                    await asyncio.sleep(extra_wait)
            await loop.run_in_executor(None, _mpd_resume)
            log.info("MPD resumed after TTS announce")
        # Give remote devices time to fetch the audio file before deleting it
        await asyncio.sleep(15)
        wav_path.unlink(missing_ok=True)
        log.debug("Deleted TTS file: %s", wav_path)

    if confirm:
        tail = asyncio.ensure_future(_finish())
        _SPEAK_TAILS.add(tail)
        tail.add_done_callback(_SPEAK_TAILS.discard)
    else:
        await _finish()
    return result


@app.post("/speak", status_code=202)
async def speak_endpoint(
    request: SpeakRequest,
    background_tasks: BackgroundTasks,
    response: Response,
    _s: None = Depends(_require_service),
):
    """Trigger speech output. An unknown/disabled target → 404 with the known targets (never a silent local play).
    wait=true → 200 once playback has started or failed, with the outcome per target; else 202 at once."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    targets, reason = _resolve_targets(request.target)
    if not targets:
        log.warning("Speak target %r is %s — not played", request.target, reason)
        raise HTTPException(
            status_code=404,
            detail={"error": f"{reason}_target", "target": request.target, "known": speak_target.known_targets(_nodes)},
        )
    log.info(
        "Speak request: target=%r lang=%s wait=%s text=%r...",
        request.target,
        request.lang,
        request.wait,
        request.text[:60],
    )
    if request.wait:
        res = await _speak_background(
            request.text, request.target, request.volume, request.lang, targets=targets, confirm=True
        )
        response.status_code = 200
        return {**res, "target": request.target, "targets": targets}
    background_tasks.add_task(
        _speak_background, request.text, request.target, request.volume, request.lang, targets=targets
    )
    return {"status": "ok", "target": request.target}


@app.post("/reload_tts_models", status_code=200)
async def reload_tts_models_endpoint(_s: None = Depends(_require_service)):
    """Hot-reload TTS model map from services.yaml without restarting the bridge."""
    _reload_tts_models()
    return {"ok": True, "models": {k: str(v) for k, v in _TTS_MODELS.items()}}


@app.get("/display_nodes")
async def get_display_nodes_endpoint(_s: None = Depends(_require_service)):
    """Return all enabled nodes that have has_display=true."""
    return [
        {"id": nid, **ncfg}
        for nid, ncfg in _nodes.items()
        if ncfg.get("enabled", True) and _has_display(ncfg)
    ]


@app.post("/reload_media_host", status_code=200)
async def reload_media_host_endpoint(_s: None = Depends(_require_service)):
    """Rebuild the speaker providers with the LAN address from services.yaml (GUI change, P127)."""
    global _provider_registry
    _provider_registry = _build_provider_registry()
    log.info("Media host reloaded: %s", _media_base())
    return {"ok": True, "media_base": _media_base()}


@app.post("/reload_voice_settings", status_code=200)
async def reload_voice_settings_endpoint(_s: None = Depends(_require_service)):
    """Re-read voice_settings.yaml (GUI: Nodes → Voice settings, P138): silence, max listen and ducking take effect
    for the next press without a restart, and so does the KTSB Direct mic (device/rate). Ports still need one."""
    global \
        cfg, \
        MAX_RECORD_SEC, \
        SILENCE_SEC, \
        SILENCE_AMP, \
        _duck_cfg, \
        MIC_DEVICE, \
        MIC_RATE, \
        END_OF_SPEECH
    try:
        new = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        stt = new.get("stt") or {}
        audio = new.get("audio") or {}
        MIC_DEVICE = audio.get("mic_device")
        MIC_RATE = audio.get("mic_sample_rate")
        MAX_RECORD_SEC = stt.get("max_record_seconds", MAX_RECORD_SEC)
        SILENCE_SEC = stt.get("silence_threshold_seconds", SILENCE_SEC)
        SILENCE_AMP = stt.get("silence_amplitude_threshold", SILENCE_AMP)
        END_OF_SPEECH = (
            stt.get("end_of_speech") if stt.get("end_of_speech") in END_MODES else "auto"
        )
        _duck_cfg = new.get("duck") or {}
        cfg = new
        if _wyoming_server is not None:
            _wyoming_server.set_silence_sec(float(SILENCE_SEC))
        log.info(
            "Voice settings reloaded: silence %.1fs/%.3f (%s), max %ss, duck %s, mic %r",
            SILENCE_SEC,
            SILENCE_AMP,
            END_OF_SPEECH,
            MAX_RECORD_SEC,
            _duck_cfg,
            MIC_DEVICE,
        )
        return {"ok": True}
    except Exception as e:
        log.error("Failed to reload voice settings: %s", e)
        return {"ok": False, "error": str(e)}


@app.get("/audio_devices")
async def audio_devices(rescan: bool = True, _s: None = Depends(_require_service)):
    """The microphones this machine has (GUI: Nodes → KTSB Direct, P138 4b). Re-scans when no recording is running,
    so a USB mic plugged in after start shows up; `rescan=false` (N29 online status, every 30 s) only lists. No
    PortAudio (Docker without sound) → an empty list + reason."""
    try:
        # PLC0415: optional dependency — sounddevice needs the PortAudio system library (absent in Docker)
        import sounddevice as sd  # noqa: PLC0415
    except Exception:
        return {"inputs": [], "error": "no_audio_backend", "current": MIC_DEVICE}
    loop = asyncio.get_running_loop()

    def _scan() -> list[dict]:
        if rescan and not _pipeline_lock.locked():
            try:
                sd._terminate()
                sd._initialize()
            except Exception as exc:
                log.debug("PortAudio re-scan failed: %s", exc)
        return _input_devices(sd)

    try:
        inputs = await loop.run_in_executor(None, _scan)
    except Exception as exc:
        log.warning("Listing audio devices failed: %s", exc)
        return {"inputs": [], "error": "scan_failed", "current": MIC_DEVICE}
    return {"inputs": inputs, "error": "" if inputs else "no_input", "current": MIC_DEVICE}


@app.post("/reload_nodes", status_code=200)
async def reload_nodes_endpoint(_s: None = Depends(_require_service)):
    """Hot-reload nodes.yaml without restarting the bridge."""
    global _nodes
    nodes_path = Path("/kaare/configs/nodes.yaml")
    try:
        _nodes = direct_node.with_direct(
            (yaml.safe_load(nodes_path.read_text()) or {}).get("nodes") or {}
        )
        log.info("Nodes reloaded: %d nodes", len(_nodes))
        return {"ok": True, "count": len(_nodes)}
    except Exception as e:
        log.error("Failed to reload nodes: %s", e)
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Browser voice endpoints — STT (transcribe) and TTS file for frontend mic button
# ---------------------------------------------------------------------------


class TtsFileRequest(BaseModel):
    text: str


@app.post("/transcribe")
async def transcribe_endpoint(file: UploadFile = File(...), _u: None = Depends(_require_user)):
    """Receive WebM/Opus audio from browser MediaRecorder, return Whisper transcription."""
    if not _stt_enabled_now():
        return {"text": "", "disabled": True}
    audio_bytes = await file.read()

    webm_path = Path(tempfile.mktemp(suffix=".webm"))
    wav_path = webm_path.with_suffix(".wav")
    try:
        webm_path.write_bytes(audio_bytes)

        # Convert browser WebM/Opus → 16 kHz mono WAV that Whisper can read
        conv = await asyncio.to_thread(
            subprocess.run,
            [
                "/usr/bin/ffmpeg",
                "-y",
                "-i",
                str(webm_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "wav",
                str(wav_path),
            ],
            capture_output=True,
            timeout=30,
        )
        if conv.returncode != 0:
            log.error("ffmpeg conversion failed: %s", conv.stderr.decode())
            raise HTTPException(status_code=500, detail="Audio conversion failed")

        audio, _ = sf.read(str(wav_path), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, transcribe, audio)
        log.info("Browser STT result: %r", text)
        return {"text": text}
    finally:
        webm_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


@app.post("/tts_file")
async def tts_file_endpoint(
    request: TtsFileRequest, background_tasks: BackgroundTasks, _u: None = Depends(_require_user)
):
    """Generate TTS WAV for the given text and return a URL the browser can play."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    loop = asyncio.get_running_loop()
    try:
        wav_path = await loop.run_in_executor(
            None, generate_speech_wav, request.text, get_speech_lang()
        )
    except Exception as exc:
        log.error("TTS generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="TTS generation failed") from exc

    # A path, not a URL: the browser prefixes its own route to the voice service (same origin /voice
    # over HTTPS, :8011 over HTTP) — the LAN address for speakers is not the browser's business (P127).
    path = f"/audio/{wav_path.name}"
    log.info("Browser TTS file ready: %s", path)

    async def _cleanup_after_delay():
        await asyncio.sleep(60)
        wav_path.unlink(missing_ok=True)
        log.debug("Deleted browser TTS file: %s", wav_path)

    background_tasks.add_task(_cleanup_after_delay)
    return {"path": path}


# ---------------------------------------------------------------------------
# Speaker enrollment endpoints
# ---------------------------------------------------------------------------


@app.post("/speaker/enroll/{username}")
async def enroll_speaker(
    username: str, file: UploadFile = File(...), _s: None = Depends(_require_service)
):
    """
    Receive a WAV file and save a voiceprint for the given user.
    Called by kaare_api proxy — admin only at the API level.
    """

    audio_bytes = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        tmp_path.write_bytes(audio_bytes)
        audio, samplerate = sf.read(str(tmp_path), dtype="float32", always_2d=False)
    finally:
        tmp_path.unlink(missing_ok=True)

    # Resample to 16 kHz if needed
    if samplerate != 16000:
        gcd = np.gcd(16000, samplerate)
        audio = resample_poly(audio, 16000 // gcd, samplerate // gcd).astype(np.float32)

    # Mix to mono if stereo
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    if len(audio) < 16000 * 2:
        raise HTTPException(status_code=400, detail="Audio too short — need at least 2 seconds.")

    try:
        loop = asyncio.get_running_loop()
        embedding = await loop.run_in_executor(None, sr.extract_embedding, audio)
        await loop.run_in_executor(None, sr.save_voiceprint, username, embedding, ENROLLMENT_DIR)
    except Exception as exc:
        log.error("Enrollment failed for '%s': %s", username, exc)
        raise HTTPException(status_code=500, detail=f"Enrollment failed: {exc}") from exc

    log.info("Enrolled speaker: %s (%d samples)", username, len(audio))
    return {"ok": True, "username": username, "samples": len(audio)}


@app.delete("/speaker/enroll/{username}")
async def delete_speaker(username: str, _s: None = Depends(_require_service)):
    """Remove stored voiceprint for a user."""

    removed = sr.delete_voiceprint(username, ENROLLMENT_DIR)
    return {"ok": True, "removed": removed}


@app.get("/speaker/status/{username}")
async def speaker_status(username: str, _s: None = Depends(_require_service)):
    """Check whether a voiceprint exists for a user."""

    return {"username": username, "has_voiceprint": sr.has_voiceprint(username, ENROLLMENT_DIR)}


# ---------------------------------------------------------------------------
# Wyoming server — startup
# ---------------------------------------------------------------------------


async def _wyoming_stt(audio_bytes: bytes, rate: int) -> str:
    """STT-callback for Wyoming server: rå PCM bytes → transkript."""
    audio_i16 = np.frombuffer(audio_bytes, dtype=np.int16)
    audio_f32 = audio_i16.astype(np.float32) / 32768.0
    if rate != TARGET_RATE:
        gcd = math.gcd(TARGET_RATE, rate)
        audio_f32 = resample_poly(audio_f32, TARGET_RATE // gcd, rate // gcd).astype(np.float32)
    loop = asyncio.get_event_loop()
    # Same level normalisation as the local mic (P138): quiet satellite mics (PDM) reach Whisper at a usable level
    audio_f32 = await loop.run_in_executor(None, _agc, audio_f32)
    return await loop.run_in_executor(None, transcribe, audio_f32)


async def _wyoming_ask_kaare(text: str, peer_host: str) -> str:
    """Wyoming satellites send no identity: the node is found by its IP, then its single
    default user or its trust level decides who speaks (P120). An unknown satellite is
    treated as unknown_user until it is added under Nodes."""
    node_id = _node_for_host(peer_host)
    rid = f"rid-wy-{int(time.time() * 1000)}"
    _wyoming_rids[peer_host] = rid
    _route_log_voice(rid, "stt_done", node=node_id or peer_host, text=text, source="wyoming")
    if node_id is None:
        log.warning(
            "Wyoming: satellite %s is not in nodes.yaml — treated as unknown_user", peer_host
        )
        return await ask_kaare(
            text, resolved_user="unknown_user", confirmed_by="unknown_node", rid=rid
        )
    single = _single_default_user(node_id)
    return await ask_kaare(
        text,
        room=_room_for_node(node_id),
        resolved_user=single or _unidentified_identity(node_id),
        confirmed_by="default" if single else "node_trust",
        rid=rid,
        node_id=node_id,
    )


async def _wyoming_listen_start(peer_host: str) -> None:
    """A satellite started streaming: quiet the devices its answer will be played on."""
    duck = _new_duck()
    if duck is None:
        return
    old = _wyoming_ducks.pop(peer_host, None)
    if old is not None:
        await old.end()
    node_id = _node_for_host(peer_host) or ""
    _wyoming_ducks[peer_host] = duck
    await duck.start(
        routing.audio_targets(node_id, _nodes, routing.INPUT_WYOMING, place_of=_place_of), _nodes
    )


async def _wyoming_cycle_end(peer_host: str) -> None:
    duck = _wyoming_ducks.pop(peer_host, None)
    if duck is not None:
        await duck.end()


async def _wyoming_route(answer: str, peer_host: str) -> WyomingReply:
    """Where a satellite's answer goes (P138): back to the satellite (auto, as before) or to the nodes in its
    audio_out. When none of those answer: the satellite gets an `output-unreachable` error (red light) and, if it has
    a speaker, the notice spoken on it — its own speaker is the emergency channel."""
    loop = asyncio.get_running_loop()
    node_id = _node_for_host(peer_host) or ""
    targets = routing.audio_targets(node_id, _nodes, routing.INPUT_WYOMING, place_of=_place_of)
    wav_path = await loop.run_in_executor(None, generate_speech_wav, answer, get_speech_lang())
    remote = [t for t in targets if t not in (routing.SELF, routing.LOCAL)]
    reply = WyomingReply()
    ok: list[str] = []
    failed: list[str] = []
    duck = _wyoming_ducks.get(peer_host)
    if duck is not None:
        await duck.restore_volumes()
    if remote:
        ok, failed = await _play_on_nodes(wav_path, remote)
    if routing.LOCAL in targets:
        await _play_local(wav_path, delete=False)
        ok.append(routing.LOCAL)
    if routing.SELF in targets:
        reply.play_here = wav_path
    else:
        loop.call_later(60, _delete_wav, wav_path)
        reply.routed_to = ok
    if failed and not ok and reply.play_here is None:
        notice = _unreachable_text(failed)
        reply.error_code, reply.error_text = ERROR_OUTPUT_UNREACHABLE, notice
        log.warning("Wyoming: reply for %s not delivered to %s", node_id or peer_host, failed)
        if routing.emergency_target(node_id, _nodes, routing.INPUT_WYOMING) == routing.SELF:
            reply.play_here = await loop.run_in_executor(
                None, generate_speech_wav, notice, get_speech_lang()
            )
    log.info("Wyoming: reply for %s → %s (failed: %s)", node_id or peer_host, targets, failed)
    rid = _wyoming_rids.pop(peer_host, "")
    if rid:
        _route_log_voice(rid, "stt_reply_targets", targets=targets, delivered=ok, failed=failed)
    return reply


@app.on_event("startup")
async def _startup_wyoming() -> None:
    wy_cfg = _providers_cfg.get("wyoming", {})
    if not wy_cfg.get("enabled", False):
        log.info("Wyoming server deaktivert i voice_providers.yaml")
        return

    listen_host = wy_cfg.get("listen_host", "0.0.0.0")
    listen_port = int(wy_cfg.get("listen_port", 10300))

    global _wyoming_server
    server = WyomingServer(
        host=listen_host,
        port=listen_port,
        stt_fn=_wyoming_stt,
        kaare_fn=_wyoming_ask_kaare,
        tts_fn=generate_speech_wav,
        silence_sec=float(SILENCE_SEC),
        route_fn=_wyoming_route,
        listen_start_fn=_wyoming_listen_start,
        cycle_end_fn=_wyoming_cycle_end,
    )
    _wyoming_server = server
    spawn(server.start())


# ---------------------------------------------------------------------------
# Oppstart
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
