"""Issue #1618: ``/new <title>`` stores a normalized display name.

Gateway mode hands the title to ``sessions.create``, which now normalizes it
server-side (see ``tests/test_gateway/test_rpc_sessions_create_display_name``).
Standalone mode never reaches the gateway, so it normalizes before calling the
session manager -- the same shape ``/rename`` stores on that surface.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.cli.chat.session_state import ChatSessionState
from agentos.cli.tui.adapters.slash_standalone import (
    StandaloneSlashContext,
    StandaloneSlashServices,
    handle_standalone_slash_command,
)


class _StandaloneManager:
    def __init__(self) -> None:
        self.creates: list[tuple[str, dict[str, Any]]] = []

    async def create(self, session_key: str, **fields: Any) -> None:
        self.creates.append((session_key, fields))


def _standalone_context(manager: _StandaloneManager) -> StandaloneSlashContext:
    state = ChatSessionState(session_key="agent:main:standalone:test", model="openai/test")
    return StandaloneSlashContext(
        state=state,
        session_key=state.session_key,
        model=state.model,
        tool_ctx=object(),
        slash_services=StandaloneSlashServices(create_session=manager.create),
        turn_runner=object(),
        build_tool_ctx=lambda _session_key: object(),
        replace_session=lambda **_updates: None,
    )


@pytest.mark.asyncio
async def test_standalone_new_persists_a_normalized_title() -> None:
    manager = _StandaloneManager()
    context = _standalone_context(manager)

    handled = await handle_standalone_slash_command("/new \x1b[2Jops \n\n  review\x1b[0m ", context)

    assert handled is True
    assert len(manager.creates) == 1
    _, fields = manager.creates[0]
    assert fields["display_name"] == "[2Jops review[0m"
    assert context.state.display_name == "[2Jops review[0m"


@pytest.mark.asyncio
async def test_standalone_new_with_a_blank_title_stores_no_name() -> None:
    manager = _StandaloneManager()
    context = _standalone_context(manager)

    handled = await handle_standalone_slash_command("/new   \t ", context)

    assert handled is True
    assert manager.creates[0][1]["display_name"] is None
    assert context.state.display_name is None


@pytest.mark.asyncio
async def test_standalone_new_without_a_title_is_unchanged() -> None:
    manager = _StandaloneManager()
    context = _standalone_context(manager)

    handled = await handle_standalone_slash_command("/new", context)

    assert handled is True
    assert manager.creates[0][1]["display_name"] is None
    assert context.state.display_name is None
