"""Issue #3164: text alongside a tool result never reached Ollama.

``_build_ollama_messages`` emitted the tool messages for a message carrying
``tool_result`` blocks and then ran ``continue``, so the line that translates
the rest of the message was never reached. Every other block on that message
-- text the engine had deliberately attached -- was dropped, silently, on the
way to the provider.

A tool-result message is not always only tool results. Two producers put text
on exactly that message:

* ``Agent._append_runtime_context_to_user_message`` appends the runtime
  context block to the user message it lands on, which on a tool turn is the
  ``Message(role="user", content=tool_result_blocks)`` the engine just built.
* the historical-image sanitiser appends its ``[historical image omitted]``
  markers to the message it stripped them from.

Under Ollama both vanished: the model was answering without context the
engine believed it had sent.
"""

from __future__ import annotations

from typing import Any

from agentos.provider.ollama import _build_ollama_messages
from agentos.provider.types import (
    ContentBlockText,
    ContentBlockToolResult,
    ContentBlockToolUse,
    Message,
)


def _assistant_call(call_id: str = "call_1", name: str = "read_file") -> Message:
    return Message(
        role="assistant",
        content=[ContentBlockToolUse(id=call_id, name=name, input={"path": "x"})],
    )


def _contents(messages: list[dict[str, Any]], role: str) -> list[str]:
    return [str(m.get("content", "")) for m in messages if m.get("role") == role]


def test_text_carried_on_a_tool_result_message_reaches_the_provider() -> None:
    """The issue's repro, in the shape the engine actually produces."""
    mixed = Message(
        role="user",
        content=[
            ContentBlockToolResult(tool_use_id="call_1", content="file body"),
            ContentBlockText(text="Runtime context: it is Tuesday."),
        ],
    )

    built = _build_ollama_messages([_assistant_call(), mixed])

    assert "file body" in _contents(built, "tool")
    assert any("Runtime context" in text for text in _contents(built, "user"))


def test_the_tool_message_still_comes_first() -> None:
    """Ollama pairs a tool message with the assistant turn before it, so the
    carried text must follow the tool results, not separate them from it."""
    mixed = Message(
        role="user",
        content=[
            ContentBlockToolResult(tool_use_id="call_1", content="body"),
            ContentBlockText(text="carried"),
        ],
    )

    roles = [m["role"] for m in _build_ollama_messages([_assistant_call(), mixed])]

    assert roles == ["assistant", "tool", "user"]


def test_text_before_the_tool_result_is_kept_too() -> None:
    """Block order within the message must not decide whether text survives."""
    mixed = Message(
        role="user",
        content=[
            ContentBlockText(text="[historical image omitted: image/png]"),
            ContentBlockToolResult(tool_use_id="call_1", content="body"),
        ],
    )

    built = _build_ollama_messages([_assistant_call(), mixed])

    assert any("historical image omitted" in text for text in _contents(built, "user"))


def test_several_tool_results_all_survive_alongside_the_text() -> None:
    mixed = Message(
        role="user",
        content=[
            ContentBlockToolResult(tool_use_id="call_1", content="first"),
            ContentBlockToolResult(tool_use_id="call_2", content="second"),
            ContentBlockText(text="carried"),
        ],
    )

    built = _build_ollama_messages([_assistant_call(), mixed])

    assert _contents(built, "tool") == ["first", "second"]
    assert _contents(built, "user") == ["carried"]


def test_a_pure_tool_result_message_is_unchanged() -> None:
    """No empty user message may appear where there was no text to carry."""
    pure = Message(
        role="user",
        content=[ContentBlockToolResult(tool_use_id="call_1", content="body")],
    )

    built = _build_ollama_messages([_assistant_call(), pure])

    assert [m["role"] for m in built] == ["assistant", "tool"]


def test_a_whitespace_only_carry_does_not_add_a_message() -> None:
    """The engine's separator is ``"\\n\\n" + context``; a block that is only
    whitespace carries nothing and must not become an empty turn."""
    mixed = Message(
        role="user",
        content=[
            ContentBlockToolResult(tool_use_id="call_1", content="body"),
            ContentBlockText(text="   \n  "),
        ],
    )

    built = _build_ollama_messages([_assistant_call(), mixed])

    assert [m["role"] for m in built] == ["assistant", "tool"]


def test_the_tool_name_is_still_paired() -> None:
    """The remainder handling must not disturb the id -> name table the tool
    messages depend on."""
    mixed = Message(
        role="user",
        content=[
            ContentBlockToolResult(tool_use_id="call_1", content="body"),
            ContentBlockText(text="carried"),
        ],
    )

    built = _build_ollama_messages([_assistant_call(name="grep_search"), mixed])

    tool_message = next(m for m in built if m["role"] == "tool")
    assert tool_message["tool_name"] == "grep_search"


def test_an_ordinary_text_message_is_untouched() -> None:
    built = _build_ollama_messages([Message(role="user", content="hello")])

    assert built == [{"role": "user", "content": "hello"}]
