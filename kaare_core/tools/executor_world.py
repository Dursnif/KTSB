import json as _json
import os
from datetime import datetime
from pathlib import Path

from kaare_core.tools.i18n import t, get_lang

_WORLD_PATH = Path("/kaare/state/world.md")
_WORLD_VARS_PATH = Path("/kaare/state/world_vars.json")

WORLD_TOOLS = {
    "world",
    "verden",
    "les_verden",
    "oppdater_felt_i_verden",
    "legg_til_i_verden",
    "slett_fra_verden",
    "rediger_verden",
}


def _read_world_vars() -> dict:
    if not _WORLD_VARS_PATH.exists():
        return {}
    try:
        data = _json.loads(_WORLD_VARS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_world_vars(data: dict) -> None:
    tmp = _WORLD_VARS_PATH.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _WORLD_VARS_PATH)


def _world_get_var(key: str, lang: str = "nb") -> str:
    vars_ = _read_world_vars()
    if not key.strip():
        if not vars_:
            return t("world_no_vars", lang)
        lines = [t("world_all_vars", lang) + "\n"]
        for k, v in sorted(vars_.items()):
            value = v.get("verdi", "") if isinstance(v, dict) else v
            desc = f" — {v['beskrivelse']}" if isinstance(v, dict) and v.get("beskrivelse") else ""
            lines.append(f"  {k}: {value}{desc}")
        return "\n".join(lines)
    k = key.strip()
    if k not in vars_:
        return t("world_var_not_found", lang, key=k)
    v = vars_[k]
    if isinstance(v, dict):
        desc = f"\n  Beskrivelse: {v['beskrivelse']}" if v.get("beskrivelse") else ""
        by = f"\n  Satt av: {v['av']} ({v.get('satt', '')})" if v.get("av") else ""
        return f"{k}: {v.get('verdi', '')}{desc}{by}"
    return f"{k}: {v}"


def _world_set_var(key: str, value: str, description: str = "", lang: str = "nb") -> str:
    if not key.strip() or not value.strip():
        return t("world_key_value_required", lang)
    vars_ = _read_world_vars()
    vars_[key.strip()] = {
        "verdi": value.strip(),
        "beskrivelse": description.strip(),
        "satt": datetime.now().strftime("%Y-%m-%d"),
        "av": "Kåre",
    }
    _write_world_vars(vars_)
    return t("world_var_set", lang, key=key.strip(), value=value.strip())


def _world_delete_var(key: str, lang: str = "nb") -> str:
    if not key.strip():
        return t("world_key_required", lang)
    vars_ = _read_world_vars()
    if key.strip() not in vars_:
        return t("world_var_key_not_found", lang, key=key)
    del vars_[key.strip()]
    _write_world_vars(vars_)
    return t("world_var_deleted", lang, key=key)


def _world_list_vars(lang: str = "nb") -> str:
    vars_ = _read_world_vars()
    if not vars_:
        return t("world_no_vars", lang)
    lines = [t("world_vars_header", lang, count=len(vars_)) + "\n"]
    for k in sorted(vars_.keys()):
        v = vars_[k]
        value = v.get("verdi", "") if isinstance(v, dict) else v
        lines.append(f"  {k}: {value}")
    return "\n".join(lines)


def _read_world(lang: str = "nb") -> str:
    try:
        content = _WORLD_PATH.read_text(encoding="utf-8").strip()
        return content if content else t("world_file_empty", lang)
    except Exception as e:
        return t("world_read_error", lang, error=e)


def _update_world_field(category: str, field: str, value: str, lang: str = "nb") -> str:
    if not category.strip() or not field.strip():
        return t("world_cat_field_required", lang)
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        cat_header = f"## {category.strip()}"
        field_prefix = f"- {field.strip()}:"
        new_line = f"- {field.strip()}: {value.strip()}\n"

        cat_idx = next((i for i, l in enumerate(lines) if l.strip() == cat_header), None)

        if cat_idx is None:
            lines.append(f"\n{cat_header}\n")
            lines.append(new_line)
            _WORLD_PATH.write_text("".join(lines), encoding="utf-8")
            return t("world_category_created", lang, category=category, field=field)

        next_cat = next(
            (i for i in range(cat_idx + 1, len(lines)) if lines[i].startswith("## ")),
            len(lines),
        )
        field_idx = next(
            (i for i in range(cat_idx + 1, next_cat) if lines[i].strip().startswith(field_prefix)),
            None,
        )

        if field_idx is not None:
            lines[field_idx] = new_line
        else:
            lines.insert(cat_idx + 1, new_line)

        _WORLD_PATH.write_text("".join(lines), encoding="utf-8")
        return t("world_field_updated", lang, field=field, value=value)
    except Exception as e:
        return t("world_update_error", lang, error=e)


def _add_to_world(category: str, text: str, lang: str = "nb") -> str:
    if not text.strip():
        return t("world_empty_text", lang)
    cat = (category.strip() or "Notes")
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        cat_header = f"## {cat}"
        entry = f"{text.strip()}\n"

        cat_idx = next((i for i, l in enumerate(lines) if l.strip() == cat_header), None)

        if cat_idx is None:
            lines.append(f"\n{cat_header}\n")
            lines.append(entry)
        else:
            insert_at = next(
                (i for i in range(cat_idx + 1, len(lines)) if lines[i].startswith("## ")),
                len(lines),
            )
            lines.insert(insert_at, entry)

        _WORLD_PATH.write_text("".join(lines), encoding="utf-8")
        return t("world_added", lang)
    except Exception as e:
        return t("world_write_error", lang, error=e)


def _delete_from_world(fragment: str, lang: str = "nb") -> str:
    if not fragment.strip():
        return t("world_empty_fragment", lang)
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        remaining = [l for l in lines if fragment.lower() not in l.lower()]
        count = len(lines) - len(remaining)
        if count == 0:
            return t("world_fragment_not_found", lang, fragment=fragment)
        _WORLD_PATH.write_text("".join(remaining), encoding="utf-8")
        return t("world_lines_deleted", lang, count=count)
    except Exception as e:
        return t("world_delete_error", lang, error=e)


def _edit_world(fragment: str, new_text: str, lang: str = "nb") -> str:
    if not fragment.strip() or not new_text.strip():
        return t("world_fragment_new_required", lang)
    try:
        lines = _WORLD_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        result = []
        changed = 0
        for line in lines:
            if fragment.lower() in line.lower():
                result.append(f"{new_text.strip()}\n")
                changed += 1
            else:
                result.append(line)
        if changed == 0:
            return t("world_fragment_not_found", lang, fragment=fragment)
        _WORLD_PATH.write_text("".join(result), encoding="utf-8")
        return t("world_lines_updated", lang, count=changed)
    except Exception as e:
        return t("world_edit_error", lang, error=e)


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
        if action == "sett_var":
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
        return f"Unknown action for world: '{action}'. Valid: read, update_field, add, delete, edit, read_var, sett_var, delete_var, list_vars."

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
