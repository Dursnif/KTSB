"""P144-s08 — Kåre reads the Frigate memory: camera actions last_seen / episodes / episode.

- probability words are computed at read time from the stored check (never stored): Frigate claim alone →
  «sannsynlig»; two independent layers agree (confirmed) → «høyst sannsynlig»; doubt / small crop → «usikkert»;
  rejected → «lite sannsynlig»;
- an opted-out person is never returned (read-time consent, fail closed);
- callers that are not logged-in household users (household / stranger / service ids) get no names;
- older than the event tier → answered from visits / day rollups, with the detail level named;
- switch off or no frigate.db → «camera memory not available», never an error.
Never touches live state: frigate.db, users.db, frigate_user.yaml and frigate_faces.yaml point at tmp_path.
Placeholders only (Person A …).
"""

import asyncio
from zoneinfo import ZoneInfo

import pytest

from kaare_core.frigate_user import faces, query
from kaare_core.frigate_user import settings as fsettings
from kaare_core.frigate_user import store as fstore
from kaare_core.tools import definitions, executor_camera
from kaare_core.users import frigate_consent
from kaare_core.users import store as ustore

OSLO = ZoneInfo("Europe/Oslo")
NOW = 1_800_000_000.0  # 2027-01-15 09:00 Oslo
DAY = 86400
CAMS = {"cam_door": {"display_name": "Inngang", "role": "front_door"},
        "cam_yard": {"display_name": "Gårdsplass", "role": "driveway"}}
L0_PASS = {"layer": "l0", "verdict": "pass", "strong": False, "reason": "face_px=20000", "contradictions": []}
L0_SMALL = {"layer": "l0", "verdict": "unsure", "strong": False, "reason": "face_px=900<10000", "contradictions": []}
DOUBT = {"strong": [{"layer": "l3", "field": "beard", "seen": "full", "profile": "no"}], "reason": "beard full≠no",
         "rule": "two_observations"}


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(ustore, "DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(fstore, "DB_PATH", tmp_path / "frigate" / "frigate.db")
    monkeypatch.setattr(fsettings, "CONFIG_PATH", tmp_path / "frigate_user.yaml")
    monkeypatch.setattr(faces, "CONFIG_PATH", tmp_path / "frigate_faces.yaml")
    (tmp_path / "frigate_user.yaml").write_text("enabled: true\nretention_dry_run: false\n")
    (tmp_path / "frigate_faces.yaml").write_text(
        "faces:\n  Person A:\n    class: safe_guest\n  Person B:\n    class: household\n")
    monkeypatch.setattr(query, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(fstore, "get_local_tz", lambda: OSLO)
    monkeypatch.setattr(query, "_camera_cards", lambda: CAMS)
    monkeypatch.setattr(executor_camera, "_audit", lambda *a, **k: None)
    fstore.reset_for_tests()
    frigate_consent.invalidate()
    ustore.init_db()
    ustore.create_user(username="boss", display_name="Boss", role="admin", pin="482915")
    ustore.create_user(username="userb", display_name="User B", role="adult", pin="482915")
    ustore.update_user("userb", frigate_faces=["Person B"])
    yield tmp_path
    frigate_consent.invalidate()
    fstore.reset_for_tests()


def _ev(event_id, start, *, camera="cam_door", ref=None, kind="unknown", claim="none", check=None, score=0.82,
        label="person"):
    return fstore.FrigateUserEvent(
        event_id=event_id, camera=camera, label=label, start_ts=start, end_ts=start + 30, person_ref=ref,
        person_kind=kind, claim_status=claim, claim_score=score if ref else None, check=check or {},
        place="gang" if camera == "cam_door" else None)


def _guest(event_id, start, *, claim="unverified", check=None, camera="cam_door"):
    check = {"decision": claim, "l0": L0_PASS} if check is None else {"decision": claim, **check}
    return _ev(event_id, start, camera=camera, ref="Person A", kind="safe_guest", claim=claim, check=check)


def _store(*events):
    for e in events:
        assert fstore.record_event(e)
    fstore.close_visits(now=NOW)


# ── certainty (pure) ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status, check, word", [
    ("unverified", {"l0": L0_PASS}, "likely"),
    ("confirmed", {"l0": L0_PASS}, "likely"),  # J14 / P149 D1: was «very_likely» — machines alone at most «likely»
    ("confirmed", {"l0": L0_PASS, "human": {"by": "u", "verdict": "yes"}}, "very_likely"),  # a person's «ja»
    ("unverified", {"l0": L0_SMALL}, "uncertain"),
    ("unverified", {"l0": L0_PASS, "doubt": DOUBT}, "uncertain"),
    ("rejected", {"doubt": DOUBT}, "unlikely"),
    ("none", {}, None),
])
def test_certainty_from_stored_check(status, check, word):
    assert query.certainty(check, status) == word


