"""Does HA have this entity, and is it available? (N26) — for paths that call HA directly instead of the gateway.

`POST /api/services/…` answers 200 for an entity HA does not have, so a direct `play_media` said «casting» to a
player that is not there. One `GET /api/states/<id>` first tells the truth (404 = no such entity).
"""

import httpx

from kaare_core.ha import action_truth as truth

OK = "ok"


async def entity_status(
    client: httpx.AsyncClient, ha_url: str, headers: dict, entity_id: str
) -> str:
    """OK, truth.NOT_FOUND, truth.UNAVAILABLE, or truth.ERROR (HA did not answer the lookup — callers go ahead as
    before rather than block on a failed check)."""
    try:
        r = await client.get(f"{ha_url.rstrip('/')}/api/states/{entity_id}", headers=headers)
    except Exception:
        return truth.ERROR
    if r.status_code == 404:
        return truth.NOT_FOUND
    if r.status_code >= 400:
        return truth.ERROR
    try:
        state = (r.json() or {}).get("state")
    except Exception:
        return truth.ERROR
    return truth.UNAVAILABLE if state == "unavailable" else OK
