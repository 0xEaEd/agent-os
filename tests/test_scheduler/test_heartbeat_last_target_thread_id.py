"""``target="last"`` heartbeats must not wipe the session's inferred thread id.

``infer_delivery`` reads ``node.last_thread_id`` for a session whose last
inbound message came from a Slack thread, a Telegram forum topic, or a
Discord thread. ``HeartbeatService.run_once()``'s ``target == "last"`` branch
used to reset ``delivery.thread_id`` back to ``""`` whenever no
``delivery_override["thread_id"]`` was configured -- the default, zero-config
operator state -- silently undoing that inference on every default heartbeat
tick (#3347). Channel_name/channel_id/account_id were never subject to the
same reset; only thread_id was.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agentos.channels.manager import ChannelManager
from agentos.channels.types import OutgoingMessage
from agentos.scheduler.heartbeat_service import HeartbeatService

_CHAT = "-1001234567890"
_TOPIC = "42"


class _FakeAdapter:
    def __init__(self) -> None:
        self.messages: list[OutgoingMessage] = []

    async def send(self, message: OutgoingMessage) -> None:
        self.messages.append(message)


class _FakeTurnRunner:
    async def run(self, **_: Any):  # noqa: ANN202
        yield SimpleNamespace(kind="done", text="heartbeat text")


class _ThreadedSessionStorage:
    """Session whose last inbound message came from a Telegram forum topic."""

    async def get_session(self, session_key: str) -> Any:
        return SimpleNamespace(
            last_channel="telegram", last_to=_CHAT, last_account_id="", last_thread_id=_TOPIC
        )


class _UnthreadedSessionStorage:
    """Session whose last inbound message was an ordinary, non-threaded chat."""

    async def get_session(self, session_key: str) -> Any:
        return SimpleNamespace(
            last_channel="slack", last_to="C123", last_account_id="", last_thread_id=""
        )


def _service(storage: Any, adapter: _FakeAdapter, channel_type: str) -> HeartbeatService:
    manager = ChannelManager(
        _channels={channel_type: adapter},  # type: ignore[dict-item]
        _turn_runner=None,
        _session_manager=None,
        _channel_types={channel_type: channel_type},
    )
    return HeartbeatService(
        turn_runner=_FakeTurnRunner(),
        session_storage=storage,
        channel_manager_ref=lambda: manager,
    )


async def test_last_target_with_no_override_keeps_the_inferred_thread() -> None:
    adapter = _FakeAdapter()
    service = _service(_ThreadedSessionStorage(), adapter, "telegram")

    result = await service.run_once(
        reason="heartbeat:loop",
        agent_id="main",
        session_key="agent:main:main",
        prompt="ping",
        target="last",
    )

    assert result.status == "delivered"
    assert adapter.messages[0].reply_to == _TOPIC


async def test_last_target_with_no_thread_stays_top_level() -> None:
    """Regression guard: an ordinary, non-threaded session must not gain a
    thread reply out of nowhere -- the fix must not overcorrect."""
    adapter = _FakeAdapter()
    service = _service(_UnthreadedSessionStorage(), adapter, "slack")

    result = await service.run_once(
        reason="heartbeat:loop",
        agent_id="main",
        session_key="agent:main:main",
        prompt="ping",
        target="last",
    )

    assert result.status == "delivered"
    assert adapter.messages[0].reply_to == "cron"


async def test_an_explicit_thread_override_still_wins() -> None:
    adapter = _FakeAdapter()
    service = _service(_ThreadedSessionStorage(), adapter, "telegram")

    result = await service.run_once(
        reason="heartbeat:loop",
        agent_id="main",
        session_key="agent:main:main",
        prompt="ping",
        target="last",
        delivery_override={"thread_id": "999"},
    )

    assert result.status == "delivered"
    assert adapter.messages[0].reply_to == "999"
