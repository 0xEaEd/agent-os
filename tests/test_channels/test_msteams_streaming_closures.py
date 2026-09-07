"""The MS Teams streaming callbacks must capture their text by value.

``send_streaming`` hands four callbacks to ``continue_conversation`` and keeps
mutating ``accumulated`` as chunks arrive. A callback that reads ``accumulated``
as a free variable sends whatever the loop has reached by the time the adapter
runs it, not the text it was built for.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.fixture()
def botbuilder_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy ``send_streaming``'s lazy ``botbuilder.schema`` import.

    Registered through ``monkeypatch`` so it is removed again afterwards; a
    module-level ``sys.modules`` assignment would leak into every later test.
    """
    if "botbuilder.schema" in sys.modules:
        return

    class _Activity:
        def __init__(self, **fields: Any) -> None:
            self.__dict__.update(fields)

    package = ModuleType("botbuilder")
    schema = ModuleType("botbuilder.schema")
    schema.Activity = _Activity  # type: ignore[attr-defined]
    package.schema = schema  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "botbuilder", package)
    monkeypatch.setitem(sys.modules, "botbuilder.schema", schema)


async def _stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


class _RecordingTurnContext:
    def __init__(self, sent: list[str], updated: list[str]) -> None:
        self._sent = sent
        self._updated = updated

    async def send_activity(self, payload: Any) -> Any:
        self._sent.append(str(payload))
        return SimpleNamespace(id="msg-1")

    async def update_activity(self, activity: Any) -> Any:
        self._updated.append(str(getattr(activity, "text", "")))
        return None


class _CapturingAdapter:
    """Runs each callback and keeps the first one for later replay."""

    def __init__(self, sent: list[str], updated: list[str]) -> None:
        self.sent = sent
        self.updated = updated
        self.first_callback: Any = None

    async def continue_conversation(self, ref: Any, callback: Any, bot_id: Any = None) -> None:
        if self.first_callback is None:
            self.first_callback = callback
        await callback(_RecordingTurnContext(self.sent, self.updated))


def _channel(adapter: _CapturingAdapter) -> Any:
    from agentos.channels.msteams import MSTeamsChannel, MSTeamsChannelConfig

    channel = MSTeamsChannel(config=MSTeamsChannelConfig(name="msteams"))
    channel._references["conv-1"] = SimpleNamespace()
    channel._adapter = adapter
    return channel


@pytest.mark.asyncio
async def test_first_chunk_sends_the_first_chunk_text(botbuilder_schema: None) -> None:
    sent: list[str] = []
    adapter = _CapturingAdapter(sent, [])

    await _channel(adapter).send_streaming(_stream("first", "second"), reply_to="conv-1")

    assert sent[0] == "first"


@pytest.mark.asyncio
async def test_first_send_callback_replayed_late_still_sends_its_own_text(
    botbuilder_schema: None,
) -> None:
    # The adapter is free to run a callback whenever it likes. Replaying the
    # first-chunk callback after the stream has finished is what separates a
    # by-value capture from a free variable: `accumulated` is "firstsecond" by
    # now, but this callback was built to send "first".
    sent: list[str] = []
    adapter = _CapturingAdapter(sent, [])

    await _channel(adapter).send_streaming(_stream("first", "second"), reply_to="conv-1")
    assert adapter.first_callback is not None

    await adapter.first_callback(_RecordingTurnContext(sent, []))

    assert sent[-1] == "first"


@pytest.mark.asyncio
async def test_single_chunk_stream_sends_that_chunk(botbuilder_schema: None) -> None:
    sent: list[str] = []
    adapter = _CapturingAdapter(sent, [])

    await _channel(adapter).send_streaming(_stream("only"), reply_to="conv-1")

    assert sent[0] == "only"
