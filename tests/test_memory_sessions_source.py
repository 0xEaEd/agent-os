from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentos.memory.retrieval import MemoryRetriever
from agentos.memory.session_source import (
    SessionSourceIndexer,
    build_session_source_document,
    session_source_path,
)
from agentos.memory.store import LongTermMemoryStore
from agentos.memory.sync_manager import MemorySyncManager
from agentos.memory.types import MemorySearchOpts, MemorySearchResult, MemorySource
from agentos.session.models import SessionNode, TranscriptEntry
from agentos.session.storage import SessionStorage
from agentos.tools.builtin.memory_tools import create_memory_tools
from agentos.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_store_indexes_sessions_source_and_reports_counts(tmp_path):
    store = LongTermMemoryStore(tmp_path / "memory.db")
    await store.initialize()
    try:
        chunks = await store.index_file(
            path="sessions/main/session-1.md",
            content="User: remember the launch flag\nAssistant: noted",
            source=MemorySource.sessions,
        )

        results, mode = await store.search("launch flag", min_score=0.0)
        counts = await store.source_counts()

        assert chunks > 0
        assert mode.value == "fts-only"
        assert results[0].source is MemorySource.sessions
        assert counts["sessions"]["files"] == 1
        assert counts["sessions"]["chunks"] >= 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_retriever_keeps_sessions_source_and_filters_hidden_memory_paths():
    class FakeStore:
        async def search(self, **_kwargs):
            return (
                [
                    MemorySearchResult(
                        chunk_id="hidden",
                        path="memory/.hidden.md",
                        source=MemorySource.memory,
                        start_line=1,
                        end_line=1,
                        snippet="hidden",
                        score=0.99,
                    ),
                    MemorySearchResult(
                        chunk_id="session",
                        path="sessions/main/session-1.md",
                        source=MemorySource.sessions,
                        start_line=1,
                        end_line=2,
                        snippet="session recall",
                        score=0.90,
                    ),
                    MemorySearchResult(
                        chunk_id="curated",
                        path="memory/a.md",
                        source=MemorySource.memory,
                        start_line=1,
                        end_line=1,
                        snippet="curated recall",
                        score=0.86,
                    ),
                ],
                "fts_only",
            )

    retriever = MemoryRetriever(FakeStore())  # type: ignore[arg-type]

    results = await retriever.search("recall", MemorySearchOpts(max_results=5, min_score=0.0))

    assert [r.chunk_id for r in results] == ["curated", "session"]
    assert results[1].metadata["search_intent"] == "tool"


def test_session_source_document_renders_transcript_with_citable_path():
    session = SessionNode(
        session_key="direct:user:thread",
        session_id="session-1",
        agent_id="main",
        updated_at=1_700_000_000_000,
    )
    entries = [
        TranscriptEntry(
            id=10,
            session_id="session-1",
            session_key=session.session_key,
            role="user",
            content="Please remember the launch flag.",
            created_at=1_700_000_000_000,
        ),
        TranscriptEntry(
            id=11,
            session_id="session-1",
            session_key=session.session_key,
            role="assistant",
            content="Launch flag recorded.",
            created_at=1_700_000_001_000,
        ),
    ]

    document = build_session_source_document(session, entries)

    assert document.path == "sessions/main/session-1.md"
    assert document.mtime == 1_700_000_000.0
    assert "session_id: session-1" in document.content
    assert "[entry 10] User: Please remember the launch flag." in document.content
    assert "[entry 11] Assistant: Launch flag recorded." in document.content


@pytest.mark.asyncio
async def test_session_source_indexer_syncs_transcript_documents(tmp_path):
    storage = SessionStorage(":memory:")
    await storage.connect()
    store = LongTermMemoryStore(tmp_path / "memory.db")
    await store.initialize()
    try:
        session = SessionNode(
            session_key="direct:user:thread",
            session_id="session-1",
            agent_id="main",
            updated_at=1_700_000_000_000,
        )
        await storage.upsert_session(session)
        await storage.append_transcript_entry(
            TranscriptEntry(
                session_id=session.session_id,
                session_key=session.session_key,
                role="user",
                content="The migration keyword is cerulean.",
                created_at=1_700_000_000_000,
            )
        )

        indexer = SessionSourceIndexer(storage=storage, store=store, agent_id="main")
        result = await indexer.sync(force=True)
        results, _mode = await store.search("cerulean", min_score=0.0)

        assert result.indexed == 1
        assert results[0].path == "sessions/main/session-1.md"
        assert results[0].source is MemorySource.sessions

        await storage.delete_session(session.session_key)
        cleanup = await indexer.sync(force=True)
        stale_results, _mode = await store.search("cerulean", min_score=0.0)

        assert cleanup.removed == 1
        assert stale_results == []
    finally:
        await store.close()
        await storage.close()


