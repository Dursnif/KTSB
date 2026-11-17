"""DT04 R0.6d (a) — P144 v2 register R05: KTSB is told, in one line, that someone opted out of camera recognition.

When at least one household member has «Frigate may track me» off, the camera tool's memory answers and the night
meeting's prompts carry one line: someone chose not to be recognised; an unknown person may be one of them — do not guess
who or build theories. The line never names a person, a time, a camera or a count. Nobody opted out → no line.
Never touches live state (tmp DBs).
"""

import asyncio

import kaare_reflection as refl
from kaare_core.tools import executor_camera
from kaare_core.tools.i18n import t
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore
from tests.test_p144_s08_kare_reads_memory import NOW, _ev, _store
from tests.test_p144_s08_kare_reads_memory import mem as _mem  # noqa: F401 — fixture

NOTICE = {lang: t("fq_opt_out_notice", lang) for lang in ("nb", "en", "de")}


def _opt_out(username="userb"):
    ustore.set_frigate_tracking(username, False)
    frigate_consent.invalidate()


def _episodes(lang="nb"):
    return asyncio.run(executor_camera._memory("episodes", {"from": "2027-01-15", "to": "2027-01-15"}, "boss", lang))


def test_nobody_opted_out_means_no_line(_mem):  # noqa: F811
    _store(_ev("u1", NOW - 600))
    assert frigate_consent.someone_opted_out() is False
    assert NOTICE["nb"] not in _episodes()
    assert refl._camera_opt_out_block("nb") == ""


def test_the_camera_tool_carries_the_line_when_someone_opted_out(_mem):  # noqa: F811
    _store(_ev("u1", NOW - 600))
    _opt_out()
    assert frigate_consent.someone_opted_out() is True
    for lang in ("nb", "en", "de"):
        assert NOTICE[lang] in _episodes(lang)
    last = asyncio.run(executor_camera._memory("last_seen", {"name": "Person A"}, "boss", "nb"))
    assert NOTICE["nb"] in last


def test_the_line_identifies_nobody(_mem):  # noqa: F811
    _opt_out()
    for text in NOTICE.values():
        assert "User B" not in text and "userb" not in text and "Person B" not in text
        assert not any(ch.isdigit() for ch in text)  # no count, time or date


def test_an_opted_out_member_without_a_linked_face_counts_too(_mem):  # noqa: F811
    _opt_out("boss")  # no frigate_faces linked
    assert frigate_consent.someone_opted_out() is True


def test_the_meeting_prompts_carry_the_line(_mem, monkeypatch):  # noqa: F811
    monkeypatch.setattr(refl, "_get_kare_language_r", lambda: "nb")
    assert NOTICE["nb"] not in refl._build_leder_system()
    _opt_out()
    assert NOTICE["nb"] in refl._build_leder_system()
    assert NOTICE["nb"] in refl._camera_opt_out_block("nb")


def test_a_consent_lookup_error_gives_no_line_and_never_raises(_mem, monkeypatch):  # noqa: F811
    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(ustore, "any_frigate_tracking_off", boom, raising=False)
    frigate_consent.invalidate()
    assert frigate_consent.someone_opted_out() is False
