"""Issue #3161: a threaded delivery was refused for every channel but Slack.

``ChannelManager._build_delivery_resolution`` answered "can this channel
thread?" with a literal::

    if thread_id and channel_type not in {"slack"}:
        return DeliveryTargetResolution(ok=False, reason="unsupported_thread")

which contradicts the adapters. ``DiscordChannel`` and ``EmailChannel`` both
declare ``threads=True`` in their capability profile and implement threaded
replies, yet a cron job or heartbeat aimed at a Discord thread or an Email
thread failed with ``delivery target resolution failed: unsupported_thread``.

The resolution is only half of it. ``DeliveryChain._post_to_channel`` read
``thread_id`` for Slack and nothing else, so simply widening the allowlist --
the fix the issue proposed -- would have let the delivery succeed while
posting to the *parent* channel, turning a loud refusal into a silent
misdelivery. Both halves are covered here.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.channels.contract import ChannelCapabilityProfile
from agentos.channels.manager import ChannelManager
from agentos.channels.types import OutgoingMessage
from agentos.scheduler.delivery import DeliveryChain
from agentos.scheduler.payloads import make_script_payload
from agentos.scheduler.types import (
    CronJob,
    DeliveryConfig,
    DeliveryMode,
    SessionTarget,
)


class _Adapter:
    """A channel double. ``profile=None`` models an adapter that declares no
    capability profile at all."""

    def __init__(self, channel_type: str, *, threads: bool | None = True) -> None:
        self.messages: list[OutgoingMessage] = []
        if threads is None:
            self.capability_profile = None  # type: ignore[assignment]
        else:
            self.capability_profile = ChannelCapabilityProfile(  # type: ignore[assignment]
                channel_type=channel_type,
                threads=threads,
                thread_messages=threads,
            )

    async def send(self, message: OutgoingMessage) -> None:
        self.messages.append(message)


def _manager(name: str, channel_type: str, adapter: Any) -> ChannelManager:
    return ChannelManager(
        _channels={name: adapter},  # type: ignore[arg-type]
        _turn_runner=None,
        _session_manager=None,
        _channel_types={name: channel_type},
    )


# ---------------------------------------------------------------------------
# Resolution: ask the adapter, not a hard-coded list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel_type", ["discord", "email", "slack"])
def test_an_adapter_that_declares_threads_may_be_given_one(channel_type: str) -> None:
    manager = _manager(channel_type, channel_type, _Adapter(channel_type, threads=True))

    resolved = manager.resolve_delivery_target(
        target=channel_type, to="C123", account_id="", thread_id="T456"
    )

    assert resolved.ok, resolved.reason
    assert resolved.thread_id == "T456"


def test_an_adapter_that_declares_no_threads_is_still_refused() -> None:
    manager = _manager("telegram", "telegram", _Adapter("telegram", threads=False))

    resolved = manager.resolve_delivery_target(
        target="telegram", to="C123", account_id="", thread_id="T456"
    )

    assert not resolved.ok
    assert resolved.reason == "unsupported_thread"


def test_a_threadless_delivery_is_unaffected() -> None:
    manager = _manager("telegram", "telegram", _Adapter("telegram", threads=False))

    resolved = manager.resolve_delivery_target(
        target="telegram", to="C123", account_id="", thread_id=""
    )

    assert resolved.ok


def test_an_adapter_with_no_profile_keeps_the_old_answer() -> None:
    """A bare double or a third-party adapter cannot be asked. It must not be
    newly refused a delivery that worked before -- nor newly granted one."""
    slack = _manager("slack", "slack", _Adapter("slack", threads=None))
    discord = _manager("discord", "discord", _Adapter("discord", threads=None))

    assert slack.resolve_delivery_target(target="slack", to="C1", account_id="", thread_id="T1").ok
    assert not discord.resolve_delivery_target(
        target="discord", to="C1", account_id="", thread_id="T1"
    ).ok


# ---------------------------------------------------------------------------
# Delivery: the thread is actually used
# ---------------------------------------------------------------------------


def _job(channel_name: str, channel_id: str, thread_id: str) -> CronJob:
    return CronJob(
        id="job-1",
        name="threaded",
        handler_key="script_run",
        payload=make_script_payload("test.sh"),
        session_target=SessionTarget.ISOLATED,
        delivery=DeliveryConfig(
            mode=DeliveryMode.CHANNEL,
            channel_name=channel_name,
            channel_id=channel_id,
            thread_id=thread_id,
        ),
    )


async def _deliver(channel_type: str, adapter: _Adapter, thread_id: str) -> _Adapter:
    manager = _manager(channel_type, channel_type, adapter)
    chain = DeliveryChain(channel_manager_ref=lambda: manager)
    report = await chain.deliver(
        _job(channel_type, "C0123", thread_id),
        result_text="hello",
        success=True,
        summary="hello",
        session_key="cron:job-1:run:deadbeef",
    )
    assert report.channel_status == "delivered", report
    return adapter


@pytest.mark.asyncio
async def test_a_discord_thread_is_addressed_directly() -> None:
    """A Discord thread *is* a channel, so its id is the target. Posting to
    the parent would put the report where the user was not looking."""
    adapter = await _deliver("discord", _Adapter("discord"), "T999")

    assert len(adapter.messages) == 1
    assert adapter.messages[0].reply_to == "T999"


@pytest.mark.asyncio
async def test_an_email_thread_is_carried_as_the_reply_target() -> None:
    """The email adapter reads the parent Message-ID off ``reply_to``."""
    adapter = await _deliver("email", _Adapter("email"), "<parent@example.test>")

    assert len(adapter.messages) == 1
    assert adapter.messages[0].reply_to == "<parent@example.test>"


@pytest.mark.asyncio
async def test_a_discord_delivery_without_a_thread_still_targets_the_channel() -> None:
    adapter = await _deliver("discord", _Adapter("discord"), "")

    assert adapter.messages[0].reply_to == "C0123"


@pytest.mark.asyncio
async def test_slack_threading_is_unchanged() -> None:
    adapter = await _deliver("slack", _Adapter("slack"), "1700000000.000100")

    message = adapter.messages[0]
    assert message.reply_to == "1700000000.000100"
    assert message.metadata["channel"] == "C0123"
