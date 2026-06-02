import logging
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_NODES_PATH = Path("/kaare/configs/nodes.yaml")
_SERVICES_PATH = Path("/kaare/configs/services.yaml")
_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        from adapters.display.registry import DisplayProviderRegistry
        try:
            svc = yaml.safe_load(_SERVICES_PATH.read_text())
            media_base_url = svc.get("voice_bridge", {}).get("media_base_url", "http://127.0.0.1:8011/media")
        except Exception:
            media_base_url = "http://127.0.0.1:8011/media"
        _registry = DisplayProviderRegistry(media_base_url)
    return _registry


def _load_nodes() -> dict:
    try:
        return yaml.safe_load(_NODES_PATH.read_text()).get("nodes", {})
    except Exception:
        return {}


async def send_display(
    node_id: str,
    text: str,
    title: str = "Kåre",
    image_path: Optional[str] = None,
    image_b64: Optional[str] = None,
    duration: int = 8,
    position: str = "bottom_right",
) -> dict:
    nodes = _load_nodes()
    node = nodes.get(node_id)
    if not node:
        return {"ok": False, "error": f"Unknown node: {node_id}"}
    if not node.get("enabled", True):
        return {"ok": False, "error": f"Node {node_id} is disabled"}

    try:
        return await _get_registry().send(
            node_id, node,
            text=text, title=title,
            image_path=image_path, image_b64=image_b64,
            duration=duration, position=position,
        )
    except Exception as e:
        log.error("send_display error for %s: %s", node_id, e)
        return {"ok": False, "error": str(e)}


async def get_display_nodes() -> list[dict]:
    nodes = _load_nodes()
    result = []
    for nid, ncfg in nodes.items():
        if not ncfg.get("enabled", True):
            continue
        has_display = ncfg.get("has_display") or ncfg.get("is_tv", False)
        if has_display:
            result.append({"id": nid, **ncfg})
    return result
