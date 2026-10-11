# /kaare/kaare_core/tools/definitions.py
"""
Kåre tool definitions (Ollama /api/chat format).

Reduced from 61 to 25 tools (merged 2026-05-14).
Action-parameter pattern: one tool per domain, action enum selects the operation.
All action enum values and parameter names are in English.
Descriptions are i18n'd via get_tools(lang).
"""

from kaare_core.memory.agenda_themes import THEMES
from kaare_core.model_lock import restartable_containers
from kaare_core.tools.i18n import t as _t


def _library_wiki_suffix(lang: str) -> str:
    """Which wikis exist and what each holds — from the wiki registry, never hardcoded
    (P111 Phase 3: Kåre must see that e.g. the English wiki has no tables)."""
    # Imported here, not at the top: wiki_registry imports kaare_core.config, and config's
    # module-level _load_tool_permissions() imports this module → circular import at startup.
    # PLC0415: circular: definitions ↔ config/model_lock (tests/test_d1_agent_pool_topology.py)
    from kaare_core.tools import wiki_registry as _wiki_registry  # noqa: PLC0415

    try:
        return _t("tool_library_wiki_suffix", lang, wikis=_wiki_registry.capability_text(lang))
    except Exception:
        return ""


def _library_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "library",
            "description": _t("tool_library_desc", lang) + _library_wiki_suffix(lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "search",
                            "fetch_article",
                            "fetch_table",
                            "fetch_url",
                            "online",
                            "law",
                        ],
                        "description": _t("tool_library_action_desc", lang),
                    },
                    "query": {
                        "type": "string",
                        "description": _t("tool_library_query_desc", lang),
                    },
                    "task": {
                        "type": "string",
                        "description": _t("tool_library_task_desc", lang),
                    },
                    "subject": {
                        "type": "string",
                        "description": _t("tool_subject_desc", lang),
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": _t("tool_library_keywords_desc", lang),
                    },
                    "background": {
                        "type": "boolean",
                        "description": _t("tool_background_desc", lang),
                    },
                    "title": {
                        "type": "string",
                        "description": _t("tool_library_title_desc", lang),
                    },
                    "url": {
                        "type": "string",
                        "description": _t("tool_library_url_desc", lang),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": _t("tool_library_max_chars_desc", lang),
                    },
                    "wiki": {
                        "type": "string",
                        "description": _t("tool_library_wiki_desc", lang),
                    },
                    "label": {
                        "type": "string",
                        "description": _t("tool_library_label_desc", lang),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["raw", "translated"],
                        "description": _t("tool_library_mode_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _library_no_online_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "library",
            "description": _t("tool_library_no_online_desc", lang) + _library_wiki_suffix(lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "fetch_article", "fetch_table", "fetch_url", "law"],
                        "description": _t("tool_library_no_online_action_desc", lang),
                    },
                    "query": {
                        "type": "string",
                        "description": _t("tool_library_query_desc", lang),
                    },
                    "task": {
                        "type": "string",
                        "description": _t("tool_library_task_desc", lang),
                    },
                    "subject": {
                        "type": "string",
                        "description": _t("tool_subject_desc", lang),
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": _t("tool_library_keywords_desc", lang),
                    },
                    "background": {
                        "type": "boolean",
                        "description": _t("tool_background_desc", lang),
                    },
                    "title": {
                        "type": "string",
                        "description": _t("tool_library_title_desc", lang),
                    },
                    "url": {
                        "type": "string",
                        "description": _t("tool_library_url_desc", lang),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": _t("tool_library_max_chars_desc", lang),
                    },
                    "wiki": {
                        "type": "string",
                        "description": _t("tool_library_wiki_desc", lang),
                    },
                    "label": {
                        "type": "string",
                        "description": _t("tool_library_label_desc", lang),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["raw", "translated"],
                        "description": _t("tool_library_mode_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _les_ha_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ha_read",
            "description": _t("tool_les_ha_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["room_list", "room_devices", "status"],
                        "description": _t("tool_les_ha_action_desc", lang),
                    },
                    "room": {
                        "type": "string",
                        "description": _t("tool_les_ha_room_desc", lang),
                    },
                    "entity_id": {
                        "type": "string",
                        "description": _t("tool_les_ha_entity_id_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _styr_enhet_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "ha_control",
            "description": _t("tool_styr_enhet_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "string",
                        "description": _t("tool_styr_enhet_entity_id_desc", lang),
                    },
                    "action": {
                        "type": "string",
                        "enum": [
                            "turn_on",
                            "turn_off",
                            "toggle",
                            "set_level",
                            "set_color_temp",
                            "set_color",
                            "ha_history",
                        ],
                        "description": _t("tool_styr_enhet_action_desc", lang),
                    },
                    "brightness_pct": {
                        "type": "integer",
                        "description": _t("tool_styr_enhet_brightness_pct_desc", lang),
                    },
                    "color_temp_kelvin": {
                        "type": "integer",
                        "description": _t("tool_styr_enhet_color_temp_desc", lang),
                    },
                    "rgb_color": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": _t("tool_styr_enhet_rgb_color_desc", lang),
                    },
                    "history_days": {
                        "type": "integer",
                        "description": _t("tool_styr_enhet_history_days_desc", lang),
                    },
                    "history_period": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": _t("tool_styr_enhet_history_period_desc", lang),
                    },
                },
                "required": ["entity_id", "action"],
            },
        },
    }


