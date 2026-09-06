"""Regression tests for issue #956.

``sync()`` lets a pending session delta bypass the clean-search fast path,
but the reset that consumes the delta used to require a session indexer.
With session indexing disabled -- the default -- ``_do_session_sync()`` is
a successful no-op, so nothing ever cleared the delta and every later
search rescanned the workspace even though neither files nor messages had
changed.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.memory.sync_manager import MemorySyncManager


class RecordingStore:
    """Minimal store that records what the sync manager asked it to do."""

    def __init__(self) -> None:
        self.indexed: list[str] = []
        self.removed: list[str] = []

    async def index_file(
        self,
        *,
        path: str,
        content: str,
        source: object,
        mtime: float | None = None,
    ) -> int:
        self.indexed.append(path)
        return 1

    async def remove_file(self, path: str) -> None:
        self.removed.append(path)


class StubSessionIndexer:
    """Session indexer whose ``sync`` can be made to fail on demand."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def sync(self, *, force: bool = False) -> Any:
        self.calls += 1
        if self.fail:
            raise RuntimeError("session index unavailable")
        return type("Result", (), {"indexed": 0, "removed": 0, "skipped": 0})()


def _make_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    memory = workspace / "memory"
    memory.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("root\n", encoding="utf-8")
    return workspace, memory


class ScanCounter:
    """Wrap ``_scan_files`` so tests can count recursive workspace walks."""

    def __init__(self, manager: MemorySyncManager) -> None:
        self.count = 0
        self._inner = manager._scan_files
        manager._scan_files = self  # type: ignore[method-assign]

    def __call__(self) -> dict[str, float]:
        self.count += 1
        return self._inner()


def _manager(store, workspace, memory, *, session_indexer=None) -> MemorySyncManager:
    return MemorySyncManager(
        store=store,
        workspace_dir=workspace,
        memory_dir=memory,
        session_indexer=session_indexer,
    )


SEARCH_REASONS = ["search", "search:tool", "search:control"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", SEARCH_REASONS)
@pytest.mark.parametrize("byte_count", [20, 200_000])
async def test_one_search_sync_consumes_the_delta_without_an_indexer(
    tmp_path, reason: str, byte_count: int
) -> None:
    """A small message and one past the byte threshold both settle after one sync."""
    workspace, memory = _make_workspace(tmp_path)
    manager = _manager(RecordingStore(), workspace, memory)
    scans = ScanCounter(manager)

    manager.notify_message(byte_count)
    await manager.sync(reason=reason)
    assert scans.count == 1

    # Nothing changed: every later search must take the clean fast path.
    await manager.sync(reason=reason)
    await manager.sync(reason=reason)
    assert scans.count == 1
    assert manager._delta.has_pending() is False


@pytest.mark.asyncio
async def test_a_new_message_after_a_search_syncs_again(tmp_path) -> None:
    workspace, memory = _make_workspace(tmp_path)
    manager = _manager(RecordingStore(), workspace, memory)
    scans = ScanCounter(manager)

    manager.notify_message(20)
    await manager.sync(reason="search:tool")
    await manager.sync(reason="search:tool")
    assert scans.count == 1

    manager.notify_message(20)
    await manager.sync(reason="search:tool")
    assert scans.count == 2


@pytest.mark.asyncio
async def test_dirty_state_still_syncs_after_the_delta_is_consumed(tmp_path) -> None:
    workspace, memory = _make_workspace(tmp_path)
    manager = _manager(RecordingStore(), workspace, memory)
    scans = ScanCounter(manager)

    manager.notify_message(20)
    await manager.sync(reason="search:tool")
    assert scans.count == 1

    manager._dirty = True
    await manager.sync(reason="search:tool")
    assert scans.count == 2


@pytest.mark.asyncio
async def test_forced_search_still_syncs_after_the_delta_is_consumed(tmp_path) -> None:
    workspace, memory = _make_workspace(tmp_path)
    manager = _manager(RecordingStore(), workspace, memory)
    scans = ScanCounter(manager)

    manager.notify_message(20)
    await manager.sync(reason="search:tool")
    assert scans.count == 1

    await manager.sync(reason="search:tool", force=True)
    assert scans.count == 2


@pytest.mark.asyncio
async def test_enabled_session_indexing_still_runs_on_a_search(tmp_path) -> None:
    workspace, memory = _make_workspace(tmp_path)
    indexer = StubSessionIndexer()
    manager = _manager(RecordingStore(), workspace, memory, session_indexer=indexer)

    manager.notify_message(20)
    await manager.sync(reason="search:tool")

    assert indexer.calls == 1
    assert manager._delta.has_pending() is False


@pytest.mark.asyncio
async def test_failed_search_session_sync_keeps_the_delta_pending_until_it_succeeds(
    tmp_path,
) -> None:
    workspace, memory = _make_workspace(tmp_path)
    indexer = StubSessionIndexer()
    indexer.fail = True
    manager = _manager(RecordingStore(), workspace, memory, session_indexer=indexer)

    manager.notify_message(20)
    await manager.sync(reason="search:tool")

    assert indexer.calls == 1
    assert manager._delta.has_pending() is True

    indexer.fail = False
    await manager.sync(reason="search:tool")

    assert indexer.calls == 2
    assert manager._delta.has_pending() is False
