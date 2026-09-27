"""``control_ui.show_thinking = false`` must stop the live reasoning stream.

docs/web-ui.md: "the gateway then neither streams thinking events nor serves
reasoning bodies". ``chat.thinking`` and ``chat.history`` honour the flag, and so
did the live stream in ``sessions.send`` — but only in the no-runtime fallback the
gateway never takes. Every real turn goes through ``TaskRuntime`` and
``_emit_task_runtime_stream_events``, which forwarded ``thinking`` events and the
``reasoning_content`` on ``done`` to every subscribed WebSocket regardless.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agentos.engine.types import DoneEvent, TextDeltaEvent, ThinkingEvent
from agentos.gateway.boot import dispatch_task_runtime_turn
from agentos.gateway.config import GatewayConfig
from agentos.gateway.routing import RouteEnvelope, SourceKind

SESSION = "agent:main:webchat:direct:alice"
SCRATCHPAD = "the model's private scratchpad"


class _ReasoningRunner:
    async def run(self, message: str, session_key: str, **kwargs: Any):  # noqa: ARG002
        yield ThinkingEvent(text=SCRATCHPAD)
        yield TextDeltaEvent(text="hi")
        yield DoneEvent(text="hi", reasoning_content=SCRATCHPAD)


def _run(sink: Any = None) -> SimpleNamespace:
    envelope = RouteEnvelope(
        source_kind=SourceKind.WEB,
        source_name="test",
        agent_id="main",
        session_key=SESSION,
        input_provenance={"kind": "web_message"},
        metadata={},
    )
    return SimpleNamespace(
        agent_id="main",
        task_id="task-1",
        session_key=SESSION,
        message="hi",
        envelope=envelope,
        attachments=[],
        input_provenance=envelope.input_provenance,
        run_kind="default",
        no_memory_capture=False,
        ingress_pipeline_steps=[],
        semantic_message=None,
        persisted_user_message_id=None,
        fresh_user_session=False,
        stream_event_sink=sink,
    )


async def _emitted(show_thinking: bool, sink: Any = None) -> list[tuple[str, dict[str, Any]]]:
    config = GatewayConfig(agent_stream_heartbeat_interval_seconds=0.0)
    config.control_ui.show_thinking = show_thinking
    seen: list[tuple[str, dict[str, Any]]] = []

    async def _emit(session_key: str, event_name: str, payload: dict[str, Any]) -> None:
        seen.append((event_name, payload))

    await dispatch_task_runtime_turn(
        _run(sink),
        config=config,
        session_manager=None,
        turn_runner=_ReasoningRunner(),
        event_emitter=_emit,
    )
    return seen


@pytest.mark.asyncio
async def test_no_thinking_event_is_streamed_when_show_thinking_is_off() -> None:
    events = await _emitted(show_thinking=False)

    assert "session.event.thinking" not in [name for name, _ in events]
    assert SCRATCHPAD not in repr(events)


@pytest.mark.asyncio
async def test_the_done_event_carries_no_reasoning_when_show_thinking_is_off() -> None:
    events = await _emitted(show_thinking=False)

    done = next(payload for name, payload in events if name == "session.event.done")
    assert "reasoning_content" not in done


@pytest.mark.asyncio
async def test_the_reply_itself_is_still_streamed_when_show_thinking_is_off() -> None:
    """Positive control: the turn ran and emitted, so the absence above is real."""
    events = await _emitted(show_thinking=False)

    names = [name for name, _ in events]
    assert names == ["session.event.text_delta", "session.event.done"]
    assert events[0][1]["text"] == "hi"
    assert events[1][1]["text"] == "hi"


@pytest.mark.asyncio
async def test_reasoning_is_streamed_when_show_thinking_is_on() -> None:
    """Pass either way by design: the default keeps the documented live block."""
    events = await _emitted(show_thinking=True)

    thinking = [payload for name, payload in events if name == "session.event.thinking"]
    assert thinking == [{"text": SCRATCHPAD}]
    done = next(payload for name, payload in events if name == "session.event.done")
    assert done["reasoning_content"] == SCRATCHPAD


@pytest.mark.asyncio
async def test_the_channel_stream_sink_is_unaffected_by_the_web_ui_flag() -> None:
    """Pass either way by design: the flag governs the WebSocket stream only."""
    sunk: list[str] = []

    async def _sink(event: Any) -> None:
        sunk.append(event.kind)

    await _emitted(show_thinking=False, sink=_sink)

    assert sunk == ["thinking", "text_delta", "done"]
