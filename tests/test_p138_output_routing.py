"""P138 phase 1 — where a voice reply goes: one routing rule shared by every input path (pure, main venv)."""

from kaare_core.voice import output_routing as r

PLACES = {
    "atom": "soverom",
    "nest": "soverom",
    "tv": "soverom",
    "kitchen": "kjokken",
    "pe": "kjokken",
}


def place_of(nid: str) -> str:
    return PLACES.get(nid, "")


NODES = {
    "atom": {"type": "wyoming", "host": "10.0.0.5"},
    "nest": {"type": "ha_media_player", "entity_id": "media_player.nest"},
    "tv": {"type": "ha_media_player", "has_display": True},
    "kitchen": {"type": "ha_media_player"},
    "pe": {"type": "esp32", "host": "10.0.0.6"},
    "off": {"type": "ha_media_player", "enabled": False},
}


def nodes(**over):
    out = {k: dict(v) for k, v in NODES.items()}
    for nid, fields in over.items():
        out[nid].update(fields)
    return out


# ── audio_targets ─────────────────────────────────────────────────────────────


def test_auto_wyoming_with_speaker_answers_on_itself():
    assert r.audio_targets("atom", nodes(), r.INPUT_WYOMING, place_of=place_of) == [r.SELF]


def test_auto_wyoming_without_speaker_picks_audio_only_place_mate():
    n = nodes(atom={"has_audio": False})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING, place_of=place_of) == ["nest"]


def test_auto_wyoming_without_speaker_and_no_mate_falls_back_to_self():
    n = nodes(atom={"has_audio": False})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING, place_of=lambda nid: "") == [r.SELF]


def test_explicit_audio_out_wins_and_skips_disabled_and_unknown():
    n = nodes(atom={"audio_out": ["nest", "off", "ghost"]})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING, place_of=place_of) == ["nest"]


def test_explicit_audio_out_string_and_self_and_local():
    n = nodes(atom={"audio_out": "atom, local"})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING) == [r.SELF, r.LOCAL]


def test_explicit_audio_out_auto_is_auto():
    n = nodes(atom={"audio_out": "auto"})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING) == [r.SELF]


def test_explicit_but_all_invalid_falls_back_to_auto():
    n = nodes(atom={"audio_out": ["off"]})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING) == [r.SELF]


def test_tts_target_local_kept():
    n = nodes(atom={"tts_target": "local"})
    assert r.audio_targets("atom", n, r.INPUT_WYOMING) == [r.LOCAL]


def test_button_auto_follows_playback_mode_as_before():
    assert r.audio_targets("pe", nodes(), r.INPUT_BUTTON, playback_mode="local") == [r.LOCAL]
    assert r.audio_targets("pe", nodes(), r.INPUT_BUTTON, playback_mode="esp32") == ["pe"]


def test_button_explicit_audio_out():
    n = nodes(pe={"audio_out": ["kitchen"]})
    assert r.audio_targets("pe", n, r.INPUT_BUTTON) == ["kitchen"]


def test_unknown_node():
    assert r.audio_targets("ghost", nodes(), r.INPUT_WYOMING) == [r.SELF]
    assert r.audio_targets("ghost", nodes(), r.INPUT_BUTTON) == [r.LOCAL]


# ── image_targets ─────────────────────────────────────────────────────────────


def test_image_auto_same_place_displays():
    assert r.image_targets("atom", nodes(), ["tv"], place_of=place_of) == ["tv"]


def test_image_explicit_only_display_nodes():
    n = nodes(atom={"image_out": ["nest", "tv"]})
    assert r.image_targets("atom", n, ["tv"], place_of=place_of) == ["tv"]


def test_image_input_node_is_display():
    assert r.image_targets("tv", nodes(), ["tv"], place_of=place_of) == ["tv"]


def test_image_nothing_node_specific():
    assert r.image_targets("kitchen", nodes(), ["tv"], place_of=place_of) == []
    assert r.image_targets("ghost", nodes(), ["tv"]) == []


# ── emergency channel ─────────────────────────────────────────────────────────


def test_emergency_is_own_speaker_even_when_audio_out_elsewhere():
    n = nodes(atom={"audio_out": ["nest"]})
    assert r.emergency_target("atom", n, r.INPUT_WYOMING) == r.SELF


def test_emergency_none_without_speaker_and_local_for_button():
    n = nodes(atom={"has_audio": False})
    assert r.emergency_target("atom", n, r.INPUT_WYOMING) is None
    assert r.emergency_target("pe", n, r.INPUT_BUTTON) == r.LOCAL


def test_label_prefers_description():
    n = nodes(nest={"description": "Nest på rommet"})
    assert r.label("nest", n) == "Nest på rommet"
    assert r.label("kitchen", n) == "kitchen"
