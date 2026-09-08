"""Regression tests for issue #1186: sessions.preview reads a bounded tail.

The preview is 120 characters of the last message, so the handler must not
pull an entire session history into memory to produce it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agentos.gateway.config import GatewayConfig
from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_sessions import (
    _PREVIEW_TRANSCRIPT_WINDOWS,
    _handle_sessions_preview,
    _preview_last_message,
    _preview_snippet,
)


@dataclass
class _Entry:
    role: str
    content: str


class _Storage:
    """Transcript storage that records how it was queried."""

    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries
        self.full_reads: list[Any] = []
        self.windows: list[int] = []

    async def get_transcript(self, session_id: str, limit: int | None = None) -> list[_Entry]:
        self.full_reads.append(limit)
        return list(self.entries)

    async def get_recent_transcript(self, session_id: str, n: int) -> list[_Entry]:
        self.windows.append(n)
        return list(self.entries[-n:])


class _LegacyStorage(_Storage):
    """Storage predating ``get_recent_transcript`` — full read is all it has."""

    get_recent_transcript = None  # type: ignore[assignment]


def _transcript(count: int, *, tail_role: str = "assistant") -> list[_Entry]:
    entries = [_Entry(role="user", content=f"msg {i}") for i in range(count - 1)]
    entries.append(_Entry(role=tail_role, content="the last thing said"))
    return entries


# ── Bounded reads ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_reads_only_the_recent_window() -> None:
    storage = _Storage(_transcript(5_000))

    assert await _preview_last_message(storage, "s1") == "the last thing said"
    assert storage.windows == [_PREVIEW_TRANSCRIPT_WINDOWS[0]]
    assert storage.full_reads == []


@pytest.mark.asyncio
async def test_preview_widens_once_when_the_tail_has_no_displayable_message() -> None:
    entries = [_Entry(role="user", content="the only thing said")]
    entries += [_Entry(role="tool", content=f"tool {i}") for i in range(30)]
    storage = _Storage(entries)

    assert await _preview_last_message(storage, "s1") == "the only thing said"
    assert storage.windows == list(_PREVIEW_TRANSCRIPT_WINDOWS)
    assert storage.full_reads == []


@pytest.mark.asyncio
async def test_preview_stops_widening_once_the_whole_session_is_in_the_window() -> None:
    storage = _Storage([_Entry(role="tool", content="only tool output")])

    assert await _preview_last_message(storage, "s1") == ""
    assert storage.windows == [_PREVIEW_TRANSCRIPT_WINDOWS[0]]


@pytest.mark.asyncio
async def test_preview_never_widens_past_the_last_window() -> None:
    storage = _Storage([_Entry(role="tool", content=f"tool {i}") for i in range(500)])

    assert await _preview_last_message(storage, "s1") == ""
    assert storage.windows == list(_PREVIEW_TRANSCRIPT_WINDOWS)
    assert max(storage.windows) == _PREVIEW_TRANSCRIPT_WINDOWS[-1]


@pytest.mark.asyncio
async def test_storage_without_a_tail_query_still_produces_a_preview() -> None:
    storage = _LegacyStorage(_transcript(20))

    assert await _preview_last_message(storage, "s1") == "the last thing said"
    assert storage.full_reads == [-1]


# ── Snippet extraction ──────────────────────────────────────────────────


def test_snippet_truncates_to_120_characters() -> None:
    assert _preview_snippet([_Entry(role="user", content="x" * 500)]) == "x" * 120


def test_snippet_skips_empty_and_non_conversational_entries() -> None:
    entries = [
        _Entry(role="user", content="kept"),
        _Entry(role="assistant", content=""),
        _Entry(role="tool", content="tool output"),
        _Entry(role="system", content="system note"),
    ]

    assert _preview_snippet(entries) == "kept"


def test_snippet_is_empty_for_an_empty_transcript() -> None:
    assert _preview_snippet([]) == ""


# ── Handler wiring ──────────────────────────────────────────────────────


class _Session:
    session_id = "sid-1"
    session_key = "agent:main:sid-1"
    display_name = "Sess"
    updated_at = 42


class _ListingStorage(_Storage):
    async def list_sessions(self, limit: int = 50) -> list[_Session]:
        return [_Session()]


@pytest.mark.asyncio
async def test_handler_previews_without_a_full_transcript_read() -> None:
    storage = _ListingStorage(_transcript(5_000))
    ctx = RpcContext(conn_id="c1", config=GatewayConfig())
    ctx.session_manager = type("_Manager", (), {"storage": storage})()

    payload = await _handle_sessions_preview(None, ctx)

    assert payload["previews"][0]["lastMessage"] == "the last thing said"
    assert storage.full_reads == []
    assert storage.windows == [_PREVIEW_TRANSCRIPT_WINDOWS[0]]
