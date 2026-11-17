import base64
import logging
from pathlib import Path

import httpx

from adapters.display.base import BaseDisplayProvider

log = logging.getLogger(__name__)

# TvOverlay (gugutab, Android TV): REST on the TV, default port 5001, `POST /notify` (checked against the app on a
# real TV and its README, 2026-10-18 — the earlier 7979 + /api/v2/notification was PiPup's port and never matched).
DEFAULT_PORT = 5001
# KTSB's position names → TvOverlay's corners (start/end follow the layout direction).
_CORNERS = {
    "bottom_right": "bottom_end",
    "bottom_left": "bottom_start",
    "top_right": "top_end",
    "top_left": "top_start",
}


class TvOverlayDisplayProvider(BaseDisplayProvider):
    async def send(
        self,
        node_config: dict,
        text: str,
        title: str = "Kåre",
        image_path: str | None = None,
        image_b64: str | None = None,
        duration: int = 8,
        position: str = "bottom_right",
    ) -> dict:
        host = node_config.get("host", "")
        port = int(node_config.get("tvoverlay_port") or DEFAULT_PORT)
        if not host:
            return {"ok": False, "error": "TvOverlay: missing host"}

        payload: dict = {
            "title": title,
            "message": text,
            "corner": _CORNERS.get(position, position),
            "duration": duration,
        }

        img = image_b64
        if not img and image_path:
            try:
                img = base64.b64encode(Path(image_path).read_bytes()).decode()
            except Exception as e:
                log.warning("TvOverlay: could not read image %s: %s", image_path, e)
        if img:
            payload["image"] = img

        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(f"http://{host}:{port}/notify", json=payload)
                if resp.status_code in (200, 201, 204):
                    return {"ok": True, "method": "tvoverlay"}
                return {"ok": False, "error": f"TvOverlay HTTP {resp.status_code}"}
        except Exception as e:
            reason = str(e) or type(e).__name__  # httpx connect errors often carry no message
            log.warning("TvOverlay error %s: %s", host, reason)
            return {"ok": False, "error": reason}
