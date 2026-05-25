"""Home Assistant REST API tool for Kåre."""
from __future__ import annotations

import json
import logging
import re

import requests

log = logging.getLogger(__name__)


class HomeAssistantTool:
    """Handles Home Assistant REST API calls and entity discovery.

    Args:
        url: Home Assistant base URL.
        token: Long-lived access token.
        timeout: Request timeout in seconds.
    """

    _RELEVANT_DOMAINS = {
        "light", "switch", "sensor", "binary_sensor", "climate",
        "cover", "fan", "media_player", "lock", "vacuum",
        "scene", "script", "automation", "input_boolean",
    }

    # Norwegian -> English word mappings for entity search.
    # Entity IDs in HA are typically in English; users speak Norwegian.
    _NO_EN_MAP: dict[str, list[str]] = {
        # Rooms
        "stue": ["livingroom", "living_room", "living"],
        "stua": ["livingroom", "living_room", "living"],
        "soverom": ["bedroom", "bed_room"],
        "soverommet": ["bedroom", "bed_room"],
        "kjøkken": ["kitchen"],
        "kjøkkenet": ["kitchen"],
        "gang": ["hallway", "hall", "entry"],
        "gangen": ["hallway", "hall", "entry"],
        "bad": ["bathroom", "bath"],
        "badet": ["bathroom", "bath"],
        "baderom": ["bathroom", "bath"],
        "kontor": ["office"],
        "kontoret": ["office"],
        "garasje": ["garage"],
        "garasjen": ["garage"],
        "entre": ["entry", "entrance", "hallway"],
        "entreen": ["entry", "entrance", "hallway"],
        "trapp": ["stair", "stairs", "stairway"],
        "trappen": ["stair", "stairs", "stairway"],
        "balkong": ["balcony"],
        "terrasse": ["terrace", "patio"],
        "hage": ["garden", "yard"],
        "kjellar": ["basement", "cellar"],
        "kjeller": ["basement", "cellar"],
        "loft": ["attic", "loft"],
        "vaskerom": ["laundry"],
        "bod": ["storage"],
        # Devices / objects
        "lys": ["light"],
        "lampe": ["lamp", "light"],
        "lampa": ["lamp", "light"],
        "bord": ["table"],
        "bordet": ["table"],
        "spisebord": ["dinner_table", "dining_table", "dinner"],
        "middag": ["dinner", "dining"],
        "tak": ["ceiling"],
        "taket": ["ceiling"],
        "gulv": ["floor"],
        "gulvet": ["floor"],
        "vindu": ["window"],
        "vinduet": ["window"],
        "dør": ["door"],
        "døren": ["door"],
        "sofa": ["sofa", "couch"],
        "tv": ["tv", "television"],
        "ute": ["outdoor", "outside", "exterior"],
        "inne": ["indoor", "inside", "interior"],
    }

    def __init__(self, url: str, token: str, timeout: float = 10.0):
        self._url = url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._entity_cache: list[dict] | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _fetch_entities(self) -> list[dict]:
        """Fetch and cache all relevant entities from HA."""
        if self._entity_cache is not None:
            return self._entity_cache
        try:
            resp = requests.get(
                f"{self._url}/api/states",
                headers=self._headers(),
                timeout=self._timeout,
            )
            resp.raise_for_status()
            entities = []
            for e in resp.json():
                eid = e["entity_id"]
                domain = eid.split(".")[0]
                if domain not in self._RELEVANT_DOMAINS:
                    continue
                name = e.get("attributes", {}).get("friendly_name", eid)
                state = e.get("state", "unknown")
                entities.append({
                    "entity_id": eid,
                    "name": name,
                    "state": state,
                    "domain": domain,
                })
            self._entity_cache = entities
            log.info("Cached %d HA entities", len(entities))
            return entities
        except requests.RequestException as exc:
            log.warning("Failed to fetch HA entities: %s", exc)
            return []

    def _expand_words(self, words: list[str]) -> list[str]:
        """Expand Norwegian words to include English equivalents for matching."""
        expanded = list(words)
        for w in words:
            translations = self._NO_EN_MAP.get(w, [])
            for t in translations:
                if t not in expanded:
                    expanded.append(t)
        return expanded

    def handle_list(self, params: dict) -> str:
        """Search for HA entities by query and/or domain.

        Uses word-level matching with Norwegian->English translation.
        Each word in the query is checked against entity_id and friendly_name.
        Results ranked by number of matching words.

        Params:
            query: Search term (matched against entity_id and friendly_name).
            domain: Filter by domain (e.g. "light", "sensor").
        """
        if not self._token:
            return "Home Assistant er ikke konfigurert (mangler token)."

        entities = self._fetch_entities()
        query = params.get("query", "").lower()
        domain = params.get("domain", "").lower()

        # Split query into individual search words (drop noise)
        _NOISE = {"på", "i", "om", "du", "får", "til", "av", "den", "det", "en", "et", "og", "er", "for"}
        words = [w for w in re.split(r'[\s,._\-&()]+', query) if w and w not in _NOISE and len(w) > 1]

        # Expand Norwegian words to English equivalents
        search_words = self._expand_words(words)
        log.info("ha_list search: query=%r words=%s expanded=%s", query, words, search_words)

        scored: list[tuple[int, dict]] = []
        for e in entities:
            if domain and e["domain"] != domain:
                continue
            if not search_words:
                scored.append((0, e))
                continue
            searchable = f"{e['entity_id']} {e['name']}".lower()
            hits = sum(1 for w in search_words if w in searchable)
            if hits > 0:
                scored.append((hits, e))

        # Sort by relevance (most matching words first, available before unavailable)
        scored.sort(key=lambda x: (x[0], x[1]["state"] != "unavailable"), reverse=True)
        matches = [e for _, e in scored]

        if not matches:
            return f"Ingen enheter funnet for '{query}' (domain={domain or 'alle'})."

        # Format compactly for token efficiency
        lines = []
        for e in matches[:30]:
            lines.append(f"- {e['entity_id']} ({e['name']}) [{e['state']}]")
        result = "\n".join(lines)
        if len(matches) > 30:
            result += f"\n... og {len(matches) - 30} til."
        return result

    def handle(self, params: dict) -> str:
        """Execute a Home Assistant API call and return human-readable result."""
        if not self._token:
            return "Home Assistant er ikke konfigurert (mangler token)."

        method = params.get("method", "GET").upper()
        path = params.get("path", "")
        body = params.get("body")

        try:
            resp = requests.request(
                method,
                f"{self._url}{path}",
                headers=self._headers(),
                json=body,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # Format state responses nicely
            if isinstance(data, dict) and "state" in data:
                attrs = data.get("attributes", {})
                name = attrs.get("friendly_name", data.get("entity_id", ""))
                unit = attrs.get("unit_of_measurement", "")
                return f"{name}: {data['state']}{' ' + unit if unit else ''}"

            return json.dumps(data, ensure_ascii=False)[:500]

        except requests.RequestException as exc:
            log.warning("HA API call failed: %s %s -> %s", method, path, exc)
            return f"Feil ved kontakt med Home Assistant: {exc}"
