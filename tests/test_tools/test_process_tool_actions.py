from __future__ import annotations

import json

import pytest

from agentos.tools.builtin.shell import process
from agentos.tools.types import ToolError


@pytest.mark.asyncio
async def test_process_rejects_invalid_action_without_session_id() -> None:
    with pytest.raises(
        ToolError,
        match=r"^Invalid action: list\|poll\|log\|kill\|remove\|write\|submit\|eof$",
    ):
        await process(action="status")


@pytest.mark.asyncio
async def test_process_rejects_invalid_action_with_session_id() -> None:
    with pytest.raises(
        ToolError,
        match=r"^Invalid action: list\|poll\|log\|kill\|remove\|write\|submit\|eof$",
    ):
        await process(action="unknown_action", session_id="nonexistent-id")


@pytest.mark.asyncio
async def test_process_valid_action_missing_session_id() -> None:
    with pytest.raises(ToolError, match=r"'session_id' required"):
        await process(action="poll")


@pytest.mark.asyncio
async def test_process_list_action_succeeds_without_session_id() -> None:
    raw = await process(action="list")
    payload = json.loads(raw)
    assert payload["status"] == "ok"
    assert payload["action"] == "list"
    assert isinstance(payload["sessions"], list)
