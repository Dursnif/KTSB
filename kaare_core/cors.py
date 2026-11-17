"""CORS for the API (DT04 R0.8): who may call KTSB from a web page.

`cors_origins` in settings.yaml is a list. Besides exact origins and «*» (everyone — the user's explicit choice) it takes
the keyword «same-host»: a page may call the API when it was served from the same machine name or address the browser
used to reach the API. That is what KTSB's own frontend does on plain HTTP (http://<host>:5173 → http://<host>:8000);
behind HTTPS (Caddy) the page and the API share one origin and need no CORS at all. Any other website a household
browser happens to visit is refused.

«same-host» is the shipped default and the fallback when the setting is missing or unreadable (never «*»).
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from starlette.middleware.cors import CORSMiddleware

log = logging.getLogger(__name__)

SAME_HOST = "same-host"
DEFAULT = [SAME_HOST]
_request_host: ContextVar[str] = ContextVar("kaare_cors_request_host", default="")


def read_origins(settings_path: Path) -> list[str]:
    """The `cors_origins` list from settings.yaml; `DEFAULT` when missing, empty, not a list or unreadable."""
    try:
        data = yaml.safe_load(Path(settings_path).read_text(encoding="utf-8")) or {}
        value = data.get("cors_origins")
    except Exception as e:
        log.info("[CORS] settings unreadable (%s) — using %s", type(e).__name__, DEFAULT)
        return list(DEFAULT)
    if not isinstance(value, list) or not value:
        return list(DEFAULT)
    return [str(v).strip() for v in value if str(v).strip()] or list(DEFAULT)


def _hostname(netloc: str) -> str:
    try:
        return (urlsplit(f"//{netloc}").hostname or "").lower()
    except ValueError:
        return ""


class HostAwareCORSMiddleware(CORSMiddleware):
    """Starlette's CORS middleware plus the «same-host» rule (see the module text)."""

    def __init__(self, app, *, same_host: bool = False, **kwargs) -> None:
        super().__init__(app, **kwargs)
        self.same_host = same_host

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not self.same_host:
            await super().__call__(scope, receive, send)
            return
        host = next((v.decode("latin-1") for k, v in scope.get("headers") or [] if k == b"host"), "")
        token = _request_host.set(_hostname(host))
        try:
            await super().__call__(scope, receive, send)
        finally:
            _request_host.reset(token)

    def is_allowed_origin(self, origin: str) -> bool:
        if super().is_allowed_origin(origin):
            return True
        if not self.same_host:
            return False
        want = _request_host.get()
        try:
            got = (urlsplit(origin).hostname or "").lower()
        except ValueError:
            return False
        return bool(want) and got == want


def add_cors(app, origins: list[str]) -> None:
    """Install the CORS middleware for `origins` (exact origins, «*», and/or «same-host»). No cookies are used (JWT in
    a header), so credentials stay off."""
    listed = [o for o in origins if o != SAME_HOST]
    app.add_middleware(
        HostAwareCORSMiddleware,
        same_host=SAME_HOST in origins,
        allow_origins=listed,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