@pytest.mark.parametrize("lang, key, text", [
    ("nb", "likely", "sannsynlig"), ("nb", "very_likely", "høyst sannsynlig"), ("nb", "uncertain", "usikkert"),
    ("nb", "unlikely", "lite sannsynlig"), ("en", "very_likely", "very likely"), ("de", "likely", "wahrscheinlich"),
])
def test_certainty_words_per_language(lang, key, text):
    assert query.certainty_word(key, lang) == text


def test_certainty_is_never_stored(mem):
    _store(_guest("a1", NOW - 3600))
    ev = fstore.events_between(0, NOW * 2)[0]
    assert "certainty" not in ev.check and "sannsynlig" not in str(ev.check)


# ── last_seen ───────────────────────────────────────────────────────────────────


def test_last_seen_guest_is_likely(mem):
    _store(_guest("a1", NOW - 2 * DAY), _guest("a2", NOW - 3600))
    out = query.last_seen("person a", names=True, lang="nb", now=NOW)
    assert "Person A" in out
    assert "sannsynlig" in out and "høyst sannsynlig" not in out
    assert "15.01.2027 08:00" in out  # NOW - 1 h, household local time
    assert "Inngang" in out
    assert "0.82" in out  # Frigate's own score, labelled as the sensor reading


def test_last_seen_two_layers_agree_is_very_likely(mem):
    # J14 / P149 D1: was «høyst sannsynlig»; machine «confirmed» now reads «sannsynlig» (only a person's «ja» goes higher)
    _store(_guest("a1", NOW - 3600, claim="confirmed"))
    out = query.last_seen("Person A", names=True, lang="nb", now=NOW)
    assert "sannsynlig" in out and "høyst sannsynlig" not in out


def test_last_seen_doubt_is_uncertain(mem):
    _store(_guest("a1", NOW - 3600, check={"l0": L0_PASS, "doubt": DOUBT}))
    out = query.last_seen("Person A", names=True, lang="nb", now=NOW)
    assert "usikkert" in out


def test_last_seen_small_crop_is_uncertain(mem):
    _store(_guest("a1", NOW - 3600, check={"l0": L0_SMALL}))
    assert "usikkert" in query.last_seen("Person A", names=True, lang="nb", now=NOW)


def test_last_seen_household_member_by_face_or_display_name(mem):
    _store(_ev("b1", NOW - 600, ref="userb", kind="household", claim="unverified",
               check={"decision": "unverified", "l0": L0_PASS}))
    for asked in ("Person B", "user b", "USERB"):
        out = query.last_seen(asked, names=True, lang="nb", now=NOW)
        assert "User B" in out and "sannsynlig" in out, asked


def test_opted_out_person_is_never_returned(mem):
    _store(_ev("b1", NOW - 600, ref="userb", kind="household", claim="unverified",
               check={"decision": "unverified", "l0": L0_PASS}))
    ustore.set_frigate_tracking("userb", False)
    frigate_consent.invalidate()
    for asked in ("Person B", "userb", "User B"):
        out = query.last_seen(asked, names=True, lang="nb", now=NOW)
        # indistinguishable from someone never seen: only the question is echoed back, no time, no place
        never = query.last_seen("Never Seen", names=True, lang="nb", now=NOW)
        assert out.replace(asked, "X") == never.replace("Never Seen", "X")
    listing = query.episodes(NOW - DAY, NOW, names=True, lang="nb")
    assert "User B" not in listing and "userb" not in listing.lower() and "Person B" not in listing


