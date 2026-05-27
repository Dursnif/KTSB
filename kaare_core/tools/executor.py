"""
Tool executor for Kåre's tool calls. Returns results as plain text
that is fed back to the LLM in the next round.
"""

import asyncio
import base64
import json as _json
import logging
import os
import subprocess as _sp
import time as _time
import yaml

logger = logging.getLogger(__name__)
import httpx
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo
from adapters.frigate_adapter import fetch_snapshot_b64, get_cameras, fetch_events, fetch_face_events
from adapters.plex_adapter import (
    get_sessions as _plex_get_sessions,
    get_history as _plex_get_history,
    search as _plex_search,
    get_libraries as _plex_get_libraries,
    get_children as _plex_get_children,
    get_clients as _plex_get_clients,
    play_on_client as _plex_play_on_client,
    get_metadata as _plex_get_metadata,
)
from adapters.llm_adapter import ask_llm
from adapters.web_search_adapter import søk_nett as _søk_nett
from adapters.yr_adapter import hent_yr_varsel as _hent_yr_varsel
from kaare_core.config import get_service as _svc, get_model as _cfg_model, get_llm_config as _llm_cfg
from kaare_core.memory.long_term import get_ltm, USER_GLOBAL
from kaare_core.tools.notisblokk import skriv_notat, les_notater, slett_notat, tøm_notater
from kaare_core.tools.lister import (
    handle_legg_til, handle_les, handle_merk_kjøpt, handle_slett, handle_tøm_kjøpte, handle_tøm,
    huske_husk, huske_les, huske_ferdig, huske_slett, huske_tøm,
    kare_husk, kare_les, kare_ferdig, kare_slett, kare_tøm,
)
from kaare_core.tools.shared_tools import (
    les_fil as _shared_les_fil,
    liste_filer as _shared_liste_filer,
    søk_kode as _shared_søk_kode,
    les_logg as _shared_les_logg,
    sjekk_tjenester as _shared_sjekk_tjenester,
    sjekk_ressurser as _shared_sjekk_ressurser,
    git_diff as _shared_git_diff,
    git_log as _shared_git_log,
)
from kaare_core.tools.think_cache import read_think_history, format_for_kare, log_think, extract_conclusion
from kaare_core.tools.timer_service import sett_timer, avbryt_timer, liste_timere

ALIASES_PATH        = "/kaare/configs/aliases.yaml"
HA_GATEWAY_URL      = _svc("internal", "ha_gateway")


def _local_tz() -> ZoneInfo:
    try:
        cfg = yaml.safe_load(_SETTINGS_PATH.read_text())
        loc = cfg.get("location") or cfg.get("lokasjon", {})
        return ZoneInfo(loc.get("timezone", "Europe/Oslo"))
    except Exception:
        return ZoneInfo("Europe/Oslo")


