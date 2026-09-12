"""``sessions.truncate`` validates ``maxMessages`` before it destroys anything.

``bool`` is a subclass of ``int``, so an unguarded read let ``maxMessages:
false`` reach the truncation as ``0`` — wiping the whole transcript while
answering ``ok: true`` — and ``true`` as "keep only the newest message". A
string reached the session manager's ``max_messages < 0`` comparison and
raised ``TypeError``, surfacing as a raw INTERNAL_ERROR. See issue #1371.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.config import GatewayConfig
from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.gateway.rpc_sessions import _require_max_messages


class _FakeSession:
    def __init__(self, session_key: str) -> None:
        self.session_key = session_key
        self.session_id = session_key.rsplit(":", 1)[-1]


class _FakeStorage:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session
        self.memory_durable_receipts: list[Any] = []

    async def get_session(self, key: str) -> _FakeSession | None:
        return self._session if key == self._session.session_key else None


class _FakeSessionManager:
    """Just enough manager for the truncate handler's happy path."""

    def __init__(self, session: _FakeSession) -> None:
        self._storage = _FakeStorage(session)
        self.transcript: list[Any] = []
        self.truncate_calls: list[tuple[str, int]] = []

    async def get_transcript(self, key: str) -> list[Any]:
        return list(self.transcript)

    async def truncate(self, session_key: str, max_messages: int = 20) -> dict:
        self.truncate_calls.append((session_key, max_messages))
        return {"truncated": False, "before_count": 0, "after_count": 0}


SESSION_KEY = "agent:main:webchat:truncate-validation"


@pytest.fixture
def dispatcher():
    return get_dispatcher()


@pytest.fixture
def manager() -> _FakeSessionManager:
    return _FakeSessionManager(_FakeSession(SESSION_KEY))


@pytest.fixture
def ctx(manager: _FakeSessionManager) -> RpcContext:
    context = RpcContext(conn_id="test-conn", config=GatewayConfig())
    context.session_manager = manager
    return context


# ---------------------------------------------------------------------------
# Unit: the guard itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [False, True, "20", 20.0, None, -1, [], {"a": 1}])
def test_rejected_values(value: Any) -> None:
    with pytest.raises(ValueError, match="params.maxMessages must be a non-negative integer"):
        _require_max_messages({"maxMessages": value})


@pytest.mark.parametrize("value", [0, 1, 20, 10_000])
def test_accepted_values_pass_through(value: int) -> None:
    assert _require_max_messages({"maxMessages": value}) == value


def test_missing_key_uses_the_default() -> None:
    assert _require_max_messages({}) == 20
    assert _require_max_messages(None) == 20


# ---------------------------------------------------------------------------
# Handler: a bad value is a client error, and nothing is truncated.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [False, True, "20", 20.0, -1])
async def test_bad_max_messages_is_invalid_request_and_truncates_nothing(
    dispatcher, ctx, manager, value: Any
) -> None:
    manager.transcript = [object(), object(), object()]

    res = await dispatcher.dispatch(
        "r1",
        "sessions.truncate",
        {"key": SESSION_KEY, "maxMessages": value},
        ctx,
    )

    assert res.ok is False
    assert res.error.code == "INVALID_REQUEST"
    assert res.error.message == "params.maxMessages must be a non-negative integer"
    assert manager.truncate_calls == []


@pytest.mark.asyncio
async def test_zero_remains_an_accepted_wipe(dispatcher, ctx, manager) -> None:
    res = await dispatcher.dispatch(
        "r1",
        "sessions.truncate",
        {"key": SESSION_KEY, "maxMessages": 0},
        ctx,
    )

    assert res.ok is True
    assert manager.truncate_calls == [(SESSION_KEY, 0)]


@pytest.mark.asyncio
async def test_default_is_used_when_max_messages_is_absent(dispatcher, ctx, manager) -> None:
    res = await dispatcher.dispatch("r1", "sessions.truncate", {"key": SESSION_KEY}, ctx)

    assert res.ok is True
    assert manager.truncate_calls == [(SESSION_KEY, 20)]


@pytest.mark.parametrize("value", [0, 1, 5, 50])
def test_snake_case_max_messages_accepted(value: int) -> None:
    assert _require_max_messages({"max_messages": value}) == value


@pytest.mark.parametrize("value", [False, True, "20", 20.0, None, -1, [], {"a": 1}])
def test_snake_case_max_messages_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="params.maxMessages must be a non-negative integer"):
        _require_max_messages({"max_messages": value})


def test_camel_case_precedence_over_snake_case() -> None:
    assert _require_max_messages({"maxMessages": 5, "max_messages": 10}) == 5


@pytest.mark.asyncio
async def test_snake_case_max_messages_truncates_correctly(dispatcher, ctx, manager) -> None:
    res = await dispatcher.dispatch(
        "r1",
        "sessions.truncate",
        {"key": SESSION_KEY, "max_messages": 5},
        ctx,
    )

    assert res.ok is True
    assert manager.truncate_calls == [(SESSION_KEY, 5)]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [False, True, "5", -1])
async def test_snake_case_bad_max_messages_rejects_and_truncates_nothing(
    dispatcher, ctx, manager, value: Any
) -> None:
    manager.transcript = [object(), object(), object()]

    res = await dispatcher.dispatch(
        "r1",
        "sessions.truncate",
        {"key": SESSION_KEY, "max_messages": value},
        ctx,
    )

    assert res.ok is False
    assert res.error.code == "INVALID_REQUEST"
    assert res.error.message == "params.maxMessages must be a non-negative integer"
    assert manager.truncate_calls == []