@pytest.mark.asyncio
async def test_sync_manager_indexes_session_source_on_session_delta(tmp_path):
    class NoopStore:
        async def index_file(self, **_kwargs):
            return 0

        async def remove_file(self, _path):
            return None

    class FakeSessionIndexer:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        async def sync(self, *, force: bool = False):
            self.calls.append(force)
            return SimpleNamespace(indexed=1, removed=0)

    indexer = FakeSessionIndexer()
    manager = MemorySyncManager(
        store=NoopStore(),  # type: ignore[arg-type]
        workspace_dir=tmp_path,
        memory_dir=tmp_path / "memory",
        session_indexer=indexer,
    )
    manager.notify_message(150_000)

    await manager.sync(reason="session-delta")

    assert indexer.calls == [False]


@pytest.mark.asyncio
async def test_sync_manager_search_indexes_pending_session_delta(tmp_path):
    class NoopStore:
        async def index_file(self, **_kwargs):
            return 0

        async def remove_file(self, _path):
            return None

    class FakeSessionIndexer:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        async def sync(self, *, force: bool = False):
            self.calls.append(force)
            return SimpleNamespace(indexed=1, removed=0)

    indexer = FakeSessionIndexer()
    manager = MemorySyncManager(
        store=NoopStore(),  # type: ignore[arg-type]
        workspace_dir=tmp_path,
        memory_dir=tmp_path / "memory",
        session_indexer=indexer,
    )
    manager.notify_message(20)

    await manager.sync(reason="search:tool")
    await manager.sync(reason="search:tool")

    assert indexer.calls == [False]


@pytest.mark.asyncio
async def test_memory_search_tool_outputs_sessions_source(tmp_path):
    registry = ToolRegistry()

    class FakeRetriever:
        def __init__(self) -> None:
            self.opts = None

        async def search(self, _query, opts, **_kwargs):
            self.opts = opts
            return [
                MemorySearchResult(
                    chunk_id="session",
                    path="sessions/main/session-1.md",
                    source=MemorySource.sessions,
                    start_line=1,
                    end_line=2,
                    snippet="session recall",
                    score=0.8,
                    text="session recall",
                )
            ]

    retriever = FakeRetriever()
    create_memory_tools(
        stores=SimpleNamespace(),
        retrievers=retriever,  # type: ignore[arg-type]
        memory_dir=str(tmp_path),
        registry=registry,
    )

    registered = registry.get("memory_search")
    assert registered is not None
    output = await registered.handler(query="recall", source="sessions")

    assert retriever.opts.source is MemorySource.sessions
    assert "source: sessions" in output
    assert "sessions/main/session-1.md" in output


class _RecordingSessionStorage:
    """Session storage that records which transcripts were actually read."""

    def __init__(self, sessions: list[SimpleNamespace]) -> None:
        self.sessions = sessions
        self.transcript_reads: list[str] = []

    async def list_sessions(self, agent_id=None, limit=100, offset=0):  # noqa: ANN001
        return self.sessions[offset : offset + limit]

    async def get_transcript(self, session_id: str):  # noqa: ANN001
        self.transcript_reads.append(session_id)
        return [SimpleNamespace(role="user", content="hello", id=1, message_id="m1")]


class _StoreWithoutMtimes:
    """The older store shape: no ``get_file_mtimes`` accessor."""

    def __init__(self, mtimes: dict[str, float]) -> None:
        self.mtimes = mtimes
        self.removed: list[str] = []

    async def index_file(self, path, content, source=None, mtime=None):  # noqa: ANN001
        return 0

    async def list_paths(self, source=None):  # noqa: ANN001
        return list(self.mtimes)

    async def remove_file(self, path: str) -> None:
        self.removed.append(path)


class _Store(_StoreWithoutMtimes):
    async def get_file_mtimes(self, paths: list[str]) -> dict[str, float]:
        return {p: self.mtimes[p] for p in paths if p in self.mtimes}


