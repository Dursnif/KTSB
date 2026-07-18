"""
Executor for the 'household' tool — home/away state machine.
"""

from datetime import datetime, timezone
from kaare_core.tools.household_state import (
    read_household_state,
    set_away as _set_away,
    set_home as _set_home,
)
from kaare_core.tools.i18n import t, get_lang

HOUSEHOLD_TOOLS = {"household"}

_ADMIN_ROLES = {"admin", "adult"}


def _write_context_entry_for_admins(text: str, expires: str | None, trigger: str | None) -> None:
    """Write a structured current_context entry to all admin/adult users' household_visible."""
    try:
        from kaare_core.users.store import list_users
        from kaare_core.users.profile_manager import update_household_visible
        entry = {
            "text": text,
            "set": datetime.now().strftime("%Y-%m-%d"),
            "expires": expires,
            "trigger": trigger,
        }
        for u in list_users():
            if u.get("role") in _ADMIN_ROLES:
                update_household_visible(u["username"], "current_context", entry)
    except Exception:
        pass


async def dispatch(name: str, arguments: dict, user_id: str = "global") -> str:
    lang = get_lang(user_id)
    action = arguments.get("action", "")

    if action == "set_away":
        reason = arguments.get("reason") or None
        expected_return = arguments.get("expected_return") or None
        _set_away(reason=reason, expected_return=expected_return)
        ctx_text = "Husstand på bortreise"
        if reason:
            ctx_text += f": {reason}"
        _write_context_entry_for_admins(
            text=ctx_text,
            expires=expected_return,
            trigger="household_home",
        )
        if expected_return:
            return t("tool_household_set_away_ok", lang, ret=expected_return)
        return t("tool_household_set_away_ok_no_date", lang)

    if action == "set_home":
        state = read_household_state()
        if state.get("mode") == "home":
            return t("tool_household_already_home", lang)
        _set_home()
        return t("tool_household_set_home_ok", lang)

    if action == "get_status":
        state = read_household_state()
        mode = state.get("mode", "home")
        updated = state.get("last_updated") or "—"
        lines = [t("tool_household_status", lang, mode=mode, updated=updated)]
        if mode == "away":
            if state.get("away_reason"):
                lines.append(f"Reason: {state['away_reason']}")
            if state.get("expected_return"):
                lines.append(f"Expected return: {state['expected_return']}")
            if state.get("away_since"):
                lines.append(f"Away since: {state['away_since']}")
        return "\n".join(lines)

    return f"Unknown household action: {action}"
