import json as _json

from kaare_core.memory import knowledge_store as ks
from kaare_core.tools.i18n import get_lang, t

WORLD_TOOLS = {
    "world",
    "verden",
    "les_verden",
    "oppdater_felt_i_verden",
    "legg_til_i_verden",
    "slett_fra_verden",
    "rediger_verden",
}

_DOMAIN = "world"
_VAR_PREFIX = "var::"


# --- variables (world_vars.json -> knowledge_store, section='var::{key}') ---


def _var_section(key: str) -> str:
    return f"{_VAR_PREFIX}{key}"


def _format_var_row(key: str, row: dict, lang: str) -> str:
    data = _json.loads(row["content"])
    value = data.get("value", "")
    desc = (
        "\n  " + t("world_var_desc_label", lang, desc=data["description"])
        if data.get("description")
        else ""
    )
    by = "\n  " + t(
        "world_var_set_by_label",
        lang,
        by=row.get("source", "kare"),
        date=(row.get("valid_from") or "")[:10],
    )
    return f"{key}: {value}{desc}{by}"


def _world_get_var(key: str, lang: str = "nb") -> str:
    if not key.strip():
        return _world_list_vars(lang)
    k = key.strip()
    row = ks.get_fact_by_section(_DOMAIN, _var_section(k))
    if not row:
        return t("world_var_not_found", lang, key=k)
    return _format_var_row(k, row, lang)


def _world_set_var(key: str, value: str, description: str = "", lang: str = "nb") -> str:
    if not key.strip() or not value.strip():
        return t("world_key_value_required", lang)
    k = key.strip()
    content = _json.dumps({"value": value.strip(), "description": description.strip()})
    section = _var_section(k)
    existing = ks.get_fact_by_section(_DOMAIN, section)
    if existing:
        ks.update_fact(existing["id"], content=content)
    else:
        ks.add_fact(domain=_DOMAIN, section=section, content=content, source="kare")
    return t("world_var_set", lang, key=k, value=value.strip())


def _world_delete_var(key: str, lang: str = "nb") -> str:
    if not key.strip():
        return t("world_key_required", lang)
    k = key.strip()
    row = ks.get_fact_by_section(_DOMAIN, _var_section(k))
    if not row:
        return t("world_var_key_not_found", lang, key=key)
    ks.retire_fact(row["id"])
    return t("world_var_deleted", lang, key=key)


def _world_list_vars(lang: str = "nb") -> str:
    rows = ks.find_by_section_prefix(_DOMAIN, _VAR_PREFIX)
    if not rows:
        return t("world_no_vars", lang)
    lines = [t("world_vars_header", lang, count=len(rows)) + "\n"]
    for row in sorted(rows, key=lambda r: r["section"]):
        k = row["section"][len(_VAR_PREFIX) :]
        data = _json.loads(row["content"])
        lines.append(f"  {k}: {data.get('value', '')}")
    return "\n".join(lines)


# --- prose (world.md -> knowledge_store, section=category or 'category::field') ---


def _field_section(category: str, field: str) -> str:
    return f"{category.strip()}::{field.strip()}"


def _read_world(lang: str = "nb") -> str:
    rows = ks.get_all_facts(_DOMAIN)
    rows = [r for r in rows if not r["section"].startswith(_VAR_PREFIX)]
    if not rows:
        return t("world_file_empty", lang)
    lines = []
    last_cat = None
    for row in rows:
        section = row["section"] or "Notes"
        cat = section.split("::", 1)[0]
        if cat != last_cat:
            lines.append(f"\n## {cat}")
            last_cat = cat
        content = row["content"]
        if "::" in section:
            field = section.split("::", 1)[1]
            lines.append(f"- {field}: {content}")
        else:
            lines.append(content)
    return "\n".join(lines).strip()


def _update_world_field(category: str, field: str, value: str, lang: str = "nb") -> str:
    if not category.strip() or not field.strip():
        return t("world_cat_field_required", lang)
    section = _field_section(category, field)
    existing = ks.get_fact_by_section(_DOMAIN, section)
    if existing:
        ks.update_fact(existing["id"], content=value.strip())
        return t("world_field_updated", lang, field=field, value=value)
    ks.add_fact(domain=_DOMAIN, section=section, content=value.strip(), source="kare")
    return t("world_field_updated", lang, field=field, value=value)


