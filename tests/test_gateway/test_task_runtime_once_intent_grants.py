"""A ``once`` destructive-intent approval must end at the session's next user message.

``IntentApprovalCache`` documents ``once`` as "cleared at the start of every new
user message". The only ``clear_scope("once", ...)`` call lived in the no-runtime
fallback of ``sessions.send``; the gateway always has a ``TaskRuntime``, so a
grant made for ``rm build`` on Telegram, in the web UI or in the CLI outlived the
turn it was approved for and answered the same command in every later turn for
the cache TTL (30 minutes) without a prompt.

These drive a real ``TaskRuntime`` whose turn handler is the gateway's own
``dispatch_task_runtime_turn``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agentos.gateway.boot import dispatch_task_runtime_turn
from agentos.gateway.config import GatewayConfig
from agentos.gateway.routing import RouteEnvelope, SourceKind
from agentos.gateway.task_runtime import TaskRuntime
from agentos.sandbox.intent_cache import get_intent_cache, reset_intent_cache
from agentos.session.models import AgentTaskRecord

SESSION = "agent:main:webchat:direct:alice"
OTHER_SESSION = "agent:main:webchat:direct:bob"
COMMAND = "rm -rf /srv/agentos-build"


class _SilentRunner:
    """A turn runner that finishes without events."""

    async def run(self, message: str, session_key: str, **kwargs: Any):  # noqa: ARG002
        if False:  # pragma: no cover - makes this an async generator
            yield None


def _storage() -> Any:
    storage = MagicMock()
    rows: dict[str, AgentTaskRecord] = {}

    async def create(record: AgentTaskRecord) -> None:
        rows[record.task_id] = record

    async def update(task_id: str, **kwargs: Any) -> None:
        row = rows.get(task_id)
        if row is not None:
            for key, value in kwargs.items():
                if hasattr(row, key):
                    object.__setattr__(row, key, value)

    async def get(task_id: str) -> AgentTaskRecord | None:
        return rows.get(task_id)

    storage.create_agent_task = create
    storage.update_agent_task = update
    storage.get_agent_task = get
    return storage


def _runtime() -> TaskRuntime:
    config = GatewayConfig(
        agent_stream_heartbeat_interval_seconds=0.0,
        agent_stream_idle_timeout_seconds=5.0,
    )

    async def _handler(run: Any) -> None:
        await dispatch_task_runtime_turn(
            run,
            config=config,
            session_manager=None,
            turn_runner=_SilentRunner(),
            event_emitter=_no_emit,
        )

    return TaskRuntime(storage=_storage(), turn_handler=_handler)


async def _no_emit(session_key: str, event_name: str, payload: dict[str, Any]) -> None:
    return None


def _envelope(kind: str, session_key: str = SESSION) -> RouteEnvelope:
    return RouteEnvelope(
        source_kind=SourceKind.WEB,
        source_name="test",
        agent_id="main",
        session_key=session_key,
        input_provenance={"kind": kind},
        metadata={},
    )


async def _run_turn(kind: str, session_key: str = SESSION) -> None:
    runtime = _runtime()
    handle = await runtime.enqueue(_envelope(kind, session_key), "hello")
    await runtime.wait(handle.task_id, timeout=5.0)


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_intent_cache()
    yield
    reset_intent_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["web_message", "channel_message", "cli_message"])
async def test_a_user_turn_ends_the_sessions_once_grant(kind: str) -> None:
    cache = get_intent_cache()
    cache.record(COMMAND, session_key=SESSION)
    assert cache.check(COMMAND, session_key=SESSION) is True  # the grant is live

    await _run_turn(kind)

    assert cache.check(COMMAND, session_key=SESSION) is False


@pytest.mark.asyncio
async def test_a_user_turn_keeps_the_sessions_always_grant() -> None:
    cache = get_intent_cache()
    cache.record_always(COMMAND, session_key=SESSION)

    await _run_turn("web_message")

    assert cache.check(COMMAND, session_key=SESSION) is True


@pytest.mark.asyncio
async def test_a_user_turn_leaves_another_sessions_once_grant_alone() -> None:
    cache = get_intent_cache()
    cache.record(COMMAND, session_key=OTHER_SESSION)

    await _run_turn("web_message", SESSION)

    assert cache.check(COMMAND, session_key=OTHER_SESSION) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cron_job", "subagent_task", "runtime_send"])
async def test_a_turn_that_is_not_a_user_message_keeps_the_grant(kind: str) -> None:
    """Pass either way by design: only a user message ends a ``once`` grant."""
    cache = get_intent_cache()
    cache.record(COMMAND, session_key=SESSION)

    await _run_turn(kind)

    assert cache.check(COMMAND, session_key=SESSION) is True
