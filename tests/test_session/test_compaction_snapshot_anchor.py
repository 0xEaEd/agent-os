"""Inline compaction must not delete messages the compactor never saw.

``persist_compaction_result`` used to derive the cut point purely from
counts -- ``len(entries) - len(kept_entries)`` against the *live* transcript.
When ``sessions.send`` appended a follow-up between the agent loading its
history and the ``CompactionEvent`` being persisted, that follow-up fell
into the overwritten tail and vanished from both the live and the canonical
transcript. The persist call now takes the ``TranscriptSnapshot`` the runner
captured at history load, rewrites only rows inside it, re-appends everything
newer verbatim, and rejects a snapshot the transcript has moved away from.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from agentos.session.manager import SessionManager, TranscriptSnapshot
from agentos.session.models import SessionIntent
from agentos.session.storage import SessionStorage

KEY = "agent:main:main"


@pytest_asyncio.fixture
async def manager():
    storage = SessionStorage(":memory:")
    await storage.connect()
    mgr = SessionManager(storage, inject_time_prefix=False)
    yield mgr
    await storage.close()


async def _seed(manager: SessionManager) -> TranscriptSnapshot:
    """A, B, C as the running agent loaded them; returns the agent's snapshot."""
    await manager.create(KEY)
    await manager.append_message(KEY, "user", "A", token_count=1)
    await manager.append_message(KEY, "assistant", "B", token_count=1)
    await manager.append_message(KEY, "user", "C", token_count=1)
    entries = await manager.get_transcript(KEY)
    node = await manager.get_session(KEY)
    assert node is not None
    return TranscriptSnapshot(session_id=node.session_id, through_message_id=entries[-1].message_id)


def _kept(*contents: str) -> list[dict]:
    roles = {"A": "user", "B": "assistant", "C": "user", "D": "user", "E": "user"}
    return [{"role": roles[c], "content": c} for c in contents]


@pytest.mark.asyncio
async def test_follow_up_appended_after_the_snapshot_survives(manager: SessionManager) -> None:
    """The regression from the report: A, B, C snapshotted, kept=[B, C], D queued."""
    snapshot = await _seed(manager)
    queued = await manager.append_message(
        KEY,
        "user",
        "D",
        token_count=7,
        provenance={"kind": "queued_follow_up", "source_channel": "cli"},
    )
    queued_row = (await manager.get_transcript(KEY))[-1]
    assert queued_row.message_id == queued.message_id

    persisted = await manager.persist_compaction_result(
        KEY, "summary of A", _kept("B", "C"), snapshot=snapshot, compaction_id="cmp_1"
    )

    live = await manager.get_transcript(KEY)
    assert [entry.content for entry in live] == ["B", "C", "D"]
    canonical = await manager.get_canonical_transcript(KEY)
    assert [entry.content for entry in canonical] == ["A", "B", "C", "D"]

    survivor = live[-1]
    assert survivor.message_id == queued.message_id
    assert survivor.token_count == 7
    assert survivor.provenance_kind == "queued_follow_up"
    assert survivor.provenance_source_channel == "cli"

    (summary,) = await manager.get_summaries(KEY)
    assert summary.removed_count == 1
    assert summary.kept_count == 2
    # Coverage stops at the last row the compactor actually saw (A here).
    assert summary.covered_through_id == canonical[0].id
    assert summary.covered_through_id < queued_row.id

    # The returned anchor points at the last kept row, not the follow-up.
    assert persisted is not None
    assert persisted.session_id == snapshot.session_id
    assert persisted.through_message_id == live[1].message_id


