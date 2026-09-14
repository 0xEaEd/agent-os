"""The per-channel in-flight cap also bounds the debounce dispatch path.

``_ChannelInFlightSet`` gates how many reply deliveries are outstanding on
one channel adapter. ``run_channel_dispatch`` holds a slot for the whole
delivery task, but ``_dispatch_combined_message_after_debounce`` used to
release its reservation as soon as the turn was enqueued -- before
``_deliver_runtime_channel_reply``, the slow part -- so with debounce
enabled the cap bounded nothing and the "Server busy" backpressure never
fired.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import agentos.gateway.channel_dispatch as cd
from agentos.gateway.channel_dispatch import _ChannelInFlightSet


class _Msg:
    def __init__(self) -> None:
        self.sender_id = "u1"
        self.channel_id = "c1"
        self.content = "hi"
        self.metadata: dict[str, Any] = {}


class _Combined:
    def __init__(self) -> None:
        self.message = _Msg()
        self.content = "hi"
        self.attachments: list[Any] = []
        self.coalesced_count = 1
        self.raw_content = "hi"


class _Channel:
    async def send(self, *args: Any, **kwargs: Any) -> None:
        return None


class _RouteEnvelope:
    def __init__(self) -> None:
        self.runtime_state = type("RS", (), {"clear_channel_admission": lambda self: None})()
        self.thread_id = None
        self.channel_id = "c1"
        self.metadata: dict[str, Any] = {}


class _Handle:
    task_id = "task-1"


class _StatusReactor:
    async def received(self, *a: Any, **k: Any) -> None: ...
    async def running(self, *a: Any, **k: Any) -> None: ...
    async def failed(self, *a: Any, **k: Any) -> None: ...
    async def completed(self, *a: Any, **k: Any) -> None: ...


class _Ingested:
    attachments: list[Any] = []
    text = "hi"


@pytest.fixture
def stubbed_dispatch(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Stub everything around the cap so only its control flow is exercised."""
    import agentos.gateway.routing as routing_mod

    counters = {"active": 0, "peak": 0}

    async def _deliver(**kwargs: Any) -> None:
        counters["active"] += 1
        counters["peak"] = max(counters["peak"], counters["active"])
        await asyncio.sleep(0.05)
        counters["active"] -= 1

    async def _record_delivery_context(*a: Any, **k: Any) -> Any:
        return None, False

    async def _ingest(*a: Any, **k: Any) -> Any:
        return _Ingested()

    async def _watermark(*a: Any, **k: Any) -> int:
        return 0

    async def _start_turn(*a: Any, **k: Any) -> Any:
        return _Handle()

    async def _append(*a: Any, **k: Any) -> Any:
        return True, "hi"

    async def _emit(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(cd, "_deliver_runtime_channel_reply", _deliver)
    monkeypatch.setattr(cd, "_record_delivery_context", _record_delivery_context)
    monkeypatch.setattr(cd, "_should_skip_unmentioned", lambda *a, **k: False)
    monkeypatch.setattr(cd, "_ingest_channel_message_attachments", _ingest)
    monkeypatch.setattr(cd, "_status_reactor", lambda channel: _StatusReactor())
    monkeypatch.setattr(cd, "_transcript_watermark", _watermark)
    no_relay = type("R", (), {"maybe_start": staticmethod(lambda *a, **k: None)})
    monkeypatch.setattr(cd, "_RuntimeChannelStreamRelay", no_relay)
    monkeypatch.setattr(cd, "start_turn_via_runtime", _start_turn)
    monkeypatch.setattr(cd, "_append_channel_user_message", _append)
    monkeypatch.setattr(cd, "_start_typing_keepalive", lambda *a, **k: None)
    monkeypatch.setattr(cd, "_emit_events", _emit)
    monkeypatch.setattr(cd, "_resolve_channel_overflow_policy", lambda *a, **k: None)
    monkeypatch.setattr(
        routing_mod,
        "build_channel_route_envelope",
        lambda msg, session_key, session_prefix: _RouteEnvelope(),
    )
    return counters


async def _dispatch(in_flight: _ChannelInFlightSet, session_key: str) -> None:
    await cd._dispatch_combined_message_after_debounce(
        _Channel(),
        _Combined(),
        turn_runner=None,
        session_manager=None,
        session_key=session_key,
        session_prefix="test",
        task_runtime=object(),
        config=None,
        event_bridge=None,
        _in_flight=in_flight,
        channel_rpc_context_factory=None,
    )


@pytest.mark.asyncio
async def test_debounce_path_respects_the_inflight_cap(stubbed_dispatch: dict[str, int]) -> None:
    in_flight = _ChannelInFlightSet(cap=1)

    await asyncio.gather(_dispatch(in_flight, "sess-A"), _dispatch(in_flight, "sess-B"))

    assert stubbed_dispatch["peak"] == 1


@pytest.mark.asyncio
async def test_debounce_path_releases_its_slot_after_delivery(
    stubbed_dispatch: dict[str, int],
) -> None:
    """A held slot must not leak, or the channel wedges after cap turns."""
    in_flight = _ChannelInFlightSet(cap=1)

    await _dispatch(in_flight, "sess-A")
    await _dispatch(in_flight, "sess-A")

    assert not in_flight.full()
    assert stubbed_dispatch["peak"] == 1
