import os
import yaml
import httpx
from pathlib import Path
from typing import Any, Dict

from kaare_core.config import get_service as _svc
from kaare_core.tools.i18n import t, get_lang

_HA_TOKEN_PATH = Path("/kaare/configs/ha_token.env")
_NODES_PATH = Path("/kaare/configs/nodes.yaml")
_ALIASES_PATH = "/kaare/configs/aliases.yaml"

HA_GATEWAY_URL = _svc("internal", "ha_gateway")

HA_TOOLS = {
    "les_ha",
    "les_alias_lista",
    "les_ha_status",
    "styr_enhet",
}

_DOMAIN_LABELS: Dict[str, str] = {
    "light":         "lys — kan styres (turn_on/turn_off/set_level/set_color_temp/set_color)",
    "switch":        "bryter — kan styres (turn_on/turn_off)",
    "climate":       "temperaturkontroll — kan styres",
    "media_player":  "mediaspiller — kan styres",
    "vacuum":        "støvsuger — kan styres",
    "cover":         "gardin/port — kan styres",
    "sensor":        "sensor — kun lesbar, bruk les_ha_status",
    "binary_sensor": "sensor — kun lesbar, bruk les_ha_status",
    "camera":        "kamera — ikke styrbar",
    "person":        "person — tilstedeværelse",
    "input_boolean": "bryter — kan styres",
}


def get_ha_token() -> str:
    token = os.environ.get("HA_TOKEN", "")
    if token:
        return token
    if _HA_TOKEN_PATH.exists():
        for line in _HA_TOKEN_PATH.read_text().splitlines():
            if line.startswith("HA_TOKEN="):
                token = line.split("=", 1)[1].strip()
                if token:
                    return token
    raise ValueError("HA_TOKEN not found — check configs/ha_token.env")


def resolve_node_entity(client_hint: str) -> str | None:
    """Return HA entity_id for a node matching client_hint (fuzzy, case-insensitive)."""
    if not _NODES_PATH.exists():
        return None
    nodes = yaml.safe_load(_NODES_PATH.read_text()).get("nodes", {})
    needle = client_hint.lower()
    for node_id, cfg in nodes.items():
        if needle == node_id.lower():
            return cfg.get("entity_id")
        if needle in node_id.lower():
            return cfg.get("entity_id")
        if needle in cfg.get("room", "").lower():
            return cfg.get("entity_id")
        if needle in cfg.get("description", "").lower():
            return cfg.get("entity_id")
    return None


