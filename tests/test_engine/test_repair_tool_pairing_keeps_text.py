"""Issue #3233: repairing tool pairing threw away the whole message.

``repair_tool_pairing`` removes messages whose tool_use/tool_result adjacency
would make a provider request invalid. It did so a whole message at a time::

    if result_ids and index not in valid_tool_result_indices:
        continue

but a message is not only its tool blocks. The engine appends the
runtime-context block to the user message it lands on -- which on a tool turn
is the very message carrying the tool results -- and the historical-image
sanitiser appends its ``[historical image omitted]`` markers the same way. An
orphaned tool result (its assistant call dropped by compaction, or the turn
truncated) therefore took that text with it, silently, and the model answered
without instructions the caller believed it had sent.

Only the unpaired blocks need to go.
"""

from __future__ import annotations

from agentos.engine.history import repair_tool_pairing
from agentos.provider.types import (
    ContentBlockText,
    ContentBlockToolResult,
    ContentBlockToolUse,
    Message,
)


def _text(message: Message) -> list[str]:
    assert isinstance(message.content, list)
    return [b.text for b in message.content if isinstance(b, ContentBlockText)]


def _call(call_id: str = "c1") -> Message:
    return Message(
        role="assistant",
        content=[ContentBlockToolUse(id=call_id, name="read_file", input={})],
    )


def _result(call_id: str = "c1", *, text: str | None = None) -> Message:
    blocks: list[object] = [
        ContentBlockToolResult(tool_use_id=call_id, content="body", is_error=False)
    ]
    if text is not None:
        blocks.append(ContentBlockText(text=text))
    return Message(role="user", content=blocks)  # type: ignore[arg-type]


def test_text_beside_an_orphan_tool_result_survives() -> None:
    """The issue's repro."""
    message = _result("orphan", text="Please process the updated data")

    repaired = repair_tool_pairing([message])

    assert len(repaired) == 1
    assert _text(repaired[0]) == ["Please process the updated data"]


def test_the_orphan_result_block_itself_is_removed() -> None:
    """Keeping the text must not keep the block that made the transcript
    invalid -- that is what this function exists to remove."""
    repaired = repair_tool_pairing([_result("orphan", text="keep me")])

    assert isinstance(repaired[0].content, list)
    assert not any(isinstance(block, ContentBlockToolResult) for block in repaired[0].content)


def test_a_message_that_was_only_an_orphan_result_is_still_dropped() -> None:
    """Nothing to preserve, so the old outcome is the right one."""
    assert repair_tool_pairing([_result("orphan")]) == []


def test_text_beside_an_orphan_tool_call_survives_too() -> None:
    """The assistant side has the same shape: a model that narrates before
    calling loses the narration when its call is orphaned."""
    message = Message(
        role="assistant",
        content=[
            ContentBlockText(text="I will look that up."),
            ContentBlockToolUse(id="orphan", name="read_file", input={}),
        ],
    )

    repaired = repair_tool_pairing([message])

    assert len(repaired) == 1
    assert _text(repaired[0]) == ["I will look that up."]
    assert not any(
        isinstance(block, ContentBlockToolUse)
        for block in repaired[0].content  # type: ignore[union-attr]
    )


def test_a_properly_paired_turn_is_returned_unchanged() -> None:
    """Including by identity: an untouched transcript must not be rebuilt."""
    messages = [_call(), _result()]

    assert repair_tool_pairing(messages) is messages


def test_a_paired_turn_carrying_text_keeps_both() -> None:
    """The common shape -- runtime context appended to the tool-result
    message -- must pass through with the results intact."""
    messages = [_call(), _result(text="Runtime context: it is Tuesday.")]

    repaired = repair_tool_pairing(messages)

    assert repaired is messages
    assert _text(repaired[1]) == ["Runtime context: it is Tuesday."]


def test_an_unrelated_message_between_call_and_result_still_breaks_the_pair() -> None:
    """The adjacency rule is unchanged: a message between the call and its
    result makes the pair invalid, and both sides are repaired -- but the
    text on each survives."""
    between = Message(role="user", content=[ContentBlockText(text="wait, actually")])
    messages = [_call(), between, _result(text="and here")]

    repaired = repair_tool_pairing(messages)

    # The assistant message held nothing but the orphaned call, so it still
    # goes entirely; the two messages carrying text keep it.
    assert [_text(m) for m in repaired] == [["wait, actually"], ["and here"]]
    for message in repaired:
        assert isinstance(message.content, list)
        assert not any(
            isinstance(b, (ContentBlockToolUse, ContentBlockToolResult)) for b in message.content
        )


def test_reasoning_content_is_carried_across_the_rewrite() -> None:
    """The message is rebuilt, so its other fields have to come along."""
    message = Message(
        role="assistant",
        content=[
            ContentBlockText(text="narration"),
            ContentBlockToolUse(id="orphan", name="t", input={}),
        ],
        reasoning_content="because",
    )

    repaired = repair_tool_pairing([message])

    assert repaired[0].reasoning_content == "because"
    assert repaired[0].role == "assistant"


def test_an_empty_transcript_is_unchanged() -> None:
    messages: list[Message] = []

    assert repair_tool_pairing(messages) is messages


def test_a_string_content_message_is_untouched() -> None:
    messages = [Message(role="user", content="plain text")]

    assert repair_tool_pairing(messages) is messages
