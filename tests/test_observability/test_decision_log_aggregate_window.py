from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentos.observability.decision_log_aggregate import (
    aggregate_co_occurrences,
    within_window,
)


def test_within_window_offset_aware() -> None:
    cutoff = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    assert within_window("2026-09-13T10:00:00Z", cutoff) is True
    assert within_window("2026-09-13T11:00:00+00:00", cutoff) is True
    assert within_window("2026-09-13T09:00:00Z", cutoff) is False


def test_within_window_offset_naive_does_not_raise() -> None:
    cutoff = datetime(2026, 9, 13, 10, 0, 0, tzinfo=UTC)
    # Naive timestamp should not raise TypeError: can't compare offset-naive and offset-aware
    assert within_window("2026-09-13T11:00:00", cutoff) is True
    assert within_window("2026-09-13T09:00:00", cutoff) is False


def test_within_window_aware_ts_naive_cutoff() -> None:
    cutoff = datetime(2026, 9, 13, 10, 0, 0)
    assert within_window("2026-09-13T11:00:00Z", cutoff) is True
    assert within_window("2026-09-13T09:00:00Z", cutoff) is False


def test_within_window_invalid_inputs() -> None:
    cutoff = datetime.now(UTC)
    assert within_window("invalid-date", cutoff) is False
    assert within_window("", cutoff) is False
    assert within_window(None, cutoff) is False  # type: ignore[arg-type]
    assert within_window(123456, cutoff) is False  # type: ignore[arg-type]


def test_aggregate_co_occurrences_with_naive_timestamps(tmp_path: Path) -> None:
    log_file = tmp_path / "decisions-001.jsonl"
    recent_naive = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    old_naive = (datetime.now(UTC) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%S")

    lines = [
        (
            f'{{"ts": "{recent_naive}", "skills_invoked": ["skillA", "skillB"], '
            '"intent_summary": "test1"}'
        ),
        (
            f'{{"ts": "{recent_naive}", "skills_invoked": ["skillA", "skillB"], '
            '"intent_summary": "test2"}'
        ),
        f'{{"ts": "{old_naive}", "skills_invoked": ["skillA", "skillB"]}}',
        '{"ts": null, "skills_invoked": ["skillA", "skillB"]}',
        '{"ts": "malformed", "skills_invoked": ["skillA", "skillB"]}',
    ]
    log_file.write_text("\n".join(lines), encoding="utf-8")
    results = aggregate_co_occurrences(tmp_path, window_days=30, top_k=5)
    assert len(results) == 1
    assert results[0]["skills"] == ["skillA", "skillB"]
    assert results[0]["freq"] == 2
