from abc import ABC, abstractmethod


class BaseDisplayProvider(ABC):
    @abstractmethod
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
        """Send content to a display node.

        Returns {"ok": True, "method": "..."} on success,
        or {"ok": False, "error": "..."} on failure.
        """
