"""Wake Word Maker — recording server.

Serves the web UI and handles audio upload/processing.
Run: uv run python recorder/server.py --port 8081 --data-dir data/
"""
from __future__ import annotations

import argparse
import datetime
import io
import logging
import ssl
import wave
from pathlib import Path
from subprocess import run as subprocess_run

import numpy as np
from aiohttp import web

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CLIP_DURATION = 1.5
CLIP_SAMPLES = int(CLIP_DURATION * SAMPLE_RATE)

# Speech detection params (from trim_positives.py)
FRAME_MS = 20
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
SMOOTH_FRAMES = 5
NOISE_PERCENTILE = 30
SPEECH_FACTOR = 4.0


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Read WAV bytes, return (mono_float32, sample_rate)."""
    with io.BytesIO(data) as buf:
        with wave.open(buf, "rb") as wf:
            rate = wf.getframerate()
            n_ch = wf.getnchannels()
            sw = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())

    if sw == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        samples = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported sample width: {sw}")

    if n_ch > 1:
        samples = samples.reshape(-1, n_ch).mean(axis=1)

    return samples, rate


def resample(samples: np.ndarray, orig_rate: int, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Resample to target rate using nearest-neighbor."""
    if orig_rate == target_rate:
        return samples
    ratio = orig_rate / target_rate
    indices = np.arange(0, len(samples), ratio).astype(int)
    indices = indices[indices < len(samples)]
    return samples[indices]


def find_speech_region(audio: np.ndarray) -> tuple[int, int] | None:
    """Find speech start/end using adaptive energy thresholding."""
    n_frames = len(audio) // FRAME_SAMPLES
    if n_frames < 3:
        return None

    energy = np.array([
        np.sqrt(np.mean(audio[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES] ** 2))
        for i in range(n_frames)
    ])

    kernel = np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES
    smoothed = np.convolve(energy, kernel, mode="same")

    noise_floor = np.percentile(smoothed, NOISE_PERCENTILE)
    threshold = max(noise_floor * SPEECH_FACTOR, 0.005)

    speech_mask = smoothed > threshold
    speech_indices = np.where(speech_mask)[0]

    if len(speech_indices) == 0:
        return None

    return (speech_indices[0] * FRAME_SAMPLES, (speech_indices[-1] + 1) * FRAME_SAMPLES)


def trim_to_clip(audio: np.ndarray) -> np.ndarray:
    """Trim audio to CLIP_SAMPLES centered on speech."""
    region = find_speech_region(audio)

    if region is None:
        center = len(audio) // 2
    else:
        start, end = region
        center = (start + end) // 2

    half = CLIP_SAMPLES // 2
    clip_start = max(0, center - half)
    clip_end = clip_start + CLIP_SAMPLES

    if clip_end > len(audio):
        clip_end = len(audio)
        clip_start = max(0, clip_end - CLIP_SAMPLES)

    clip = audio[clip_start:clip_end]

    if len(clip) < CLIP_SAMPLES:
        clip = np.pad(clip, (0, CLIP_SAMPLES - len(clip)))

    return clip


