"""API side of KTSB's own Cast (P139 phase 3): one call to the voice bridge's /cast/* or /display/* endpoint.

Never raises — a bridge that is down, a refused host or a device that does not answer all come back as
{"ok": False, "error": ...}, so the tool can say what happened (N26).
"""

import httpx

from kaare_core.config import get_service
from kaare_core.service_auth import service_headers


async def call(path: str, payload: dict, timeout: float = 60.0) -> dict:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{get_service('internal', 'voice_bridge')}{path}", json=payload, headers=service_headers()
            )
    except Exception as e:
        return {"ok": False, "error": f"voice bridge: {e}", "after": {}}
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code != 200:
        return {"ok": False, "error": str(data.get("detail") or f"HTTP {r.status_code}"), "after": {}}
    return data if isinstance(data, dict) else {"ok": False, "error": "bad reply", "after": {}}