def _søk_nett_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": _t("tool_søk_nett_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _t("tool_søk_nett_query_desc", lang),
                    },
                    "near_home": {
                        "type": "boolean",
                        "description": _t("tool_søk_nett_near_home_desc", lang),
                    },
                    "subject": {
                        "type": "string",
                        "description": _t("tool_subject_desc", lang),
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": _t("tool_keywords_desc", lang),
                    },
                    "stakes": {
                        "type": "string",
                        "enum": ["trivial", "normal", "high", "extraordinary"],
                        "description": _t("tool_stakes_desc", lang),
                    },
                    "background": {
                        "type": "boolean",
                        "description": _t("tool_background_desc", lang),
                    },
                },
                "required": ["query", "near_home", "stakes"],
            },
        },
    }


def _get_weather_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": _t("tool_get_weather_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": _t("tool_get_weather_location_desc", lang),
                    }
                },
                "required": [],
            },
        },
    }


def _timer_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "timer",
            "description": _t("tool_timer_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["clock", "set", "cancel", "list", "ack"],
                        "description": _t("tool_timer_action_desc", lang),
                    },
                    "prompt": {
                        "type": "string",
                        "description": _t("tool_timer_prompt_desc", lang),
                    },
                    "at_time": {
                        "type": "string",
                        "description": _t("tool_timer_at_time_desc", lang),
                    },
                    "in_seconds": {
                        "type": "integer",
                        "description": _t("tool_timer_in_seconds_desc", lang),
                    },
                    "repeat": {
                        "type": "string",
                        "enum": ["hourly", "daily", "weekdays", "weekend", "weekly"],
                        "description": _t("tool_timer_repeat_desc", lang),
                    },
                    "timer_id": {
                        "type": "string",
                        "description": _t("tool_timer_timer_id_desc", lang),
                    },
                    "action_type": {
                        "type": "string",
                        "enum": ["tts_response", "ha_action", "llm_task", "none"],
                        "description": _t("tool_timer_action_type_desc", lang),
                    },
                    "notify_via": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["tts", "chat"]},
                        "description": _t("tool_timer_notify_via_desc", lang),
                    },
                    "tts_text": {
                        "type": "string",
                        "description": _t("tool_timer_tts_text_desc", lang),
                    },
                    "target_node": {
                        "type": "string",
                        "description": _t("tool_timer_target_node_desc", lang),
                    },
                    "ha_payload": {
                        "type": "string",
                        "description": _t("tool_timer_ha_payload_desc", lang),
                    },
                    "for_user_id": {
                        "type": "string",
                        "description": _t("tool_timer_for_user_id_desc", lang),
                    },
                    "notif_id": {
                        "type": "string",
                        "description": _t("tool_timer_notif_id_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _les_møte_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "read_meeting",
            "description": _t("tool_les_møte_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["reflection", "development"],
                        "description": _t("tool_les_møte_type_desc", lang),
                    },
                    "date": {
                        "type": "string",
                        "description": _t("tool_les_møte_date_desc", lang),
                    },
                },
                "required": ["type"],
            },
        },
    }


