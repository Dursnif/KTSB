from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kaare_core.memory.short_term import ShortTermMemory, STMRegistry

STM_REGISTRY: "STMRegistry | None" = None


def get_stm(user_id: str) -> "ShortTermMemory":
    return STM_REGISTRY.get(user_id)

CAPABILITY_MAP: dict = {}
ALIASES: dict = {}
LANG_NORMALIZE: dict = {}

_AGENT_ENABLED: dict[str, bool] = {}
_OLLAMA_PULL_STATUS: dict[str, dict] = {}

_MEETING_STATUS: dict = {
    "reflection": {"running": False, "progress": 0, "round": 0, "max_rounds": 6,
                   "step": "", "log": [], "started_at": None, "source": None},
    "dev":        {"running": False, "progress": 0, "round": 0, "max_rounds": 6,
                   "step": "", "log": [], "started_at": None, "source": None},
}
_MEETING_PROCS: dict = {}
_NIGHTJOB_STATUS: dict = {
    "running": False, "episodes": 0, "compressed": 0,
    "step": "", "log": [], "started_at": None, "finished_at": None, "error": None,
}
_NIGHTJOB_PROC = None

_REFLECTION_ENABLED: bool = False
_JANG_INTERVAL_S: int = 600

_last_jang_run_time: float = 0.0
_last_user_prompt_time: float = 0.0