@pytest.mark.asyncio
async def test_repeated_inline_compactions_keep_queued_follow_ups(manager: SessionManager) -> None:
    snapshot = await _seed(manager)
    await manager.append_message(KEY, "user", "D", token_count=1)
    snapshot = await manager.persist_compaction_result(
        KEY, "summary of A", _kept("B", "C"), snapshot=snapshot
    )
    assert snapshot is not None
    await manager.append_message(KEY, "user", "E", token_count=1)

    # Second compaction in the same turn: the agent still only knows B, C.
    second = await manager.persist_compaction_result(
        KEY, "summary of A and B", _kept("C"), snapshot=snapshot
    )

    live = await manager.get_transcript(KEY)
    assert [entry.content for entry in live] == ["C", "D", "E"]
    canonical = await manager.get_canonical_transcript(KEY)
    assert [entry.content for entry in canonical] == ["A", "B", "C", "D", "E"]
    summaries = await manager.get_summaries(KEY)
    assert [s.removed_count for s in summaries] == [1, 1]
    assert second is not None
    assert second.through_message_id == live[0].message_id


@pytest.mark.asyncio
async def test_full_compaction_leaves_an_empty_snapshot_that_still_protects_follow_ups(
    manager: SessionManager,
) -> None:
    snapshot = await _seed(manager)
    await manager.append_message(KEY, "user", "D", token_count=1)

    emptied = await manager.persist_compaction_result(
        KEY, "summary of everything", [], snapshot=snapshot
    )
    assert emptied is not None
    assert emptied.through_message_id is None
    assert [entry.content for entry in await manager.get_transcript(KEY)] == ["D"]

    # Later in the same turn the agent compacts again; the rows it holds are
    # all in-memory, so nothing in the transcript belongs to its snapshot.
    await manager.append_message(KEY, "user", "E", token_count=1)
    await manager.persist_compaction_result(
        KEY,
        "summary again",
        [{"role": "assistant", "content": "in-memory reply"}],
        snapshot=emptied,
    )

    assert [entry.content for entry in await manager.get_transcript(KEY)] == [
        "in-memory reply",
        "D",
        "E",
    ]
    canonical = await manager.get_canonical_transcript(KEY)
    assert [entry.content for entry in canonical] == ["A", "B", "C", "in-memory reply", "D", "E"]


@pytest.mark.asyncio
async def test_stale_result_after_same_key_reset_is_not_persisted(manager: SessionManager) -> None:
    snapshot = await _seed(manager)
    await manager.apply_intent(KEY, SessionIntent.RESET_SAME_KEY)
    await manager.append_message(KEY, "user", "fresh start", token_count=1)
    before = await manager.get_transcript(KEY)

    result = await manager.persist_compaction_result(
        KEY, "summary of A", _kept("B", "C"), snapshot=snapshot
    )

    assert result is None
    assert await manager.get_transcript(KEY) == before
    assert await manager.get_summaries(KEY) == []


@pytest.mark.asyncio
async def test_stale_result_after_the_anchor_row_disappeared_is_not_persisted(
    manager: SessionManager,
) -> None:
    snapshot = await _seed(manager)
    await manager.truncate(KEY, max_messages=0)
    await manager.append_message(KEY, "user", "D", token_count=1)
    before = await manager.get_transcript(KEY)

    result = await manager.persist_compaction_result(
        KEY, "summary of A", _kept("B", "C"), snapshot=snapshot
    )

    assert result is None
    assert await manager.get_transcript(KEY) == before
    assert await manager.get_summaries(KEY) == []


@pytest.mark.asyncio
async def test_empty_summary_with_removed_rows_keeps_the_snapshot_valid(
    manager: SessionManager,
) -> None:
    snapshot = await _seed(manager)

    result = await manager.persist_compaction_result(KEY, "", _kept("B", "C"), snapshot=snapshot)

    # Nothing was persisted, so the caller keeps anchoring on the old snapshot.
    assert result is None
    assert [entry.content for entry in await manager.get_transcript(KEY)] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_without_a_snapshot_the_legacy_tail_arithmetic_is_unchanged(
    manager: SessionManager,
) -> None:
    await _seed(manager)

    result = await manager.persist_compaction_result(KEY, "summary of A", _kept("B", "C"))

    assert [entry.content for entry in await manager.get_transcript(KEY)] == ["B", "C"]
    assert [entry.content for entry in await manager.get_canonical_transcript(KEY)] == [
        "A",
        "B",
        "C",
    ]
    assert result is not None
    assert result.through_message_id == (await manager.get_transcript(KEY))[-1].message_id
