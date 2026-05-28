import asyncio
import httpx
import yaml
from pathlib import Path

from adapters.web_search_adapter import søk_nett as _søk_nett
from kaare_core.config import get_service as _svc, get_model as _cfg_model, get_llm_config as _llm_cfg
from kaare_core.memory.long_term import get_ltm
from kaare_core.tools.executor_personality import PERSONALITY_CORE_TEXT
from kaare_core.tools.i18n import t, get_lang
from kaare_core.tools.think_cache import read_think_history, format_for_kare, log_think, extract_conclusion

_SETTINGS_PATH = Path("/kaare/configs/settings.yaml")
_KARE_MODEL = _cfg_model("kare")
_OLLAMA_PROXY_URL = _llm_cfg("default")["base_url"]

LIBRARY_TOOLS = {
    "søk_nett",
    "library",
    "spør_frøken_library_online",
    "spør_frøken_library",
    "hent_wiki_artikkel",
    "reason_freely",
}


def _build_location_prefix() -> str:
    try:
        s = yaml.safe_load(_SETTINGS_PATH.read_text(encoding="utf-8"))
        loc = s.get("location") or s.get("lokasjon", {})
    except Exception:
        return ""
    city = loc.get("city") or loc.get("sted", "")
    country = loc.get("country") or loc.get("land", "")
    if city and country:
        return f"[Kontekst: {city}, {country}] "
    if country:
        return f"[Kontekst: {country}] "
    return ""

_LOCATION_PREFIX = _build_location_prefix()


def location_prefix() -> str:
    return _LOCATION_PREFIX


async def _ask_library_online(question: str, arguments: dict, lang: str = "nb") -> str:
    if not question.strip():
        return t("lib_empty_query", lang)
    if not _llm_cfg("cloud").get("enabled", True):
        return t("lib_online_disabled", lang)
    if not _llm_cfg("library").get("enabled", True):
        return t("lib_library_disabled", lang)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library/cloud",
                json={"question": question},
            )
            r.raise_for_status()
            answer = r.json().get("answer", "").strip()
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library_online",
                    query=question,
                    response=answer,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                ))
            except Exception:
                pass
            return answer if answer else t("lib_online_no_answer", lang)
    except Exception as e:
        return t("lib_online_unavailable", lang, error=e)


async def _ask_library(question: str, arguments: dict, lang: str = "nb") -> str:
    if not question.strip():
        return t("lib_empty_query", lang)
    if not _llm_cfg("library").get("enabled", True):
        return t("lib_library_disabled", lang)
    loc = location_prefix()
    question_with_context = f"{loc}{question}" if loc else question
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library",
                json={"question": question_with_context},
            )
            r.raise_for_status()
            answer = r.json().get("answer", "").strip()
            try:
                asyncio.create_task(get_ltm().log_agent_message(
                    from_agent="kare",
                    to_agent="miss_library",
                    query=question,
                    response=answer,
                    rid=arguments.get("_rid", ""),
                    user_id=arguments.get("_user_id", "global"),
                ))
            except Exception:
                pass
            return answer if answer else t("lib_no_answer", lang)
    except Exception as e:
        return t("lib_unavailable", lang, error=e)


async def _fetch_wiki_article(title: str, max_chars: int = 8000, lang: str = "nb") -> str:
    if not title.strip():
        return t("lib_empty_title", lang)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/wiki/article",
                json={"title": title, "max_chars": max_chars},
            )
            r.raise_for_status()
            data = r.json()
        if not data.get("text"):
            return t("lib_article_not_found", lang, title=title)
        return f"# {data['title']} ({data['chunk_count']} biter)\n\n{data['text']}"
    except Exception as e:
        return t("lib_article_error", lang, error=e)


async def _fetch_url(url: str, arguments: dict, lang: str = "nb") -> str:
    if not url.strip():
        return t("lib_empty_url", lang)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{_svc('internal', 'agents')}/ask/miss_library/hent_url",
                json={"url": url},
            )
            r.raise_for_status()
            answer = r.json().get("answer", "").strip()
        try:
            asyncio.create_task(get_ltm().log_agent_message(
                from_agent="kare",
                to_agent="miss_library",
                query=url,
                response=answer,
                rid=arguments.get("_rid", ""),
                user_id=arguments.get("_user_id", "global"),
            ))
        except Exception:
            pass
        return answer if answer else t("lib_url_no_answer", lang)
    except Exception as e:
        return t("lib_url_error", lang, error=e)


async def _reason_freely(query: str, lang: str = "nb") -> str:
    if not query.strip():
        return t("lib_empty_rf_query", lang)
    rf_cfg = _llm_cfg("reason_freely")
    system = (
        PERSONALITY_CORE_TEXT
        + "\n\n---\n\n"
        "Du bruker nå din fulle interne kunnskap fritt. "
        "Ingen smarthus-begrensninger gjelder her. "
        "Tenk åpent og presist basert på det du vet fra treningen. "
        "Dette er et internt verktøykall — svaret integreres i din vanlige respons til brukeren."
    )
    try:
        base_url = rf_cfg.get("base_url", _OLLAMA_PROXY_URL)
        options = rf_cfg.get("options", {})
        provider = rf_cfg.get("provider", "ollama")

        if provider == "vllm":
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": query},
            ]
            payload: dict = {
                "model": _KARE_MODEL,
                "messages": messages,
                "stream": False,
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
            msg = (data.get("choices") or [{}])[0].get("message", {})
            think_text = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
            text = (msg.get("content") or "").strip()
        else:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{base_url}/api/generate",
                    json={
                        "model": _KARE_MODEL,
                        "prompt": query,
                        "system": system,
                        "stream": False,
                        "think": rf_cfg.get("think", True),
                        "options": options,
                    },
                    headers={"x-kaare-source": "reason_freely"},
                )
                r.raise_for_status()
                data = r.json()
            think_text = (data.get("thinking") or "").strip()
            text = (data.get("response") or "").strip()

            if not think_text and "<think>" in text.lower():
                upper = text.upper()
                t_start = upper.find("<THINK>")
                t_end = upper.find("</THINK>")
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

        return text if text else t("lib_empty_model_answer", lang)
    except Exception as e:
        return t("lib_rf_error", lang, error=e)


async def dispatch(name: str, arguments: dict) -> str:
    lang = get_lang(arguments.get("_user_id", "global"))

    if name == "søk_nett":
        raw_query = arguments.get("query", "")
        loc = location_prefix()
        query = f"{loc}{raw_query}" if loc else raw_query
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
            return await _ask_library(arguments.get("spørsmål", ""), arguments, lang)
        if action == "hent_artikkel":
            return await _fetch_wiki_article(
                arguments.get("title", ""),
                arguments.get("max_chars", 8000),
                lang,
            )
        if action == "hent_url":
            return await _fetch_url(arguments.get("url", ""), arguments, lang)
        if action == "online":
            return await _ask_library_online(arguments.get("spørsmål", ""), arguments, lang)
        return f"Unknown action for library: '{action}'. Valid: søk, hent_artikkel, hent_url, online."

    if name == "spør_frøken_library_online":
        return await _ask_library_online(arguments.get("spørsmål", ""), arguments, lang)

    if name == "spør_frøken_library":
        return await _ask_library(arguments.get("spørsmål", ""), arguments, lang)

    if name == "hent_wiki_artikkel":
        return await _fetch_wiki_article(
            arguments.get("title", ""),
            arguments.get("max_chars", 8000),
            lang,
        )

    if name == "reason_freely":
        return await _reason_freely(arguments.get("query", ""), lang)

    return f"[executor_library] Unknown tool: {name}"
