"""
Kåre's tool set in the dev slot (P118 B3, P115 D6) — read-only, enforced by the list itself.

Offered: self_trace, search_argus, memory (search / fetch_unverified / fetch_stm),
utforsk (read / list / grep code), inspiser (logs / services / resources), dev_history (this
slot's own learning.db history for a topic) and delegate_read (one narrow read run by the 9B
executor; its RAW tool output comes back, no 9B prose — P115 D14).
Not offered, and refused if called anyway: everything that writes to the house, configs, code,
memory or notes (ha_control, announce, household, media, camera, restart_docker_container,
note, world / self_image writes, Mechanic's shell / hukommelse / sandkasse) — and read_meeting
(old meeting reports are not evidence, see _KARE_READ_TOOLS).

Every executed call writes exactly one line under the run's rid to tool_calls.log, and its
result is returned to Kåre prefixed with the citeable ref `tool:<rid>#<n>` — the same form the
S4 check resolves (dev_slot/verify.py).
"""

from __future__ import annotations

import copy
import json
import logging
import time
from urllib.parse import urlparse

import httpx

from kaare_core.agents.mechanic import tools as mechanic_tools
from kaare_core.config import get_llm_config, is_agent_tool_enabled, pool_call_options
from kaare_core.memory import learning_store
from kaare_core.model_lock import LockTimeout, lock_agent_pool, pool_order, role_url
from kaare_core.ollama_tool_rescue import post_chat
from kaare_core.tools import executor as kaare_executor
from kaare_core.tools.definitions import get_tools
from kaare_core.tools.i18n import t
from kaare_core.tools.shared_tools import log_tool_call

log = logging.getLogger("dev_slot")

# read_meeting is deliberately NOT here (deviation from P115 D6, 2026-10-03): in the live S3 test
# Kåre cited the old persona meeting as evidence — the very channel that recycled invented
# findings night after night (BUG-log 2026-10-03). A quote from it would pass S4 as "verified".
# Earlier verdicts of the new slot come from learning.db via dev_history instead.
_KARE_READ_TOOLS = {"self_trace", "search_argus", "memory"}
_MEMORY_READ_ACTIONS = ["search", "fetch_unverified", "fetch_stm"]
_INSPISER_READ_ACTIONS = ["logg", "tjenester", "ressurser"]  # git_* point at a dead repo (fP98)
_MECHANIC_READ_TOOLS = {"utforsk", "inspiser"}
_DELEGATE_TOOLS = {"utforsk", "inspiser", "søk_argus"}
_RESULT_MAX = 6000  # chars of one tool result shown to Kåre
_DELEGATE_TIMEOUT_S = 120
_DELEGATE_MAX_TOKENS = 512


def _narrow(tool: dict, field: str, allowed: list[str]) -> dict:
    out = copy.deepcopy(tool)
    out["function"]["parameters"]["properties"][field]["enum"] = list(allowed)
    return out


def _mechanic_schema(name: str) -> dict | None:
    for tool in mechanic_tools.MECHANIC_TOOLS:
        if tool["function"]["name"] == name:
            return copy.deepcopy(tool)
    return None


def _own_schemas(lang: str) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "dev_history",
                "description": t("slot_tool_history_desc", lang),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic_id": {
                            "type": "integer",
                            "description": t("slot_tool_history_topic_desc", lang),
                        },
                    },
                    "required": ["topic_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delegate_read",
                "description": t("slot_tool_delegate_desc", lang),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": t("slot_tool_delegate_task_desc", lang),
                        },
                    },
                    "required": ["task"],
                },
            },
        },
    ]


