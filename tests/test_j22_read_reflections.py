"""J22 (BUG-log 2026-11-05; the user's chat 2026-11-07 ~11:47): Kåre could only read the reflection meetings of 22.–26. April —
the reading tool looked only at the old shared folder, not the per-user folders `reflections/<user>/` where every meeting
since then lives. Rules: a user reads their own meetings + the old shared ones; nobody reads another user's through Kåre
(admin as today — the GUI with PIN is admin's way); no path tricks. Tmp folders; nothing live."""
import asyncio

import pytest

from kaare_core.tools import executor_memory as em


@pytest.fixture
def refl(tmp_path, monkeypatch):
    root = tmp_path / "reflections"
    (root / "userone").mkdir(parents=True)
    (root / "userb").mkdir()
    for d in ("2026-04-22", "2026-04-26"):
        (root / f"{d}.md").write_text(f"# Refleksjon – {d}\nfelles", encoding="utf-8")
    for d in ("2026-11-06", "2026-11-07"):
        (root / "userone" / f"{d}.md").write_text(f"# Refleksjon – {d}\nuserones møte {d}", encoding="utf-8")
    (root / "userone" / "latest.md").write_text("# Refleksjon – 2026-11-07\nuserones møte 2026-11-07", encoding="utf-8")
    (root / "userb" / "2026-11-07.md").write_text("# Refleksjon – 2026-11-07\nuserbs møte", encoding="utf-8")
    old_latest = tmp_path / "reflection_latest.md"
    old_latest.write_text("# Refleksjon – 2026-04-26\nfelles", encoding="utf-8")
    monkeypatch.setattr(em, "REFLECTIONS_DIR", root)
    monkeypatch.setattr(em, "REFLECTION_LATEST", old_latest)
    monkeypatch.setattr(em, "get_lang", lambda *_a: "nb")  # rule 4: never the house's user settings
    return root


def test_the_newest_meeting_is_the_users_own_not_april(refl):
    out = em._read_reflection(None, lang="nb", user_id="userone")
    assert "userones møte 2026-11-07" in out and "felles" not in out


def test_a_date_in_the_users_folder_is_found(refl):
    assert "userones møte 2026-11-06" in em._read_reflection("2026-11-06", lang="nb", user_id="userone")


def test_the_old_shared_meetings_are_still_readable(refl):
    assert "felles" in em._read_reflection("2026-04-22", lang="nb", user_id="userone")


def test_a_missing_date_lists_the_newest_with_their_year(refl):
    out = em._read_reflection("2026-11-01", lang="nb", user_id="userone")
    assert "2026-11-07" in out and "2026-11-06" in out and "2026-04-26" in out


def test_never_another_users_meeting(refl):
    out = em._read_reflection("2026-11-07", lang="nb", user_id="userone")
    assert "userbs møte" not in out
    assert "userbs møte" not in em._read_reflection(None, lang="nb", user_id="userone")


@pytest.mark.parametrize("uid", ["global", "", "../userb", "userb/../userone"])
def test_no_user_or_a_path_trick_reads_only_the_shared_meetings(refl, uid):
    out = em._read_reflection(None, lang="nb", user_id=uid)
    assert "userbs møte" not in out and "userones møte" not in out
    assert "felles" in out


def test_a_bad_date_is_never_a_path(refl):
    out = em._read_reflection("../userb/2026-11-07", lang="nb", user_id="userone")
    assert "userbs møte" not in out


def test_the_tool_passes_the_caller(refl):
    out = asyncio.run(em.dispatch("read_meeting", {"type": "reflection", "_user_id": "userone", "_lang": "nb"}))
    assert "userones møte 2026-11-07" in out
