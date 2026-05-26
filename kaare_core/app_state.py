from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kaare_core.memory.short_term import ShortTermMemory

STM: "ShortTermMemory | None" = None

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