def split_to_clips(audio: np.ndarray) -> list[np.ndarray]:
    """Split long audio into CLIP_SAMPLES-length clips."""
    clips = []
    for i in range(0, len(audio) - CLIP_SAMPLES // 2, CLIP_SAMPLES):
        clip = audio[i:i + CLIP_SAMPLES]
        if len(clip) < CLIP_SAMPLES:
            clip = np.pad(clip, (0, CLIP_SAMPLES - len(clip)))
        clips.append(clip)
    return clips


def write_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode float32 array as 16-bit WAV bytes."""
    int16 = (audio * 32767).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(int16.tobytes())
    return buf.getvalue()


def make_filename(prefix: str, speaker: str, seq: int = 0) -> str:
    """Generate unique filename."""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{speaker}_{ts}_{seq:03d}.wav"


# ── Handlers ──────────────────────────────────

async def handle_upload(request: web.Request) -> web.Response:
    """Handle audio upload, process and save."""
    data_dir: Path = request.app["data_dir"]

    reader = await request.multipart()
    file_data = None
    rec_type = "wakeword_positive"
    speaker = "unknown"
    text = ""

    async for part in reader:
        if part.name == "file":
            file_data = await part.read()
        elif part.name == "type":
            rec_type = (await part.read()).decode()
        elif part.name == "speaker":
            speaker = (await part.read()).decode()
        elif part.name == "text":
            text = (await part.read()).decode()

    if not file_data:
        return web.json_response({"error": "No file uploaded"}, status=400)

    try:
        samples, orig_rate = read_wav(file_data)
    except Exception as e:
        return web.json_response({"error": f"Invalid WAV: {e}"}, status=400)

    audio = resample(samples, orig_rate)
    results = []

    if rec_type in ("wakeword_positive", "wakeword_negative"):
        clip = trim_to_clip(audio)
        subdir = "positive" if rec_type == "wakeword_positive" else "negative"
        out_dir = data_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = "pos" if "positive" in rec_type else "neg"
        fname = make_filename(prefix, speaker)
        (out_dir / fname).write_bytes(write_wav_bytes(clip))
        results.append(fname)
        log.info("Saved %s -> %s/%s (%.1fs input)", rec_type, subdir, fname, len(audio) / SAMPLE_RATE)

    elif rec_type == "ambient":
        out_dir = data_dir / "negative"
        out_dir.mkdir(parents=True, exist_ok=True)
        clips = split_to_clips(audio)
        for i, clip in enumerate(clips):
            fname = make_filename("amb", speaker, i)
            (out_dir / fname).write_bytes(write_wav_bytes(clip))
            results.append(fname)
        log.info("Saved ambient: %d clips from %.1fs", len(clips), len(audio) / SAMPLE_RATE)

    elif rec_type == "tts":
        out_dir = data_dir / "tts" / speaker
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = make_filename("tts", speaker)
        (out_dir / fname).write_bytes(write_wav_bytes(audio))
        (out_dir / fname).with_suffix(".txt").write_text(text, encoding="utf-8")
        results.append(fname)
        log.info("Saved TTS: %s (%s)", fname, text[:50])

    return web.json_response({"ok": True, "files": results, "count": len(results)})


async def handle_stats(request: web.Request) -> web.Response:
    """Return recording counts."""
    data_dir: Path = request.app["data_dir"]

    pos_dir = data_dir / "positive"
    neg_dir = data_dir / "negative"

    pos_count = len(list(pos_dir.glob("*.wav"))) if pos_dir.exists() else 0
    neg_count = len(list(neg_dir.glob("*.wav"))) if neg_dir.exists() else 0

    return web.json_response({"positive": pos_count, "negative": neg_count})


# ── SSL ───────────────────────────────────────

def ensure_ssl_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Generate self-signed cert if not present."""
    cert_file = cert_dir / "cert.pem"
    key_file = cert_dir / "key.pem"

    if cert_file.exists() and key_file.exists():
        return cert_file, key_file

    log.info("Generating self-signed SSL certificate...")
    cert_dir.mkdir(parents=True, exist_ok=True)
    subprocess_run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_file), "-out", str(cert_file),
        "-days", "365", "-nodes",
        "-subj", "/CN=Wake Word Maker",
    ], check=True)
    return cert_file, key_file


# ── App ───────────────────────────────────────

def create_app(data_dir: Path) -> web.Application:
    app = web.Application(client_max_size=50 * 1024 * 1024)
    app["data_dir"] = data_dir

    static_dir = Path(__file__).parent / "static"

    async def index_handler(request):
        return web.FileResponse(static_dir / "index.html")

    app.router.add_get("/", index_handler)
    app.router.add_post("/api/upload", handle_upload)
    app.router.add_get("/api/stats", handle_stats)
    app.router.add_static("/static", static_dir)

    return app


def main():
    parser = argparse.ArgumentParser(description="Wake Word Maker server")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--no-ssl", action="store_true", help="Disable HTTPS")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    app = create_app(args.data_dir)

    ssl_ctx = None
    if not args.no_ssl:
        cert_file, key_file = ensure_ssl_cert(Path(__file__).parent)
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert_file, key_file)

    proto = "https" if ssl_ctx else "http"
    log.info("Wake Word Maker on %s://%s:%d (data: %s)", proto, args.host, args.port, args.data_dir)
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
