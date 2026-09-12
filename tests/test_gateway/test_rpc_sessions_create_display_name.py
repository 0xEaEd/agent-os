"""``sessions.create`` normalizes ``displayName`` like ``rename`` / ``patch`` do.

Regression for #1618: ``_handle_sessions_create`` passed ``displayName``
to ``SessionManager.create`` verbatim, the one write path that skipped
``normalize_session_name``. Raw ANSI/OSC bytes reached whatever terminal later
rendered the session list or toolbar chip (``markup_escape`` only handles
Rich's bracket syntax), and multi-line or over-length names broke the
single-line, <=120-character shape every list row assumes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentos.gateway.config import GatewayConfig
from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.session.naming import MAX_SESSION_NAME_LENGTH, normalize_session_name


@dataclass
class _Session:
    session_key: str
    session_id: str
    display_name: str | None


class _SessionManager:
    """Records exactly what ``sessions.create`` hands to ``create``."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    async def create(
        self,
        session_key: str,
        agent_id: str = "main",
        display_name: str | None = None,
        model: str | None = None,
    ) -> _Session:
        self.create_calls.append(
            {"session_key": session_key, "agent_id": agent_id, "display_name": display_name}
        )
        return _Session(session_key, session_key.rsplit(":", 1)[-1], display_name)


def _ctx(manager: _SessionManager) -> RpcContext:
    ctx = RpcContext(conn_id="test-conn", config=GatewayConfig())
    ctx.session_manager = manager
    return ctx


async def _create(manager: _SessionManager, params: dict[str, Any]):
    return await get_dispatcher().dispatch("r1", "sessions.create", params, _ctx(manager))


@pytest.mark.asyncio
async def test_create_strips_terminal_escape_sequences() -> None:
    manager = _SessionManager()
    raw = "\x1b[2J\x1b]0;pwned\x07ops \x1b[31mreview\x1b[0m"

    res = await _create(manager, {"displayName": raw})

    assert res.ok is True
    stored = manager.create_calls[0]["display_name"]
    assert stored == "[2J]0;pwnedops [31mreview[0m"
    assert "\x1b" not in stored and "\x07" not in stored
    assert stored == normalize_session_name(raw)


@pytest.mark.asyncio
async def test_create_collapses_pasted_multi_line_names() -> None:
    manager = _SessionManager()

    res = await _create(manager, {"displayName": "  first line\n\n\tsecond   line \r\n"})

    assert res.ok is True
    assert manager.create_calls[0]["display_name"] == "first line second line"


@pytest.mark.asyncio
async def test_create_trims_over_length_names() -> None:
    manager = _SessionManager()

    res = await _create(manager, {"displayName": "x" * (MAX_SESSION_NAME_LENGTH * 3)})

    assert res.ok is True
    assert manager.create_calls[0]["display_name"] == "x" * MAX_SESSION_NAME_LENGTH


@pytest.mark.asyncio
async def test_create_stores_no_name_for_a_blank_display_name() -> None:
    manager = _SessionManager()

    res = await _create(manager, {"displayName": "  \n\t "})

    assert res.ok is True
    assert manager.create_calls[0]["display_name"] is None


@pytest.mark.asyncio
async def test_create_without_display_name_is_unchanged() -> None:
    manager = _SessionManager()

    res = await _create(manager, {"agentId": "main"})

    assert res.ok is True
    assert manager.create_calls[0]["display_name"] is None


@pytest.mark.asyncio
async def test_create_keeps_an_already_clean_name() -> None:
    manager = _SessionManager()

    res = await _create(manager, {"displayName": "Ops review"})

    assert res.ok is True
    assert manager.create_calls[0]["display_name"] == "Ops review"


@pytest.mark.asyncio
async def test_create_rejects_a_non_string_display_name() -> None:
    manager = _SessionManager()

    res = await _create(manager, {"displayName": ["not", "a", "string"]})

    assert res.ok is False
    assert res.error is not None
    assert "session name must be a string" in res.error.message
    assert manager.create_calls == []


@pytest.mark.asyncio
async def test_create_matches_what_rename_would_store() -> None:
    """The invariant the normalizer's docstring promises: every write path
    stores the same shape for the same input."""
    manager = _SessionManager()
    raw = "\x1b[1m Q3 \n planning \x1b[0m"

    await _create(manager, {"displayName": raw})

    assert manager.create_calls[0]["display_name"] == normalize_session_name(raw)
