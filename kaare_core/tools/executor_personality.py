import asyncio
import yaml
from datetime import datetime
from pathlib import Path

from kaare_core.config import get_service as _svc
from kaare_core.memory.long_term import get_ltm, USER_GLOBAL
from kaare_core.tools.i18n import t, get_lang

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_PERSONALITY_SELF_PATH = Path("/kaare/state/personality_self.md")

PERSONALITY_TOOLS = {
    "self_image",
    "user_profile",
    "selvbilde",
    "les_selvbilde",
    "slett_fra_selvbilde",
    "rediger_selvbilde",
    "oppdater_selvbilde",
    "brukerprofil",
    "oppdater_brukerprofil",
    "les_brukerprofil",
    "read_user_profile",
    "sett_profilfelt",
    "slett_fra_brukerprofil",
    "rediger_brukerprofil",
    "oppdater_nysgjerrighet",
}


def _load_personality_core() -> str:
    try:
        return Path("/kaare/configs/personality_core.md").read_text(encoding="utf-8").strip()
    except Exception:
        return ""

PERSONALITY_CORE_TEXT = _load_personality_core()


def _is_allowed_self_contributor(user_id: str) -> bool:
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
        return True


def _update_self_image(observation: str, lang: str = "nb") -> str:
    if not observation.strip():
        return t("pers_empty_observation", lang)
    try:
        date = datetime.now().strftime("%Y-%m-%d")
        entry = f"\n- [{date}] {observation.strip()}"
        with open(_PERSONALITY_SELF_PATH, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        return t("pers_noted", lang)
    except Exception as e:
        return t("pers_self_write_error", lang, error=e)


def _delete_from_self_image(fragment: str, lang: str = "nb") -> str:
    if not fragment.strip():
        return t("pers_empty_observation", lang)
    try:
        lines = _PERSONALITY_SELF_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        remaining = [l for l in lines if fragment.lower() not in l.lower()]
        count = len(lines) - len(remaining)
        if count == 0:
            return t("pers_fragment_not_found", lang, fragment=fragment)
        _PERSONALITY_SELF_PATH.write_text("".join(remaining), encoding="utf-8")
        return t("pers_lines_deleted", lang, count=count)
    except Exception as e:
        return t("pers_delete_error", lang, error=e)


def _edit_self_image(fragment: str, new_text: str, lang: str = "nb") -> str:
    if not fragment.strip() or not new_text.strip():
        return t("pers_fragment_new_required", lang)
    try:
        lines = _PERSONALITY_SELF_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        result = []
        changed = 0
        date = datetime.now().strftime("%Y-%m-%d")
        for line in lines:
            if fragment.lower() in line.lower():
                result.append(f"- [{date}] {new_text.strip()}\n")
                changed += 1
            else:
                result.append(line)
        if changed == 0:
            return t("pers_fragment_not_found", lang, fragment=fragment)
        _PERSONALITY_SELF_PATH.write_text("".join(result), encoding="utf-8")
        return t("pers_lines_updated", lang, count=changed)
    except Exception as e:
        return t("pers_edit_error", lang, error=e)


def _update_curiosity(curiosity: str, user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    if not curiosity.strip():
        return t("pers_empty_curiosity", lang)
    try:
        from kaare_core.users.profile_manager import update_nested_profile_field
        update_nested_profile_field(user_id, "prompt_top", "curiosity", curiosity.strip())
        return t("pers_curiosity_updated", lang)
    except Exception as e:
        return t("pers_curiosity_error", lang, error=e)


def _update_user_profile(observation: str, user_id: str, lang: str = "nb") -> str:
    if not observation.strip():
        return t("pers_empty_observation", lang)
    if not user_id or user_id == "global":
        return t("pers_no_user_observation", lang)
    try:
        from kaare_core.users.profile_manager import add_observation
        add_observation(user_id, observation.strip())
        return t("pers_noted", lang)
    except Exception as e:
        return t("pers_profile_write_error", lang, error=e)


def _set_profile_field(section: str, field: str, value: str, user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("pers_no_user_profile", lang)
    if not section.strip() or not field.strip():
        return t("pers_section_field_required", lang)
    try:
        from kaare_core.users.profile_manager import update_nested_profile_field
        return update_nested_profile_field(user_id, section.strip(), field.strip(), value)
    except Exception as e:
        return t("pers_profile_field_error", lang, error=e)


def _read_user_profile(user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    try:
        from kaare_core.users.profile_manager import read_profile_yaml_as_text, get_recent_observations
        yaml_content = read_profile_yaml_as_text(user_id)
    except Exception:
        yaml_content = ""
    obs_content = ""
    try:
        obs_content = get_recent_observations(user_id, days=365).strip()
    except Exception:
        pass
    parts = []
    if yaml_content and yaml_content != "Ingen profildata registrert ennå.":
        parts.append(f"=== PROFIL (profile.yaml) ===\n{yaml_content}")
    if obs_content:
        if len(obs_content) > 6000:
            obs_content = obs_content[:6000] + "\n\n[… resten er kuttet]"
        parts.append(f"=== OBSERVASJONER (observations.md) ===\n{obs_content}")
    return "\n\n".join(parts) if parts else t("pers_no_profile_data", lang)


def _delete_from_user_profile(fragment: str, user_id: str, lang: str = "nb") -> str:
    if not fragment.strip():
        return t("pers_empty_observation", lang)
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    try:
        from kaare_core.users.profile_manager import delete_observation_fragment
        found, count = delete_observation_fragment(user_id, fragment)
        if not found:
            return t("pers_fragment_not_found", lang, fragment=fragment)
        return t("pers_lines_deleted", lang, count=count)
    except Exception as e:
        return t("pers_profile_delete_error", lang, error=e)


def _edit_user_profile(fragment: str, new_text: str, user_id: str, lang: str = "nb") -> str:
    if not fragment.strip() or not new_text.strip():
        return t("pers_fragment_new_required", lang)
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    try:
        from kaare_core.users.profile_manager import edit_observation_fragment
        found, count = edit_observation_fragment(user_id, fragment, new_text)
        if not found:
            return t("pers_fragment_not_found", lang, fragment=fragment)
        return t("pers_lines_updated", lang, count=count)
    except Exception as e:
        return t("pers_profile_edit_error", lang, error=e)


async def dispatch(name: str, arguments: dict) -> str:
    user_id = arguments.get("_user_id", "global")
    lang = get_lang(user_id)

    if name in ("self_image", "selvbilde"):
        action = arguments.get("action", "")
        if action == "read":
            try:
                content = _PERSONALITY_SELF_PATH.read_text(encoding="utf-8").strip()
                return content if content else t("pers_self_empty", lang)
            except Exception as e:
                return t("pers_self_read_error", lang, error=e)
        if action == "update":
            if _is_allowed_self_contributor(user_id):
                return _update_self_image((arguments.get("observation") or arguments.get("observasjon") or ""), lang)
            return t("pers_noted", lang)
        if action == "edit":
            if _is_allowed_self_contributor(user_id):
                return _edit_self_image(
                    fragment=(arguments.get("fragment") or ""),
                    new_text=(arguments.get("new_text") or arguments.get("ny_tekst") or ""),
                    lang=lang,
                )
            return t("pers_noted", lang)
        if action == "delete":
            if _is_allowed_self_contributor(user_id):
                return _delete_from_self_image((arguments.get("fragment") or ""), lang)
            return t("pers_noted", lang)
        return f"Unknown action for self_image: '{action}'. Valid: read, update, edit, delete."

    if name in ("user_profile", "brukerprofil"):
        action = arguments.get("action", "")
        if action == "read":
            return _read_user_profile(user_id=user_id, lang=lang)
        if action == "update":
            return _update_user_profile(
                observation=(arguments.get("observation") or arguments.get("observasjon") or ""),
                user_id=user_id,
                lang=lang,
            )
        if action == "set_field":
            return _set_profile_field(
                section=(arguments.get("section") or arguments.get("seksjon") or ""),
                field=(arguments.get("field") or arguments.get("felt") or ""),
                value=(arguments.get("value") or arguments.get("verdi") or ""),
                user_id=user_id,
                lang=lang,
            )
        if action == "edit":
            return _edit_user_profile(
                fragment=(arguments.get("fragment") or ""),
                new_text=(arguments.get("new_text") or arguments.get("ny_tekst") or ""),
                user_id=user_id,
                lang=lang,
            )
        if action == "delete":
            return _delete_from_user_profile(
                fragment=(arguments.get("fragment") or ""),
                user_id=user_id,
                lang=lang,
            )
        if action == "curiosity":
            return _update_curiosity(
                curiosity=(arguments.get("text") or arguments.get("nysgjerrighet") or ""),
                user_id=user_id,
                lang=lang,
            )
        if action == "update_house":
            field = (arguments.get("field") or arguments.get("felt") or "")
            value = (arguments.get("value") or arguments.get("verdi") or "")
            if not field or not value:
                return t("pers_house_update_required", lang)
            try:
                from kaare_core.users.profile_manager import update_household_visible
                result = update_household_visible(user_id=user_id, field=field, value=value)
                try:
                    from adapters.llm_adapter import reload_config
                    reload_config()
                except Exception:
                    pass
                try:
                    ltm = get_ltm()
                    summary = f"House update: {field} = {value} (user: {user_id})"
                    asyncio.get_event_loop().create_task(
                        ltm.log_interaction(
                            user_id=USER_GLOBAL,
                            prompt=summary,
                            source="update_house",
                            response=result,
                        )
                    )
                except Exception:
                    pass
                return result
            except Exception as e:
                return t("pers_house_update_error", lang, error=e)
        return f"Unknown action for user_profile: '{action}'. Valid: read, update, update_house, set_field, edit, delete, curiosity."

    if name == "les_selvbilde":
        try:
            content = _PERSONALITY_SELF_PATH.read_text(encoding="utf-8").strip()
            return content if content else t("pers_self_empty", lang)
        except Exception as e:
            return t("pers_self_read_error", lang, error=e)

    if name == "slett_fra_selvbilde":
        if _is_allowed_self_contributor(user_id):
            return _delete_from_self_image((arguments.get("fragment") or ""), lang)
        return t("pers_noted", lang)

    if name == "rediger_selvbilde":
        if _is_allowed_self_contributor(user_id):
            return _edit_self_image(
                fragment=(arguments.get("fragment") or ""),
                new_text=(arguments.get("new_text") or arguments.get("ny_tekst") or ""),
                lang=lang,
            )
        return t("pers_noted", lang)

    if name == "oppdater_selvbilde":
        if _is_allowed_self_contributor(user_id):
            return _update_self_image((arguments.get("observation") or arguments.get("observasjon") or ""), lang)
        return t("pers_noted", lang)

    if name == "oppdater_nysgjerrighet":
        return _update_curiosity(
            curiosity=(arguments.get("text") or arguments.get("nysgjerrighet") or ""),
            user_id=user_id,
            lang=lang,
        )

    if name == "oppdater_brukerprofil":
        return _update_user_profile(
            observation=(arguments.get("observation") or arguments.get("observasjon") or ""),
            user_id=user_id,
            lang=lang,
        )

    if name == "les_brukerprofil":
        return _read_user_profile(user_id=user_id, lang=lang)

    if name == "sett_profilfelt":
        return _set_profile_field(
            section=(arguments.get("section") or arguments.get("seksjon") or ""),
            field=(arguments.get("field") or arguments.get("felt") or ""),
            value=(arguments.get("value") or arguments.get("verdi") or ""),
            user_id=user_id,
            lang=lang,
        )

    if name == "slett_fra_brukerprofil":
        return _delete_from_user_profile(
            fragment=(arguments.get("fragment") or ""),
            user_id=user_id,
            lang=lang,
        )

    if name == "rediger_brukerprofil":
        return _edit_user_profile(
            fragment=(arguments.get("fragment") or ""),
            new_text=(arguments.get("new_text") or arguments.get("ny_tekst") or ""),
            user_id=user_id,
            lang=lang,
        )

    return f"[executor_personality] Unknown tool: {name}"
