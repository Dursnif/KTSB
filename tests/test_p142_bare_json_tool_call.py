"""DT04-S05p (2026-11-17): the 27B wrote the tool call as a bare JSON list/object with «action» —
`[{"action": "write", "list_name": "shopping", "text": "brød"}]` — nothing ran, the user saw raw JSON,
and tool_call_as_text did not fire. Bare action-JSON must be caught; JSON the user asked for must not."""

import pytest

from kaare_core.model_enforcement import ModelEnforcer, ToolCallAsTextDetector

OFFERED = ["note", "ha_read", "timer"]


def _hit(text, offered=OFFERED):
    return ToolCallAsTextDetector().matches(
        text_out=text, used_tools=[], meta={"offered_tools": offered}, lang="nb"
    )


# The two replies seen in S05p, plus the same shapes as an object and in a code fence.
@pytest.mark.parametrize(
    "text",
    [
        '[{"action": "write", "list_name": "shopping", "text": "brød"}]',
        '[{"action": "room_list"}]',
        '{"action": "room_list"}',
        '```json\n[{"action": "write", "list_name": "shopping", "text": "brød"}]\n```',
        '  [ {"action":"write","list_name":"shopping","text":"melk"} ]  ',
    ],
)
def test_bare_action_json_is_caught(text):
    assert _hit(text)


# False hits weigh heavier (N-D1): none of these may fire.
@pytest.mark.parametrize(
    "text",
    [
        # JSON the user asked for — no «action» key
        '{"name": "Ola", "rom": "stue", "temperatur": 21}',
        # «action» value that is no action of an offered tool
        '{"action": "dance", "tempo": 120}',
        # action-JSON inside ordinary prose (an explanation, not a call)
        'Et verktøykall ser slik ut: {"action": "write"} — men det gjør jeg for deg.',
        # plain answer
        "Brød er lagt på handlelista.",
        # a list of data rows
        '[{"dag": "mandag", "action": "write"}, 3]',
    ],
)
def test_other_json_is_not_caught(text):
    assert not _hit(text)


def test_not_caught_when_the_action_belongs_to_a_tool_not_offered():
    assert not _hit('[{"action": "room_list"}]', offered=["timer"])


def test_retry_that_repeats_bare_json_is_never_shown():
    enf = ModelEnforcer.for_profile("chat", "nb", rid="s05p")
    enf.offer_tools(OFFERED)
    bare = '[{"action": "room_list"}]'
    assert enf.on_answer(bare)
    out = enf.resolve(bare, None)
    assert '"action"' not in out and out.strip()
