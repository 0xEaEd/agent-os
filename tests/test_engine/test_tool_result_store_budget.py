"""An oversized write must not take the existing records down with it.

``_prune_to_fit`` used to delete every stored record while trying to make room
for a snapshot that could never fit, and only then raise. The caller in
``agent.py`` logs a ``skipped`` metric and moves on, so the loss was silent.
"""

from __future__ import annotations

import pytest

from agentos.engine.tool_result_store import (
    ToolResultStore,
    ToolResultStoreBudgetError,
)


def _store(tmp_path) -> ToolResultStore:
    return ToolResultStore(tmp_path / "tool-results")


def _write(store: ToolResultStore, content: str, *, handle_hint: str, budget: int | None):
    return store.write(
        content,
        tool_use_id=f"tu-{handle_hint}",
        tool_name="x",
        session_id="s1",
        session_key="k",
        agent_id="a",
        disk_budget_bytes=budget,
        retention_seconds=None,
    )


def _handles(store: ToolResultStore) -> set[str]:
    return {record.handle for record in store._iter_records()}


def test_oversized_write_leaves_prior_records_intact(tmp_path) -> None:
    store = _store(tmp_path)
    for index in range(3):
        _write(store, f"record-{index}", handle_hint=str(index), budget=500)
    before = _handles(store)
    assert len(before) == 3

    with pytest.raises(ToolResultStoreBudgetError, match="exceeds disk budget"):
        _write(store, "X" * 2000, handle_hint="big", budget=500)

    assert _handles(store) == before


def test_oversized_write_against_a_zero_budget_keeps_records(tmp_path) -> None:
    store = _store(tmp_path)
    _write(store, "keep me", handle_hint="0", budget=500)
    before = _handles(store)

    with pytest.raises(ToolResultStoreBudgetError):
        _write(store, "anything", handle_hint="1", budget=0)

    assert _handles(store) == before


def test_oversized_write_is_rejected_on_an_empty_store(tmp_path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ToolResultStoreBudgetError, match="exceeds disk budget"):
        _write(store, "X" * 2000, handle_hint="big", budget=500)
    assert _handles(store) == set()


def test_a_fitting_write_still_prunes_the_oldest_records(tmp_path) -> None:
    store = _store(tmp_path)
    first = _write(store, "A" * 100, handle_hint="0", budget=250)
    second = _write(store, "B" * 100, handle_hint="1", budget=250)

    third = _write(store, "C" * 100, handle_hint="2", budget=250)

    handles = _handles(store)
    assert third.handle in handles
    assert first.handle not in handles
    # Only as much as needed comes off: 100 + 100 fits inside 250.
    assert second.handle in handles


def test_a_write_that_fits_prunes_nothing(tmp_path) -> None:
    store = _store(tmp_path)
    first = _write(store, "A" * 100, handle_hint="0", budget=10_000)
    second = _write(store, "B" * 100, handle_hint="1", budget=10_000)
    assert _handles(store) == {first.handle, second.handle}


def test_unbounded_budget_never_prunes(tmp_path) -> None:
    store = _store(tmp_path)
    handles = {
        _write(store, "X" * 5000, handle_hint=str(index), budget=None).handle
        for index in range(3)
    }
    assert _handles(store) == handles


def test_records_survive_when_the_per_result_cap_rejects_the_write(tmp_path) -> None:
    store = _store(tmp_path)
    _write(store, "keep me", handle_hint="0", budget=500)
    before = _handles(store)

    with pytest.raises(ToolResultStoreBudgetError, match="per-result budget"):
        store.write(
            "X" * 2000,
            tool_use_id="tu-big",
            tool_name="x",
            session_id="s1",
            session_key="k",
            agent_id="a",
            max_bytes=10,
            disk_budget_bytes=500,
            retention_seconds=None,
        )

    assert _handles(store) == before


def test_read_back_after_a_rejected_oversized_write(tmp_path) -> None:
    store = _store(tmp_path)
    kept = _write(store, "still here", handle_hint="0", budget=500)

    with pytest.raises(ToolResultStoreBudgetError):
        _write(store, "X" * 2000, handle_hint="big", budget=500)

    assert store.read(kept.handle, session_id="s1").content == "still here"
