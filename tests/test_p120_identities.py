"""P120 Phase 1 — system identities (global_user / unknown_user), no admin fallback, enforced
tool lists, fail-closed learning gates and the stranger knowledge boundary.
Every test runs against a temporary users.db / settings file — never the live ones."""

import pytest

from adapters import llm_adapter
from kaare_core import config
from kaare_core.routers.router_generate import offered_tool_actions, refuse_unoffered_call
from kaare_core.users import auth, contributors, store


@pytest.fixture
def users_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    store.create_user(
        username="anna", display_name="Anna", role="adult", pin="482915", must_change_pin=False
    )
    return tmp_path


# ── identities ───────────────────────────────────────────────────────────────


def test_system_identities_created_once(users_db):
    store.init_db()  # second start: idempotent
    all_users = store.list_users(include_system=True)
    names = [u["username"] for u in all_users]
    assert names.count("global_user") == 1 and names.count("unknown_user") == 1
    roles = {u["username"]: u["role"] for u in all_users}
    assert roles["global_user"] == "household" and roles["unknown_user"] == "stranger"


def test_people_list_excludes_system_identities(users_db):
    names = [u["username"] for u in store.list_users()]
    assert "global_user" not in names and "unknown_user" not in names and "anna" in names


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "global_user"),
        ("", "global_user"),
        ("global", "global_user"),
        ("anna", "anna"),
        ("global_user", "global_user"),
        ("unknown_user", "unknown_user"),
        ("testbruker_ny2", "unknown_user"),
        ("stian ", "unknown_user"),
    ],
)
def test_resolve_request_user(users_db, raw, expected):
    assert store.resolve_request_user(raw) == expected


@pytest.mark.parametrize("username", ["global_user", "unknown_user"])
def test_system_identity_guards(users_db, username):
    with pytest.raises(ValueError):
        store.delete_user(username)
    with pytest.raises(ValueError):
        store.update_user(username, role="admin")
    with pytest.raises(ValueError):
        store.update_user(username, display_name="x")
    with pytest.raises(ValueError):
        store.generate_temp_pin(username)
    with pytest.raises(ValueError):
        store.update_pin(username, "482915")
    assert auth.login(username, "1234") is None


def test_builtin_admin_cannot_be_deleted(users_db):
    store.create_user(username="other_admin", display_name="x", role="admin", pin="482915")
    with pytest.raises(ValueError):
        store.delete_user("admin")  # even with another admin present


def test_system_roles_cannot_be_given_to_people(users_db):
    for role in ("household", "stranger"):
        with pytest.raises(ValueError):
            store.create_user(username=f"p_{role}", display_name="x", role=role, pin="482915")
        with pytest.raises(ValueError):
            store.update_user("anna", role=role)
    with pytest.raises(ValueError):
        store.create_user(username="global_user", display_name="x", role="adult", pin="482915")


# ── tools per role, enforced ─────────────────────────────────────────────────


def _names(role):
    return {t["function"]["name"] for t in config.get_tools_for_role(role)}


def test_role_tool_sets_and_knowledge_tools():
    household, stranger, unknown = _names("household"), _names("stranger"), _names("no_such_role")
    assert {"ha_control", "timer"} <= household
    assert (
        not {
            "user_profile",
            "inner_thoughts",
            "thought_history",
            "camera",
            "dev_proposals",
            "restart_docker_container",
            "household",
        }
        & household
    )
    assert not {"ha_control", "ha_read", "world", "self_image", "user_profile", "camera"} & stranger
    assert unknown == stranger  # unknown role → most restricted, fail closed


def test_unoffered_tool_and_action_refused():
    offered = offered_tool_actions(config.get_tools_for_role("child"))
    assert refuse_unoffered_call("restart_docker_container", {}, offered, "en")
    assert refuse_unoffered_call("library", {"action": "online", "query": "x"}, offered, "en")
    assert (
        refuse_unoffered_call("library", {"action": "search", "query": "x"}, offered, "en") is None
    )
    assert refuse_unoffered_call("", {}, offered, "en")


# ── learning-influence gate ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mode,allowed,expect",
    [
        (
            "all",
            [],
            {
                "anna": True,
                "global_user": False,
                "unknown_user": False,
                "global": False,
                "kare": True,
            },
        ),
        (
            "selected",
            ["anna", "global_user"],
            {"anna": True, "global_user": True, "unknown_user": False, "kare": True},
        ),
        ("admin_only", [], {"anna": False, "global_user": False, "kare": True}),
    ],
)
def test_contributor_gate(users_db, tmp_path, monkeypatch, mode, allowed, expect):
    settings = tmp_path / "settings.yaml"
    settings.write_text(f"personality_self:\n  contributors: {mode}\n  allowed_users: {allowed}\n")
    monkeypatch.setattr(contributors, "_SETTINGS_PATH", settings)
    for user, ok in expect.items():
        assert contributors.contributor_allowed("personality_self", "all", user) is ok, user


def test_contributor_gate_fails_closed(users_db, tmp_path, monkeypatch):
    monkeypatch.setattr(contributors, "_SETTINGS_PATH", tmp_path / "missing" / "settings.yaml")
    assert contributors.contributor_allowed("personality_self", "all", "anna") is False
    assert contributors.contributor_allowed("personality_self", "all", "kare") is True


# ── knowledge boundary ───────────────────────────────────────────────────────


def test_stranger_prompt_has_no_household_knowledge(users_db, monkeypatch):
    monkeypatch.setattr(llm_adapter, "_HOUSEHOLD_BLOCK", "HOUSEHOLD-MEMBERS-SECRET")
    monkeypatch.setattr(llm_adapter, "_load_kare_reminders", lambda lang: "KARE-NOTES-SECRET")
    monkeypatch.setattr(
        llm_adapter, "_build_situational_context", lambda uid, lang: "SITUATION-SECRET"
    )
    stranger = llm_adapter._build_system("", user_id="unknown_user", include_time=False)
    household = llm_adapter._build_system("", user_id="global_user", include_time=False)
    for secret in ("HOUSEHOLD-MEMBERS-SECRET", "KARE-NOTES-SECRET", "SITUATION-SECRET"):
        assert secret not in stranger
        assert secret in household


@pytest.mark.parametrize(
    "uid,expected",
    [
        ("unknown_user", True),
        ("global_user", False),
        ("anna", False),
        ("kare", False),
        ("global", False),
        ("", False),
    ],
)
def test_is_stranger(users_db, uid, expected):
    assert llm_adapter.is_stranger(uid) is expected
