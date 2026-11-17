"""P144 R143 / run-queue J16 — a camera conclusion claims fire, smoke, weather, sound or smell that nothing in its input
supports (ep 266: «… med bål og vind i morgenlufta»; the scene, code's facts and Frigate's labels said none of it).

Code decides (UnsupportedClaimDetector pattern): the claim is looked for with i18n expressions in the summary's own language
(«fire» is a number in Norwegian), the support with looser stems in every language over the input the 27B got (frame text
is English). Shadow in profile `frigate`; if Userone turns it on: one correction round whose text never names the word.
Measured on the frigate.db copy 2026-11-06: 285 summaries — fire 1 unsupported (ep 266), smoke 1 supported (ep 83),
weather 1 supported (ep 4) + 1 unsupported (ep 266), sound 0, smell 0."""

import inspect

from kaare_core import model_enforcement as me
from kaare_core.frigate_user import conclude
from kaare_core.tools.i18n import t

EP266 = ("Jeg så userd (ubekreftet) forlate huset via inngangsdøren mot 07:16:45 og deretter vandre nedover kjøreveien "
         "mot vest, hvor han sto ved søppeldunken og flyttet den, med bål og vind i morgenlufta.")
EP266_INPUT = "## Hendelser\n- 07:16:37 ringeklokke person Userb (usikkert)\n## Scene\nA person walks down the drive."


def test_ep266_fire_and_wind_have_no_support():
    assert me.unsupported_senses(EP266, "nb", EP266_INPUT) == ["fire", "weather"]


def test_support_in_the_input_is_enough_in_any_language():
    assert me.unsupported_senses("Det regnet.", "nb", "scene: a rainy grey landscape") == []
    assert me.unsupported_senses("en kvinne som røyker", "nb", '"at_door": "woman smoking"') == []
    assert me.unsupported_senses("vind fra vest", "nb", "## Vær\nVind 6 m/s fra vest") == []


def test_norwegian_four_is_not_fire_and_window_is_not_wind():
    assert me.unsupported_senses("Jeg så fire personer ved vinduet.", "nb", "") == []


def test_detector_is_shadow_in_the_frigate_profile_and_never_names_the_word():
    d = me.FrigateUnsupportedSenseDetector()
    assert d.matches(text_out=EP266, used_tools=[], meta={"kind": "conclusion", "unsupported_senses": ["fire"]}, lang="nb")
    assert not d.matches(text_out=EP266, used_tools=[], meta={"kind": "frame", "unsupported_senses": ["fire"]}, lang="nb")
    assert me.profile_default_mode("frigate", d) == me.MODE_SHADOW
    assert any(isinstance(x, me.FrigateUnsupportedSenseDetector) for x in me._frigate_detectors())
    for lang in ("nb", "en", "de"):
        msg = d.correction_message(lang)
        assert msg == t("frigate_correction_unsupported_sense", lang)
        assert not any(w in msg.lower() for w in ("bål", "fire", "vind", "wind", "feuer", "rauch", "røyk", "smoke"))


def test_conclusion_ask_hands_the_fact_to_the_profile():
    src = inspect.getsource(conclude._ask)
    assert "unsupported_senses" in src and '"kind"' in src
