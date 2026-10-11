import asyncio
from pathlib import Path

from adapters.llm_adapter import reload_config
from kaare_core.config import today_local
from kaare_core.memory import knowledge_store as ks
from kaare_core.memory.long_term import USER_GLOBAL, get_ltm
from kaare_core.tools.i18n import get_lang, t
from kaare_core.users.contributors import contributor_allowed
from kaare_core.users.profile_manager import (
    add_observation,
    delete_observation_fragment,
    edit_observation_fragment,
    get_recent_observations,
    read_profile_yaml_as_text,
    update_household_visible,
    update_nested_profile_field,
)

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_PERSONALITY_DOMAIN = "personality"

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
    """Gate on which HUMAN user's chat influence may steer Kåre's self-image.

    Deliberately does not apply to Kåre's own autonomous updates (user_id=="kare",
    used by reflection_loop.py's Jang loop when it calls Kåre's tools with no real
    user in the loop — see kaare_core/reflection_loop.py:151). The contributors/
    allowed_users setting exists to stop OTHER PEOPLE from steering Kåre's sense
    of self via conversation; it was never meant to gate Kåre's own introspection,
    which has no "user's context" to check against in the first place. Found and
    fixed 2026-09-07 (BUG-log) — autonomous self_image updates were silently
    failing this check, so background self-reflection never actually saved.
    """
    return contributor_allowed("personality_self", "all", user_id)


def _update_self_image(observation: str, lang: str = "nb") -> str:
    if not observation.strip():
        return t("pers_empty_observation", lang)
    date = today_local()
    content = f"[{date}] {observation.strip()}"
    fact_id = ks.add_fact(domain=_PERSONALITY_DOMAIN, content=content, source="kare")
    if fact_id is None:
        return t("pers_self_write_error", lang, error="knowledge_store write failed")
    return t("pers_noted", lang)


def _delete_from_self_image(fragment: str, lang: str = "nb") -> str:
    if not fragment.strip():
        return t("pers_empty_observation", lang)
    matches = ks.find_facts(_PERSONALITY_DOMAIN, fragment)
    if not matches:
        return t("pers_fragment_not_found", lang, fragment=fragment)
    for row in matches:
        ks.retire_fact(row["id"])
    return t("pers_lines_deleted", lang, count=len(matches))


def _edit_self_image(fragment: str, new_text: str, lang: str = "nb") -> str:
    if not fragment.strip() or not new_text.strip():
        return t("pers_fragment_new_required", lang)
    matches = ks.find_facts(_PERSONALITY_DOMAIN, fragment)
    if not matches:
        return t("pers_fragment_not_found", lang, fragment=fragment)
    date = today_local()
    for row in matches:
        ks.update_fact(row["id"], content=f"[{date}] {new_text.strip()}")
    return t("pers_lines_updated", lang, count=len(matches))


def _read_self_image(lang: str) -> str:
    rows = ks.get_all_facts(_PERSONALITY_DOMAIN)
    if not rows:
        return t("pers_self_empty", lang)
    return "\n".join(f"- {r['content']}" for r in rows)


def _update_curiosity(curiosity: str, user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    if not curiosity.strip():
        return t("pers_empty_curiosity", lang)
    try:
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
        return update_nested_profile_field(user_id, section.strip(), field.strip(), value)
    except Exception as e:
        return t("pers_profile_field_error", lang, error=e)


def _read_user_profile(user_id: str, lang: str = "nb") -> str:
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    try:
        yaml_content = read_profile_yaml_as_text(user_id)
    except Exception:
        yaml_content = ""
    obs_content = ""
    try:
        obs_content = get_recent_observations(user_id, days=365).strip()
    except Exception:
        pass
    parts = []
    if yaml_content and yaml_content != t("prof_no_data", lang):
        parts.append(f"{t('pers_profile_section', lang)}\n{yaml_content}")
    if obs_content:
        if len(obs_content) > 6000:
            obs_content = obs_content[:6000] + "\n\n" + t("mem_truncated", lang)
        parts.append(f"{t('pers_observations_section', lang)}\n{obs_content}")
    return "\n\n".join(parts) if parts else t("pers_no_profile_data", lang)


def _delete_from_user_profile(fragment: str, user_id: str, lang: str = "nb") -> str:
    if not fragment.strip():
        return t("pers_empty_observation", lang)
    if not user_id or user_id == "global":
        return t("pers_no_user", lang)
    try:
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
            return _read_self_image(lang)
        if action == "update":
            if _is_allowed_self_contributor(user_id):
                return _update_self_image(
                    (arguments.get("observation") or arguments.get("observasjon") or ""), lang
                )
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
            field = arguments.get("field") or arguments.get("felt") or ""
            value = arguments.get("value") or arguments.get("verdi") or ""
            if not field or not value:
                return t("pers_house_update_required", lang)
            try:
                result = update_household_visible(user_id=user_id, field=field, value=value)
                try:
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
                            rid=arguments.get("_rid", ""),
                        )
                    )
                except Exception:
                    pass
                return result
            except Exception as e:
                return t("pers_house_update_error", lang, error=e)
        return f"Unknown action for user_profile: '{action}'. Valid: read, update, update_house, set_field, edit, delete, curiosity."

    if name == "les_selvbilde":
        return _read_self_image(lang)

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
            return _update_self_image(
                (arguments.get("observation") or arguments.get("observasjon") or ""), lang
            )
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
