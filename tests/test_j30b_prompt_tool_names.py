"""Run-queue J30b (b): every prompt / tool text names tools that exist. The per-turn rid note named the removed
`inspect_system` (J30); the night meeting told Mechanic to use `search_argus`, `inspect_system(…)` and `note(…)`, none of
which he has there (his tools: utforsk, inspiser, søk_argus, shell, hukommelse); `sys_invalid_path` pointed to `les_fil()`
/ `liste_filer()`, which are `utforsk` actions now. A call-shaped name (`name(` + an argument) or a «Bruk/Use/Verwende
<name_with_underscore>» in any i18n text must be a tool Kåre or Mechanic has (or an alias an executor still accepts)."""

import re
from pathlib import Path

from kaare_core.agents.mechanic.tools import UNDERSØKER_TOOLS
from kaare_core.tools import i18n

ROOT = Path(__file__).resolve().parent.parent
_NAME = re.compile(r'"name":\s*"([a-zæøå_]+)"')
_CALL = re.compile(r"\b([a-zæøå_]{4,})\((?:action|rid|query|sti|path|[a-z_]+=|\))")
_USE = re.compile(r"(?:Bruk|Use|Verwende|bruk|use|verwende)\s+[`'«\"]?([a-zæøå]{3,}_[a-zæøå_]+)")


def _real_tools() -> set[str]:
    src = "".join((ROOT / f).read_text() for f in (
        "kaare_core/tools/definitions.py", "kaare_core/agents/mechanic/tools.py", "kaare_core/dev_slot/tools.py"))
    return set(_NAME.findall(src))


def _aliases() -> set[str]:
    src = "".join(p.read_text() for p in (ROOT / "kaare_core/tools").glob("executor*.py"))
    src += (ROOT / "kaare_core/agents/mechanic/tools.py").read_text() + (ROOT / "kaare_core/config.py").read_text()
    return set(re.findall(r'"([a-zæøå_]{4,})"', src))


def _texts():
    for table in (v for v in vars(i18n).values() if isinstance(v, dict)):
        for key, per_lang in table.items():
            if isinstance(per_lang, dict):
                for lang, text in per_lang.items():
                    if isinstance(text, str):
                        yield key, lang, text


def test_every_named_tool_exists():
    known = _real_tools() | _aliases()
    stale = sorted({(key, name) for key, _lang, text in _texts()
                    for name in _CALL.findall(text) + _USE.findall(text) if name not in known})
    assert stale == []


def test_the_meeting_mechanic_is_told_only_his_own_tools():
    his = {t["function"]["name"] for t in UNDERSØKER_TOOLS}
    for lang in ("nb", "en", "de"):
        text = i18n.t("meet_mechanic_investigate_user", lang)
        named = set(_CALL.findall(text)) | {w for w in re.findall(r"[a-zæøå_]{4,}", text) if "_" in w}
        assert named <= his | {"list_name"}, named - his
