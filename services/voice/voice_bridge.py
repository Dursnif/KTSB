"""
Kåre Voice Bridge
FastAPI-server som tar imot wakeword/knapp-trigge fra noder
og kjører lokal voice pipeline (TTS ack → opptak → STT → Kåre API → TTS svar → avspilling).
"""

import asyncio
import io
import logging
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import math

import httpx
import numpy as np
import soundfile as sf
import yaml
from kaare_core.voice.registry import VoiceProviderRegistry
from kaare_core.voice.wyoming_server import WyomingServer
from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from scipy.signal import resample_poly
import uvicorn

# ---------------------------------------------------------------------------
# Konfig
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "configs" / "voice_settings.yaml"
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

KAARE_API_URL  = cfg["kaare"]["api_url"]
KAARE_TIMEOUT  = cfg["kaare"]["timeout"]

STT_BACKEND    = _voice_svc["stt"].get("backend", "openvino")
STT_MODEL_DIR  = Path(_voice_svc["stt"]["model_dir"])
STT_FW_MODEL   = _voice_svc["stt"].get("faster_whisper_model", "large-v3")
STT_FW_COMPUTE = _voice_svc["stt"].get("faster_whisper_compute_type", "int8")
VENV_PYTHON    = Path(_voice_svc["venv_python"])
PIPER_BIN      = VENV_PYTHON  # called with -m piper.__main__
MAX_RECORD_SEC = 8
SILENCE_SEC    = cfg["stt"]["silence_threshold_seconds"]
SILENCE_AMP    = cfg["stt"]["silence_amplitude_threshold"]

TTS_VOICE      = Path(_voice_svc["tts"]["voice"])
TTS_RATE       = _voice_svc["tts"]["sample_rate"]

MIC_DEVICE     = cfg["audio"]["mic_device"]
MIC_RATE       = cfg["audio"]["mic_sample_rate"]
MIC_CHANNELS   = 1
TARGET_RATE    = 16000

CACHE_DIR      = BASE_DIR / cfg["fast_responses"]["cache_dir"]
WAKE_ACK_WAV   = CACHE_DIR / "wake_ack_0.wav"

HTTP_PORT      = cfg["bridge"]["http_port"]
BRIDGE_HOST    = cfg["bridge"]["serve_host"]

TMP_AUDIO_DIR  = Path("/tmp/kaare_tts")
TMP_AUDIO_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("voice_bridge")

# ---------------------------------------------------------------------------
# STT backend — openvino (Intel Arc GPU) or faster_whisper (universal CPU)
# ---------------------------------------------------------------------------

def _load_stt_backend():
    if STT_BACKEND == "faster_whisper":
        from faster_whisper import WhisperModel
        log.info("Laster faster-whisper model '%s' (compute_type=%s)...", STT_FW_MODEL, STT_FW_COMPUTE)
        model = WhisperModel(STT_FW_MODEL, device="cpu", compute_type=STT_FW_COMPUTE)
        log.info("faster-whisper klar.")
        return model
    # Default: openvino
    import openvino_genai
    log.info("Laster OpenVINO GenAI WhisperPipeline på Intel Arc GPU...")
    pipe = openvino_genai.WhisperPipeline(str(STT_MODEL_DIR), device="GPU")
    log.info("WhisperPipeline klar på Intel Arc GPU.")
    return pipe