class SlotTools:
    """Tool schemas + execution for one run. Not thread-safe; calls are sequential."""

    def __init__(self, run_rid: str, lang: str):
        self.run_rid = run_rid
        self.lang = lang
        self.calls = 0  # lines this run has written under run_rid (→ the #n in refs)
        # ref → the full text the tool returned, exactly what Kåre saw. S4 and the 9B verifier
        # check quotes against this; tool_calls.log keeps only a 120-char preview.
        self.results: dict[str, str] = {}
        self.schemas = self._build_schemas()
        self.names = {s["function"]["name"] for s in self.schemas}

    def _build_schemas(self) -> list[dict]:
        schemas: list[dict] = []
        for tool in get_tools(self.lang):
            name = tool.get("function", {}).get("name")
            if name not in _KARE_READ_TOOLS:
                continue
            if name == "memory":
                tool = _narrow(tool, "action", _MEMORY_READ_ACTIONS)
            schemas.append(copy.deepcopy(tool))
        for name in ("utforsk", "inspiser"):
            if not is_agent_tool_enabled("mechanic", name, default=True):
                continue
            schema = _mechanic_schema(name)
            if schema is None:
                continue
            if name == "inspiser":
                schema = _narrow(schema, "action", _INSPISER_READ_ACTIONS)
            schemas.append(schema)
        schemas.extend(_own_schemas(self.lang))
        return schemas

    def _ref(self) -> str:
        return f"tool:{self.run_rid}#{self.calls}"

    def _log(self, name: str, args: dict, result: str, ms: int) -> str:
        log_tool_call(name, args, result, ms, source="meet", rid=self.run_rid)
        self.calls += 1
        return self._ref()

    def _wrap(self, ref: str, result: str) -> str:
        self.results[ref] = str(result)[:_RESULT_MAX]
        text = str(result)
        if len(text) > _RESULT_MAX:
            text = (
                text[:_RESULT_MAX]
                + "\n"
                + t("slot_tool_truncated", self.lang, chars=len(str(result)))
            )
        return f"[{ref}]\n{text}"

    async def execute(self, name: str, args: dict) -> str:
        """Run one tool call. Refusals are not logged and get no ref (nothing to cite)."""
        args = dict(args) if isinstance(args, dict) else {}
        if name not in self.names:
            return t("slot_tool_refused", self.lang, tool=name)
        if "action" in args and name in _MECHANIC_READ_TOOLS:
            # same cleaning as Mechanic's execute_tool (`logg>` → `logg`), so the read-only
            # check below judges the action that will actually run (P119 B8)
            args["action"] = mechanic_tools.normalize_action(name, args["action"])
        action = str(args.get("action", ""))
        if name == "memory" and action not in _MEMORY_READ_ACTIONS:
            return t("slot_tool_refused", self.lang, tool=f"memory.{action}")
        if name == "inspiser" and action not in _INSPISER_READ_ACTIONS:
            return t("slot_tool_refused", self.lang, tool=f"inspiser.{action}")

        if name in _KARE_READ_TOOLS:
            # Kåre's executor writes the tool_calls.log line itself (one per call).
            res = await kaare_executor.execute_tool(
                name, {**args, "_rid": self.run_rid, "_user_id": "global", "_source": "meet"}
            )
            self.calls += 1
            return self._wrap(self._ref(), res)

        if name in _MECHANIC_READ_TOOLS:
            t0 = time.monotonic()
            res = await mechanic_tools.execute_tool(name, args, self.lang)
            ref = self._log(name, args, res, int((time.monotonic() - t0) * 1000))
            return self._wrap(ref, res)

        if name == "dev_history":
            t0 = time.monotonic()
            try:
                hist = learning_store.topic_history(int(args.get("topic_id")))
            except (TypeError, ValueError):
                hist = None
            res = (
                json.dumps(hist, ensure_ascii=False, default=str)
                if hist
                else t("slot_tool_no_topic", self.lang, topic=args.get("topic_id"))
            )
            ref = self._log(name, args, res, int((time.monotonic() - t0) * 1000))
            return self._wrap(ref, res)

        if name == "delegate_read":
            return await self._delegate(str(args.get("task", "")).strip())

        return t("slot_tool_refused", self.lang, tool=name)

    async def _delegate(self, task: str) -> str:
        """One narrow read by the 9B: it chooses ONE read tool call for `task`; code runs it and
        returns the raw output. The 9B's own text is not passed on."""
        if not task:
            return t("slot_tool_delegate_empty", self.lang)
        offered = []
        for name in sorted(_DELEGATE_TOOLS):
            if not is_agent_tool_enabled("mechanic", name, default=True):
                continue
            schema = _mechanic_schema(name)
            if schema is None:
                continue
            if name == "inspiser":
                schema = _narrow(schema, "action", _INSPISER_READ_ACTIONS)
            offered.append(schema)
        if not offered:
            return t("slot_tool_delegate_unavailable", self.lang, error="no read tools enabled")
        offered_names = {s["function"]["name"] for s in offered}
        cfg = get_llm_config("mechanic")
        payload = {
            "model": mechanic_tools.MECHANIC_MODEL,
            "stream": False,
            "think": False,
            "options": pool_call_options(cfg, {"num_predict": _DELEGATE_MAX_TOKENS}),
            "tools": offered,
            "messages": [
                {"role": "system", "content": t("slot_delegate_system", self.lang)},
                {"role": "user", "content": task},
            ],
        }
        path = urlparse(mechanic_tools.MECHANIC_URL).path
        try:
            async with (
                lock_agent_pool(
                    pool_order(role_url("mechanic")), "dev_slot", max_wait=120
                ) as inst_url,
                httpx.AsyncClient(timeout=_DELEGATE_TIMEOUT_S) as client,
            ):
                resp = await post_chat(
                    client,
                    inst_url + path,
                    payload,
                    headers={"x-kaare-source": "dev_slot"},
                    rid=self.run_rid,
                    source="meet",
                )
        except LockTimeout as e:
            return t("slot_tool_delegate_unavailable", self.lang, error=e)
        except Exception as e:
            return t("slot_tool_delegate_unavailable", self.lang, error=e)

        calls = (resp.get("message") or {}).get("tool_calls") or []
        for tc in calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments", {})
            try:
                args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            except Exception:
                args = {}
            if name not in offered_names:  # P117 C26: only what was offered is ever executed
                continue
            if "action" in args:
                args["action"] = mechanic_tools.normalize_action(name, args["action"])
            if name == "inspiser" and str(args.get("action", "")) not in _INSPISER_READ_ACTIONS:
                continue
            t0 = time.monotonic()
            res = await mechanic_tools.execute_tool(name, args, self.lang)
            ref = self._log(name, args, res, int((time.monotonic() - t0) * 1000))
            header = t(
                "slot_tool_delegate_header",
                self.lang,
                tool=name,
                args=json.dumps(args, ensure_ascii=False),
            )
            return self._wrap(ref, f"{header}\n{res}")
        return t("slot_tool_delegate_no_call", self.lang)
