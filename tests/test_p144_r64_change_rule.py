"""P144 R64 / run-queue J10: the 27B ignored the change position (ep 102: 0 of 3 conclusions mentioned it). With the
block's line also saying to include the place in the summary, 3 of 3 did (measured 2026-11-06, ep 102, frigate.db copy).
No example words, no verb that suggests what it is."""

from kaare_core.frigate_user import conclude


def test_change_block_asks_for_the_place_in_every_language():
    for lang, word in (("nb", "oppsummeringen"), ("en", "summary"), ("de", "Zusammenfassung")):
        assert word in conclude._t("h_changes", lang), lang
