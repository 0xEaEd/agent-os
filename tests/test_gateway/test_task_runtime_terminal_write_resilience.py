"""A storage failure during a task's terminal write must not strand it.

``TaskRuntime._mark_terminal`` claims its idempotency guard
(``task.terminal_emitted``) and removes the task from every live tracking
structure before the fallible tail: the storage write, error-string
formatting, and event emission. The guard is genuinely load-bearing -- a
DROP_OLDEST eviction (``_apply_overflow_policy``) and a task's own
cancellation path can both race to call ``_mark_terminal`` for the same
task, and the guard is what makes the loser's call a safe no-op -- so it
cannot simply move later. But before the fix, any exception in that tail
made the guard's own claim permanent while nothing had actually been
persisted: the record stayed at its pre-terminal status forever, and
``task.done`` was never set, so every waiter hung (#3353).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agentos.gateway.routing import RouteEnvelope, SourceKind
from agentos.gateway.task_runtime import TaskRuntime
from agentos.session.models import AgentTaskRecord, AgentTaskStatus


def _make_envelope(session_key: str = "agent-1::sess-1") -> RouteEnvelope:
    return RouteEnvelope(
        source_kind=SourceKind.WEB,
        source_name="test",
        agent_id="agent-1",
        session_key=session_key,
        input_provenance={"kind": "test"},
        metadata={},
    )


def _make_flaky_storage(*, fail_terminal_writes: int) -> tuple[Any, dict[str, AgentTaskRecord]]:
    """A storage double whose terminal write (the call carrying ``finished_at``)
    fails the first ``fail_terminal_writes`` times, then succeeds normally.
    Every other call (the running-transition write, reads) always succeeds.
    """
    storage = MagicMock()
    task_db: dict[str, AgentTaskRecord] = {}
    calls = {"n": 0}

    async def create(record: AgentTaskRecord) -> None:
        task_db[record.task_id] = record

    async def update(task_id: str, **kwargs: Any) -> None:
        if "finished_at" in kwargs:
            calls["n"] += 1
            if calls["n"] <= fail_terminal_writes:
                raise RuntimeError("simulated transient storage failure")
        rec = task_db.get(task_id)
        if rec is None:
            return
        for key, value in kwargs.items():
            if hasattr(rec, key):
                object.__setattr__(rec, key, value)

    async def get(task_id: str) -> AgentTaskRecord | None:
        return task_db.get(task_id)

    storage.create_agent_task = create
    storage.update_agent_task = update
    storage.get_agent_task = get
    return storage, task_db


async def test_a_transient_terminal_write_failure_still_finalizes_via_fallback() -> None:
    """The rich write fails once; the minimal fallback write in the same
    call must still land, so the task reaches its real terminal status."""
    storage, task_db = _make_flaky_storage(fail_terminal_writes=1)

    async def handler(_run: Any) -> str:
        return "the turn actually completed successfully"

    runtime = TaskRuntime(storage=storage, turn_handler=handler)
    handle = await runtime.enqueue(_make_envelope(), "hello")

    record = await runtime.wait(handle.task_id, timeout=2.0)

    assert record.status == AgentTaskStatus.SUCCEEDED
    assert task_db[handle.task_id].status == AgentTaskStatus.SUCCEEDED


async def test_terminal_write_failure_does_not_hang_a_waiter_even_when_storage_stays_down() -> None:
    """Even if every terminal-write attempt fails for the whole window,
    task.done must still fire -- a waiter gets an answer, not a hang."""
    storage, task_db = _make_flaky_storage(fail_terminal_writes=10_000)

    async def handler(_run: Any) -> str:
        return "the turn actually completed successfully"

    runtime = TaskRuntime(storage=storage, turn_handler=handler)
    handle = await runtime.enqueue(_make_envelope(), "hello")

    # Before the fix this raised asyncio.TimeoutError -- task.done was never set.
    record = await runtime.wait(handle.task_id, timeout=2.0)

    # Storage genuinely never accepted the write, so the persisted record is
    # stale -- but the waiter got an answer instead of hanging forever, and
    # the task is no longer reachable for a stuck double-cancel/double-run.
    assert record.status == AgentTaskStatus.RUNNING
    assert handle.task_id not in runtime._tasks


async def test_a_failed_turn_with_a_flaky_terminal_write_still_finalizes_as_failed() -> None:
    """The fix must not only cover the success path -- a turn that raised
    and then hit a flaky terminal write must still resolve, not hang."""
    storage, task_db = _make_flaky_storage(fail_terminal_writes=1)

    async def handler(_run: Any) -> str:
        raise RuntimeError("the turn itself failed")

    runtime = TaskRuntime(storage=storage, turn_handler=handler)
    handle = await runtime.enqueue(_make_envelope(), "hello")

    record = await runtime.wait(handle.task_id, timeout=2.0)

    assert record.status == AgentTaskStatus.FAILED
    assert task_db[handle.task_id].status == AgentTaskStatus.FAILED
