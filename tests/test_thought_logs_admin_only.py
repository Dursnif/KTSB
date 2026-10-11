"""thought_history / inner_thoughts are admin only (2026-10-11).

The think cache (state/think_cache.jsonl) holds prompt previews and replies with no user_id, and
the inner thoughts are fed by the Argus digest, which quotes the start of every user message —
so both can show one user another user's (also PIN-encrypted) conversations. Both are removed
from every non-admin role's tool list and refused in the executor, fail closed.
Every test runs against a temporary users.db; the readers are patched, so no live state is read
or consumed (inner_thoughts.txt is deleted on read)."""

import asyncio

import pytest

from kaare_core import config
from kaare_core.tools import executor_memory
from kaare_core.tools.i18n import t
from kaare_core.users import store

_TOOLS = ("thought_history", "inner_thoughts", "les_tankehistorikk", "les_indre_tanker")
_SECRET = "SECRET-FROM-ANOTHER-USER"


@pytest.fixture
def users_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "users.db")
    store.init_db()
    for name, role in (
        ("kid", "child"),
        ("teen1", "teen"),
        ("ya1", "young_adult"),
        ("anna", "adult"),
    ):
        store.create_user(
            username=name, display_name=name, role=role, pin="482915", must_change_pin=False
        )
    store.create_user(
        username="boss", display_name="boss", role="admin", pin="482915", must_change_pin=False
    )
    monkeypatch.setattr(executor_memory, "read_think_history", lambda **_kw: [{"x": _SECRET}])
    monkeypatch.setattr(executor_memory, "format_for_kare", lambda entries: str(entries))
    monkeypatch.setattr(executor_memory, "_read_inner_thoughts", lambda lang="nb": _SECRET)
    return tmp_path


def _run(name, user_id):
    args = {"count": 5} if user_id is None else {"count": 5, "_user_id": user_id}
    return asyncio.run(executor_memory.dispatch(name, args))


@pytest.mark.parametrize(
    "user_id",
    ["kid", "teen1", "ya1", "anna", "global_user", "unknown_user", "nobody", "global", "", None],
)
@pytest.mark.parametrize("name", _TOOLS)
def test_non_admin_refused(users_db, name, user_id):
    out = _run(name, user_id)
    assert _SECRET not in out
    assert out == t("mem_admin_only_tool", executor_memory.get_lang(user_id or "global"))


@pytest.mark.parametrize("name", _TOOLS)
def test_admin_gets_answer(users_db, name):
    assert _SECRET in _run(name, "boss")


@pytest.mark.parametrize(
    "role", ["child", "teen", "young_adult", "adult", "household", "stranger", "no_such_role"]
)
def test_not_offered_to_non_admin_roles(role):
    names = {t["function"]["name"] for t in config.get_tools_for_role(role)}
    assert not names & set(_TOOLS)


def test_offered_to_admin():
    names = {t["function"]["name"] for t in config.get_tools_for_role("admin")}
    assert {"thought_history", "inner_thoughts"} <= names


def test_always_included_check_still_clean():
    # the always-tools validator must not flag the deliberate removal
    assert config.verify_always_included_survives("adult") == []
    assert config.verify_always_included_survives("child") == []


def test_refusal_text_in_three_languages():
    texts = {lang: t("mem_admin_only_tool", lang) for lang in ("nb", "en", "de")}
    assert len(set(texts.values())) == 3 and all(texts.values())