def _minne_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "memory",
            "description": _t("tool_minne_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "fetch_unverified", "confirm", "fetch_stm"],
                        "description": _t("tool_minne_action_desc", lang),
                    },
                    "query": {
                        "type": "string",
                        "description": _t("tool_minne_query_desc", lang),
                    },
                    "count": {
                        "type": "integer",
                        "description": _t("tool_minne_count_desc", lang),
                    },
                    "skip": {
                        "type": "integer",
                        "description": _t("tool_minne_skip_desc", lang),
                    },
                    "ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": _t("tool_minne_ids_desc", lang),
                    },
                    "dom": {
                        "type": "string",
                        "enum": ["verified", "denied", "test"],
                        "description": _t("tool_minne_dom_desc", lang),
                    },
                    "date": {
                        "type": "string",
                        "description": _t("tool_minne_date_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _search_argus_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "search_argus",
            "description": _t("tool_søk_i_argus_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _t("tool_søk_i_argus_query_desc", lang),
                    },
                    "limit": {
                        "type": "integer",
                        "description": _t("tool_søk_i_argus_limit_desc", lang),
                    },
                    "entity_id": {
                        "type": "string",
                        "description": _t("tool_søk_i_argus_entity_id_desc", lang),
                    },
                    "since": {
                        "type": "string",
                        "description": _t("tool_søk_i_argus_since_desc", lang),
                    },
                    "until": {
                        "type": "string",
                        "description": _t("tool_søk_i_argus_until_desc", lang),
                    },
                },
                "required": ["query"],
            },
        },
    }


def _mechanic_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "mechanic",
            "description": _t("tool_mechanic_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "delegate", "result", "cancel", "comment"],
                        "description": _t("tool_mechanic_action_desc", lang),
                    },
                    "type": {
                        "type": "string",
                        "enum": ["files", "grep", "log"],
                        "description": _t("tool_mechanic_type_desc", lang),
                    },
                    "query": {
                        "type": "string",
                        "description": _t("tool_mechanic_query_desc", lang),
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": _t("tool_mechanic_files_desc", lang),
                    },
                    "from_line": {
                        "type": "integer",
                        "description": _t("tool_mechanic_from_line_desc", lang),
                    },
                    "to_line": {
                        "type": "integer",
                        "description": _t("tool_mechanic_to_line_desc", lang),
                    },
                    "pattern": {
                        "type": "string",
                        "description": _t("tool_mechanic_pattern_desc", lang),
                    },
                    "directory": {
                        "type": "string",
                        "description": _t("tool_mechanic_directory_desc", lang),
                    },
                    "service": {
                        "type": "string",
                        "description": _t("tool_mechanic_service_desc", lang),
                    },
                    "log_file": {
                        "type": "string",
                        "description": _t("tool_mechanic_log_file_desc", lang),
                    },
                    "lines": {
                        "type": "integer",
                        "description": _t("tool_mechanic_lines_desc", lang),
                    },
                    "filter": {
                        "type": "string",
                        "description": _t("tool_mechanic_filter_desc", lang),
                    },
                    "task": {
                        "type": "string",
                        "description": _t("tool_mechanic_task_desc", lang),
                    },
                    "job_id": {
                        "type": "string",
                        "description": _t("tool_mechanic_job_id_desc", lang),
                    },
                    "comment": {
                        "type": "string",
                        "description": _t("tool_mechanic_comment_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _restart_docker_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "restart_docker_container",
            "description": _t("tool_restart_docker_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "enum": restartable_containers(),
                        "description": _t("tool_restart_docker_container_desc", lang),
                    },
                },
                "required": ["container"],
            },
        },
    }