def _add_to_world(category: str, text: str, lang: str = "nb") -> str:
    if not text.strip():
        return t("world_empty_text", lang)
    cat = category.strip() or "Notes"
    ks.add_fact(domain=_DOMAIN, section=cat, content=text.strip(), source="kare")
    return t("world_added", lang)


def _delete_from_world(fragment: str, lang: str = "nb") -> str:
    if not fragment.strip():
        return t("world_empty_fragment", lang)
    matches = [
        r for r in ks.find_facts(_DOMAIN, fragment) if not r["section"].startswith(_VAR_PREFIX)
    ]
    if not matches:
        return t("world_fragment_not_found", lang, fragment=fragment)
    for row in matches:
        ks.retire_fact(row["id"])
    return t("world_lines_deleted", lang, count=len(matches))


def _edit_world(fragment: str, new_text: str, lang: str = "nb") -> str:
    if not fragment.strip() or not new_text.strip():
        return t("world_fragment_new_required", lang)
    matches = [
        r for r in ks.find_facts(_DOMAIN, fragment) if not r["section"].startswith(_VAR_PREFIX)
    ]
    if not matches:
        return t("world_fragment_not_found", lang, fragment=fragment)
    for row in matches:
        ks.update_fact(row["id"], content=new_text.strip())
    return t("world_lines_updated", lang, count=len(matches))


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name in ("world", "verden"):
        action = arguments.get("action", "")
        if action == "read":
            return _read_world(lang)
        if action == "update_field":
            return _update_world_field(
                category=(arguments.get("category") or arguments.get("kategori") or ""),
                field=(arguments.get("field") or arguments.get("felt") or ""),
                value=(arguments.get("value") or arguments.get("verdi") or ""),
                lang=lang,
            )
        if action == "add":
            return _add_to_world(
                category=(arguments.get("category") or arguments.get("kategori") or ""),
                text=(arguments.get("text") or arguments.get("tekst") or ""),
                lang=lang,
            )
        if action == "delete":
            return _delete_from_world((arguments.get("fragment") or ""), lang)
        if action == "edit":
            return _edit_world(
                fragment=(arguments.get("fragment") or ""),
                new_text=(arguments.get("new_text") or arguments.get("ny_tekst") or ""),
                lang=lang,
            )
        if action == "read_var":
            return _world_get_var((arguments.get("key") or arguments.get("nokkel") or ""), lang)
        if action in ("set_var", "sett_var"):  # sett_var = old name, kept for history replay
            return _world_set_var(
                key=(arguments.get("key") or arguments.get("nokkel") or ""),
                value=(arguments.get("value") or arguments.get("verdi") or ""),
                description=(arguments.get("description") or arguments.get("beskrivelse") or ""),
                lang=lang,
            )
        if action == "delete_var":
            return _world_delete_var((arguments.get("key") or arguments.get("nokkel") or ""), lang)
        if action == "list_vars":
            return _world_list_vars(lang)
        return f"Unknown action for world: '{action}'. Valid: read, update_field, add, delete, edit, read_var, set_var, delete_var, list_vars."

    if name == "les_verden":
        return _read_world(lang)
    if name == "oppdater_felt_i_verden":
        return _update_world_field(
            category=(arguments.get("category") or arguments.get("kategori") or ""),
            field=(arguments.get("field") or arguments.get("felt") or ""),
            value=(arguments.get("value") or arguments.get("verdi") or ""),
            lang=lang,
        )
    if name == "legg_til_i_verden":
        return _add_to_world(
            category=(arguments.get("category") or arguments.get("kategori") or ""),
            text=(arguments.get("text") or arguments.get("tekst") or ""),
            lang=lang,
        )
    if name == "slett_fra_verden":
        return _delete_from_world((arguments.get("fragment") or ""), lang)
    if name == "rediger_verden":
        return _edit_world(
            fragment=(arguments.get("fragment") or ""),
            new_text=(arguments.get("new_text") or arguments.get("ny_tekst") or ""),
            lang=lang,
        )

    return f"[executor_world] Unknown tool: {name}"