def _session(index: int, updated_at: int) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=f"s{index}",
        agent_id="main",
        updated_at=updated_at,
        label=None,
        display_name=None,
    )


def _indexed_at(sessions: list[SimpleNamespace], mtime: float) -> dict[str, float]:
    return {session_source_path(session): mtime for session in sessions}


async def test_sync_skips_reading_transcripts_that_are_already_indexed() -> None:
    """An unchanged session must not cost a transcript read.

    ``index_file`` decides "unchanged" by hashing the rendered document, so
    reaching that verdict used to require the full read, a redaction pass over
    every entry and the whole render -- per session, for every session, on
    every sync.
    """
    sessions = [_session(i, 1_000_000) for i in range(3)]
    storage = _RecordingSessionStorage(list(sessions))
    store = _Store(_indexed_at(sessions, 1000.0))

    result = await SessionSourceIndexer(storage=storage, store=store).sync()

    assert storage.transcript_reads == []
    assert result.indexed == 0


async def test_sync_still_reads_the_session_that_changed() -> None:
    """The skip is per session: a newer ``updated_at`` is always re-read."""
    sessions = [_session(0, 1_000_000), _session(1, 5_000_000), _session(2, 1_000_000)]
    storage = _RecordingSessionStorage(sessions)
    store = _Store(_indexed_at([_session(i, 0) for i in range(3)], 1000.0))

    await SessionSourceIndexer(storage=storage, store=store).sync()

    assert storage.transcript_reads == ["s1"]


async def test_skipped_sessions_are_not_swept_as_stale_paths() -> None:
    """A skipped document is up to date, not orphaned.

    ``sync`` deletes every indexed path it did not see this run, so a skipped
    session has to stay in ``expected_paths`` or the fast path would delete the
    documents it just decided were fine.
    """
    sessions = [_session(i, 1_000_000) for i in range(3)]
    storage = _RecordingSessionStorage(list(sessions))
    store = _Store(_indexed_at(sessions, 1000.0))

    result = await SessionSourceIndexer(storage=storage, store=store).sync()

    assert store.removed == []
    assert result.removed == 0


async def test_force_still_reads_every_transcript() -> None:
    """``force=True`` counts an unchanged session as indexed, so it must not skip."""
    sessions = [_session(i, 1_000_000) for i in range(3)]
    storage = _RecordingSessionStorage(list(sessions))
    store = _Store(_indexed_at(sessions, 1000.0))

    await SessionSourceIndexer(storage=storage, store=store).sync(force=True)

    assert len(storage.transcript_reads) == 3


async def test_a_store_without_the_mtime_accessor_still_syncs() -> None:
    """The store is duck-typed; an older one must lose the speed-up, not work."""
    sessions = [_session(i, 1_000_000) for i in range(3)]
    storage = _RecordingSessionStorage(list(sessions))
    store = _StoreWithoutMtimes(_indexed_at(sessions, 1000.0))

    await SessionSourceIndexer(storage=storage, store=store).sync()

    assert len(storage.transcript_reads) == 3


async def test_a_session_without_updated_at_is_never_skipped() -> None:
    """Without a timestamp there is nothing to compare, so read it."""
    sessions = [_session(0, 0)]
    storage = _RecordingSessionStorage(sessions)
    store = _Store(_indexed_at(sessions, 1000.0))

    await SessionSourceIndexer(storage=storage, store=store).sync()

    assert storage.transcript_reads == ["s0"]


async def test_a_session_never_indexed_before_is_read() -> None:
    """No stored mtime means no baseline to skip against."""
    sessions = [_session(i, 1_000_000) for i in range(3)]
    storage = _RecordingSessionStorage(list(sessions))
    store = _Store({})

    await SessionSourceIndexer(storage=storage, store=store).sync()

    assert len(storage.transcript_reads) == 3


async def test_an_older_indexed_copy_is_refreshed() -> None:
    """A stored mtime behind the session's own must not count as up to date."""
    sessions = [_session(i, 1_000_000) for i in range(3)]
    storage = _RecordingSessionStorage(list(sessions))
    store = _Store(_indexed_at(sessions, 1.0))

    await SessionSourceIndexer(storage=storage, store=store).sync()

    assert len(storage.transcript_reads) == 3
