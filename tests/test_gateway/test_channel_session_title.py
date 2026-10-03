"""A session started from a channel (Telegram, Slack, …) gets a title too.

``chat.send`` titles WebChat and desktop-app sessions from their first
message; channel dispatch never called the titler, so a Telegram session kept
its short id in the sidebar forever. These tests drive each dispatch path
(runtime, debounced, legacy) against a real ``SessionManager`` and the real
``_record_delivery_context``, so the session is created exactly as a channel
creates it, and assert the title lands in ``display_name`` and reaches open
clients as ``sessions.changed``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from agentos.channels.types import IncomingMessage
from agentos.gateway import session_titler as titler_mod
from agentos.gateway.channel_dispatch import (
    _ChannelInFlightSet,
    _dispatch_combined_message_after_debounce,
    run_channel_dispatch,
)
from agentos.gateway.session_titler import reset_titlers, titler_for
from agentos.provider import auxiliary as aux
from agentos.session.manager import SessionManager
from agentos.session.storage import SessionStorage

KEY = "agent:main:telegram:direct:u1"
TEXT = "Kiểm tra giúp tôi giá ETH hôm nay"


@pytest.fixture(autouse=True)
def _titler(monkeypatch: pytest.MonkeyPatch):
    reset_titlers()

    class _Client:
        async def complete(self, **_kwargs: Any) -> aux.AuxResult:
            return aux.AuxResult(text="Giá ETH hôm nay", provider="p", model="m")

    monkeypatch.setattr(titler_mod, "get_auxiliary_client", lambda: _Client())
    yield
    reset_titlers()


@pytest_asyncio.fixture
async def manager():
    storage = SessionStorage(":memory:")
    await storage.connect()
    mgr = SessionManager(storage, inject_time_prefix=False)
    yield mgr
    await storage.close()


class _Bridge:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def emit(self, key: str, event: str, payload: dict[str, Any] | None = None) -> None:
        self.events.append((key, event, payload or {}))


def _msg() -> IncomingMessage:
    return IncomingMessage(sender_id="u1", channel_id="u1", content=TEXT, metadata={})


def _channel(msg: IncomingMessage) -> MagicMock:
    channel = MagicMock()
    channel.channel_id = "telegram"
    channel.send = AsyncMock()
    channel.supports_slash_commands = False
    calls = 0

    async def _receive() -> IncomingMessage:
        nonlocal calls
        calls += 1
        if calls == 1:
            return msg
        raise asyncio.CancelledError

    channel.receive = _receive
    return channel


def _turn_runner() -> MagicMock:
    runner = MagicMock()
    runner._get_session_lock.return_value = None
    return runner


def _status_reactor() -> SimpleNamespace:
    return SimpleNamespace(
        received=AsyncMock(), running=AsyncMock(), completed=AsyncMock(), failed=AsyncMock()
    )


def _turn_patches(msg: IncomingMessage) -> list[Any]:
    return [
        patch(
            "agentos.gateway.channel_dispatch._should_skip_unmentioned",
            new=MagicMock(return_value=False),
        ),
        patch(
            "agentos.gateway.channel_dispatch._ingest_channel_message_attachments",
            new=AsyncMock(return_value=SimpleNamespace(text=msg.content, attachments=[])),
        ),
        patch(
            "agentos.gateway.channel_dispatch._RuntimeChannelStreamRelay.maybe_start",
            new=MagicMock(return_value=None),
        ),
        patch(
            "agentos.gateway.channel_dispatch._transcript_watermark",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "agentos.gateway.channel_dispatch.start_turn_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(task_id="task-1")),
        ),
        patch(
            "agentos.gateway.channel_dispatch._append_channel_user_message",
            new=AsyncMock(return_value=(MagicMock(), msg.content)),
        ),
        patch(
            "agentos.gateway.channel_dispatch._deliver_runtime_channel_reply",
            new=AsyncMock(),
        ),
        patch(
            "agentos.gateway.channel_dispatch._run_turn_with_streaming",
            new=AsyncMock(),
        ),
        patch(
            "agentos.gateway.channel_dispatch._status_reactor",
            new=MagicMock(return_value=_status_reactor()),
        ),
    ]


async def _run_loop(
    manager: SessionManager,
    bridge: _Bridge,
    *,
    task_runtime: Any,
    config: Any = None,
) -> None:
    msg = _msg()
    patches = _turn_patches(msg)
    for p in patches:
        p.start()
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_channel_dispatch(
                channel=_channel(msg),
                turn_runner=_turn_runner(),
                session_manager=manager,
                session_key_builder=lambda _msg: KEY,
                session_prefix="telegram",
                event_bridge=bridge,  # type: ignore[arg-type]
                config=config,
                task_runtime=task_runtime,
                _in_flight=_ChannelInFlightSet(cap=4),
            )
        await asyncio.sleep(0)
        await titler_for(manager).drain()
    finally:
        for p in reversed(patches):
            p.stop()


def _renamed(bridge: _Bridge) -> list[dict[str, Any]]:
    return [
        payload
        for _key, event, payload in bridge.events
        if event == "sessions.changed" and payload.get("reason") == "renamed"
    ]


@pytest.mark.asyncio
async def test_runtime_path_titles_a_new_channel_session(manager):
    bridge = _Bridge()
    await _run_loop(manager, bridge, task_runtime=MagicMock())

    node = await manager.get_session(KEY)
    assert node is not None and node.display_name == "Giá ETH hôm nay"
    assert _renamed(bridge) == [
        {
            "schema_version": 1,
            "key": KEY,
            "reason": "renamed",
            "display_name": "Giá ETH hôm nay",
            "displayName": "Giá ETH hôm nay",
        }
    ]


@pytest.mark.asyncio
async def test_legacy_path_titles_a_new_channel_session(manager):
    bridge = _Bridge()
    await _run_loop(manager, bridge, task_runtime=None)

    node = await manager.get_session(KEY)
    assert node is not None and node.display_name == "Giá ETH hôm nay"
    assert len(_renamed(bridge)) == 1


@pytest.mark.asyncio
async def test_debounced_batch_titles_a_new_channel_session(manager):
    bridge = _Bridge()
    msg = _msg()
    combined = SimpleNamespace(message=msg, raw_content=TEXT, coalesced_count=2)
    patches = _turn_patches(msg)
    for p in patches:
        p.start()
    try:
        await _dispatch_combined_message_after_debounce(
            _channel(msg),
            combined,
            _turn_runner(),
            manager,
            KEY,
            "telegram",
            MagicMock(),
            None,
            bridge,  # type: ignore[arg-type]
            _ChannelInFlightSet(cap=4),
        )
        await titler_for(manager).drain()
    finally:
        for p in reversed(patches):
            p.stop()

    node = await manager.get_session(KEY)
    assert node is not None and node.display_name == "Giá ETH hôm nay"
    assert len(_renamed(bridge)) == 1


@pytest.mark.asyncio
async def test_a_named_channel_session_keeps_its_name(manager):
    await manager.create(KEY, display_name="My Telegram chat")
    bridge = _Bridge()
    await _run_loop(manager, bridge, task_runtime=MagicMock())

    node = await manager.get_session(KEY)
    assert node is not None and node.display_name == "My Telegram chat"
    assert _renamed(bridge) == []


@pytest.mark.asyncio
async def test_auto_title_off_leaves_channel_sessions_alone(manager):
    bridge = _Bridge()
    config = SimpleNamespace(sessions=SimpleNamespace(auto_title=False))
    await _run_loop(manager, bridge, task_runtime=MagicMock(), config=config)

    node = await manager.get_session(KEY)
    assert node is not None and node.display_name is None
    assert _renamed(bridge) == []
