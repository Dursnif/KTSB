import logging
from typing import Optional

import httpx

from adapters.display.base import BaseDisplayProvider

log = logging.getLogger(__name__)


class HaDisplayProvider(BaseDisplayProvider):
    def __init__(self, ha_url: str, ha_token: str):
        self._ha_url = ha_url
        self._ha_token = ha_token

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
        entity_id = node_config.get("entity_id", "")
        if not entity_id:
            return {"ok": False, "error": "HA fallback: missing entity_id"}
        if not self._ha_url or not self._ha_token:
            return {"ok": False, "error": "HA not configured"}
        if not image_path:
            return {"ok": False, "error": "HA media_player: requires image URL"}

        headers = {
            "Authorization": f"Bearer {self._ha_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "entity_id": entity_id,
            "media_content_type": "image",
            "media_content_id": image_path,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._ha_url}/api/services/media_player/play_media",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in (200, 201):
                    return {"ok": True, "method": "ha_media"}
                return {"ok": False, "error": f"HA media HTTP {resp.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