def _les_indre_tanker_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "inner_thoughts",
            "description": _t("tool_les_indre_tanker_desc", lang),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


def _selvbilde_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "self_image",
            "description": _t("tool_selvbilde_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "update", "edit", "delete"],
                        "description": _t("tool_selvbilde_action_desc", lang),
                    },
                    "observation": {
                        "type": "string",
                        "description": _t("tool_selvbilde_observation_desc", lang),
                    },
                    "fragment": {
                        "type": "string",
                        "description": _t("tool_selvbilde_fragment_desc", lang),
                    },
                    "new_text": {
                        "type": "string",
                        "description": _t("tool_selvbilde_new_text_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _verden_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "world",
            "description": _t("tool_verden_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "read",
                            "update_field",
                            "add",
                            "delete",
                            "edit",
                            "read_var",
                            "set_var",
                            "delete_var",
                            "list_vars",
                        ],
                        "description": _t("tool_verden_action_desc", lang),
                    },
                    "category": {
                        "type": "string",
                        "description": _t("tool_verden_category_desc", lang),
                    },
                    "field": {
                        "type": "string",
                        "description": _t("tool_verden_field_desc", lang),
                    },
                    "value": {
                        "type": "string",
                        "description": _t("tool_verden_value_desc", lang),
                    },
                    "text": {
                        "type": "string",
                        "description": _t("tool_verden_text_desc", lang),
                    },
                    "fragment": {
                        "type": "string",
                        "description": _t("tool_verden_fragment_desc", lang),
                    },
                    "new_text": {
                        "type": "string",
                        "description": _t("tool_verden_new_text_desc", lang),
                    },
                    "key": {
                        "type": "string",
                        "description": _t("tool_verden_key_desc", lang),
                    },
                    "description": {
                        "type": "string",
                        "description": _t("tool_verden_description_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _brukerprofil_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "user_profile",
            "description": _t("tool_brukerprofil_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "read",
                            "update",
                            "update_house",
                            "set_field",
                            "edit",
                            "delete",
                            "curiosity",
                        ],
                        "description": _t("tool_brukerprofil_action_desc", lang),
                    },
                    "observation": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_observation_desc", lang),
                    },
                    "section": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_section_desc", lang),
                    },
                    "field": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_field_desc", lang),
                    },
                    "value": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_value_desc", lang),
                    },
                    "fragment": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_fragment_desc", lang),
                    },
                    "new_text": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_new_text_desc", lang),
                    },
                    "text": {
                        "type": "string",
                        "description": _t("tool_brukerprofil_text_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _notat_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "note",
            "description": _t("tool_notat_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "write",
                            "read",
                            "delete",
                            "clear",
                            "done",
                            "mark_bought",
                            "clear_all",
                            "pin",
                            "unpin",
                            "mark_important",
                            "edit",
                            "resolve",
                            "reopen",
                            "move",
                        ],
                        "description": _t("tool_notat_action_desc", lang),
                    },
                    "list_name": {
                        "type": "string",
                        "enum": [
                            "architect",
                            "shopping",
                            "remember",
                            "kare",
                            "agenda",
                            "observations",
                            "lessons",
                            "resolved",
                        ],
                        "description": _t("tool_notat_list_name_desc", lang),
                    },
                    "text": {
                        "type": "string",
                        "description": _t("tool_notat_text_desc", lang),
                    },
                    "category": {
                        "type": "string",
                        "description": _t("tool_notat_category_desc", lang),
                    },
                    "note_id": {
                        "type": "string",
                        "description": _t("tool_notat_note_id_desc", lang),
                    },
                    "quantity": {
                        "type": "string",
                        "description": _t("tool_notat_quantity_desc", lang),
                    },
                    "unit": {
                        "type": "string",
                        "description": _t("tool_notat_unit_desc", lang),
                    },
                    "context": {
                        "type": "string",
                        "description": _t("tool_notat_context_desc", lang),
                    },
                    "theme": {
                        "type": "string",
                        "enum": list(THEMES),
                        "description": _t("tool_notat_theme_desc", lang),
                    },
                    "title": {
                        "type": "string",
                        "description": _t("tool_notat_title_desc", lang),
                    },
                    "topic_id": {
                        "type": "integer",
                        "description": _t("tool_notat_topic_id_desc", lang),
                    },
                    "conclusion": {
                        "type": "string",
                        "description": _t("tool_notat_conclusion_desc", lang),
                    },
                    "to": {
                        "type": "string",
                        "enum": ["observations", "agenda", "lessons"],
                        "description": _t("tool_notat_to_desc", lang),
                    },
                    "note_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": _t("tool_notat_note_ids_desc", lang),
                    },
                    "remind_on_login": {
                        "type": "boolean",
                        "description": _t("tool_notat_remind_on_login_desc", lang),
                    },
                    "search": {
                        "type": "string",
                        "description": _t("tool_notat_search_desc", lang),
                    },
                    "date_from": {
                        "type": "string",
                        "description": _t("tool_notat_date_from_desc", lang),
                    },
                    "date_to": {
                        "type": "string",
                        "description": _t("tool_notat_date_to_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _reason_freely_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "reason_freely",
            "description": _t("tool_reason_freely_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _t("tool_reason_freely_query_desc", lang),
                    }
                },
                "required": ["query"],
            },
        },
    }


