"""Full-screen pictures on Cast / DLNA screens through the voice bridge (P139 phase 3, D5: Cast lives in the bridge).

The API never talks Cast or DLNA itself: it sends the picture bytes to the bridge (`POST /display/show`), the bridge
serves them on its LAN address and hands the URL to the screen. A notice without a picture becomes a text card, so a
picture-only channel can still show it (and the node «Test» works there — BUG-log 2026-10-18 DLNA point 1).
"""

import base64
import io
import logging
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from adapters.display.base import BaseDisplayProvider
from kaare_core.config import get_service
from kaare_core.service_auth import service_headers

log = logging.getLogger(__name__)

_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
_FONT_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def _font(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.load_default(size=size)


def text_card(title: str, text: str, size: tuple[int, int] = (1280, 720)) -> bytes:
    """A plain 16:9 card with the title and the wrapped text — PNG bytes."""
    img = Image.new("RGB", size, (18, 18, 24))
    draw = ImageDraw.Draw(img)
    w = size[0]
    head, body = _font(_FONT_BOLD, 56), _font(_FONT, 40)
    draw.text((80, 80), title or "Kåre", font=head, fill=(235, 235, 245))
    lines: list[str] = []
    for para in (text or "").splitlines() or [""]:
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=body) > w - 160 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    y = 190
    for line in lines[:10]:
        draw.text((80, y), line, font=body, fill=(205, 205, 215))
        y += 56
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class BridgePictureProvider(BaseDisplayProvider):
    """One picture channel (`cast` or `dlna`) through the voice bridge."""

    channel = ""

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
        host = str(node_config.get("host") or "").strip()
        if not host:
            return {"ok": False, "error": "missing host"}
        mime = "image/png"
        if image_b64:
            payload = image_b64
        elif image_path:
            try:
                payload = base64.b64encode(Path(image_path).read_bytes()).decode()
                mime = _MIME.get(Path(image_path).suffix.lower(), "image/jpeg")
            except Exception as e:
                return {"ok": False, "error": f"cannot read picture: {e}"}
        else:
            payload = base64.b64encode(text_card(title, text)).decode()
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    f"{get_service('internal', 'voice_bridge')}/display/show",
                    json={"channel": self.channel, "host": host, "image_b64": payload, "mime": mime, "title": title,
                          "duration": int(duration or 0)},
                    headers=service_headers(),
                )
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        except Exception as e:
            return {"ok": False, "error": f"voice bridge: {e}"}
        if r.status_code != 200:
            return {"ok": False, "error": str(data.get("detail") or r.status_code)}
        if data.get("ok"):
            return {"ok": True, "method": self.channel, "after": data.get("after") or {}}
        out = {"ok": False, "error": str(data.get("error") or "not confirmed")}
        if data.get("busy"):
            out["busy"] = str(data["busy"])  # another app plays there — the registry tries no replacing channel
        return out
