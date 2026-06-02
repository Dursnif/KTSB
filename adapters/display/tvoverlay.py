import base64
import logging
from pathlib import Path
from typing import Optional

import httpx

from adapters.display.base import BaseDisplayProvider

log = logging.getLogger(__name__)


class TvOverlayDisplayProvider(BaseDisplayProvider):
    async def send(
        self,
        node_config: dict,
        text: str,
        title: str = "Kåre",
        image_path: Optional[str] = None,
        image_b64: Optional[str] = None,
        duration: int = 8,
        position: str = "bottom_right",
    ) -> dict:
        host = node_config.get("host", "")
        port = node_config.get("tvoverlay_port", 7979)
        if not host:
            return {"ok": False, "error": "TvOverlay: missing host"}

        payload: dict = {
            "title": title,
            "message": text,
            "position": position,
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
                resp = await client.post(
                    f"http://{host}:{port}/api/v2/notification",
                    json=payload,
                )
                if resp.status_code in (200, 201, 204):
                    return {"ok": True, "method": "tvoverlay"}
                return {"ok": False, "error": f"TvOverlay HTTP {resp.status_code}"}
        except Exception as e:
            log.error("TvOverlay error %s: %s", host, e)
            return {"ok": False, "error": str(e)}