def _fmt_ts_local(ts_raw: str) -> str:
    """Konverter UTC ISO-tidsstempel til lokal tid for visning."""
    if not ts_raw:
        return "?"
    try:
        dt = datetime.fromisoformat(ts_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_local_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_raw[:16].replace("T", " ")
_VOICE_BRIDGE_URL   = _svc("internal", "voice_bridge")
_TOOL_LOG         = Path("/kaare/logs/tool_calls.log")
_SETTINGS_PATH    = Path("/kaare/configs/settings.yaml")
_NODES_PATH       = Path("/kaare/configs/nodes.yaml")
_HA_TOKEN_PATH    = Path("/kaare/configs/ha_token.env")
_OLLAMA_PROXY_URL = _llm_cfg("default")["base_url"]
_KARE_MODEL       = _cfg_model("kare")


def _get_ha_token() -> str:
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


def _resolve_node_entity(client_hint: str) -> str | None:
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


async def _ha_play_media(entity_id: str, content_type: str, content_id: str) -> str:
    """Call HA media_player.play_media via REST API."""
    import json as _json_mod
    ha_url = yaml.safe_load(Path("/kaare/configs/services.yaml").read_text())["home_assistant"]["url"]
    headers = {
        "Authorization": f"Bearer {_get_ha_token()}",
        "Content-Type": "application/json",
    }
    payload = {
        "entity_id": entity_id,
        "media_content_type": content_type,
        "media_content_id": content_id,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{ha_url}/api/services/media_player/play_media",
            headers=headers,
            json=payload,
        )
        if resp.status_code in (200, 201):
            return "ok"
        return f"HA {resp.status_code}: {resp.text[:200]}"


def _allowed_self_contributor(user_id: str) -> bool:
    """Returns True if user_id may write to personality_self.md."""
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8")) or {}
        ps = s.get("personality_self", {})
        mode = ps.get("contributors", "all")
        if mode == "all":
            return True
        if mode == "admin_only":
            from kaare_core.users.store import _user_store
            u = _user_store.get_user(user_id)
            return bool(u and u.get("role") == "admin")
        if mode == "selected":
            return user_id in ps.get("allowed_users", [])
        return True
    except Exception:
        return True  # fail open — never block Kåre due to a config error

def _load_personality_core() -> str:
    try:
        return Path("/kaare/configs/personality_core.md").read_text(encoding="utf-8").strip()
    except Exception:
        return ""

_PERSONALITY_CORE_TEXT = _load_personality_core()

def _build_lok_prefix() -> str:
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        lok = s.get("location") or s.get("lokasjon", {})
    except Exception:
        return ""
    city = lok.get("city") or lok.get("sted", "")
    country = lok.get("country") or lok.get("land", "")
    if city and country:
        return f"[Kontekst: {city}, {country}] "
    if country:
        return f"[Kontekst: {country}] "
    return ""

_LOK_PREFIX = _build_lok_prefix()

def _lok_prefix() -> str:
    """Returnerer f.eks. '[Kontekst: Gursken, Norge] ' (cachet ved oppstart)."""
    return _LOK_PREFIX


def _log_tool(name: str, arguments: Dict, result: str, duration_ms: int, source: str = "kare"):
    try:
        safe_args = {k: v for k, v in arguments.items() if not k.startswith("_")}
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "tool": name,
            "args": safe_args,
            "result_preview": str(result)[:120],
            "duration_ms": duration_ms,
        }
        with open(_TOOL_LOG, "a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _get_stm_history(dato: str | None = None) -> str:
    """Return a formatted STM snapshot for the given date, or list available dates."""
    try:
        hist_dir = Path(
            yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
            .get("stm", {})
            .get("history_dir", "/kaare/state/stm_history")
        )
    except Exception:
        hist_dir = Path("/kaare/state/stm_history")

    if not dato:
        if not hist_dir.exists():
            return "Ingen STM-historikk funnet ennå."
        files = sorted(hist_dir.glob("*.json"), reverse=True)
        if not files:
            return "Ingen daglige STM-snapshots lagret ennå."
        datoer = [f.stem for f in files[:14]]
        return "Tilgjengelige STM-datoer:\n" + "\n".join(f"  - {d}" for d in datoer)

    snap_path = hist_dir / f"{dato}.json"
    if not snap_path.exists():
        available = sorted(hist_dir.glob("*.json"), reverse=True) if hist_dir.exists() else []
        tip = ", ".join(f.stem for f in available[:5]) if available else "ingen"
        return f"Ingen STM-snapshot for {dato}. Tilgjengelige: {tip}"

    try:
        import json as _j
        data = _j.loads(snap_path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"Kunne ikke lese STM-snapshot for {dato}: {e}"

    parts = []
    saved_at = data.get("saved_at", "ukjent")[:16].replace("T", " ")
    parts.append(f"STM-snapshot for {dato} (lagret {saved_at} UTC):")

    if data.get("daily_summary"):
        parts.append(f"\nDaglig sammendrag:\n{data['daily_summary'][:500]}")

    dialog = data.get("dialog", [])
    if dialog:
        parts.append(f"\nDialog ({len(dialog)} turns, siste 20 vises):")
        for t in dialog[-20:]:
            role = t.get("role", "?")
            text = t.get("text", "")[:200]
            ts = t.get("ts", "")[:16].replace("T", " ")
            parts.append(f"  [{ts}] {role}: {text}")

    ok_actions = [a for a in data.get("actions", []) if a.get("ok")]
    if ok_actions:
        parts.append(f"\nHandlinger (siste 10 vellykkede av {len(ok_actions)}):")
        for a in ok_actions[-10:]:
            ts = a.get("ts", "")[:16].replace("T", " ")
            parts.append(f"  [{ts}] {a['action']} {a['entity_id']}")

    return "\n".join(parts)


def _les_indre_tanker() -> str:
    path = Path("/kaare/state/inner_thoughts.txt")
    if not path.exists() or not path.stat().st_size:
        return "Ingen indre tanker akkumulert ennå."
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception as e:
        return f"Kunne ikke lese indre tanker: {e}"
    try:
        path.unlink()  # kaare owns the dir — can delete even though stian owns the file
    except Exception:
        pass
    return content if content else "Ingen indre tanker akkumulert ennå."


def _les_refleksjon(dato: str | None = None) -> str:
    if dato:
        path = Path(f"/kaare/state/memory/reflections/{dato}.md")
        if not path.exists():
            available = sorted(Path("/kaare/state/memory/reflections").glob("*.md"))
            datoer = ", ".join(p.stem for p in available[-5:]) if available else "ingen"
            return f"Fant ingen refleksjon for {dato}. Tilgjengelige datoer: {datoer}."
    else:
        path = Path("/kaare/state/memory/reflection_latest.md")
        if not path.exists():
            return "Ingen refleksjonsfil funnet."
    try:
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > 6000:
            content = content[:6000] + "\n\n[… resten er kuttet]"
        return content
    except Exception as e:
        return f"Kunne ikke lese refleksjonsfil: {e}"


def _les_utviklingsmote(dato: str | None = None) -> str:
    if dato:
        path = Path(f"/kaare/state/memory/dev_meetings/{dato}.md")
        if not path.exists():
            available = sorted(Path("/kaare/state/memory/dev_meetings").glob("*.md"))
            datoer = ", ".join(p.stem for p in available[-5:]) if available else "ingen"
            return f"Fant ingen utviklingsmøte for {dato}. Tilgjengelige datoer: {datoer}."
    else:
        path = Path("/kaare/state/memory/dev_meeting_latest.md")
        if not path.exists():
            return "Ingen utviklingsmøtefil funnet."
    try:
        content = path.read_text(encoding="utf-8").strip()
        if len(content) > 6000:
            content = content[:6000] + "\n\n[… resten er kuttet]"
        return content
    except Exception as e:
        return f"Kunne ikke lese utviklingsmøtefil: {e}"


def _oppdater_selvbilde(observasjon: str) -> str:
    path = Path("/kaare/state/personality_self.md")
    if not observasjon.strip():
        return "Feil: observasjon kan ikke være tom."
    try:
        dato = datetime.now().strftime("%Y-%m-%d")
        entry = f"\n- [{dato}] {observasjon.strip()}"
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        return "Notert."
    except Exception as e:
        return f"Kunne ikke skrive til selvbilde: {e}"


def _slett_fra_selvbilde(fragment: str) -> str:
    path = Path("/kaare/state/personality_self.md")
    if not fragment.strip():
        return "Feil: fragment kan ikke være tomt."
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        ny = [l for l in lines if fragment.lower() not in l.lower()]
        antall = len(lines) - len(ny)
        if antall == 0:
            return f"Fant ingen linjer med '{fragment}'."
        path.write_text("".join(ny), encoding="utf-8")
        return f"{antall} linje(r) slettet."
    except Exception as e:
        return f"Kunne ikke slette fra selvbilde: {e}"


def _rediger_selvbilde(fragment: str, ny_tekst: str) -> str:
    path = Path("/kaare/state/personality_self.md")
    if not fragment.strip() or not ny_tekst.strip():
        return "Feil: både fragment og ny_tekst må fylles ut."
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        ny = []
        endret = 0
        dato = datetime.now().strftime("%Y-%m-%d")
        for l in lines:
            if fragment.lower() in l.lower():
                ny.append(f"- [{dato}] {ny_tekst.strip()}\n")
                endret += 1
            else:
                ny.append(l)
        if endret == 0:
            return f"Fant ingen linje med '{fragment}'."
        path.write_text("".join(ny), encoding="utf-8")
        return f"{endret} linje(r) oppdatert."
    except Exception as e:
        return f"Kunne ikke redigere selvbilde: {e}"



_WORLD_PATH = Path("/kaare/state/world.md")
_WORLD_VARS_PATH = Path("/kaare/state/world_vars.json")


def _les_world_vars() -> dict:
    if not _WORLD_VARS_PATH.exists():
        return {}
    try:
        data = _json.loads(_WORLD_VARS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _skriv_world_vars(data: dict) -> None:
    import os as _os
    tmp = _WORLD_VARS_PATH.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _os.replace(tmp, _WORLD_VARS_PATH)


def _verden_les_var(nokkel: str) -> str:
    vars_ = _les_world_vars()
    if not nokkel.strip():
        if not vars_:
            return "Ingen variabler lagret ennå."
        lines = ["Alle variabler:\n"]
        for k, v in sorted(vars_.items()):
            verdi = v.get("verdi", "") if isinstance(v, dict) else v
            besk = f" — {v['beskrivelse']}" if isinstance(v, dict) and v.get("beskrivelse") else ""
            lines.append(f"  {k}: {verdi}{besk}")
        return "\n".join(lines)
    k = nokkel.strip()
    if k not in vars_:
        return f"Ingen variabel med nøkkel '{k}'."
    v = vars_[k]
    if isinstance(v, dict):
        besk = f"\n  Beskrivelse: {v['beskrivelse']}" if v.get("beskrivelse") else ""
        av = f"\n  Satt av: {v['av']} ({v.get('satt', '')})" if v.get("av") else ""
        return f"{k}: {v.get('verdi', '')}{besk}{av}"
    return f"{k}: {v}"


def _verden_sett_var(nokkel: str, verdi: str, beskrivelse: str = "") -> str:
    if not nokkel.strip() or not verdi.strip():
        return "Feil: nokkel og verdi må fylles ut."
    vars_ = _les_world_vars()
    vars_[nokkel.strip()] = {
        "verdi": verdi.strip(),
        "beskrivelse": beskrivelse.strip(),
        "satt": datetime.now().strftime("%Y-%m-%d"),
        "av": "Kåre",
    }
    _skriv_world_vars(vars_)
    return f"Variabel satt: {nokkel.strip()} = {verdi.strip()}"


def _verden_slett_var(nokkel: str) -> str:
    if not nokkel.strip():
        return "Feil: nokkel mangler."
    vars_ = _les_world_vars()
    if nokkel.strip() not in vars_:
        return f"Fant ingen variabel med nøkkel '{nokkel}'."
    del vars_[nokkel.strip()]
    _skriv_world_vars(vars_)
    return f"Variabel '{nokkel}' slettet."


def _verden_liste_vars() -> str:
    vars_ = _les_world_vars()
    if not vars_:
        return "Ingen variabler lagret ennå."
    lines = [f"Variabler ({len(vars_)}):\n"]
    for k in sorted(vars_.keys()):
        v = vars_[k]
        verdi = v.get("verdi", "") if isinstance(v, dict) else v
        lines.append(f"  {k}: {verdi}")
    return "\n".join(lines)


def _les_verden() -> str:
    try:
        content = _WORLD_PATH.read_text(encoding="utf-8").strip()
        return content if content else "Verden-filen er tom."
    except Exception as e:
        return f"Kunne ikke lese verden-filen: {e}"


def _oppdater_felt_i_verden(kategori: str, felt: str, verdi: str) -> str:
    if not kategori.strip() or not felt.strip():
        return "Feil: kategori og felt må fylles ut."
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        kat_header = f"## {kategori.strip()}"
        felt_prefix = f"- {felt.strip()}:"
        ny_linje = f"- {felt.strip()}: {verdi.strip()}\n"

        kat_idx = next((i for i, l in enumerate(lines) if l.strip() == kat_header), None)

        if kat_idx is None:
            lines.append(f"\n{kat_header}\n")
            lines.append(ny_linje)
            _WORLD_PATH.write_text("".join(lines), encoding="utf-8")
            return f"Ny kategori '{kategori}' opprettet med felt '{felt}'."

        next_kat = next(
            (i for i in range(kat_idx + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        felt_idx = next(
            (i for i in range(kat_idx + 1, next_kat) if lines[i].strip().startswith(felt_prefix)),
            None,
        )

        if felt_idx is not None:
            lines[felt_idx] = ny_linje
        else:
            lines.insert(kat_idx + 1, ny_linje)

        _WORLD_PATH.write_text("".join(lines), encoding="utf-8")
        return f"Oppdatert: {felt} = {verdi}"
    except Exception as e:
        return f"Kunne ikke oppdatere verden-filen: {e}"


def _legg_til_i_verden(kategori: str, tekst: str) -> str:
    if not tekst.strip():
        return "Feil: tekst kan ikke være tom."
    kat = (kategori.strip() or "Notes")
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        kat_header = f"## {kat}"
        entry = f"{tekst.strip()}\n"

        kat_idx = next((i for i, l in enumerate(lines) if l.strip() == kat_header), None)

        if kat_idx is None:
            lines.append(f"\n{kat_header}\n")
            lines.append(entry)
        else:
            insert_at = next(
                (i for i in range(kat_idx + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            lines.insert(insert_at, entry)

        _WORLD_PATH.write_text("".join(lines), encoding="utf-8")
        return "Lagt til."
    except Exception as e:
        return f"Kunne ikke skrive til verden-filen: {e}"


def _slett_fra_verden(fragment: str) -> str:
    if not fragment.strip():
        return "Feil: fragment kan ikke være tomt."
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        ny = [l for l in lines if fragment.lower() not in l.lower()]
        antall = len(lines) - len(ny)
        if antall == 0:
            return f"Fant ingen linjer med '{fragment}'."
        _WORLD_PATH.write_text("".join(ny), encoding="utf-8")
        return f"{antall} linje(r) slettet."
    except Exception as e:
        return f"Kunne ikke slette fra verden-filen: {e}"


def _rediger_verden(fragment: str, ny_tekst: str) -> str:
    if not fragment.strip() or not ny_tekst.strip():
        return "Feil: både fragment og ny_tekst må fylles ut."
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        ny = []
        endret = 0
        for l in lines:
            if fragment.lower() in l.lower():
                ny.append(f"{ny_tekst.strip()}\n")
                endret += 1
            else:
                ny.append(l)
        if endret == 0:
            return f"Fant ingen linje med '{fragment}'."
        _WORLD_PATH.write_text("".join(ny), encoding="utf-8")
        return f"{endret} linje(r) oppdatert."
    except Exception as e:
        return f"Kunne ikke redigere verden-filen: {e}"


def _oppdater_nysgjerrighet(nysgjerrighet: str, user_id: str) -> str:
    if not user_id or user_id == "global":
        return "Ingen innlogget bruker."
    if not nysgjerrighet.strip():
        return "Feil: nysgjerrighet kan ikke være tom."
    try:
        from kaare_core.users.profile_manager import update_nested_profile_field
        update_nested_profile_field(user_id, "prompt_top", "curiosity", nysgjerrighet.strip())
        return "Nysgjerrighet oppdatert."
    except Exception as e:
        return f"Kunne ikke oppdatere nysgjerrighet: {e}"


def _oppdater_brukerprofil(observasjon: str, user_id: str) -> str:
    if not observasjon.strip():
        return "Feil: observasjon kan ikke være tom."
    if not user_id or user_id == "global":
        return "Ingen innlogget bruker — kan ikke lagre brukerobservasjon."
    try:
        from kaare_core.users.profile_manager import add_observation
        add_observation(user_id, observasjon.strip())
        return "Notert."
    except Exception as e:
        return f"Kunne ikke skrive til brukerprofil: {e}"


def _sett_profilfelt(seksjon: str, felt: str, verdi: str, user_id: str) -> str:
    if not user_id or user_id == "global":
        return "Ingen innlogget bruker — kan ikke oppdatere profil."
    if not seksjon.strip() or not felt.strip():
        return "Feil: seksjon og felt må fylles ut."
    try:
        from kaare_core.users.profile_manager import update_nested_profile_field
        return update_nested_profile_field(user_id, seksjon.strip(), felt.strip(), verdi)
    except Exception as e:
        return f"Kunne ikke oppdatere profilfelt: {e}"


def _les_brukerprofil(user_id: str) -> str:
    if not user_id or user_id == "global":
        return "Ingen innlogget bruker."
    try:
        from kaare_core.users.profile_manager import read_profile_yaml_as_text
        yaml_content = read_profile_yaml_as_text(user_id)
    except Exception:
        yaml_content = ""
    obs_path = Path(f"/kaare/state/users/{user_id}/observations.md")
    obs_content = ""
    if obs_path.exists():
        try:
            obs_content = obs_path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    parts = []
    if yaml_content and yaml_content != "Ingen profildata registrert ennå.":
        parts.append(f"=== PROFIL (profile.yaml) ===\n{yaml_content}")
    if obs_content:
        if len(obs_content) > 6000:
            obs_content = obs_content[:6000] + "\n\n[… resten er kuttet]"
        parts.append(f"=== OBSERVASJONER (observations.md) ===\n{obs_content}")
    return "\n\n".join(parts) if parts else "Ingen profildata registrert ennå."


def _slett_fra_brukerprofil(fragment: str, user_id: str) -> str:
    if not fragment.strip():
        return "Feil: fragment kan ikke være tomt."
    if not user_id or user_id == "global":
        return "Ingen innlogget bruker."
    path = Path(f"/kaare/state/users/{user_id}/observations.md")
    if not path.exists():
        return "Ingen observasjoner å slette fra."
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        ny = [l for l in lines if fragment.lower() not in l.lower()]
        antall = len(lines) - len(ny)
        if antall == 0:
            return f"Fant ingen linjer med '{fragment}'."
        path.write_text("".join(ny), encoding="utf-8")
        return f"{antall} linje(r) slettet."
    except Exception as e:
        return f"Kunne ikke slette fra brukerprofil: {e}"


def _rediger_brukerprofil(fragment: str, ny_tekst: str, user_id: str) -> str:
    if not fragment.strip() or not ny_tekst.strip():
        return "Feil: både fragment og ny_tekst må fylles ut."
    if not user_id or user_id == "global":
        return "Ingen innlogget bruker."
    path = Path(f"/kaare/state/users/{user_id}/observations.md")
    if not path.exists():
        return "Ingen observasjoner å redigere."
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        ny = []
        endret = 0
        dato = datetime.now().strftime("%Y-%m-%d")
        for l in lines:
            if fragment.lower() in l.lower():
                ny.append(f"[{dato}] {ny_tekst.strip()}\n")
                endret += 1
            else:
                ny.append(l)
        if endret == 0:
            return f"Fant ingen linje med '{fragment}'."
        path.write_text("".join(ny), encoding="utf-8")
        return f"{endret} linje(r) oppdatert."
    except Exception as e:
        return f"Kunne ikke redigere brukerprofil: {e}"


def _søk_i_minne(spørsmål: str) -> str:
    if not spørsmål.strip():
        return "Feil: spørsmål kan ikke være tomt."
    try:
        hits = get_ltm().search_interactions(spørsmål, limit=6)
        if not hits:
            return f"Fant ingenting i minnet om '{spørsmål}'."
        lines = [f"Fant {len(hits)} relevante episoder fra minnet:\n"]
        for h in hits:
            ts    = h["ts"][:16].replace("T", " ")
            prompt_short = h["prompt"][:80].replace("\n", " ")
            resp_short   = h["response"][:80].replace("\n", " ")
            entity = f" [{h['entity_id']}]" if h["entity_id"] else ""
            lines.append(
                f"- {ts}{entity}\n"
                f"  Bruker: {prompt_short}\n"
                f"  Kåre: {resp_short}\n"
                f"  Utfall: {h['outcome']} | Tillit: {h['confidence']}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Søk i minne feilet: {e}"

def _bekreft_interaksjoner(ids, verdict: str, user_id: str) -> str:
    if not ids:
        return "Ingen IDer oppgitt."
    uid = user_id if user_id else USER_GLOBAL
    try:
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.strip("[] ").split(",") if x.strip()]
        ids_int = [int(i) for i in ids]
        updated = get_ltm().mark_interactions(ids=ids_int, verdict=verdict, user_id=uid)
        label = {"verified": "bekreftet ✓", "denied": "avvist ✗", "test": "merket som test 🧪"}
        return f"{updated} interaksjon(er) {label.get(verdict, verdict)}: {ids_int}"
    except Exception as e:
        return f"Kunne ikke merke interaksjoner: {e}"


def _hent_ubekreftede(user_id: str, limit: int = 10, offset: int = 0) -> str:
    uid = user_id if user_id else USER_GLOBAL
    try:
        rows = get_ltm().get_unverified_interactions(user_id=uid, limit=limit, offset=offset)
        if not rows:
            return "Ingen ubekreftede interaksjoner funnet — du er à jour!"
        total_note = f" (viser {offset + 1}–{offset + len(rows)})"
        lines = [f"Ubekreftede interaksjoner{total_note}:"]
        for r in rows:
            ts = r["ts"][:10]
            prompt_short = r["prompt"][:100].replace("\n", " ")
            resp_short = r["response"][:150].replace("\n", " ")
            lines.append(
                f"\n[ID {r['id']} | {ts}]\n"
                f"  Du: {prompt_short}\n"
                f"  Kåre: {resp_short}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"Kunne ikke hente ubekreftede interaksjoner: {e}"


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



def _load_yaml() -> Dict:
    try:
        with open(ALIASES_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _format_aliases(rom: str | None = None) -> str:
    data      = _load_yaml()
    aliases   = data.get("aliases", {})
    rooms_cfg = data.get("rooms", {})

    if not aliases:
        return "Kjenner ikke til noen enheter ennå."

    def _label(entity_id: str) -> str:
        domain = entity_id.split(".")[0] if "." in entity_id else ""
        return _DOMAIN_LABELS.get(domain, domain)

    room_kw: Dict[str, list] = {
        r: [str(k).lower() for k in kws]
        for r, kws in rooms_cfg.items()
    }

    # sort longest-first so "kjellerstue" beats "stue" as the room match
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

    if rom:
        rl = rom.lower()
        target, best_len = None, 0
        for r, kws in room_kw.items():
            for kw in kws:
                if kw == rl and len(kw) > best_len:
                    target, best_len = r, len(kw)
        # fallback: exact room name match
        if not target and rl in room_kw:
            target = rl
        if target and target in groups:
            lines = [f"Kjenner til følgende i {rom}:"]
            for k in groups[target]:
                eid = aliases[k]
                lines.append(f"  '{k}' → {eid}  [{_label(eid)}]")
            return "\n".join(lines)
        return f"Kjenner ikke til noe rom som heter '{rom}'."

    # call again with rom='<room name>' to list devices in a specific room
    known_rooms = sorted(groups.keys())
    lines = [
        "Kjenner til følgende rom i huset:",
        ", ".join(r.replace("_", " ") for r in known_rooms),
        "",
        "Kall les_alias_lista med rom='<romnavn>' for å se enheter i et rom.",
        "Eksempel: les_alias_lista(rom='ute') for uteenheter.",
    ]
    return "\n".join(lines)



async def _styr_enhet(
    entity_id: str,
    action: str,
    brightness_pct: int | None = None,
    color_temp_kelvin: int | None = None,
    rgb_color: list | None = None,
) -> str:
    if not entity_id or not action:
        return "Feil: entity_id og action er påkrevd."

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
                msg = f"'{entity_id}' skrudd på."
            elif action == "turn_off":
                msg = f"'{entity_id}' skrudd av."
            elif action == "set_level":
                msg = f"'{entity_id}' lysstyrke satt til {brightness_pct}%."
            elif action == "set_color_temp":
                msg = f"'{entity_id}' fargetemperatur satt til {color_temp_kelvin}K."
            elif action == "set_color":
                msg = f"'{entity_id}' farge satt."
            else:
                msg = f"'{entity_id}' {action} utført."
            return f"OK: {msg}"
        return f"HA svarte med status: {data.get('status', 'ukjent')}."
    except Exception as e:
        return f"Feil ved HA-kall: {e}"



async def _les_ha_status(entity_id: str) -> str:
    if not entity_id:
        return "Feil: entity_id er påkrevd."
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{HA_GATEWAY_URL}/api/ha_status/{entity_id}")
            if r.status_code == 404:
                return f"Entitet '{entity_id}' ikke funnet i Home Assistant."
            r.raise_for_status()
            data = r.json()
        state   = data.get("state", "ukjent")
        unit    = data.get("unit", "")
        friendly = data.get("friendly", entity_id)
        verdi = f"{state} {unit}".strip()
        return f"{friendly}: {verdi}"
    except Exception as e:
        return f"Feil ved lesing av '{entity_id}': {e}"


async def _søk_i_argus(spørsmål: str, grense: int = 8) -> str:
    if not spørsmål.strip():
        return "Feil: spørsmål kan ikke være tomt."
    grense = max(1, min(grense, 20))
    qdrant_url = _svc("storage", "qdrant")
    embed_url  = _svc("ollama", "embed") + "/api/embed"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            emb_r = await client.post(embed_url, json={"model": "bge-m3", "input": spørsmål}, timeout=15.0)
            emb_r.raise_for_status()
            vector = emb_r.json()["embeddings"][0]
            r = await client.post(
                f"{qdrant_url}/collections/argus_events/points/query",
                json={"query": vector, "limit": grense, "with_payload": True},
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"Argus utilgjengelig: {e}"
    hits = data.get("result", {}).get("points", [])
    if not hits:
        return f"Fant ingen hendelser for '{spørsmål}' i systemloggen."
    lines = [f"Fant {len(hits)} hendelser i systemloggen:\n"]
    for h in hits:
        f = h.get("payload", {})
        ts   = _fmt_ts_local(f.get("ts", ""))
        msg   = f.get("message", "")
        level = f.get("level", "info")
        prefix = "⚠️ " if level == "warning" else "❌ " if level == "error" else ""
        lines.append(f"[{ts}] {prefix}{msg}")
    return "\n".join(lines)


async def _spør_pettersmart(oppgave: str, arguments: Dict) -> str:
    """Kaller agents-serveren (port 11450) og lar Pettersmart løse en teknisk oppgave."""
    if not oppgave.strip():
        return "Feil: oppgave kan ikke være tom."
    if not _llm_cfg("pettersmart").get("enabled", True):
        return "Pettersmart er deaktivert. Aktiver den under Innstillinger → LLM/Modeller."
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/pettersmart",
                json={"task": oppgave},
            )
            r.raise_for_status()
            svar = r.json().get("answer", "").strip()
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="pettersmart",
                    query=oppgave,
                    response=svar,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                ))
            except Exception:
                pass
            return svar if svar else "Pettersmart fant ingenting."
    except Exception as e:
        return f"Pettersmart ikke tilgjengelig: {e}"


async def _spør_frøken_library_online(spørsmål: str, arguments: Dict) -> str:
    """Calls Miss Library's cloud endpoint — large online model, no wiki lookup."""
    if not spørsmål.strip():
        return "Feil: spørsmål kan ikke være tomt."
    if not _llm_cfg("cloud").get("enabled", True):
        return "Online LLM er deaktivert. Aktiver den under Innstillinger → LLM → Sky-modell."
    if not _llm_cfg("library").get("enabled", True):
        return "Frøken Library er deaktivert. Aktiver den under Innstillinger → LLM/Modeller."
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library/cloud",
                json={"question": spørsmål},
            )
            r.raise_for_status()
            svar = r.json().get("answer", "").strip()
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library_online",
                    query=spørsmål,
                    response=svar,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                ))
            except Exception:
                pass
            return svar if svar else "Frøken Library Online fant ingen svar."
    except Exception as e:
        return f"Frøken Library Online ikke tilgjengelig: {e}"


async def _spør_frøken_library(spørsmål: str, arguments: Dict) -> str:
    """Kaller agents-serveren (port 11450) og ber Frøken Library svare."""
    if not spørsmål.strip():
        return "Feil: spørsmål kan ikke være tomt."
    if not _llm_cfg("library").get("enabled", True):
        return "Frøken Library er deaktivert. Aktiver den under Innstillinger → LLM/Modeller."
    lok = _lok_prefix()
    spørsmål_med_kontekst = f"{lok}{spørsmål}" if lok else spørsmål
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library",
                json={"question": spørsmål_med_kontekst},
            )
            r.raise_for_status()
            svar = r.json().get("answer", "").strip()
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library",
                    query=spørsmål,
                    response=svar,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                ))
            except Exception:
                pass
            return svar if svar else "Frøken Library fant ingenting."
    except Exception as e:
        return f"Frøken Library ikke tilgjengelig: {e}"


async def _hent_wiki_artikkel(title: str, max_chars: int = 8000) -> str:
    """Fetch full article text from local wiki via agents-server."""
    if not title.strip():
        return "Feil: tittel kan ikke være tom."
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/wiki/article",
                json={"title": title, "max_chars": max_chars},
            )
            r.raise_for_status()
            data = r.json()
        if not data.get("text"):
            return f"Ingen artikkel funnet med tittelen «{title}»."
        return f"# {data['title']} ({data['chunk_count']} biter)\n\n{data['text']}"
    except Exception as e:
        return f"Kunne ikke hente artikkel: {e}"


async def _hent_url_via_library(url: str, arguments: Dict) -> str:
    """Fetch a specific URL (trusted domains only) and let Miss Library summarize it."""
    if not url.strip():
        return "Feil: url kan ikke være tom."
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library/hent_url",
                json={"url": url},
            )
            r.raise_for_status()
            svar = r.json().get("answer", "").strip()
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library",
                query=url,
                response=svar,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return svar if svar else "Frøken Library fant ingenting på den adressen."
    except Exception as e:
        return f"Kunne ikke hente URL: {e}"


async def _reason_freely(query: str) -> str:
    """
    Calls Kåre's own model with a minimal system prompt — no smarthus constraints.
    think: true is passed explicitly so the model reasons before answering.
    The think block is logged to think_cache; only the final answer is returned.
    """
    if not query.strip():
        return "Feil: query kan ikke være tomt."
    rf_cfg = _llm_cfg("reason_freely")
    system = (
        _PERSONALITY_CORE_TEXT
        + "\n\n---\n\n"
        "Du bruker nå din fulle interne kunnskap fritt. "
        "Ingen smarthus-begrensninger gjelder her. "
        "Tenk åpent og presist basert på det du vet fra treningen. "
        "Dette er et internt verktøykall — svaret integreres i din vanlige respons til brukeren."
    )
    try:
        base_url = rf_cfg.get("base_url", _OLLAMA_PROXY_URL)
        options  = rf_cfg.get("options", {})
        provider = rf_cfg.get("provider", "ollama")

        if provider == "vllm":
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": query},
            ]
            payload: dict = {
                "model":    _KARE_MODEL,
                "messages": messages,
                "stream":   False,
            }
            for src, dst in (("max_tokens", "max_tokens"), ("temperature", "temperature"),
                             ("top_p", "top_p"), ("presence_penalty", "presence_penalty")):
                if src in options:
                    payload[dst] = options[src]
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{base_url}/v1/chat/completions",
                    json=payload,
                    headers={"x-kaare-source": "reason_freely"},
                )
                r.raise_for_status()
                data = r.json()
            msg        = (data.get("choices") or [{}])[0].get("message", {})
            think_text = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
            text       = (msg.get("content") or "").strip()
        else:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{base_url}/api/generate",
                    json={
                        "model":   _KARE_MODEL,
                        "prompt":  query,
                        "system":  system,
                        "stream":  False,
                        "think":   rf_cfg.get("think", True),
                        "options": options,
                    },
                    headers={"x-kaare-source": "reason_freely"},
                )
                r.raise_for_status()
                data = r.json()
            think_text = (data.get("thinking") or "").strip()
            text       = (data.get("response") or "").strip()

            # Strip inline <think> tags if model embeds them in response field
            if not think_text and "<think>" in text.lower():
                upper = text.upper()
                t_start = upper.find("<THINK>")
                t_end   = upper.find("</THINK>")
                if t_start != -1 and t_end != -1:
                    think_text = text[t_start + len("<THINK>"):t_end].strip()
                    text = text[t_end + len("</THINK>"):].strip()

        if think_text:
            try:
                log_think(
                    think_text=think_text,
                    response=text,
                    role="reason_freely",
                    model=_KARE_MODEL,
                    prompt_preview=query[:200],
                    recovered=not bool(text),
                )
            except Exception:
                pass

        if not text and think_text:
            text = extract_conclusion(think_text)

        return text if text else "Fikk tomt svar fra modellen."
    except Exception as e:
        return f"reason_freely feilet: {e}"