def _load_aliases() -> Dict:
    try:
        with open(_ALIASES_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _format_aliases(room: str | None = None, lang: str = "nb") -> str:
    data = _load_aliases()
    aliases = data.get("aliases", {})
    rooms_cfg = data.get("rooms", {})

    if not aliases:
        return t("ha_no_devices", lang)

    def _label(entity_id: str) -> str:
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        return _DOMAIN_LABELS.get(domain, domain)

    room_kw: Dict[str, list] = {
        r: [str(k).lower() for k in kws]
        for r, kws in rooms_cfg.items()
    }

    groups: Dict[str, list] = {}
    unmatched: list = []
    for key in aliases:
        kl = key.lower()
        best_room, best_len = None, 0
        for r, kws in room_kw.items():
            for kw in kws:
                if kw in kl and len(kw) > best_len:
                    best_room, best_len = r, len(kw)
        if best_room:
            groups.setdefault(best_room, []).append(key)
        else:
            unmatched.append(key)

    if room:
        rl = room.lower()
        target, best_len = None, 0
        for r, kws in room_kw.items():
            for kw in kws:
                if kw == rl and len(kw) > best_len:
                    target, best_len = r, len(kw)
        if not target and rl in room_kw:
            target = rl
        if target and target in groups:
            lines = [t("ha_known_in_room", lang, room=room)]
            for k in groups[target]:
                eid = aliases[k]
                lines.append(f"  '{k}' → {eid}  [{_label(eid)}]")
            return "\n".join(lines)
        return t("ha_room_not_found", lang, room=room)

    known_rooms = sorted(groups.keys())
    lines = [
        t("ha_known_rooms", lang),
        ", ".join(r.replace("_", " ") for r in known_rooms),
        "",
        t("ha_room_hint", lang),
        t("ha_room_example", lang),
    ]
    return "\n".join(lines)


async def _control_entity(
    entity_id: str,
    action: str,
    brightness_pct: int | None = None,
    color_temp_kelvin: int | None = None,
    rgb_color: list | None = None,
    lang: str = "nb",
) -> str:
    if not entity_id or not action:
        return t("ha_entity_action_required", lang)

    params: Dict[str, Any] = {}
    if action == "set_level" and brightness_pct is not None:
        params["level"] = int(brightness_pct)
    elif action == "set_color_temp" and color_temp_kelvin is not None:
        params["color_temp_kelvin"] = int(color_temp_kelvin)
    elif action == "set_color" and rgb_color is not None:
        params["rgb_color"] = rgb_color

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                f"{HA_GATEWAY_URL}/api/ha_apply",
                json={"action": action, "entity_id": entity_id, "params": params, "source": "kare-tool"},
            )
            r.raise_for_status()
            data = r.json()
        if data.get("status") == "ok":
            if action == "turn_on":
                msg = t("ha_turned_on", lang, entity_id=entity_id)
            elif action == "turn_off":
                msg = t("ha_turned_off", lang, entity_id=entity_id)
            elif action == "set_level":
                msg = t("ha_brightness_set", lang, entity_id=entity_id, brightness_pct=brightness_pct)
            elif action == "set_color_temp":
                msg = t("ha_color_temp_set", lang, entity_id=entity_id, color_temp_kelvin=color_temp_kelvin)
            elif action == "set_color":
                msg = t("ha_color_set", lang, entity_id=entity_id)
            else:
                msg = t("ha_action_done", lang, entity_id=entity_id, action=action)
            return f"OK: {msg}"
        return t("ha_status_response", lang, status=data.get("status", "ukjent"))
    except Exception as e:
        return t("ha_call_error", lang, error=e)


async def _read_ha_status(entity_id: str, lang: str = "nb") -> str:
    if not entity_id:
        return t("ha_entity_id_required", lang)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{HA_GATEWAY_URL}/api/ha_status/{entity_id}")
            if r.status_code == 404:
                return t("ha_entity_not_found", lang, entity_id=entity_id)
            r.raise_for_status()
            data = r.json()
        state = data.get("state", "ukjent")
        unit = data.get("unit", "")
        friendly = data.get("friendly", entity_id)
        value = f"{state} {unit}".strip()
        return f"{friendly}: {value}"
    except Exception as e:
        return t("ha_read_error", lang, entity_id=entity_id, error=e)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "les_ha":
        action = arguments.get("action", "")
        if action == "rom_liste":
            return _format_aliases(None, lang=lang)
        if action == "rom_enheter":
            return _format_aliases(arguments.get("rom"), lang=lang)
        if action == "status":
            return await _read_ha_status(arguments.get("entity_id", ""), lang=lang)
        return f"Unknown action for les_ha: '{action}'. Valid: rom_liste, rom_enheter, status."

    if name == "les_alias_lista":
        return _format_aliases(arguments.get("rom"), lang=lang)

    if name == "les_ha_status":
        return await _read_ha_status(arguments.get("entity_id", ""), lang=lang)

    if name == "styr_enhet":
        if arguments.get("_block_ha_write"):
            return t("ha_blocked_external", lang)
        return await _control_entity(
            entity_id=arguments.get("entity_id", ""),
            action=arguments.get("action", ""),
            brightness_pct=arguments.get("brightness_pct"),
            color_temp_kelvin=arguments.get("color_temp_kelvin"),
            rgb_color=arguments.get("rgb_color"),
            lang=lang,
        )

    return f"[executor_ha] Unknown tool: {name}"
