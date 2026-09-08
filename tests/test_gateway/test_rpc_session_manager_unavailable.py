"""An unwired session manager is UNAVAILABLE, never NOT_FOUND.

The session handlers used to raise ``KeyError("No session manager available")``
for a missing manager or storage. ``RpcRegistry.dispatch`` maps ``KeyError`` to
``NOT_FOUND``, so a gateway with no session backend answered as though the
caller had asked for a session key that does not exist — a permanent,
non-retryable verdict for a condition that is neither. It also made the
capability failure indistinguishable from a real lookup miss for any caller
whose ``except KeyError:`` was meant to catch only the latter.

``RpcUnavailableError`` is the shape the rest of the gateway already uses for
this (``rpc_chat._require_chat_session_manager``, ``rpc_memory``,
``sessions.create(message=...)``), and it dispatches as a retryable
``UNAVAILABLE``.
"""

from __future__ import annotations

import pytest

import agentos.gateway  # noqa: F401  - ensure handler modules are imported
from agentos.gateway.access import ConnectionSurface
from agentos.gateway.auth import AccessContext
from agentos.gateway.protocol import ERROR_UNAVAILABLE
from agentos.gateway.rpc import RpcContext, get_dispatcher

SESSION_KEY = "agent:main:abc123"

# Every session RPC that guards on ``ctx.session_manager``, with the smallest
# params each one accepts before reaching that guard.
UNAVAILABLE_CASES = [
    ("sessions.get", {"key": SESSION_KEY}),
    ("sessions.send", {"key": SESSION_KEY, "message": "hi"}),
    ("sessions.patch", {"key": SESSION_KEY, "displayName": "x"}),
    ("sessions.rename", {"key": SESSION_KEY, "name": "x"}),
    ("sessions.reset", {"key": SESSION_KEY}),
    ("sessions.delete", {"key": SESSION_KEY}),
    ("sessions.contextCompact", {"key": SESSION_KEY}),
    ("sessions.truncate", {"key": SESSION_KEY, "messageId": "m1"}),
    ("sessions.resolve", {"key": SESSION_KEY}),
    ("chat.inject", {"sessionKey": "webchat:main", "role": "user", "content": "hi"}),
]


def _ctx() -> RpcContext:
    """A control connection on a gateway with no session manager wired."""
    return RpcContext(
        conn_id="conn-1",
        access=AccessContext(
            surface=ConnectionSurface.CONTROL, admitted=True, credential_verified=True
        ),
        session_manager=None,
    )


@pytest.mark.parametrize(
    ("method", "params"), UNAVAILABLE_CASES, ids=[case[0] for case in UNAVAILABLE_CASES]
)
async def test_missing_session_manager_is_retryable_unavailable(
    method: str, params: dict
) -> None:
    res = await get_dispatcher().dispatch("r1", method, params, _ctx())

    assert res.error is not None, res
    assert res.error.code == ERROR_UNAVAILABLE, res.error
    assert res.error.retryable is True, res.error


@pytest.mark.parametrize(
    ("method", "params"), UNAVAILABLE_CASES, ids=[case[0] for case in UNAVAILABLE_CASES]
)
async def test_missing_session_manager_is_not_reported_as_not_found(
    method: str, params: dict
) -> None:
    """NOT_FOUND is reserved for a session key that genuinely does not exist."""
    res = await get_dispatcher().dispatch("r1", method, params, _ctx())

    assert res.error is not None, res
    assert res.error.code != "NOT_FOUND", res.error
