from abc import ABC, abstractmethod
from typing import Optional


class BaseDisplayProvider(ABC):
    @abstractmethod
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
        """Send content to a display node.

        Returns {"ok": True, "method": "..."} on success,
        or {"ok": False, "error": "..."} on failure.
        """
