"""Heartbeat delivery tells the email adapter when ``channel_id`` is a mailbox.

``EmailChannel`` never reads a recipient off ``reply_to``: an inbound thread
key is a Message-ID with the same ``local@domain`` shape, and its domain is
chosen by whoever sent the original mail. A heartbeat that delivers to an
operator-configured address therefore has to name it in ``metadata["to"]``,
while one that delivers back into the session's last email thread must not --
there ``channel_id`` *is* the thread key.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentos.channels.manager import ChannelManager
from agentos.channels.types import OutgoingMessage
from agentos.scheduler.heartbeat_loop import HeartbeatLoop
from agentos.scheduler.heartbeat_service import HeartbeatService
from agentos.scheduler.types import DeliveryConfig, DeliveryMode

_THREAD_KEY = "CAGr5Gg=xyz@mail.gmail.com"
_MAILBOX = "alerts@example.com"


class _FakeAdapter:
    def __init__(self) -> None:
        self.messages: list[OutgoingMessage] = []

    async def send(self, message: OutgoingMessage) -> None:
        self.messages.append(message)


class _FakeTurnRunner:
    async def run(self, **_: Any):  # noqa: ANN202
        yield SimpleNamespace(kind="done", text="heartbeat text")


class _FakeSessionStorage:
    """A session whose last inbound message came from an email thread."""

    async def get_session(self, session_key: str) -> Any:
        return SimpleNamespace(
            last_channel="email", last_to=_THREAD_KEY, last_account_id="", last_thread_id=""
        )


def _service(adapter: _FakeAdapter) -> HeartbeatService:
    manager = ChannelManager(
        _channels={"mail": adapter},  # type: ignore[dict-item]
        _turn_runner=None,
        _session_manager=None,
        _channel_types={"mail": "email"},
    )
    return HeartbeatService(
        turn_runner=_FakeTurnRunner(),
        session_storage=_FakeSessionStorage(),
        channel_manager_ref=lambda: manager,
    )


async def test_send_delivery_names_the_mailbox_for_a_configured_email_target() -> None:
    adapter = _FakeAdapter()
    delivery = DeliveryConfig(mode=DeliveryMode.CHANNEL, channel_name="email", channel_id=_MAILBOX)

    assert await _service(adapter)._send_delivery(delivery, "hi") is None

    (msg,) = adapter.messages
    assert msg.reply_to == _MAILBOX
    assert msg.metadata == {"to": _MAILBOX}


async def test_send_delivery_keeps_an_inferred_email_thread_as_reply_to_only() -> None:
    adapter = _FakeAdapter()
    delivery = DeliveryConfig(
        mode=DeliveryMode.ORIGIN, channel_name="email", channel_id=_THREAD_KEY
    )

    assert await _service(adapter)._send_delivery(delivery, "hi") is None

    (msg,) = adapter.messages
    assert msg.reply_to == _THREAD_KEY
    assert msg.metadata == {}


async def _run_last(adapter: _FakeAdapter, override: dict[str, str] | None) -> OutgoingMessage:
    result = await _service(adapter).run_once(
        reason="test",
        agent_id="main",
        session_key="agent:main:main",
        prompt="ping",
        target="last",
        delivery_override=override,
    )
    assert result.status == "delivered", result
    (msg,) = adapter.messages
    return msg


async def test_target_last_without_override_replies_into_the_email_thread() -> None:
    msg = await _run_last(_FakeAdapter(), None)

    assert msg.reply_to == _THREAD_KEY
    assert msg.metadata == {}


async def test_target_last_with_a_configured_recipient_override_names_the_mailbox() -> None:
    """A ``mode: channel`` override is an operator-chosen address, not a thread."""
    msg = await _run_last(_FakeAdapter(), {"channel_id": _MAILBOX, "mode": "channel"})

    assert msg.reply_to == _MAILBOX
    assert msg.metadata == {"to": _MAILBOX}


async def test_target_last_with_a_snapshot_override_keeps_the_thread_key_as_reply_to() -> None:
    """A snapshot override carries the originating conversation's key."""
    msg = await _run_last(
        _FakeAdapter(), {"channel_name": "email", "channel_id": "other-thread@mail.example"}
    )

    assert msg.reply_to == "other-thread@mail.example"
    assert msg.metadata == {}


async def test_target_last_with_a_channel_mode_override_but_no_id_stays_a_thread_reply() -> None:
    """``mode: channel`` without an id still delivers to the inferred thread."""
    msg = await _run_last(_FakeAdapter(), {"channel_name": "email", "mode": "channel"})

    assert msg.reply_to == _THREAD_KEY
    assert msg.metadata == {}


async def test_explicit_email_target_names_the_configured_mailbox() -> None:
    adapter = _FakeAdapter()
    result = await _service(adapter).run_once(
        reason="test",
        agent_id="main",
        session_key="agent:main:main",
        prompt="ping",
        target="email",
        delivery_override={"channel_id": _MAILBOX},
    )

    assert result.status == "delivered", result
    (msg,) = adapter.messages
    assert msg.metadata == {"to": _MAILBOX}


class _CapturingService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_once(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.mark.parametrize(
    ("to", "expected_override"),
    [
        (
            _MAILBOX,
            {
                "channel_name": "",
                "channel_id": _MAILBOX,
                "account_id": "",
                "thread_id": "th-1",
                "mode": "channel",
            },
        ),
        # No configured recipient: the thread comes from the session, so it
        # must not be promoted to a mailbox.
        ("", {"channel_name": "", "channel_id": "", "account_id": "", "thread_id": "th-1"}),
    ],
)
async def test_loop_marks_an_operator_configured_recipient_as_channel_mode(
    tmp_path: Path, to: str, expected_override: dict[str, str]
) -> None:
    heartbeat_md = tmp_path / "HEARTBEAT.md"
    heartbeat_md.write_text("Check the inbox and report anything urgent.\n", encoding="utf-8")
    config = SimpleNamespace(
        workspace_dir=str(tmp_path),
        heartbeat=SimpleNamespace(
            enabled=True,
            interval_ms=60_000,
            target="last",
            prompt=None,
            ack_max_chars=300,
            light_context=False,
            to=to,
            account_id="",
            thread_id="th-1",
            config_path=str(heartbeat_md),
        ),
    )
    service = _CapturingService()
    loop = HeartbeatLoop(config=config, heartbeat_service=service)

    await loop._tick()

    (call,) = service.calls
    assert call["target"] == "last"
    assert call["delivery_override"] == expected_override
