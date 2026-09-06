"""A predecessor's done callback must not evict its replacement.

``register(cancel_existing=True)`` cancels the in-flight task and stores the new
one under the same session key. Cancellation is not synchronous, so the old
task's done callback runs *after* the replacement is already registered — and an
unconditional ``pop`` there made abort/status queries lose a task that is still
running.
"""

from __future__ import annotations

import asyncio

import pytest

from agentos.gateway.agent_tasks import AgentTaskRegistry


async def _never() -> None:
    await asyncio.Event().wait()


async def _settle() -> None:
    """Let cancellation and done callbacks run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_replacement_survives_predecessor_done_callback() -> None:
    registry = AgentTaskRegistry()
    first = asyncio.create_task(_never())
    registry.register("s1", first)

    second = asyncio.create_task(_never())
    registry.register("s1", second)

    await _settle()

    assert first.cancelled()
    assert not second.done()
    assert registry.get("s1") is second
    assert registry.is_running("s1") is True
    assert registry.get_all() == {"s1": second}

    second.cancel()
    await _settle()


@pytest.mark.asyncio
async def test_replacement_is_removed_when_it_finishes() -> None:
    registry = AgentTaskRegistry()
    first = asyncio.create_task(_never())
    registry.register("s1", first)
    second = asyncio.create_task(_never())
    registry.register("s1", second)
    await _settle()

    second.cancel()
    await _settle()

    assert registry.get("s1") is None
    assert registry.is_running("s1") is False
    assert registry.get_all() == {}


@pytest.mark.asyncio
async def test_single_task_cleanup_is_unchanged() -> None:
    registry = AgentTaskRegistry()

    async def _done() -> str:
        return "ok"

    task = asyncio.create_task(_done())
    registry.register("s1", task)
    assert registry.is_running("s1") is True

    await task
    await _settle()

    assert registry.get("s1") is None
    assert registry.is_running("s1") is False


@pytest.mark.asyncio
async def test_failed_task_is_removed_and_does_not_raise() -> None:
    registry = AgentTaskRegistry()

    async def _boom() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(_boom())
    registry.register("s1", task)
    with pytest.raises(RuntimeError):
        await task
    await _settle()

    assert registry.get("s1") is None


@pytest.mark.asyncio
async def test_cancel_existing_false_still_refuses_a_live_task() -> None:
    registry = AgentTaskRegistry()
    first = asyncio.create_task(_never())
    registry.register("s1", first)
    second = asyncio.create_task(_never())

    with pytest.raises(RuntimeError, match="cancel_existing=False"):
        registry.register("s1", second, cancel_existing=False)

    assert registry.get("s1") is first

    first.cancel()
    second.cancel()
    await _settle()


@pytest.mark.asyncio
async def test_other_sessions_are_untouched_by_a_replacement() -> None:
    registry = AgentTaskRegistry()
    other = asyncio.create_task(_never())
    registry.register("s2", other)
    first = asyncio.create_task(_never())
    registry.register("s1", first)
    second = asyncio.create_task(_never())
    registry.register("s1", second)
    await _settle()

    assert registry.get("s2") is other
    assert registry.get("s1") is second

    other.cancel()
    second.cancel()
    await _settle()