def test_unknown_person_says_so(mem):
    # an older unnamed event: the memory's start line must not be mistaken for Person A's visit time
    _store(_ev("u0", NOW - 2 * DAY + 1800), _guest("a1", NOW - 3600))
    out = query.last_seen("Person Z", names=True, lang="nb", now=NOW)
    assert "Person Z" in out and "08:00" not in out


def test_no_names_for_household_or_stranger(mem):
    _store(_guest("a1", NOW - 3600))
    out = query.last_seen("Person A", names=False, lang="nb", now=NOW)
    assert "Person A" not in out and "08:00" not in out
    listing = query.episodes(NOW - DAY, NOW, names=False, lang="nb")
    assert "Person A" not in listing
    assert "08:00" in listing  # times and places stay
    assert "Inngang" in listing


@pytest.mark.parametrize("uid, allowed", [
    ("boss", True), ("userb", True), ("global_user", False), ("unknown_user", False), ("global", False),
    ("kare", False), ("nobody", False), ("", False),
])
def test_names_allowed_only_for_logged_in_household_users(mem, uid, allowed):
    assert query.names_allowed(uid) is allowed


def test_older_than_event_tier_names_the_level(mem, monkeypatch):
    # P150: the event leaves the event tier because the store is over its size limit (was: older than 7 days)
    fsettings.update({"frigate_db_max_mb": 1})
    monkeypatch.setattr(fstore, "db_logical_bytes", lambda: 50 * 1_048_576)
    _store(_guest("a1", NOW - 10 * DAY))
    report = fstore.run_retention(now=NOW, dry_run=False)
    assert not report.get("errors"), report
    out = query.last_seen("Person A", names=True, lang="nb", now=NOW)
    assert "05.01.2027" in out
    assert "besøksnivå" in out


def test_day_rollup_only_answers_at_day_level(mem):
    def seed(conn):
        fstore._add_daily(conn, "2026-12-01", "Person A", "safe_guest", ["gang"], NOW - 45 * DAY,
                          NOW - 45 * DAY + 600, 3, 1)
    fstore._tx(seed)
    out = query.last_seen("Person A", names=True, lang="nb", now=NOW)
    assert "01.12.2026" in out and "dagnivå" in out
    assert "sannsynlig" not in out  # the per-event check is not kept at day level — no word invented


# ── episodes / episode ─────────────────────────────────────────────────────────


def _two_episodes():
    _store(_guest("a1", NOW - 7200), _ev("c1", NOW - 600, camera="cam_yard", label="car"))
    eps = fstore.episodes_between(0, NOW * 2)
    first = eps[0].episode_id
    fstore.conclude_episode(first, {"status": "done", "by": "model", "summary": "Person A ringte på.",
                                    "unusual": {"value": True, "reason": "sjelden"},
                                    "confidence": {"level": "medium", "reasons": []}})
    fstore.set_episode_scene(first, {"status": "done", "scene": "En person ved døra", "persons": 1}, "Inngang")
    return eps[0].episode_id, eps[1].episode_id


def test_episodes_newest_first_with_word_and_unusual(mem):
    first, second = _two_episodes()
    out = query.episodes(NOW - DAY, NOW, names=True, lang="nb")
    assert out.index(f"#{second}") < out.index(f"#{first}")
    assert "Person A" in out and "sannsynlig" in out
    assert "Person A ringte på." in out
    assert "sjelden" in out


def test_episodes_place_filter(mem):
    first, second = _two_episodes()
    out = query.episodes(NOW - DAY, NOW, place="inngangsdøra", names=True, lang="nb")
    assert f"#{first}" in out and f"#{second}" not in out


def test_episodes_person_filter_and_limit(mem):
    first, second = _two_episodes()
    out = query.episodes(NOW - DAY, NOW, person="person a", names=True, lang="nb")
    assert f"#{first}" in out and f"#{second}" not in out
    assert f"#{second}" in query.episodes(NOW - DAY, NOW, limit=1, names=True, lang="nb")
    assert f"#{first}" not in query.episodes(NOW - DAY, NOW, limit=1, names=True, lang="nb")


def test_episode_detail(mem):
    first, _ = _two_episodes()
    out = query.episode(first, names=True, lang="nb")
    assert "En person ved døra" in out and "Person A ringte på." in out
    assert "Person A" in out and "sannsynlig" in out
    no_names = query.episode(first, names=False, lang="nb")
    assert "Person A" not in no_names and "En person ved døra" in no_names


