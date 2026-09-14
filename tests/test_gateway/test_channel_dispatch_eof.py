"""``run_channel_dispatch`` stops when a channel's input stream is exhausted.

A terminal channel on a closed stdin used to return an empty message
forever: ``receive()`` reported EOF and a blank line identically, and the
loop had no EOF check, so it spun, pushing a spurious empty turn through
the session pipeline on every iteration. ``receive()`` now raises
``EOFError`` and the loop returns on it instead of re-reading.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentos.gateway.channel_dispatch import run_channel_dispatch


class _ExhaustedChannel:
    """Channel whose input is already at EOF."""

    def __init__(self) -> None:
        self.receive_calls = 0

    async def receive(self) -> Any:
        self.receive_calls += 1
        raise EOFError("stdin is closed")


@pytest.mark.asyncio
async def test_dispatch_loop_returns_once_input_is_exhausted() -> None:
    channel = _ExhaustedChannel()

    await asyncio.wait_for(
        run_channel_dispatch(
            channel,
            turn_runner=None,
            session_manager=None,
            session_key_builder=lambda msg: "sess",
            session_prefix="test",
        ),
        timeout=5,
    )

    assert channel.receive_calls == 1
