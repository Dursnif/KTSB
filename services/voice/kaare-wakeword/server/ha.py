"""Home Assistant REST API client.

Provides methods to list entities and call services via the
Home Assistant REST API. Used by the NLU engine to execute
smart home actions.

API docs: https://developers.home-assistant.io/docs/api/rest/
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class HomeAssistantClient:
    """Home Assistant REST API client.

    Args:
        url: HA base URL (e.g. http://homeassistant.local:8123).
        token: Long-lived access token.
    """

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    _RELEVANT_DOMAINS = {
        "light", "switch", "sensor", "binary_sensor", "climate",
        "cover", "fan", "media_player", "lock", "vacuum",
        "scene", "script", "automation", "input_boolean",
    }

    def list_entities(self) -> list[str]:
        """List all entity IDs from Home Assistant.

        Returns:
            List of entity ID strings.
        """
        resp = requests.get(
            f"{self.url}/api/states",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return [e["entity_id"] for e in resp.json()]

    def list_entities_with_names(
        self, domains: set[str] | None = None,
    ) -> list[dict[str, str]]:
        """List entities with friendly names, filtered by domain.

        Returns:
            List of dicts with 'entity_id', 'friendly_name', 'state'.
        """
        domains = domains or self._RELEVANT_DOMAINS
        resp = requests.get(
            f"{self.url}/api/states",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        entities = []
        for e in resp.json():
            eid = e["entity_id"]
            domain = eid.split(".")[0]
            if domain not in domains:
                continue
            name = e.get("attributes", {}).get("friendly_name", eid)
            state = e.get("state", "unknown")
            entities.append({
                "entity_id": eid,
                "friendly_name": name,
                "state": state,
            })
        return entities

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: dict | None = None,
    ) -> None:
        """Call a Home Assistant service.

        Args:
            domain: Service domain (e.g. "light").
            service: Service name (e.g. "turn_on").
            entity_id: Target entity ID.
            data: Additional service data.
        """
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)

        resp = requests.post(
            f"{self.url}/api/services/{domain}/{service}",
            headers=self._headers(),
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        log.info("HA service called: %s.%s -> %s", domain, service, entity_id)

    def get_state(self, entity_id: str) -> dict:
        """Get current state of an entity.

        Args:
            entity_id: Entity ID to query.

        Returns:
            State dict with 'state', 'attributes', etc.
        """
        resp = requests.get(
            f"{self.url}/api/states/{entity_id}",
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
