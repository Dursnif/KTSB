"""P151: the screen core's I/O — Frigate's camera list, ffprobe per stream, the MSE websocket proxy and the re-encoded
fMP4 stream. Runs in the voice bridge (D6: the screen only ever talks to KTSB; Frigate's address stays inside).

Frigate's address is read live from `configs/services.yaml` (`frigate.url`, GUI) so a change needs no restart; the
RTSP restream port is `frigate.rtsp_port` (default 8554, Frigate's own default).
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx
import websockets
import yaml

from kaare_core.voice.screen import StreamChoice, StreamInfo, camera_streams, choose_stream

log = logging.getLogger(__name__)

SERVICES_PATH = Path("/kaare/configs/services.yaml")
DEFAULT_RTSP_PORT = 8554
PROBE_TTL_S = 6 * 3600.0
CONFIG_TTL_S = 60.0
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

_svc_cache: dict = {"mtime": None, "frigate": {}}
_probe_cache: dict[str, tuple[float, StreamInfo | None]] = {}
_config_cache: dict = {"at": 0.0, "base": "", "config": {}, "streams": []}


def frigate_settings(path: Path = SERVICES_PATH) -> dict:
    try:
        m = path.stat().st_mtime
    except OSError:
        return {}
    if _svc_cache["mtime"] != m:
        try:
            _svc_cache.update(mtime=m, frigate=(yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("frigate") or {})
        except Exception as e:
            log.warning("services.yaml unreadable for screens: %s", e)
    return _svc_cache["frigate"]


def frigate_base() -> str:
    return str(frigate_settings().get("url") or "").rstrip("/")


def rtsp_url(stream: str) -> str:
    host = urlparse(frigate_base()).hostname or ""
    try:
        port = int(frigate_settings().get("rtsp_port") or DEFAULT_RTSP_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_RTSP_PORT
    return f"rtsp://{host}:{port}/{stream}"


def mse_ws_url(stream: str) -> str:
    u = urlparse(frigate_base())
    scheme = "wss" if u.scheme == "https" else "ws"
    return f"{scheme}://{u.netloc}/live/mse/api/ws?src={stream}"


async def _frigate_lists() -> tuple[dict, list[str]]:
    base = frigate_base()
    if not base:
        raise RuntimeError("no Frigate address set (Settings → Frigate)")
    c = _config_cache
    if c["base"] == base and time.monotonic() - c["at"] < CONFIG_TTL_S:
        return c["config"], c["streams"]
    async with httpx.AsyncClient(timeout=8.0) as client:
        cfg = (await client.get(f"{base}/api/config")).json()
    streams = list(((cfg.get("go2rtc") or {}).get("streams") or {}).keys())
    c.update(at=time.monotonic(), base=base, config=cfg, streams=streams)
    return cfg, streams


async def camera_ids() -> dict[str, str]:
    """Frigate camera id → friendly name (for the «which camera» list)."""
    cfg, _ = await _frigate_lists()
    return {cid: str(c.get("friendly_name") or cid) for cid, c in (cfg.get("cameras") or {}).items()}


async def probe(stream: str) -> StreamInfo | None:
    """Codec and size of one restream (cached — a camera's stream does not change often)."""
    hit = _probe_cache.get(stream)
    if hit and time.monotonic() - hit[0] < PROBE_TTL_S:
        return hit[1]
    info = None
    try:
        p = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", rtsp_url(stream),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=12)
        parts = out.decode().strip().split(",")
        if len(parts) >= 3:
            info = StreamInfo(stream, parts[0], int(parts[1]), int(parts[2]))
    except Exception as e:
        log.info("probe %s failed: %s", stream, type(e).__name__)
    _probe_cache[stream] = (time.monotonic(), info)
    return info


async def pick(camera: str, max_w: int, max_h: int, force_reencode: bool = False) -> StreamChoice | None:
    cfg, streams = await _frigate_lists()
    names = camera_streams(cfg, camera, streams)
    infos = [i for i in await asyncio.gather(*(probe(n) for n in names)) if i]
    return choose_stream(infos, max_w, max_h, force_reencode)


def h264_codec(width: int, height: int) -> tuple[str, str]:
    """ffmpeg level + MSE codec string for our re-encode (Main profile). Level 3.1 covers 1280×720; above it 4.1."""
    return ("3.1", "avc1.4D401F") if width * height <= 1280 * 720 else ("4.1", "avc1.4D4029")


def ffmpeg_args(choice: StreamChoice) -> list[str]:
    level, _ = h264_codec(choice.width, choice.height)
    vf = f"scale={choice.width}:{choice.height}" if choice.scale_width else "null"
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp", "-fflags", "nobuffer",
            "-i", rtsp_url(choice.stream), "-an", "-vf", vf,
            "-c:v", "libx264", "-profile:v", "main", "-level", level, "-preset", "ultrafast", "-tune", "zerolatency",
            "-g", "20", "-pix_fmt", "yuv420p", "-b:v", "2500k",
            "-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-frag_duration", "200000",
            "pipe:1"]


async def fmp4(choice: StreamChoice, alive: Callable[[], bool]) -> AsyncIterator[bytes]:
    """The re-encoded stream as fragmented MP4 bytes while the session lives; ffmpeg is killed when it ends."""
    p = await asyncio.create_subprocess_exec(*ffmpeg_args(choice), stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.DEVNULL)
    try:
        while alive():
            try:
                chunk = await asyncio.wait_for(p.stdout.read(65536), timeout=10)
            except TimeoutError:
                log.info("re-encode of %s gave no data for 10 s — stopping", choice.stream)
                break
            if not chunk:
                break
            yield chunk
    finally:
        if p.returncode is None:
            p.kill()
            try:
                await asyncio.wait_for(p.wait(), timeout=3)
            except TimeoutError:
                pass


async def proxy_mse(client_ws, stream: str, alive: Callable[[], bool]) -> None:
    """Pipe go2rtc's MSE websocket (through Frigate) to the screen, both ways, until either side or the session ends.
    `client_ws` is a Starlette WebSocket that is already accepted."""
    async with websockets.connect(mse_ws_url(stream), max_size=None, open_timeout=8) as up:
        async def down_to_client():
            async for msg in up:
                if not alive():
                    break
                if isinstance(msg, bytes):
                    await client_ws.send_bytes(msg)
                else:
                    await client_ws.send_text(msg)

        async def client_to_up():
            while alive():
                m = await client_ws.receive()
                if m.get("type") == "websocket.disconnect":
                    break
                if m.get("text") is not None:
                    await up.send(m["text"])
                elif m.get("bytes") is not None:
                    await up.send(m["bytes"])

        async def watchdog():
            # polls a plain function (the session store), not an event — there is nothing to wait on
            while alive():  # noqa: ASYNC110
                await asyncio.sleep(1)

        tasks = [asyncio.create_task(c) for c in (down_to_client(), client_to_up(), watchdog())]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
