"""``UsageTracker._query_in_memory`` -- the no-database query path.

``tool_name``, ``start_date`` and ``end_date`` were accepted and then ignored, so a
filtered query returned every record instead of the matching ones (#3034). A gateway
builds its tracker without ``db_path``, so this is the path ``usage.cost`` takes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentos.engine.usage import UsageTracker


def _day(offset_days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


@pytest.fixture
def tracker() -> UsageTracker:
    return UsageTracker(db_path="")


def _recorded(tracker: UsageTracker) -> None:
    """A session recorded the ordinary way, so it carries per-model detail."""
    tracker.add("session-rec", 100, 50, "gpt-4o", provider_id="openai")


def test_per_model_sessions_still_produce_one_row_per_model(tracker: UsageTracker) -> None:
    tracker.add("session-abc", 100, 50, "gpt-4o", provider_id="openai")
    tracker.add("session-abc", 10, 5, "claude-opus-5", provider_id="anthropic")

    rows = tracker.query_usage(session_key="session-abc")

    assert sorted(r["model"] for r in rows) == ["claude-opus-5", "gpt-4o"]


def test_a_tool_name_filter_matches_nothing_rather_than_everything(tracker: UsageTracker) -> None:
    """In-memory rows are per-model turn totals; none carries a tool name."""
    _recorded(tracker)

    assert tracker.query_usage() != []
    assert tracker.query_usage(tool_name="bash") == []


def test_a_start_date_in_the_future_excludes_current_records(tracker: UsageTracker) -> None:
    _recorded(tracker)

    assert tracker.query_usage(start_date=_day(1)) == []


def test_an_end_date_in_the_past_excludes_current_records(tracker: UsageTracker) -> None:
    _recorded(tracker)

    assert tracker.query_usage(end_date=_day(-1)) == []


def test_a_date_range_covering_today_keeps_the_records(tracker: UsageTracker) -> None:
    _recorded(tracker)

    rows = tracker.query_usage(start_date=_day(-1), end_date=_day(1))

    assert len(rows) == 1


def test_an_unparsable_date_is_ignored_as_in_the_sql_path(tracker: UsageTracker) -> None:
    _recorded(tracker)

    assert len(tracker.query_usage(start_date="not-a-date")) == 1


def test_other_filters_still_apply(tracker: UsageTracker) -> None:
    _recorded(tracker)

    assert tracker.query_usage(session_key="other") == []
    assert len(tracker.query_usage(session_key="session-rec")) == 1