def _les_tankehistorikk_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "thought_history",
            "description": _t("tool_les_tankehistorikk_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": _t("tool_les_tankehistorikk_count_desc", lang),
                    },
                    "filter": {
                        "type": "string",
                        "description": _t("tool_les_tankehistorikk_filter_desc", lang),
                    },
                    "only_recovery": {
                        "type": "boolean",
                        "description": _t("tool_les_tankehistorikk_only_recovery_desc", lang),
                    },
                },
                "required": [],
            },
        },
    }


def _self_trace_tool(lang: str) -> dict:
    """Kåre's own self-improvement trace view — NOT system exploration (that's Mechanic's
    job now, see Fase B / P104). Deliberately kept narrow: only fetch_trace/trace_patterns,
    the two actions that inspect Kåre's own response history rather than the live system."""
    return {
        "type": "function",
        "function": {
            "name": "self_trace",
            "description": _t("tool_self_trace_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["fetch_trace", "trace_patterns"],
                        "description": _t("tool_self_trace_action_desc", lang),
                    },
                    "rid": {
                        "type": "string",
                        "description": _t("tool_self_trace_rid_desc", lang),
                    },
                    "count": {
                        "type": "integer",
                        "description": _t("tool_self_trace_count_desc", lang),
                    },
                    "source": {
                        "type": "string",
                        "enum": ["user", "refl", "meet", "all"],
                        "description": _t("tool_self_trace_source_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _kamera_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "camera",
            "description": _t("tool_kamera_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["snapshot", "events", "frigate", "list", "analyze", "show_event"],
                        "description": _t("tool_kamera_action_desc", lang),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["one", "all"],
                        "description": _t("tool_kamera_scope_desc", lang),
                    },
                    "camera": {
                        "type": "string",
                        "description": _t("tool_kamera_camera_desc", lang),
                    },
                    "query": {
                        "type": "string",
                        "description": _t("tool_kamera_query_desc", lang),
                    },
                    "name": {
                        "type": "string",
                        "description": _t("tool_kamera_name_desc", lang),
                    },
                    "hours_back": {
                        "type": "integer",
                        "description": _t("tool_kamera_hours_back_desc", lang),
                    },
                    "label": {
                        "type": "string",
                        "description": _t("tool_kamera_label_desc", lang),
                    },
                    "count": {
                        "type": "integer",
                        "description": _t("tool_kamera_count_desc", lang),
                    },
                    "faces_only": {
                        "type": "boolean",
                        "description": _t("tool_kamera_faces_only_desc", lang),
                    },
                    "event_id": {
                        "type": "string",
                        "description": _t("tool_kamera_event_id_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _kare_image_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "kare_image",
            "description": _t("tool_kare_image_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["generate"],
                        "description": _t("tool_kare_image_mode_desc", lang),
                    },
                    "prompt": {
                        "type": "string",
                        "description": _t("tool_kare_image_prompt_desc", lang),
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": _t("tool_kare_image_negative_prompt_desc", lang),
                    },
                    "image_b64": {
                        "type": "string",
                        "description": _t("tool_kare_image_image_b64_desc", lang),
                    },
                },
                "required": ["mode", "prompt"],
            },
        },
    }


def _se_bilder_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "view_images",
            "description": _t("tool_se_bilder_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "string",
                        "description": _t("tool_se_bilder_user_id_desc", lang),
                    },
                    "folder": {
                        "type": "string",
                        "enum": ["input", "output", "all"],
                        "description": _t("tool_se_bilder_folder_desc", lang),
                    },
                    "limit": {
                        "type": "integer",
                        "description": _t("tool_se_bilder_limit_desc", lang),
                    },
                    "image_id": {
                        "type": "string",
                        "description": _t("tool_se_bilder_image_id_desc", lang),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["view", "analyze"],
                        "description": _t("tool_se_bilder_mode_desc", lang),
                    },
                },
                "required": ["user_id"],
            },
        },
    }