def _load_radio_stations() -> list[dict]:
    path = Path("/kaare/configs/radio_stations.yaml")
    try:
        return yaml.safe_load(path.read_text()).get("stations", [])
    except Exception:
        return []


def _resolve_station(name_or_url: str) -> str | None:
    """Resolve a station name/alias to a stream URL. Returns None if not found."""
    if name_or_url.startswith(("http://", "https://")):
        return name_or_url
    needle = name_or_url.lower().strip()
    for station in _load_radio_stations():
        if needle == station.get("name", "").lower():
            return station["url"]
        for alias in station.get("aliases", []):
            if needle == alias.lower():
                return station["url"]
    return None


async def _media(arguments: Dict[str, Any]) -> str:
    action = arguments.get("action", "")

    if action == "plex_sessions":
        return await _plex_get_sessions()

    if action == "plex_history":
        return await _plex_get_history(
            user=arguments.get("user"),
            limit=int(arguments.get("limit") or 20),
        )

    if action == "plex_search":
        query = arguments.get("query", "").strip()
        if not query:
            return "Mangler søketekst (query)."
        return await _plex_search(query)

    if action == "plex_library":
        return await _plex_get_libraries()

    if action == "plex_episodes":
        key = arguments.get("rating_key", "").strip()
        if not key:
            return "Mangler rating_key. Søk med plex_search først for å finne id."
        return await _plex_get_children(key)

    if action == "plex_clients":
        return await _plex_get_clients()

    if action == "plex_play":
        client = arguments.get("client", "").strip()
        key = arguments.get("rating_key", "").strip()
        if not client:
            return "Mangler 'client' — oppgi rom/enhetsnavn (f.eks. 'verksted'). Sjekk nodes.yaml for gyldige navn."
        if not key:
            return "Mangler 'rating_key' — hent episode-id med plex_episodes først."
        offset_s = int(arguments.get("offset") or 0)
        resume = bool(arguments.get("resume", False))

        # Resolve Cast entity from nodes.yaml
        entity_id = _resolve_node_entity(client)
        if not entity_id:
            return f"Fant ingen node/enhet som matcher «{client}» i nodes.yaml."

        # Fetch Plex metadata to build the plex:// content ID
        try:
            meta = await _plex_get_metadata(key)
        except Exception as exc:
            return f"Kunne ikke hente Plex-metadata for id {key}: {exc}"

        if not meta:
            return f"Ingen Plex-metadata funnet for ratingKey {key}."

        item_type = meta.get("type", "")
        library_name = meta.get("library_name", "")

        if item_type == "episode":
            content: dict = {"library_name": library_name, "show_name": meta["show_name"]}
            if meta.get("season_number") is not None:
                content["season_number"] = meta["season_number"]
            if meta.get("episode_number") is not None:
                content["episode_number"] = meta["episode_number"]
            ha_type = "episode"
            label = f"{meta['show_name']} S{meta.get('season_number','?'):02d}E{meta.get('episode_number','?'):02d}"
        elif item_type == "movie":
            content = {"library_name": library_name, "title": meta["title"]}
            ha_type = "movie"
            label = meta["title"]
        else:
            return f"Støtter ikke medietypen «{item_type}» ennå (kun episode og movie)."

        if resume:
            content["resume"] = True
        if offset_s > 0:
            content["offset"] = offset_s

        content_id = f"plex://{_json.dumps(content, ensure_ascii=False)}"

        try:
            result = await _ha_play_media(entity_id, ha_type, content_id)
        except Exception as exc:
            return f"Feil ved HA cast: {exc}"

        if result == "ok":
            return f"▶ Caster «{label}» til {entity_id} ✅"
        return f"HA svarte: {result}"

    if action == "radio_status":
        try:
            result = _sp.run(
                ["mpc", "status"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return "MPD svarer ikke — radioen er sannsynligvis ikke i gang."
            current = _sp.run(["mpc", "current"], capture_output=True, text=True, timeout=5)
            lines = result.stdout.strip().splitlines()
            status_line = next((l for l in lines if "[" in l), lines[0] if lines else "")
            current_title = current.stdout.strip()
            if current_title:
                return f"Radio: {current_title}\n{status_line}"
            return "\n".join(lines)
        except Exception as exc:
            return f"Feil ved lesing av radio-status: {exc}"

    if action == "radio_play":
        station_input = arguments.get("station", "").strip()
        if not station_input:
            return "Mangler 'station' — oppgi navn (f.eks. 'P4', 'NRK P1') eller stream-URL."
        url = _resolve_station(station_input)
        if not url:
            known = ", ".join(s["name"] for s in _load_radio_stations())
            return f"Ukjent stasjon «{station_input}». Kjente stasjoner: {known}."
        try:
            # Ensure MPD is running
            _sp.run(["mpd", f"{Path.home()}/.mpdconf"], capture_output=True, timeout=5)
            _sp.run(["mpc", "clear"], capture_output=True, timeout=5)
            _sp.run(["mpc", "add", url], capture_output=True, text=True, timeout=5)
            play_result = _sp.run(["mpc", "play"], capture_output=True, text=True, timeout=5)
            station_name = next(
                (s["name"] for s in _load_radio_stations()
                 if s["url"] == url),
                station_input,
            )
            if play_result.returncode == 0:
                return f"Spiller nå: {station_name}"
            return f"Klarte ikke å starte {station_name}: {play_result.stderr.strip()}"
        except Exception as exc:
            return f"Feil ved oppstart av radio: {exc}"

    if action == "radio_stop":
        try:
            _sp.run(["mpc", "stop"], capture_output=True, timeout=5)
            return "Radioen er stoppet."
        except Exception as exc:
            return f"Feil ved stopp av radio: {exc}"

    if action == "radio_volume":
        vol = arguments.get("volume")
        if vol is None:
            return "Mangler 'volume' (0–100)."
        vol = max(0, min(100, int(vol)))
        try:
            _sp.run(["mpc", "volume", str(vol)], capture_output=True, timeout=5)
            return f"Radiovolum satt til {vol}%."
        except Exception as exc:
            return f"Feil ved endring av volum: {exc}"

    return f"Ukjent media-action: '{action}'."


async def _dispatch(name: str, arguments: Dict[str, Any]) -> str:
    """Dispatcher — kalles fra execute_tool. Returnerer alltid streng."""

    if name == "les_ha":
        action = arguments.get("action", "")
        if action == "rom_liste":
            return _format_aliases(None)
        if action == "rom_enheter":
            return _format_aliases(arguments.get("rom"))
        if action == "status":
            return await _les_ha_status(arguments.get("entity_id", ""))
        return f"Unknown action for les_ha: '{action}'. Valid: rom_liste, rom_enheter, status."

    # backward-compat shim — remove when all callers use les_ha instead
    if name == "les_alias_lista":
        return _format_aliases(arguments.get("rom"))

    if name == "les_ha_status":
        return await _les_ha_status(arguments.get("entity_id", ""))

    if name == "styr_enhet":
        if arguments.get("_block_ha_write"):
            return "Smarthus-kontroll er ikke tilgjengelig eksternt for din bruker."
        return await _styr_enhet(
            entity_id=arguments.get("entity_id", ""),
            action=arguments.get("action", ""),
            brightness_pct=arguments.get("brightness_pct"),
            color_temp_kelvin=arguments.get("color_temp_kelvin"),
            rgb_color=arguments.get("rgb_color"),
        )

    if name == "søk_nett":
        raw_query = arguments.get("query", "")
        lok = _lok_prefix()
        query = f"{lok}{raw_query}" if lok else raw_query
        response = await _søk_nett(query)
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library",
                query=query,
                response=response,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return response

    if name == "library":
        action = arguments.get("action", "")
        if action == "søk":
            return await _spør_frøken_library(arguments.get("spørsmål", ""), arguments)
        if action == "hent_artikkel":
            return await _hent_wiki_artikkel(
                arguments.get("title", ""),
                arguments.get("max_chars", 8000),
            )
        if action == "hent_url":
            return await _hent_url_via_library(arguments.get("url", ""), arguments)
        if action == "online":
            return await _spør_frøken_library_online(arguments.get("spørsmål", ""), arguments)
        return f"Unknown action for library: '{action}'. Valid: søk, hent_artikkel, hent_url, online."

    if name == "timer":
        action = arguments.get("action", "")
        if action == "klokke":
            now = datetime.now()
            return f"Klokka er {now.strftime('%H:%M')}. Dato: {now.strftime('%d.%m.%Y')}."
        if action == "sett":
            return sett_timer(
                prompt=arguments.get("prompt", ""),
                in_seconds=int(arguments.get("in_seconds", 0)),
                notify=bool(arguments.get("notify", True)),
                repeat=arguments.get("repeat") or None,
                at_time=arguments.get("at_time") or None,
            )
        if action == "avbryt":
            return avbryt_timer(arguments.get("timer_id", ""))
        if action == "liste":
            return liste_timere()
        return f"Unknown action for timer: '{action}'. Valid: klokke, sett, avbryt, liste."

    if name == "minne":
        action = arguments.get("action", "")
        if action == "søk":
            return _søk_i_minne(arguments.get("spørsmål", ""))
        if action == "hent_ubekreftede":
            return _hent_ubekreftede(
                user_id=arguments.get("_user_id", ""),
                limit=min(int(arguments.get("antall", 10)), 20),
                offset=int(arguments.get("hopp_over", 0)),
            )
        if action == "bekreft":
            return _bekreft_interaksjoner(
                ids=arguments.get("ids", []),
                verdict=arguments.get("dom", "verified"),
                user_id=arguments.get("_user_id", ""),
            )
        if action == "hent_stm":
            return _get_stm_history(arguments.get("dato"))
        return f"Unknown action for minne: '{action}'. Valid: søk, hent_ubekreftede, bekreft, hent_stm."

    if name == "pettersmart":
        action = arguments.get("action", "")
        if action == "søk":
            search_type = arguments.get("type", "filer")
            spørsmål    = arguments.get("spørsmål", "Oppsummer innholdet.")
            if not _llm_cfg("pettersmart").get("enabled", True):
                return "Pettersmart er deaktivert. Aktiver den under Innstillinger → LLM/Modeller."
            raw_filer = arguments.get("filer", [])
            if isinstance(raw_filer, str):
                try:
                    raw_filer = _json.loads(raw_filer)
                except Exception:
                    raw_filer = []
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    r = await client.post(
                        f"{_svc('internal', 'agents')}/ask/pettersmart/søk",
                        json={
                            "search_type": search_type,
                            "files":       raw_filer,
                            "from_line":   arguments.get("fra_linje"),
                            "to_line":     arguments.get("til_linje"),
                            "pattern":     arguments.get("mønster", ""),
                            "directory":   arguments.get("mappe", "/kaare"),
                            "service":     arguments.get("tjeneste", ""),
                            "log_file":    arguments.get("logg_fil", ""),
                            "lines":       arguments.get("linjer", 100),
                            "log_filter":  arguments.get("filter", ""),
                            "question":    spørsmål,
                        },
                    )
                    r.raise_for_status()
                    svar = r.json().get("answer", "").strip()
                try:
                    asyncio.create_task(get_ltm().log_agent_message(
                        from_agent="kare", to_agent="pettersmart",
                        query=spørsmål, response=svar,
                        rid=arguments.get("_rid", ""),
                        user_id=arguments.get("_user_id", "global"),
                    ))
                except Exception:
                    pass
                return svar or "Pettersmart fant ingenting."
            except Exception as e:
                return f"Pettersmart søk feilet: {e}"
        if action == "deleger":
            oppgave = arguments.get("oppgave", "").strip()
            if not oppgave:
                return "Error: oppgave cannot be empty."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(
                        f"{_svc('internal', 'agents')}/jobs/pettersmart",
                        json={"task": oppgave},
                    )
                    r.raise_for_status()
                    data = r.json()
                job_id = data.get("job_id", "")
                status = data.get("status", "")
                if status == "error":
                    return f"Could not start job: {data.get('result', 'unknown error')}"
                return (
                    f"Job started. job_id={job_id}\n"
                    f"Pettersmart is working in the background. "
                    f"Monitor with ssh_kommando/local_kommando, "
                    f"then poll with pettersmart(action='svar', job_id='{job_id}')."
                )
            except Exception as e:
                return f"pettersmart deleger failed: {e}"
        if action == "svar":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return "Error: job_id is required."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(
                        f"{_svc('internal', 'agents')}/jobs/pettersmart/{job_id}",
                    )
                    r.raise_for_status()
                    data = r.json()
                status = data.get("status", "unknown")
                result = data.get("result")
                if status == "running":
                    return f"[Job {job_id[:8]}…] Still running — check again in a moment."
                return f"[Job {job_id[:8]}…] {status.upper()}\n{result or '(no output)'}"
            except Exception as e:
                return f"pettersmart svar failed: {e}"
        if action == "avbryt":
            job_id = arguments.get("job_id", "").strip()
            if not job_id:
                return "Error: job_id is required."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.delete(
                        f"{_svc('internal', 'agents')}/jobs/pettersmart/{job_id}",
                    )
                    r.raise_for_status()
                    data = r.json()
                status = data.get("status", "unknown")
                return (
                    f"[Job {job_id[:8]}…] Cancellation sent. Status: {status}. "
                    "Pettersmart's current LLM call is being aborted."
                )
            except Exception as e:
                return f"pettersmart avbryt failed: {e}"
        if action == "kommenter":
            job_id = arguments.get("job_id", "").strip()
            comment = arguments.get("comment", "").strip()
            if not job_id or not comment:
                return "Error: job_id and comment are required."
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.patch(
                        f"{_svc('internal', 'agents')}/jobs/pettersmart/{job_id}",
                        json={"comment": comment},
                    )
                    r.raise_for_status()
                    data = r.json()
                if "not running" in (data.get("result") or ""):
                    return f"[Job {job_id[:8]}…] Job is no longer running — comment not delivered."
                return f"[Job {job_id[:8]}…] Comment queued. Pettersmart will see it at the next tool round."
            except Exception as e:
                return f"pettersmart kommenter failed: {e}"
        return f"Unknown action for pettersmart: '{action}'. Valid: søk, deleger, svar, avbryt, kommenter."

    if name == "selvbilde":
        action = arguments.get("action", "")
        if action == "les":
            try:
                content = Path("/kaare/state/personality_self.md").read_text(encoding="utf-8").strip()
                return content if content else "Selvbilde-filen er tom."
            except Exception as e:
                return f"Kunne ikke lese selvbilde-filen: {e}"
        if action == "oppdater":
            if _allowed_self_contributor(arguments.get("_user_id", "")):
                return _oppdater_selvbilde(arguments.get("observasjon", ""))
            return "Notert."
        if action == "rediger":
            if _allowed_self_contributor(arguments.get("_user_id", "")):
                return _rediger_selvbilde(
                    fragment=arguments.get("fragment", ""),
                    ny_tekst=arguments.get("ny_tekst", ""),
                )
            return "Notert."
        if action == "slett":
            if _allowed_self_contributor(arguments.get("_user_id", "")):
                return _slett_fra_selvbilde(arguments.get("fragment", ""))
            return "Notert."
        return f"Unknown action for selvbilde: '{action}'. Valid: les, oppdater, rediger, slett."

    if name == "verden":
        action = arguments.get("action", "")
        if action == "les":
            return _les_verden()
        if action == "oppdater_felt":
            return _oppdater_felt_i_verden(
                kategori=arguments.get("kategori", ""),
                felt=arguments.get("felt", ""),
                verdi=arguments.get("verdi", ""),
            )
        if action == "legg_til":
            return _legg_til_i_verden(
                kategori=arguments.get("kategori", ""),
                tekst=arguments.get("tekst", ""),
            )
        if action == "slett":
            return _slett_fra_verden(arguments.get("fragment", ""))
        if action == "rediger":
            return _rediger_verden(
                fragment=arguments.get("fragment", ""),
                ny_tekst=arguments.get("ny_tekst", ""),
            )
        if action == "les_var":
            return _verden_les_var(arguments.get("nokkel", ""))
        if action == "sett_var":
            return _verden_sett_var(
                nokkel=arguments.get("nokkel", ""),
                verdi=arguments.get("verdi", ""),
                beskrivelse=arguments.get("beskrivelse", ""),
            )
        if action == "slett_var":
            return _verden_slett_var(arguments.get("nokkel", ""))
        if action == "liste_vars":
            return _verden_liste_vars()
        return f"Unknown action for verden: '{action}'. Valid: les, oppdater_felt, legg_til, slett, rediger, les_var, sett_var, slett_var, liste_vars."

    if name == "brukerprofil":
        action = arguments.get("action", "")
        user_id = arguments.get("_user_id", "global")
        if action == "les":
            return _les_brukerprofil(user_id=user_id)
        if action == "oppdater":
            return _oppdater_brukerprofil(
                observasjon=arguments.get("observasjon", ""),
                user_id=user_id,
            )
        if action == "sett_felt":
            return _sett_profilfelt(
                seksjon=arguments.get("seksjon", ""),
                felt=arguments.get("felt", ""),
                verdi=arguments.get("verdi", ""),
                user_id=user_id,
            )
        if action == "rediger":
            return _rediger_brukerprofil(
                fragment=arguments.get("fragment", ""),
                ny_tekst=arguments.get("ny_tekst", ""),
                user_id=user_id,
            )
        if action == "slett":
            return _slett_fra_brukerprofil(
                fragment=arguments.get("fragment", ""),
                user_id=user_id,
            )
        if action == "nysgjerrighet":
            return _oppdater_nysgjerrighet(
                nysgjerrighet=arguments.get("nysgjerrighet", ""),
                user_id=user_id,
            )
        if action == "oppdater_hus":
            felt = arguments.get("felt", "")
            verdi = arguments.get("verdi", "")
            if not felt or not verdi:
                return "oppdater_hus krever 'felt' og 'verdi'."
            try:
                from kaare_core.users.profile_manager import update_household_visible
                result = update_household_visible(user_id=user_id, field=felt, value=verdi)
                try:
                    from adapters.llm_adapter import reload_config
                    reload_config()
                except Exception:
                    pass
                # Log to global LTM so nightjob picks it up in _compress_global_events
                try:
                    import asyncio as _asyncio
                    _ltm = get_ltm()
                    _summary = f"Hus-oppdatering: {felt} = {verdi} (bruker: {user_id})"
                    _asyncio.get_event_loop().create_task(
                        _ltm.log_interaction(
                            user_id=USER_GLOBAL,
                            prompt=_summary,
                            source="oppdater_hus",
                            response=result,
                        )
                    )
                except Exception:
                    pass
                return result
            except Exception as e:
                return f"Feil ved oppdatering av hus-profil: {e}"
        return f"Unknown action for brukerprofil: '{action}'. Valid: les, oppdater, oppdater_hus, sett_felt, rediger, slett, nysgjerrighet."

    if name == "notat":
        action = arguments.get("action", "")
        liste = arguments.get("liste", "arkitekt")
        user_id = arguments.get("_user_id", "global")

        if liste == "handle":
            if action in ("skriv", "legg_til"):
                return handle_legg_til(
                    tekst=arguments.get("tekst", ""),
                    mengde=arguments.get("mengde", ""),
                    enhet=arguments.get("enhet", ""),
                    lagt_til_av=user_id,
                )
            if action == "les":
                return handle_les()
            if action == "merk_kjøpt":
                return handle_merk_kjøpt(arguments.get("notat_id", ""))
            if action == "slett":
                return handle_slett(arguments.get("notat_id", ""))
            if action in ("tøm", "tøm_kjøpte"):
                return handle_tøm_kjøpte()
            if action == "tøm_alt":
                return handle_tøm()
            return f"Unknown action for handle-liste: '{action}'. Valid: skriv, les, merk_kjøpt, slett, tøm, tøm_alt."

        if liste == "huske":
            if action in ("skriv", "husk"):
                return huske_husk(
                    tekst=arguments.get("tekst", ""),
                    user_id=user_id,
                    påminn_ved_login=bool(arguments.get("påminn_ved_login", False)),
                )
            if action == "les":
                return huske_les(user_id=user_id)
            if action == "ferdig":
                return huske_ferdig(arguments.get("notat_id", ""), user_id=user_id)
            if action == "slett":
                return huske_slett(arguments.get("notat_id", ""), user_id=user_id)
            if action == "tøm":
                return huske_tøm(user_id=user_id)
            return f"Unknown action for huskeliste: '{action}'. Valid: skriv, les, ferdig, slett, tøm."

        if liste == "kare":
            if action in ("skriv", "husk"):
                return kare_husk(
                    tekst=arguments.get("tekst", ""),
                    kontekst=arguments.get("kontekst", ""),
                )
            if action == "les":
                return kare_les()
            if action in ("ferdig", "slett"):
                return kare_ferdig(arguments.get("notat_id", ""))
            if action == "tøm":
                return kare_tøm()
            return f"Unknown action for kare-liste: '{action}'. Valid: skriv, les, ferdig, slett, tøm."

        if action == "skriv":
            return skriv_notat(
                tekst=arguments.get("tekst", ""),
                kategori=arguments.get("kategori", "diverse"),
            )
        if action == "les":
            return les_notater(arguments.get("kategori"))
        if action == "slett":
            return slett_notat(arguments.get("notat_id", ""))
        if action == "tøm":
            return tøm_notater(arguments.get("kategori"))
        return f"Unknown action for notat: '{action}'. Valid: skriv, les, slett, tøm."

    if name == "utforsk_kode":
        action = arguments.get("action", "")
        if action == "les":
            return _shared_les_fil(arguments)
        if action == "liste":
            return _shared_liste_filer(arguments)
        if action == "søk":
            return _shared_søk_kode(arguments)
        return f"Unknown action for utforsk_kode: '{action}'. Valid: les, liste, søk."

    if name == "inspiser_system":
        action = arguments.get("action", "")
        if action == "logg":
            return _shared_les_logg(arguments)
        if action == "tjenester":
            return _shared_sjekk_tjenester(arguments)
        if action == "ressurser":
            return _shared_sjekk_ressurser(arguments)
        if action == "git_diff":
            return _shared_git_diff(arguments)
        if action == "git_log":
            return _shared_git_log(arguments)
        return f"Unknown action for inspiser_system: '{action}'. Valid: logg, tjenester, ressurser, git_diff, git_log."

    if name == "kamera":
        action = arguments.get("action", "")

        if action == "snapshot":
            scope = arguments.get("scope", "ett")
            ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            if scope == "alle":
                spørsmål = arguments.get("spørsmål", "").strip() or (
                    "Du ser bilder fra flere overvåkningskameraer. "
                    "Beskriv hvert bilde kort: hva skjer, er det personer, kjøretøy eller hendelser? "
                    "Angi kameranavnet i svaret."
                )
                cams = await get_cameras()
                if not cams:
                    return "Ingen kameraer funnet i Frigate."

                async def _fetch(cam):
                    try:
                        b64 = await fetch_snapshot_b64(cam["api_name"])
                        return cam["friendly_name"], b64
                    except Exception:
                        return cam["friendly_name"], None

                fetched = await asyncio.gather(*[_fetch(c) for c in cams])
                images = [b64 for _, b64 in fetched if b64]
                cam_names = [n for n, b64 in fetched if b64]
                failed = [n for n, b64 in fetched if b64 is None]
                if not images:
                    return f"Klarte ikke hente bilder fra noen kameraer ({ts})."
                prompt = f"Kameraer: {', '.join(cam_names)}.\n{spørsmål}"
                try:
                    vlm_result = await ask_llm(prompt=prompt, images=images)
                    description = vlm_result.get("text", "").strip() or "Fikk tomt svar fra bildeanalyse."
                except Exception as e:
                    return f"Bilder hentet ({ts}), men analyse feilet: {e}"
                result = f"[Alle kameraer — {ts}]\n{description}"
                if failed:
                    result += f"\n\nKunne ikke hente: {', '.join(failed)}"
                return result
            kamera_navn = arguments.get("kamera", "").strip()
            spørsmål = arguments.get("spørsmål", "").strip() or "Beskriv hva du ser på bildet. Nevn personer, kjøretøy, dyr og eventuelle hendelser."
            if not kamera_navn:
                cams = await get_cameras()
                cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
                return f"Oppgi kameranavn. Tilgjengelige kameraer: {cam_list}"
            try:
                img_b64 = await fetch_snapshot_b64(kamera_navn)
            except ValueError as e:
                cams = await get_cameras()
                cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
                return f"Kamera ikke funnet: {e}\nTilgjengelige: {cam_list}"
            except Exception as e:
                return f"Kunne ikke hente snapshot fra '{kamera_navn}': {e}"
            try:
                vlm_result = await ask_llm(prompt=spørsmål, images=[img_b64])
                description = vlm_result.get("text", "").strip() or "Fikk tomt svar fra bildeanalyse."
            except Exception as e:
                return f"Snapshot hentet ({ts}), men bildeanalyse feilet: {e}"
            return f"[{kamera_navn} — {ts}]\n{description}"

        if action == "hendelser":
            navn_filter = (arguments.get("navn") or "").strip().lower()
            timer_tilbake = min(int(arguments.get("timer_tilbake", 24)), 48)
            face_path = Path("/kaare/state/argus/face_events.txt")
            if not face_path.exists():
                return "Ingen kamerahendelser registrert ennå."
            try:
                raw_lines = [l for l in face_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            except Exception as e:
                return f"Kunne ikke lese kamerahendelser: {e}"
            if not raw_lines:
                return "Ingen kamerahendelser registrert ennå."
            try:
                cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
                _loc = cfg.get("location") or cfg.get("lokasjon", {})
                local_tz = ZoneInfo(_loc.get("timezone") or _loc.get("tidssone", "Europe/Oslo"))
            except Exception:
                local_tz = ZoneInfo("Europe/Oslo")
            cutoff = datetime.now(local_tz) - timedelta(hours=timer_tilbake)
            filtered = []
            for line in raw_lines:
                try:
                    inner = line[1: line.index("]")]
                    ts_part = inner.split("→")[0].strip()
                    dt = datetime.strptime(ts_part, "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
                    if dt < cutoff:
                        continue
                except Exception:
                    pass
                if navn_filter and navn_filter not in line.lower():
                    continue
                filtered.append(line)
            if not filtered:
                label = f" for «{arguments['navn']}»" if arguments.get("navn") else ""
                return f"Ingen kamerahendelser{label} funnet siste {timer_tilbake} timer."
            header = f"Kamerahendelser siste {timer_tilbake} timer ({len(filtered)} hendelse{'r' if len(filtered) != 1 else ''}):"
            return header + "\n" + "\n".join(filtered)

        if action == "frigate":
            kamera_navn = arguments.get("kamera", "").strip() or None
            label = arguments.get("label", "").strip() or None
            antall = min(int(arguments.get("antall", 10)), 50)
            kun_ansikter = bool(arguments.get("kun_ansikter", False))
            try:
                if kun_ansikter:
                    events = await fetch_face_events(limit=antall)
                else:
                    events = await fetch_events(camera=kamera_navn, label=label, limit=antall)
            except Exception as e:
                return f"Kunne ikke hente Frigate-hendelser: {e}"
            if not events:
                return "Ingen hendelser funnet."
            lines = []
            for ev in events:
                ts_raw = ev.get("start_time") or ev.get("ts", 0)
                try:
                    ts_str = datetime.fromtimestamp(float(ts_raw)).strftime("%d.%m %H:%M:%S")
                except Exception:
                    ts_str = str(ts_raw)
                cam = ev.get("camera_friendly") or ev.get("camera", "?")
                lbl = ev.get("label", "?")
                conf = ev.get("top_score") or ev.get("score") or ev.get("confidence") or 0
                face = ev.get("_face_name") or ev.get("sub_label") or ""
                face_str = f" — ansikt: {face}" if face else ""
                lines.append(f"[{ts_str}] {cam}: {lbl} ({int(float(conf)*100)}%){face_str}")
            return "\n".join(lines)

        if action == "liste":
            try:
                cams = await get_cameras()
            except Exception as e:
                return f"Kunne ikke hente kameraliste fra Frigate: {e}"
            if not cams:
                return "Ingen kameraer funnet."
            lines = [f"  {c['friendly_name']} → {c['api_name']}" for c in cams]
            return f"Kameraer ({len(cams)}):\n" + "\n".join(lines)

        if action == "analyser":
            antall = min(int(arguments.get("antall", 10)), 50)
            log_path = Path("/kaare/logs/frigate_analysis.log")
            if not log_path.exists():
                return "Ingen analyserte kamerahendelser funnet ennå."
            try:
                raw_lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                entries = [_json.loads(l) for l in raw_lines][-antall:]
            except Exception as e:
                return f"Kunne ikke lese analyse-logg: {e}"
            if not entries:
                return "Ingen analyserte hendelser funnet i loggen."
            parts = []
            for e in reversed(entries):
                ts = (e.get("ts") or "")[:16].replace("T", " ")
                cam = e.get("display_name") or e.get("camera", "?")
                label = e.get("label", "?")
                dur = float(e.get("duration", 0))
                sub = f" — {e['sub_label']}" if e.get("sub_label") else ""
                analysis = e.get("analysis", "")
                eid = e.get("event_id", "")
                parts.append(f"[{ts}] {cam} — {label}{sub} ({dur:.0f}s)\n{analysis}\nevent_id: {eid}")
            return "\n\n---\n\n".join(parts)

        if action == "vis_hendelse":
            event_id = arguments.get("event_id", "").strip()
            if not event_id:
                return "vis_hendelse krever 'event_id'. Bruk action='analyser' for å se tilgjengelige event_id-er."
            snap_path = Path("/kaare/state/frigate_snapshots") / f"{event_id}.jpg"
            if not snap_path.exists():
                return (
                    f"Ingen lagret snapshot funnet for event_id '{event_id}'. "
                    f"Bildet er kanskje for gammelt eller lagring er deaktivert."
                )
            try:
                img_bytes = snap_path.read_bytes()
                img_b64 = base64.b64encode(img_bytes).decode()
            except Exception as e:
                return f"Kunne ikke lese snapshot: {e}"

            # Copy snapshot to user's image folder so it can be displayed in chat
            _img_url = ""
            try:
                from kaare_core.image_store import save_image as _save_image
                _uid = arguments.get("_user_id", "global")
                _img_id = _save_image(img_bytes, _uid, "input", ext="jpg")
                _img_url = f"/api/image/{_img_id}"
            except Exception as _e:
                logger.warning("[vis_hendelse] save_image feilet for %s (user=%s): %s",
                               event_id, arguments.get("_user_id"), _e)
            # Fallback: serve directly from Frigate snapshots if save failed
            if not _img_url:
                _img_url = f"/api/frigate_snapshot/{event_id}"

            stored_analysis = ""
            try:
                log_path = Path("/kaare/logs/frigate_analysis.log")
                for line in log_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    entry = _json.loads(line)
                    if entry.get("event_id") == event_id:
                        stored_analysis = entry.get("analysis", "")
                        break
            except Exception:
                pass
            context = f"Du ser et lagret Frigate-kameraopptak (event_id: {event_id})."
            if stored_analysis:
                context += f"\n\nDen automatiske analysen sa:\n{stored_analysis}"
            context += "\n\nBeskriv hva du ser på bildet og om den lagrede analysen stemmer. Svar på norsk."
            spørsmål = arguments.get("spørsmål", "").strip() or context
            try:
                result = await ask_llm(spørsmål, images=[img_b64])
                analysis = result.get("text", "").strip() or "Fikk tomt svar fra VLM."
            except Exception as e:
                return f"VLM-analyse feilet: {e}"
            if _img_url:
                return f"{analysis}\n\nBildet er klart: {_img_url}"
            return analysis

        return f"Unknown action for kamera: '{action}'. Valid: snapshot, hendelser, frigate, liste, analyser, vis_hendelse."

    if name == "hent_yr_varsel":
        return await _hent_yr_varsel(arguments.get("sted"))

    if name == "hent_klokke":
        now = datetime.now()
        return f"Klokka er {now.strftime('%H:%M')}. Dato: {now.strftime('%d.%m.%Y')}."

    if name == "les_møte":
        meeting_type = arguments.get("type", "refleksjon")
        dato = arguments.get("dato")
        if meeting_type == "utvikling":
            return _les_utviklingsmote(dato)
        return _les_refleksjon(dato)

    if name == "les_indre_tanker":
        return _les_indre_tanker()

    if name == "søk_i_minne":
        return _søk_i_minne(arguments.get("spørsmål", ""))

    if name == "bekreft_interaksjoner":
        return _bekreft_interaksjoner(
            ids=arguments.get("ids", []),
            verdict=arguments.get("dom", "verified"),
            user_id=arguments.get("_user_id", ""),
        )

    if name == "hent_ubekreftede":
        return _hent_ubekreftede(
            user_id=arguments.get("_user_id", ""),
            limit=min(int(arguments.get("antall", 10)), 20),
            offset=int(arguments.get("hopp_over", 0)),
        )

    if name == "søk_i_argus":
        return await _søk_i_argus(
            spørsmål=arguments.get("spørsmål", ""),
            grense=int(arguments.get("grense", 8)),
        )

    if name == "spør_pettersmart":
        return await _spør_pettersmart(arguments.get("oppgave", ""), arguments)

    if name == "deleger_til_pettersmart":
        oppgave = arguments.get("oppgave", "").strip()
        if not oppgave:
            return "Error: oppgave cannot be empty."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{_svc('internal', 'agents')}/jobs/pettersmart",
                    json={"task": oppgave},
                )
                r.raise_for_status()
                data = r.json()
            job_id = data.get("job_id", "")
            status = data.get("status", "")
            if status == "error":
                return f"Could not start job: {data.get('result', 'unknown error')}"
            return (
                f"Job started. job_id={job_id}\n"
                f"Pettersmart is working in the background. "
                f"Monitor with ssh_kommando/local_kommando, then poll with hent_pettersmart_svar(job_id='{job_id}')."
            )
        except Exception as e:
            return f"deleger_til_pettersmart failed: {e}"

    if name == "hent_pettersmart_svar":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return "Error: job_id is required."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{_svc('internal', 'agents')}/jobs/pettersmart/{job_id}",
                )
                r.raise_for_status()
                data = r.json()
            status = data.get("status", "unknown")
            result = data.get("result")
            if status == "running":
                return f"[Job {job_id[:8]}…] Still running — check again in a moment."
            return f"[Job {job_id[:8]}…] {status.upper()}\n{result or '(no output)'}"
        except Exception as e:
            return f"hent_pettersmart_svar failed: {e}"

    if name == "avbryt_pettersmart":
        job_id = arguments.get("job_id", "").strip()
        if not job_id:
            return "Error: job_id is required."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.delete(
                    f"{_svc('internal', 'agents')}/jobs/pettersmart/{job_id}",
                )
                r.raise_for_status()
                data = r.json()
            status = data.get("status", "unknown")
            return (
                f"[Job {job_id[:8]}…] Cancellation sent. "
                f"Status: {status}. "
                "Pettersmart's current LLM call is being aborted — Ollama will stop generating."
            )
        except Exception as e:
            return f"avbryt_pettersmart failed: {e}"

    if name == "kommenter_pettersmart":
        job_id = arguments.get("job_id", "").strip()
        comment = arguments.get("comment", "").strip()
        if not job_id or not comment:
            return "Error: job_id and comment are required."
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.patch(
                    f"{_svc('internal', 'agents')}/jobs/pettersmart/{job_id}",
                    json={"comment": comment},
                )
                r.raise_for_status()
                data = r.json()
            if "not running" in (data.get("result") or ""):
                return f"[Job {job_id[:8]}…] Job is no longer running — comment not delivered."
            return f"[Job {job_id[:8]}…] Comment queued. Pettersmart will see it at the next tool round."
        except Exception as e:
            return f"kommenter_pettersmart failed: {e}"

    if name == "restart_docker_container":
        container = arguments.get("container", "").strip()
        _ALLOWED_CONTAINERS = ("ollama-kare", "ollama-miss_kare", "ollama-library")
        if container not in _ALLOWED_CONTAINERS:
            return (
                f"[Rejected: '{container}' is not in the allowed list. "
                f"Allowed: {', '.join(_ALLOWED_CONTAINERS)}]"
            )
        try:
            result = _sp.run(
                ["docker", "restart", container],
                capture_output=True, text=True, timeout=30,
            )
            out = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                return (
                    f"Container '{container}' restarted. "
                    "Model reload takes ~3.5 minutes before it is ready again."
                )
            return f"docker restart failed (rc={result.returncode}): {out}"
        except Exception as e:
            return f"restart_docker_container failed: {e}"

    if name == "spør_frøken_library_online":
        return await _spør_frøken_library_online(arguments.get("spørsmål", ""), arguments)

    if name == "spør_frøken_library":
        return await _spør_frøken_library(arguments.get("spørsmål", ""), arguments)

    if name == "hent_wiki_artikkel":
        return await _hent_wiki_artikkel(
            arguments.get("title", ""),
            arguments.get("max_chars", 8000),
        )

    if name == "reason_freely":
        return await _reason_freely(arguments.get("query", ""))

    if name == "les_selvbilde":
        try:
            content = Path("/kaare/state/personality_self.md").read_text(encoding="utf-8").strip()
            return content if content else "Selvbilde-filen er tom."
        except Exception as e:
            return f"Kunne ikke lese selvbilde-filen: {e}"

    if name == "slett_fra_selvbilde":
        if _allowed_self_contributor(arguments.get("_user_id", "")):
            return _slett_fra_selvbilde(arguments.get("fragment", ""))
        return "Notert."

    if name == "rediger_selvbilde":
        if _allowed_self_contributor(arguments.get("_user_id", "")):
            return _rediger_selvbilde(
                fragment=arguments.get("fragment", ""),
                ny_tekst=arguments.get("ny_tekst", ""),
            )
        return "Notert."

    if name == "oppdater_selvbilde":
        if _allowed_self_contributor(arguments.get("_user_id", "")):
            return _oppdater_selvbilde(arguments.get("observasjon", ""))
        return "Notert."

    if name == "les_verden":
        return _les_verden()

    if name == "oppdater_felt_i_verden":
        return _oppdater_felt_i_verden(
            kategori=arguments.get("kategori", ""),
            felt=arguments.get("felt", ""),
            verdi=arguments.get("verdi", ""),
        )

    if name == "legg_til_i_verden":
        return _legg_til_i_verden(
            kategori=arguments.get("kategori", ""),
            tekst=arguments.get("tekst", ""),
        )

    if name == "slett_fra_verden":
        return _slett_fra_verden(arguments.get("fragment", ""))

    if name == "rediger_verden":
        return _rediger_verden(
            fragment=arguments.get("fragment", ""),
            ny_tekst=arguments.get("ny_tekst", ""),
        )

    if name == "oppdater_nysgjerrighet":
        return _oppdater_nysgjerrighet(
            nysgjerrighet=arguments.get("nysgjerrighet", ""),
            user_id=arguments.get("_user_id", "global"),
        )

    if name == "oppdater_brukerprofil":
        return _oppdater_brukerprofil(
            observasjon=arguments.get("observasjon", ""),
            user_id=arguments.get("_user_id", "global"),
        )

    if name == "les_brukerprofil":
        return _les_brukerprofil(user_id=arguments.get("_user_id", "global"))

    if name == "sett_profilfelt":
        return _sett_profilfelt(
            seksjon=arguments.get("seksjon", ""),
            felt=arguments.get("felt", ""),
            verdi=arguments.get("verdi", ""),
            user_id=arguments.get("_user_id", "global"),
        )

    if name == "slett_fra_brukerprofil":
        return _slett_fra_brukerprofil(
            fragment=arguments.get("fragment", ""),
            user_id=arguments.get("_user_id", "global"),
        )

    if name == "rediger_brukerprofil":
        return _rediger_brukerprofil(
            fragment=arguments.get("fragment", ""),
            ny_tekst=arguments.get("ny_tekst", ""),
            user_id=arguments.get("_user_id", "global"),
        )

    if name == "skriv_notat":
        return skriv_notat(
            tekst=arguments.get("tekst", ""),
            kategori=arguments.get("kategori", "diverse"),
        )

    if name == "les_notater":
        return les_notater(arguments.get("kategori"))

    if name == "slett_notat":
        return slett_notat(arguments.get("notat_id", ""))

    if name == "tøm_notater":
        return tøm_notater(arguments.get("kategori"))

    if name == "hent_snapshot":
        scope = arguments.get("scope", "ett")
        ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        if scope == "alle":
            spørsmål = arguments.get("spørsmål", "").strip() or (
                "Du ser bilder fra flere overvåkningskameraer. "
                "Beskriv hvert bilde kort: hva skjer, er det personer, kjøretøy eller hendelser? "
                "Angi kameranavnet i svaret."
            )
            cams = await get_cameras()
            if not cams:
                return "Ingen kameraer funnet i Frigate."

            async def _fetch(cam):
                try:
                    b64 = await fetch_snapshot_b64(cam["api_name"])
                    return cam["friendly_name"], b64
                except Exception:
                    return cam["friendly_name"], None

            fetched = await asyncio.gather(*[_fetch(c) for c in cams])
            images = [b64 for _, b64 in fetched if b64]
            cam_names = [n for n, b64 in fetched if b64]
            failed = [n for n, b64 in fetched if b64 is None]

            if not images:
                return f"Klarte ikke hente bilder fra noen kameraer ({ts})."

            prompt = f"Kameraer: {', '.join(cam_names)}.\n{spørsmål}"
            try:
                vlm_result = await ask_llm(prompt=prompt, images=images)
                description = vlm_result.get("text", "").strip() or "Fikk tomt svar fra bildeanalyse."
            except Exception as e:
                return f"Bilder hentet ({ts}), men analyse feilet: {e}"

            result = f"[Alle kameraer — {ts}]\n{description}"
            if failed:
                result += f"\n\nKunne ikke hente: {', '.join(failed)}"
            return result

        kamera = arguments.get("kamera", "").strip()
        spørsmål = arguments.get("spørsmål", "").strip() or "Beskriv hva du ser på bildet. Nevn personer, kjøretøy, dyr og eventuelle hendelser."
        if not kamera:
            cams = await get_cameras()
            cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
            return f"Oppgi kameranavn. Tilgjengelige kameraer: {cam_list}"
        try:
            img_b64 = await fetch_snapshot_b64(kamera)
        except ValueError as e:
            cams = await get_cameras()
            cam_list = ", ".join(f"{c['friendly_name']} ({c['api_name']})" for c in cams)
            return f"Kamera ikke funnet: {e}\nTilgjengelige: {cam_list}"
        except Exception as e:
            return f"Kunne ikke hente snapshot fra '{kamera}': {e}"
        try:
            vlm_result = await ask_llm(prompt=spørsmål, images=[img_b64])
            description = vlm_result.get("text", "").strip() or "Fikk tomt svar fra bildeanalyse."
        except Exception as e:
            return f"Snapshot hentet ({ts}), men bildeanalyse feilet: {e}"
        return f"[{kamera} — {ts}]\n{description}"

    if name == "hent_frigate_hendelser":
        kamera = arguments.get("kamera", "").strip() or None
        label = arguments.get("label", "").strip() or None
        antall = min(int(arguments.get("antall", 10)), 50)
        kun_ansikter = bool(arguments.get("kun_ansikter", False))
        try:
            if kun_ansikter:
                events = await fetch_face_events(limit=antall)
            else:
                events = await fetch_events(camera=kamera, label=label, limit=antall)
        except Exception as e:
            return f"Kunne ikke hente Frigate-hendelser: {e}"
        if not events:
            return "Ingen hendelser funnet."
        lines = []
        for ev in events:
            ts_raw = ev.get("start_time") or ev.get("ts", 0)
            try:
                ts_str = datetime.fromtimestamp(float(ts_raw)).strftime("%d.%m %H:%M:%S")
            except Exception:
                ts_str = str(ts_raw)
            cam = ev.get("camera_friendly") or ev.get("camera", "?")
            lbl = ev.get("label", "?")
            conf = ev.get("top_score") or ev.get("score") or ev.get("confidence") or 0
            face = ev.get("_face_name") or ev.get("sub_label") or ""
            face_str = f" — ansikt: {face}" if face else ""
            lines.append(f"[{ts_str}] {cam}: {lbl} ({int(float(conf)*100)}%){face_str}")
        return "\n".join(lines)

    if name == "les_kamerahendelser":
        navn_filter    = (arguments.get("navn") or "").strip().lower()
        timer_tilbake  = min(int(arguments.get("timer_tilbake", 24)), 48)
        face_path      = Path("/kaare/state/argus/face_events.txt")

        if not face_path.exists():
            return "Ingen kamerahendelser registrert ennå."
        try:
            raw_lines = [l for l in face_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        except Exception as e:
            return f"Kunne ikke lese kamerahendelser: {e}"
        if not raw_lines:
            return "Ingen kamerahendelser registrert ennå."

        try:
            cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
            local_tz = ZoneInfo(cfg["lokasjon"]["tidssone"])
        except Exception:
            local_tz = ZoneInfo("Europe/Oslo")

        cutoff = datetime.now(local_tz) - timedelta(hours=timer_tilbake)
        filtered = []
        for line in raw_lines:
            try:
                inner   = line[1 : line.index("]")]
                ts_part = inner.split("→")[0].strip()
                dt      = datetime.strptime(ts_part, "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
                if dt < cutoff:
                    continue
            except Exception:
                pass  # include lines we cannot parse
            if navn_filter and navn_filter not in line.lower():
                continue
            filtered.append(line)

        if not filtered:
            label = f" for «{arguments['navn']}»" if arguments.get("navn") else ""
            return f"Ingen kamerahendelser{label} funnet siste {timer_tilbake} timer."

        header = f"Kamerahendelser siste {timer_tilbake} timer ({len(filtered)} hendelse{'r' if len(filtered) != 1 else ''}):"
        return header + "\n" + "\n".join(filtered)

    if name == "liste_kameraer":
        try:
            cams = await get_cameras()
        except Exception as e:
            return f"Kunne ikke hente kameraliste fra Frigate: {e}"
        if not cams:
            return "Ingen kameraer funnet."
        lines = [f"  {c['friendly_name']} → {c['api_name']}" for c in cams]
        return f"Kameraer ({len(cams)}):\n" + "\n".join(lines)

    if name == "hent_gammel_stm":
        return _get_stm_history(arguments.get("dato"))

    if name == "les_tankehistorikk":
        entries = read_think_history(
            n=min(int(arguments.get("antall", 10)), 50),
            search=arguments.get("søk") or None,
            recovered_only=bool(arguments.get("kun_recovery", False)),
        )
        return format_for_kare(entries)

    if name == "les_fil":
        return _shared_les_fil(arguments)

    if name == "liste_filer":
        return _shared_liste_filer(arguments)

    if name == "søk_kode":
        return _shared_søk_kode(arguments)

    if name == "les_logg":
        return _shared_les_logg(arguments)

    if name == "sjekk_tjenester":
        return _shared_sjekk_tjenester(arguments)

    if name == "sjekk_ressurser":
        return _shared_sjekk_ressurser(arguments)

    if name == "ssh_kommando":
        node = arguments.get("node", "").strip()
        kommando = arguments.get("kommando", "").strip()
        _VALID_NODES = ("ainuc", "dnspi", "proxypi", "hapi")
        if node not in _VALID_NODES:
            return f"[Ukjent node '{node}'. Tillatte: {', '.join(_VALID_NODES)}]"
        if not kommando:
            return "[Tom kommando]"
        # Sudo commands — node-specific (hapi runs as root, uses ha CLI instead)
        _SUDO_ALL = (
            "sudo apt update",
            "sudo apt upgrade",
            "sudo reboot now",
        )
        _SUDO_DNSPI = (
            "sudo pihole -up",
            "sudo pihole -g",
        )
        _HA_PRIVILEGED = (
            "ha core restart",
            "ha core update",
            "ha supervisor restart",
            "ha supervisor update",
            "ha os update",
            "ha addon restart",
            "ha addon start",
            "ha addon stop",
        )
        if kommando.startswith("sudo "):
            if node == "hapi":
                return "[Rejected: hapi runs as root — use 'ha ...' commands directly, no sudo needed.]"
            allowed_sudo = _SUDO_ALL + (_SUDO_DNSPI if node == "dnspi" else ())
            if not any(kommando.startswith(s) for s in allowed_sudo):
                node_extra = " + sudo pihole -up/-g" if node == "dnspi" else ""
                return (
                    f"[Rejected: sudo command '{kommando[:60]}' not allowed on {node}. "
                    f"Allowed sudo: apt update, apt upgrade, reboot now{node_extra}]"
                )
        elif node == "hapi" and any(kommando.startswith(h) for h in _HA_PRIVILEGED):
            pass  # HA CLI privileged commands allowed on hapi
        else:
            _ALLOWED = (
                # File reading — any path
                "cat ", "head ", "tail ", "grep ", "find ", "stat ", "file ", "wc ", "diff ",
                "ls", "pwd", "du ", "which ",
                # System info
                "uptime", "hostname", "uname", "date", "whoami", "id", "who", "w ", "last",
                "env", "printenv", "echo ",
                # Processes & resources
                "ps", "top -bn", "free", "df", "lsof", "lsblk", "lscpu", "lsusb", "lspci",
                # Network
                "ip ", "ss", "netstat", "ping ", "ifconfig",
                # Systemd
                "systemctl status", "systemctl list-units", "systemctl list-timers",
                "systemctl is-active", "systemctl is-enabled",
                "journalctl", "dmesg",
                # Packages (read-only)
                "dpkg -l", "dpkg -s", "dpkg -L", "apt list", "apt-cache ",
                # Docker
                "docker ps", "docker logs", "docker stats", "docker inspect",
                # Hardware / GPU
                "nvidia-smi",
                # Pi-hole (read)
                "pihole status", "pihole -v", "pihole -c",
                # Home Assistant CLI (read-only)
                "ha core info", "ha core logs", "ha core check",
                "ha supervisor info", "ha supervisor logs",
                "ha os info", "ha network info",
                "ha addon info", "ha addon list", "ha addon logs",
                "ha host info", "ha hardware info",
            )
            # Block destructive patterns regardless
            _BLOCKED = ("rm ", "mv ", "chmod ", "chown ", "dd ", "mkfs", "fdisk",
                        ">", ">>", "tee ", "sed -i", "awk -i", "truncate",
                        "apt install", "apt remove", "apt-get install",
                        "pip install", "npm install",
                        "systemctl start ", "systemctl stop ", "systemctl restart ",
                        "systemctl enable ", "systemctl disable ")
            if any(b in kommando for b in _BLOCKED):
                return f"[Rejected: '{kommando[:60]}' contains a write/destructive operation.]"
            if not any(kommando.startswith(a) for a in _ALLOWED):
                return (
                    f"[Rejected: '{kommando[:50]}' is not a recognised read-only command on {node}. "
                    f"Allowed: cat, head, tail, grep, find, ls, ps, df, free, uptime, journalctl, "
                    f"systemctl status/list, dpkg, pihole status/version, docker ps/logs, ip, ss, "
                    f"ha core/supervisor/os/addon info/logs, ...]"
                )
        result = _sp.run(
            [
                "ssh",
                "-i", "/kaare/.ssh/id_ed25519",
                "-F", "/kaare/.ssh/config",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new",
                node,
                kommando,
            ],
            capture_output=True, text=True, timeout=30,
        )
        out = (result.stdout + result.stderr).strip()
        return out[:4000] if out else "[Ingen output]"

    if name == "local_kommando":
        # Shell commands require developer_tools: true in settings.yaml.
        # This mirrors Frigate's "protected mode" — off by default, user opts in knowingly.
        try:
            _dev_cfg = yaml.safe_load(Path("/kaare/configs/settings.yaml").read_text())
            _dev_tools_enabled = bool(_dev_cfg.get("developer_tools", False))
        except Exception:
            _dev_tools_enabled = False
        if not _dev_tools_enabled:
            return (
                "[Utviklerverktøy er deaktivert. Shell-kommandoer er ikke tilgjengelig. "
                "Admin kan aktivere dette under Innstillinger → Sikkerhet i GUI-et.]"
            )
        kommando = arguments.get("kommando", "").strip()
        if not kommando:
            return "[Tom kommando]"
        _ALLOWED = (
            # File reading
            "cat ", "head ", "tail ", "grep ", "find ", "stat ", "file ", "wc ", "diff ",
            "ls", "pwd", "du ", "which ",
            # System info
            "uptime", "hostname", "uname", "date", "whoami", "id", "who", "w ", "last",
            "env", "printenv", "echo ",
            # Processes & resources
            "ps", "top -bn", "free", "df", "lsof", "lsblk", "lscpu", "lsusb", "lspci",
            # Network
            "ip ", "ss", "netstat", "ping ", "ifconfig",
            # Systemd (read only)
            "systemctl status", "systemctl list-units", "systemctl list-timers",
            "systemctl is-active", "systemctl is-enabled",
            "journalctl", "dmesg",
            # Packages (read)
            "dpkg -l", "dpkg -s", "dpkg -L", "apt list", "apt-cache ",
            # Docker (read)
            "docker ps", "docker logs", "docker stats", "docker inspect",
            # Hardware / GPU
            "nvidia-smi",
        )
        _BLOCKED = ("sudo", "rm ", "mv ", "chmod ", "chown ", "dd ", "mkfs",
                    ">", ">>", "tee ", "sed -i", "awk -i", "truncate",
                    "apt install", "apt remove", "apt upgrade", "apt-get",
                    "pip install", "npm install",
                    "systemctl start ", "systemctl stop ", "systemctl restart ",
                    "systemctl enable ", "systemctl disable ", "reboot", "shutdown")
        if any(b in kommando for b in _BLOCKED):
            return f"[Rejected: '{kommando[:60]}' contains sudo or a write/destructive operation. No sudo on AI-pc.]"
        if not any(kommando.startswith(a) for a in _ALLOWED):
            return (
                f"[Rejected: '{kommando[:50]}' is not a recognised read-only command. "
                f"Allowed: cat, head, tail, grep, find, ls, ps, df, free, uptime, "
                f"journalctl, dmesg, systemctl status/list, dpkg, docker ps/logs, "
                f"ip, ss, netstat, lsblk, lscpu, nvidia-smi, ...]"
            )
        result = _sp.run(
            kommando, shell=True, capture_output=True, text=True, timeout=30,
        )
        out = (result.stdout + result.stderr).strip()
        return out[:4000] if out else "[Ingen output]"

    if name == "git_inspect":
        action = arguments.get("action", "log")
        if action == "diff":
            return _shared_git_diff(arguments)
        return _shared_git_log(arguments)

    if name == "sett_timer":
        return sett_timer(
            prompt=arguments.get("prompt", ""),
            in_seconds=int(arguments.get("in_seconds", 0)),
            notify=bool(arguments.get("notify", True)),
            repeat=arguments.get("repeat") or None,
            at_time=arguments.get("at_time") or None,
        )

    if name == "avbryt_timer":
        return avbryt_timer(arguments.get("timer_id", ""))

    if name == "liste_timere":
        return liste_timere()

    if name == "kare_image":
        if not _llm_cfg("image_edit").get("enabled", True):
            return "Bilderedigering er deaktivert. Aktiver den under Innstillinger → LLM/Modeller."
        from adapters.image_generation_adapter import generate_image, edit_image
        mode = arguments.get("mode", "generate")
        prompt = arguments.get("prompt", "").strip()
        negative_prompt = arguments.get("negative_prompt", "").strip()
        image_b64 = arguments.get("image_b64", "").strip()
        uid = arguments.get("_user_id", "global")
        if not prompt:
            return "Oppgi en beskrivelse av bildet du vil lage."
        if mode == "edit":
            if not image_b64:
                return "Edit-modus krever et input-bilde (image_b64)."
            res = await edit_image(image_b64, prompt, negative_prompt, user_id=uid)
        else:
            res = await generate_image(prompt, negative_prompt, user_id=uid)
        if not res.get("ok"):
            return f"Bildegenerering feilet: {res.get('error', 'ukjent feil')}."
        return f"Bildet er klart: /api/image/{res['image_id']}"

    if name == "se_bilder":
        import base64 as _b64
        from kaare_core.image_store import list_images, find_image
        uid = arguments.get("user_id") or arguments.get("_user_id", "global")
        folder = arguments.get("folder", "all")
        limit = int(arguments.get("limit", 10))
        image_id = arguments.get("image_id", "").strip()
        mode = arguments.get("mode", "vis").strip()

        if image_id:
            path = find_image(image_id)
            if not path:
                return f"Bilde '{image_id}' ikke funnet."
            if mode == "analyser":
                b64 = _b64.b64encode(path.read_bytes()).decode()
                return f"[VISION:{b64}]"
            return f"Bildet er klart. Inkluder denne URL-en ordrett i svaret ditt: /api/image/{image_id}"

        imgs = list_images(uid, folder, limit)
        if not imgs:
            return f"Ingen bilder funnet for {uid} ({folder})."
        lines = [f"{i['folder']}/{i['id']} ({i['size_kb']} KB)" for i in imgs]
        return f"Bilder for {uid}:\n" + "\n".join(lines)

    if name == "announce":
        text = arguments.get("text", "").strip()
        target = arguments.get("target", "local").strip() or "local"
        raw_volume = arguments.get("volume")
        volume = float(raw_volume) if raw_volume is not None else None
        if not text:
            return "No text provided for announcement."
        payload: dict = {"text": text, "target": target}
        if volume is not None:
            payload["volume"] = max(0.0, min(1.0, volume))
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{_VOICE_BRIDGE_URL}/speak",
                    json=payload,
                )
                resp.raise_for_status()
            target_label = "alle rom" if target in ("all", "alle") else f"'{target}'"
            vol_label = f" (volum {int(volume * 100)}%)" if volume is not None else ""
            return f"Kunngjøring sendt til {target_label}{vol_label}."
        except Exception as exc:
            return f"Could not reach voice bridge: {exc}"

    if name == "media":
        return await _media(arguments)

    return f"Ukjent tool: '{name}'."


async def execute_tool(name: str, arguments: Dict[str, Any]) -> str:
    """
    Utfører ett tool-kall og logger det.
    Returnerer alltid en streng — aldri exception.
    """
    t0 = _time.time()
    try:
        result = await _dispatch(name, arguments)
    except Exception as e:
        result = f"Tool '{name}' feilet uventet: {e}"
    duration_ms = int((_time.time() - t0) * 1000)

    # timer tools log themselves via timer_service
    if name not in ("sett_timer", "avbryt_timer", "liste_timere"):
        _log_tool(name, arguments, result, duration_ms)

    return result