def test_episode_missing(mem):
    _store(_guest("a1", NOW - 3600))
    out = query.episode(9999, names=True, lang="nb")
    assert "9999" in out


def test_parse_local_time():
    tz = OSLO
    assert query.parse_local("2027-01-15", tz) == pytest.approx(NOW - 9 * 3600)
    assert query.parse_local("2027-01-15 09:00", tz) == pytest.approx(NOW)
    assert query.parse_local("2027-01-15", tz, end=True) == pytest.approx(NOW + 15 * 3600)
    assert query.parse_local("tomorrow-ish", tz) is None


# ── switch ──────────────────────────────────────────────────────────────────────


def test_switch_off_is_not_available(mem):
    _store(_guest("a1", NOW - 3600))
    (mem / "frigate_user.yaml").write_text("enabled: false\n")
    for out in (query.last_seen("Person A", names=True, lang="nb", now=NOW),
                query.episodes(NOW - DAY, NOW, names=True, lang="nb"), query.episode(1, names=True, lang="nb")):
        assert out == query.t("fq_unavailable", "nb")


def test_missing_db_is_not_available_and_not_created(mem):
    assert not fstore.DB_PATH.exists()
    assert query.last_seen("Person A", names=True, lang="nb", now=NOW) == query.t("fq_unavailable", "nb")
    assert not fstore.DB_PATH.exists()


# ── the tool ────────────────────────────────────────────────────────────────────


def test_camera_tool_offers_the_new_actions():
    tool = next(t for t in definitions.get_tools("nb") if t["function"]["name"] == "camera")
    props = tool["function"]["parameters"]["properties"]
    assert {"last_seen", "episodes", "episode"} <= set(props["action"]["enum"])
    assert {"from", "to", "episode_id"} <= set(props)
    for lang in ("nb", "en", "de"):
        desc = next(t for t in definitions.get_tools(lang) if t["function"]["name"] == "camera")["function"]
        assert "last_seen" in desc["description"]
        assert "skygge" not in desc["description"].lower() and "shadow" not in desc["description"].lower()


def test_executor_dispatch_uses_caller_role(mem):
    _store(_guest("a1", NOW - 3600))
    named = asyncio.run(executor_camera.dispatch(
        "camera", {"action": "last_seen", "name": "Person A", "_user_id": "boss"}))
    assert "Person A" in named
    anon = asyncio.run(executor_camera.dispatch(
        "camera", {"action": "last_seen", "name": "Person A", "_user_id": "unknown_user"}))
    assert "Person A" not in anon
    listing = asyncio.run(executor_camera.dispatch(
        "camera", {"action": "episodes", "from": "2027-01-15", "to": "2027-01-15", "_user_id": "boss"}))
    assert "Person A" in listing
    bad = asyncio.run(executor_camera.dispatch(
        "camera", {"action": "episode", "episode_id": "x", "_user_id": "boss"}))
    assert isinstance(bad, str) and bad


# ── live finding 2026-10-28: Kåre fell back to the raw `frigate` action and stated Frigate's names as fact ──


def test_empty_window_points_to_the_memory_not_raw_actions(mem):
    _store(_guest("a1", NOW - 2 * DAY))
    out = query.episodes(NOW - 3600, NOW, place="inngangsdøra", names=True, lang="nb")
    assert "last_seen" in out and "'from'" in out


@pytest.mark.parametrize("lang", ["nb", "en", "de"])
def test_description_marks_raw_actions_as_unchecked(lang):
    desc = next(t for t in definitions.get_tools(lang) if t["function"]["name"] == "camera")["function"]["description"]
    raw = desc[desc.index("action='events'"):desc.index("action='list'")]
    assert "KTSB" in raw  # the raw actions say they carry no KTSB check


def test_not_found_says_how_far_back_the_memory_goes(mem):
    _store(_guest("a1", NOW - 2 * DAY))  # memory begins 13.01.2027 09:00 Oslo
    out = query.last_seen("Person Z", names=True, lang="nb", now=NOW)
    assert "13.01.2027 09:00" in out