def _media_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "media",
            "description": _t("tool_media_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "plex_sessions",
                            "plex_history",
                            "plex_search",
                            "plex_library",
                            "plex_episodes",
                            "plex_clients",
                            "plex_play",
                            "radio_status",
                            "radio_play",
                            "radio_stop",
                            "radio_volume",
                        ],
                        "description": _t("tool_media_action_desc", lang),
                    },
                    "query": {
                        "type": "string",
                        "description": _t("tool_media_query_desc", lang),
                    },
                    "rating_key": {
                        "type": "string",
                        "description": _t("tool_media_rating_key_desc", lang),
                    },
                    "client": {
                        "type": "string",
                        "description": _t("tool_media_client_desc", lang),
                    },
                    "offset": {
                        "type": "integer",
                        "description": _t("tool_media_offset_desc", lang),
                    },
                    "user": {
                        "type": "string",
                        "description": _t("tool_media_user_desc", lang),
                    },
                    "limit": {
                        "type": "integer",
                        "description": _t("tool_media_limit_desc", lang),
                    },
                    "station": {
                        "type": "string",
                        "description": _t("tool_media_station_desc", lang),
                    },
                    "volume": {
                        "type": "integer",
                        "description": _t("tool_media_volume_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _announce_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "announce",
            "description": _t("tool_announce_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["say", "display", "list_display"],
                        "description": _t("tool_announce_action_desc", lang),
                    },
                    "text": {
                        "type": "string",
                        "description": _t("tool_announce_text_desc", lang),
                    },
                    "target": {
                        "type": "string",
                        "description": _t("tool_announce_target_desc", lang),
                    },
                    "volume": {
                        "type": "number",
                        "description": _t("tool_announce_volume_desc", lang),
                    },
                    "image_id": {
                        "type": "string",
                        "description": _t("tool_announce_image_id_desc", lang),
                    },
                    "title": {
                        "type": "string",
                        "description": _t("tool_announce_title_desc", lang),
                    },
                    "duration": {
                        "type": "integer",
                        "description": _t("tool_announce_duration_desc", lang),
                    },
                    "position": {
                        "type": "string",
                        "enum": ["bottom_right", "bottom_left", "top_right", "top_left", "center"],
                        "description": _t("tool_announce_position_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _skriv_reflex_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "write_reflex",
            "description": _t("tool_skriv_reflex_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["suggest", "confirm", "reject", "list"],
                        "description": _t("tool_skriv_reflex_action_desc", lang),
                    },
                    "proposal_id": {
                        "type": "string",
                        "description": _t("tool_skriv_reflex_proposal_id_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _dev_proposals_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "dev_proposals",
            "description": _t("tool_dev_proposals_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "confirm", "reject"],
                        "description": _t("tool_dev_proposals_action_desc", lang),
                    },
                    "proposal_id": {
                        "type": "integer",
                        "description": _t("tool_dev_proposals_id_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def _household_tool(lang: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "household",
            "description": _t("tool_household_desc", lang),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set_away", "set_home", "get_status"],
                        "description": _t("tool_household_action_desc", lang),
                    },
                    "reason": {
                        "type": "string",
                        "description": _t("tool_household_reason_desc", lang),
                    },
                    "expected_return": {
                        "type": "string",
                        "description": _t("tool_household_return_desc", lang),
                    },
                },
                "required": ["action"],
            },
        },
    }


def get_tools(lang: str = "nb") -> list:
    """Return all tool definitions with descriptions in the given language."""
    return [
        # Smart home / Home Assistant
        _les_ha_tool(lang),
        _styr_enhet_tool(lang),
        # Information retrieval
        _søk_nett_tool(lang),
        _get_weather_tool(lang),
        _library_tool(lang),
        # Time and timers
        _timer_tool(lang),
        # Meetings
        _les_møte_tool(lang),
        # Memory
        _minne_tool(lang),
        _search_argus_tool(lang),
        # Mechanic and system
        _mechanic_tool(lang),
        _restart_docker_tool(lang),
        # Inner thoughts
        _les_indre_tanker_tool(lang),
        # Self-image and world model
        _selvbilde_tool(lang),
        _verden_tool(lang),
        _brukerprofil_tool(lang),
        # Notes
        _notat_tool(lang),
        # Reasoning and reflection
        _reason_freely_tool(lang),
        _les_tankehistorikk_tool(lang),
        # Self-improvement trace (fetch_trace/trace_patterns only — code/system
        # exploration and shell access moved to Mechanic, see Fase B / P104)
        _self_trace_tool(lang),
        # Cameras
        _kamera_tool(lang),
        # Images
        _kare_image_tool(lang),
        _se_bilder_tool(lang),
        # Media
        _media_tool(lang),
        # Announce
        _announce_tool(lang),
        # Reflex learning
        _skriv_reflex_tool(lang),
        # Dev slot proposals (P118 B4) — admin only: not in any role list in tool_permissions.yaml
        _dev_proposals_tool(lang),
        # Household presence mode
        _household_tool(lang),
    ]


def get_library_no_online(lang: str = "nb") -> dict:
    """Library tool without the online action (child/teen roles)."""
    return _library_no_online_tool(lang)


# Backward compatibility — Norwegian fallback constants.
# These are used in old call sites that have not yet been updated to call get_tools(lang).
KAARE_TOOLS = get_tools("nb")
LIBRARY_NO_ONLINE = get_library_no_online("nb")

# Minimum model size (billions of parameters) required to use each tool.
# Tools not listed here default to tier 0 (any model).
# always_included tools (selvbilde, verden, brukerprofil, les_indre_tanker, les_tankehistorikk)
# are never filtered — handled in filter_tools_by_model().
TOOL_MODEL_TIERS: dict[str, float] = {
    # Tier 0 — any model (0.8B+)
    "timer": 0.0,
    "note": 0.0,
    "ha_control": 0.0,
    "ha_read": 0.0,
    "get_weather": 0.0,
    "announce": 0.0,
    # Tier 3 — needs 3B+ to reason about context and search results
    "web_search": 3.0,
    "library": 3.0,
    "memory": 3.0,
    "camera": 3.0,
    "read_meeting": 3.0,
    "kare_image": 3.0,
    "view_images": 3.0,
    "media": 3.0,
    # Tier 9 — needs 9B+ for multi-step reasoning, shell access, delegation
    "mechanic": 9.0,
    "self_trace": 9.0,
    "restart_docker_container": 9.0,
    "search_argus": 9.0,
    "reason_freely": 9.0,
    "write_reflex": 9.0,
    "dev_proposals": 9.0,
    "household": 0.0,
}