_stt_model = _load_stt_backend()

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
    from df.enhance import init_df
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
    from df.enhance import enhance
    # Resample 16000 → 48000 (DeepFilterNet sitt format)
    gcd = np.gcd(DF_SR, TARGET_RATE)
    audio_48k = resample_poly(audio_16k, DF_SR // gcd, TARGET_RATE // gcd).astype(np.float32)

    # DeepFilterNet forventer (1, samples) tensor
    import torch
    tensor_in = torch.from_numpy(audio_48k).unsqueeze(0)
    tensor_out = enhance(_df_model, _df_state, tensor_in)
    audio_48k_clean = tensor_out.squeeze(0).numpy()

    # Resample tilbake 48000 → 16000
    audio_16k_clean = resample_poly(audio_48k_clean, TARGET_RATE // gcd, DF_SR // gcd).astype(np.float32)

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

def play_wav(path: Path) -> None:
    """Play a WAV file via aplay (PipeWire/ALSA, handles resampling automatically)."""
    result = subprocess.run(["aplay", str(path)], capture_output=True)
    if result.returncode != 0:
        log.error("aplay failed: %s", result.stderr.decode())


def speak(text: str) -> None:
    """Generate speech with Piper and play locally (used for wake-ack fallback)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = [str(VENV_PYTHON), "-m", "piper.__main__", "--model", str(TTS_VOICE), "--output_file", str(tmp_path)]
        result = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=15)
        if result.returncode != 0:
            log.error("Piper failed: %s", result.stderr.decode())
            return
        play_wav(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def generate_speech_wav(text: str) -> Path:
    """
    Generer tale med Piper og lagre som WAV-fil i TMP_AUDIO_DIR.
    Returnerer Path til filen (ryddes opp av kalleren).
    """
    cmd = [str(VENV_PYTHON), "-m", "piper.__main__", "--model", str(TTS_VOICE), "--output_raw"]
    result = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=15)
    if result.returncode != 0:
        log.error("Piper feilet: %s", result.stderr.decode())
        raise RuntimeError(f"Piper TTS feilet: {result.stderr.decode()}")

    raw = np.frombuffer(result.stdout, dtype=np.int16)
    filename = f"tts_{uuid.uuid4().hex}.wav"
    wav_path = TMP_AUDIO_DIR / filename
    sf.write(str(wav_path), raw, TTS_RATE, subtype="PCM_16")
    log.info("TTS WAV lagret: %s", wav_path)
    return wav_path


async def play_on_esp32(wav_path: Path, node_id: str) -> None:
    """
    Spill av en WAV-fil på ESP32-noden via aioesphomeapi media_player.
    Filen serves fra FastAPI GET /audio/{filename}.
    """
    node = _nodes.get(node_id, {})
    host = node.get("host")
    api_port = node.get("api_port", 6053)
    noise_psk = node.get("encryption_key")

    if not host:
        log.error("Ingen host konfigurert for node %s", node_id)
        return

    media_url = f"http://{BRIDGE_HOST}:{HTTP_PORT}/audio/{wav_path.name}"
    log.info("Kobler til ESP32 %s:%s for avspilling av %s", host, api_port, media_url)

    import aioesphomeapi
    api = aioesphomeapi.APIClient(host, api_port, password=None, noise_psk=noise_psk)
    try:
        await api.connect(login=True)
        entities, _ = await api.list_entities_services()
        media_players = [e for e in entities if isinstance(e, aioesphomeapi.MediaPlayerInfo)]

        if not media_players:
            log.error("Ingen media_player funnet på node %s – sjekk ESPHome-konfig", node_id)
            return

        mp_key = media_players[0].key
        log.info("Spiller på media_player key=%s: %s", mp_key, media_url)
        await api.media_player_command(mp_key, media_url=media_url, announcement=True)
    except Exception as exc:
        log.error("Feil mot ESP32 node %s: %s", node_id, exc)
    finally:
        await api.disconnect()


async def set_volume_ha(entity_id: str, volume: float) -> None:
    """Set volume on a HA media player (0.0–1.0)."""
    volume = max(0.0, min(1.0, volume))
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{_HA_URL}/api/services/media_player/volume_set",
                headers={"Authorization": f"Bearer {_HA_TOKEN}"},
                json={"entity_id": entity_id, "volume_level": round(volume, 2)},
            )
            resp.raise_for_status()
            log.info("Volume set to %.0f%% on '%s'", volume * 100, entity_id)
    except Exception as exc:
        log.error("Failed to set volume on '%s': %s", entity_id, exc)


async def play_on_ha_media_player(wav_path: Path, entity_id: str) -> None:
    """Play a TTS WAV file on a HA media player (Nest Hub, Chromecast, Google TV).

    The AI-PC serves the file; HA tells the device to fetch and play it.
    Uses announcement mode so the device returns to its previous state afterwards.
    """
    if not _HA_URL or not _HA_TOKEN:
        log.error("HA credentials not configured — cannot play on '%s'", entity_id)
        return

    media_url = f"http://{BRIDGE_HOST}:{HTTP_PORT}/audio/{wav_path.name}"
    payload = {
        "entity_id": entity_id,
        "media_content_id": media_url,
        "media_content_type": "music",
        "announce": True,
    }
    log.info("HA media player '%s' <- %s", entity_id, media_url)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_HA_URL}/api/services/media_player/play_media",
                headers={"Authorization": f"Bearer {_HA_TOKEN}"},
                json=payload,
            )
            resp.raise_for_status()
    except Exception as exc:
        log.error("Failed to play on HA media player '%s': %s", entity_id, exc)


def _delete_wav(path: Path) -> None:
    try:
        path.unlink()
        log.debug("Slettet midlertidig TTS-fil: %s", path)
    except OSError:
        pass


def record_audio() -> np.ndarray:
    """
    Ta opp fra mikrofon (device 4, 44100Hz stereo).
    Stopper ved 1.5s stillhet eller maks 8s.
    Returnerer mono 16kHz float32-array.
    """
    import sounddevice as sd
    chunk = 2048  # samples per chunk
    silence_chunks_needed = int(SILENCE_SEC * MIC_RATE / chunk)
    max_chunks = int(MAX_RECORD_SEC * MIC_RATE / chunk)

    frames = []
    silent_chunks = 0

    log.info("Starter opptak …")
    with sd.InputStream(device=MIC_DEVICE, samplerate=MIC_RATE,
                        channels=MIC_CHANNELS, dtype="float32",
                        blocksize=chunk) as stream:
        for _ in range(max_chunks):
            data, _ = stream.read(chunk)
            frames.append(data)
            # Bruk venstre kanal for amplitudemåling
            amplitude = np.abs(data[:, 0]).mean()
            if amplitude < SILENCE_AMP:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_needed:
                    log.info("Stillhet detektert, stopper opptak.")
                    break
            else:
                silent_chunks = 0

    if not frames:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(frames, axis=0)  # (N, 2)
    mono  = audio[:, 0]                     # venstre kanal → mono

    # Resample fra 44100 til 16000 Hz
    gcd = np.gcd(TARGET_RATE, MIC_RATE)
    up  = TARGET_RATE // gcd
    down = MIC_RATE   // gcd
    resampled = resample_poly(mono, up, down).astype(np.float32)

    log.info("Opptak ferdig: %.1f s", len(resampled) / TARGET_RATE)
    return resampled



_HALLUCINATIONS = {"takk for at du så på.", "takk for titten.", "takk for meg."}


def _transcribe_openvino(audio: np.ndarray) -> str:
    import openvino_genai
    config = openvino_genai.WhisperGenerationConfig(str(STT_MODEL_DIR / "generation_config.json"))
    config.task = "transcribe"
    config.language = "<|no|>"
    config.num_beams = 1
    result = _stt_model.generate(np.ascontiguousarray(audio.flatten(), dtype=np.float32), config)
    return result.texts[0].strip() if result.texts else ""


def _transcribe_faster_whisper(audio: np.ndarray) -> str:
    segments, _ = _stt_model.transcribe(
        audio.flatten().astype(np.float32),
        language="no",
        beam_size=1,
        vad_filter=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe(audio: np.ndarray) -> str:
    if audio.size == 0:
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
    speaker_id: str | None = None,
    speaker_confidence: float = 0.0,
) -> str:
    """Send tekst til Kåre API og returner svaret."""
    payload: dict = {"prompt": text, "source": "stt"}
    context: dict = {}
    if room:
        context["room"] = room
    if speaker_id:
        payload["user_id"] = speaker_id
        context["speaker_confidence"] = round(speaker_confidence, 3)
    if context:
        payload["context"] = context

    async with httpx.AsyncClient(timeout=KAARE_TIMEOUT) as client:
        try:
            resp = await client.post(KAARE_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            # Støtt begge svarformater
            answer = (
                data.get("response")
                or data.get("message")
                or data.get("text")
                or str(data)
            )
            log.info("Kåre svar: %s", answer)
            return answer
        except Exception as exc:
            log.error("Feil mot Kåre API: %s", exc)
            return "Beklager, jeg fikk ikke kontakt med Kåre."


# ---------------------------------------------------------------------------
# Hoved voice pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(node_id: str, room: str = "", default_user: str = "") -> None:
    """Komplett voice pipeline: ack → opptak → STT → Kåre → TTS → avspilling på ESP32."""
    loop = asyncio.get_running_loop()

    # 1. Spill av "Ja?" lokalt (rask ack mens vi venter på bruker)
    if WAKE_ACK_WAV.exists():
        await loop.run_in_executor(None, play_wav, WAKE_ACK_WAV)
    else:
        await loop.run_in_executor(None, speak, "Ja?")

    # 2. Ta opp brukerens stemme
    audio = await loop.run_in_executor(None, record_audio)

    if audio.size < TARGET_RATE * 0.3:
        log.warning("For lite lyd fra %s, avbryter.", node_id)
        return

    # 2b. AGC: normaliser lydnivå for Whisper
    audio = await loop.run_in_executor(None, _agc, audio)

    # 2c. Speaker identification (non-blocking, feiler stille)
    speaker_id: str | None = None
    speaker_confidence = 0.0
    try:
        import speaker_recognition as sr
        speaker_id, speaker_confidence = await loop.run_in_executor(
            None, sr.identify, audio, ENROLLMENT_DIR, SPEAKER_THRESHOLD
        )
    except Exception as exc:
        log.warning("Speaker identification skipped: %s", exc)

    # Resolved user: voice ID wins; fall back to node default if voice unrecognized
    resolved_user = speaker_id or (default_user if default_user else None)
    if resolved_user and not speaker_id:
        log.info("Speaker not recognized — using node default_user: %s", resolved_user)

    # 3. STT
    text = await loop.run_in_executor(None, transcribe, audio)
    if not text:
        log.warning("Tom transkripsjon fra %s, avbryter.", node_id)
        return

    # 4. Kåre API
    answer = await ask_kaare(text, room=room, speaker_id=resolved_user, speaker_confidence=speaker_confidence)

    # 5. TTS → WAV → avspilling
    wav_path = await loop.run_in_executor(None, generate_speech_wav, answer)
    playback_mode = cfg.get("bridge", {}).get("playback_mode", "local")
    if playback_mode == "esp32":
        await play_on_esp32(wav_path, node_id)
        asyncio.get_running_loop().call_later(60, _delete_wav, wav_path)
    else:
        # local: spill av via Kåre sine høytalere
        await loop.run_in_executor(None, play_wav, wav_path)
        _delete_wav(wav_path)


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
        _nodes = yaml.safe_load(f).get("nodes", {})

# Home Assistant credentials (for ha_media_player nodes)
_HA_URL: str = _svc.get("home_assistant", {}).get("url", "")
_HA_TOKEN: str = ""
_ha_token_path = Path("/kaare/configs/ha_token.env")
if _ha_token_path.exists():
    for _line in _ha_token_path.read_text().splitlines():
        if _line.startswith("HA_TOKEN="):
            _HA_TOKEN = _line.split("=", 1)[1].strip()
            break


_provider_registry = VoiceProviderRegistry(
    bridge_host=BRIDGE_HOST,
    bridge_port=HTTP_PORT,
    ha_url=_HA_URL,
    ha_token=_HA_TOKEN,
)


def _room_for_node(node_id: str) -> str:
    return _nodes.get(node_id, {}).get("room", "")


def _default_user_for_node(node_id: str) -> str:
    return _nodes.get(node_id, {}).get("default_user", "") or ""


@app.post("/wakeword/{node_id}", status_code=202)
async def wakeword_trigger(node_id: str, background_tasks: BackgroundTasks):
    """Trigger fra wakeword-deteksjon på en node."""
    log.info("Wakeword-trigger fra node: %s", node_id)
    background_tasks.add_task(
        trigger_pipeline, node_id, _room_for_node(node_id), _default_user_for_node(node_id)
    )
    return {"status": "ok", "node": node_id, "trigger": "wakeword"}


@app.post("/button/{node_id}", status_code=202)
async def button_trigger(node_id: str, background_tasks: BackgroundTasks):
    """Trigger fra knapptrykk på en node."""
    log.info("Knapp-trigger fra node: %s", node_id)
    background_tasks.add_task(
        trigger_pipeline, node_id, _room_for_node(node_id), _default_user_for_node(node_id)
    )
    return {"status": "ok", "node": node_id, "trigger": "button"}


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    """Serve en midlertidig TTS WAV-fil til ESP32."""
    wav_path = TMP_AUDIO_DIR / filename
    if not wav_path.exists() or not wav_path.parent == TMP_AUDIO_DIR:
        raise HTTPException(status_code=404, detail="Fil ikke funnet")
    return FileResponse(str(wav_path), media_type="audio/wav")


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


def _resolve_targets(target: str) -> list[str]:
    """Map a target string to a list of playback destinations.

    Returns a list of node IDs from nodes.yaml, plus the special
    sentinel 'local' which means play via aplay on the AI-PC.
    """
    t = target.strip().lower()
    if t in ("local", "lokal", ""):
        return ["local"]
    if t in ("all", "alle"):
        enabled = [nid for nid, cfg in _nodes.items() if cfg.get("enabled", True)]
        return ["local"] + enabled
    if t in _nodes:
        if not _nodes[t].get("enabled", True):
            log.warning("Node '%s' is disabled, falling back to local", t)
            return ["local"]
        return [t]
    # Try matching by room name (enabled nodes only)
    for nid, cfg in _nodes.items():
        if cfg.get("room", "").lower() == t and cfg.get("enabled", True):
            return [nid]
    log.warning("Unknown or disabled speak target '%s', falling back to local", target)
    return ["local"]


async def _speak_background(text: str, target: str, volume: float | None = None) -> None:
    """Generate TTS audio and play it on the requested target(s)."""
    loop = asyncio.get_event_loop()
    targets = _resolve_targets(target)
    log.info("Announce: target=%r -> %s volume=%s", target, targets, volume)

    try:
        wav_path = await loop.run_in_executor(None, generate_speech_wav, text)
    except Exception as exc:
        log.error("TTS generation failed: %s", exc)
        return

    # Set volume before playback if requested (provider handles it if supported)
    if volume is not None:
        vol_tasks = []
        for t in targets:
            if t != "local":
                node_cfg = _nodes.get(t, {})
                provider = _provider_registry.get(node_cfg.get("type", "esp32"))
                if provider:
                    vol_tasks.append(provider.set_volume(volume, node_cfg))
        if vol_tasks:
            await asyncio.gather(*vol_tasks, return_exceptions=True)

    tasks: list = []
    for t in targets:
        if t == "local":
            tasks.append(loop.run_in_executor(None, play_wav, wav_path))
        else:
            node_cfg = _nodes.get(t, {})
            node_type = node_cfg.get("type", "esp32")
            provider = _provider_registry.get(node_type)
            if provider:
                tasks.append(provider.speak(wav_path, node_cfg))
            else:
                log.warning("Ingen provider for node-type '%s', hopper over node '%s'", node_type, t)

    await asyncio.gather(*tasks, return_exceptions=True)

    # Give remote devices time to fetch the audio file before deleting it
    await asyncio.sleep(15)
    wav_path.unlink(missing_ok=True)
    log.debug("Deleted TTS file: %s", wav_path)


@app.post("/speak", status_code=202)
async def speak_endpoint(request: SpeakRequest, background_tasks: BackgroundTasks):
    """Trigger speech output. Returns immediately; playback runs in background."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")
    log.info("Speak request: target=%r text=%r...", request.target, request.text[:60])
    background_tasks.add_task(_speak_background, request.text, request.target, request.volume)
    return {"status": "ok", "target": request.target}


# ---------------------------------------------------------------------------
# Browser voice endpoints — STT (transcribe) and TTS file for frontend mic button
# ---------------------------------------------------------------------------

class TtsFileRequest(BaseModel):
    text: str


@app.post("/transcribe")
async def transcribe_endpoint(file: UploadFile = File(...)):
    """Receive WebM/Opus audio from browser MediaRecorder, return Whisper transcription."""
    if not _STT_ENABLED:
        return {"text": "", "disabled": True}
    audio_bytes = await file.read()

    webm_path = Path(tempfile.mktemp(suffix=".webm"))
    wav_path = webm_path.with_suffix(".wav")
    try:
        webm_path.write_bytes(audio_bytes)

        # Convert browser WebM/Opus → 16 kHz mono WAV that Whisper can read
        conv = subprocess.run(
            [
                "/usr/bin/ffmpeg", "-y",
                "-i", str(webm_path),
                "-ar", "16000",
                "-ac", "1",
                "-f", "wav",
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
async def tts_file_endpoint(request: TtsFileRequest, background_tasks: BackgroundTasks):
    """Generate TTS WAV for the given text and return a URL the browser can play."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    loop = asyncio.get_running_loop()
    try:
        wav_path = await loop.run_in_executor(None, generate_speech_wav, request.text)
    except Exception as exc:
        log.error("TTS generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="TTS generation failed")

    url = f"http://{BRIDGE_HOST}:{HTTP_PORT}/audio/{wav_path.name}"
    log.info("Browser TTS file ready: %s", url)

    async def _cleanup_after_delay():
        await asyncio.sleep(60)
        wav_path.unlink(missing_ok=True)
        log.debug("Deleted browser TTS file: %s", wav_path)

    background_tasks.add_task(_cleanup_after_delay)
    return {"url": url}


# ---------------------------------------------------------------------------
# Speaker enrollment endpoints
# ---------------------------------------------------------------------------

@app.post("/speaker/enroll/{username}")
async def enroll_speaker(username: str, file: UploadFile = File(...)):
    """
    Receive a WAV file and save a voiceprint for the given user.
    Called by kaare_api proxy — admin only at the API level.
    """
    import speaker_recognition as sr

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
        from scipy.signal import resample_poly
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
        raise HTTPException(status_code=500, detail=f"Enrollment failed: {exc}")

    log.info("Enrolled speaker: %s (%d samples)", username, len(audio))
    return {"ok": True, "username": username, "samples": len(audio)}


@app.delete("/speaker/enroll/{username}")
async def delete_speaker(username: str):
    """Remove stored voiceprint for a user."""
    import speaker_recognition as sr
    removed = sr.delete_voiceprint(username, ENROLLMENT_DIR)
    return {"ok": True, "removed": removed}


@app.get("/speaker/status/{username}")
async def speaker_status(username: str):
    """Check whether a voiceprint exists for a user."""
    import speaker_recognition as sr
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
    return await loop.run_in_executor(None, transcribe, audio_f32)


async def _wyoming_ask_kaare(text: str, room: str) -> str:
    return await ask_kaare(text, room=room)


@app.on_event("startup")
async def _startup_wyoming() -> None:
    wy_cfg = _providers_cfg.get("wyoming", {})
    if not wy_cfg.get("enabled", False):
        log.info("Wyoming server deaktivert i voice_providers.yaml")
        return

    listen_host = wy_cfg.get("listen_host", "0.0.0.0")
    listen_port = int(wy_cfg.get("listen_port", 10300))

    server = WyomingServer(
        host=listen_host,
        port=listen_port,
        stt_fn=_wyoming_stt,
        kaare_fn=_wyoming_ask_kaare,
        tts_fn=generate_speech_wav,
    )
    asyncio.create_task(server.start())


# ---------------------------------------------------------------------------
# Oppstart
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
