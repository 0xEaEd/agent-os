"""A child process learns which agent session is running it (#trading desk)."""

from __future__ import annotations

import os

import pytest

from agentos.tools.builtin import shell
from agentos.tools.types import ToolContext, current_tool_context


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX shell")
async def test_exec_command_exports_session_key_and_agent() -> None:
    token = current_tool_context.set(
        ToolContext(session_key="agent:main:webchat:desk1", agent_id="main")
    )
    try:
        result = await shell.exec_command('printf "%s|%s" "$AGENTOS_SESSION_KEY" "$AGENTOS_AGENT"')
    finally:
        current_tool_context.reset(token)
    assert result == "exit_code=0\nagent:main:webchat:desk1|main"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX shell")
async def test_exec_command_keeps_explicit_env_override() -> None:
    token = current_tool_context.set(ToolContext(session_key="agent:main:webchat:desk1"))
    try:
        result = await shell.exec_command(
            'printf "%s" "$AGENTOS_SESSION_KEY"', env={"AGENTOS_SESSION_KEY": "custom"}
        )
    finally:
        current_tool_context.reset(token)
    assert result == "exit_code=0\ncustom"


def test_add_session_env_without_context_is_noop() -> None:
    env: dict[str, str] = {}
    shell._add_session_env(env)
    assert env == {}
