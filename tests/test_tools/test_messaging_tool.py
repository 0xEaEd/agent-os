from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agentos.tools.builtin.messaging import message, register_channel, unregister_channel
from agentos.tools.types import ToolError


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_text", ["", "   ", "\t\n  ", None])
async def test_message_send_rejects_empty_or_whitespace_text(empty_text: str | None) -> None:
    with pytest.raises(
        ToolError,
        match="'text' is required and must not be empty or whitespace-only for send action",
    ):
        await message(channel="slack", target="C123", text=empty_text, action="send")


@pytest.mark.asyncio
async def test_message_send_success() -> None:
    mock_channel = AsyncMock()
    mock_channel.send = AsyncMock()
    register_channel("mock_chan", mock_channel)
    try:
        res = await message(channel="mock_chan", target="user1", text="hello", action="send")
        data = json.loads(res)
        assert data["status"] == "sent"
        assert data["channel"] == "mock_chan"
        assert data["target"] == "user1"
        assert mock_channel.send.await_count == 1
    finally:
        unregister_channel("mock_chan")
