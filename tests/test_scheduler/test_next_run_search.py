"""Issue #3099: the next-run search stepped one minute at a time.

``_next_run`` found a cron job's next fire by trying every minute for up to
four years, on the gateway's event loop: about a second for a yearly schedule
(1.4 s with a timezone), four for a leap-day one, and four before giving up
on an impossible date -- at every add, after every run, and for every job at
boot. The search now jumps a field at a time. The old scan is kept here as
the oracle: over a grid of schedules, start instants and zones the two must
agree instant for instant, DST edges included.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agentos.scheduler.jobs import _next_cron_instant, _next_run
from agentos.scheduler.parser import parse_cron
from agentos.scheduler.types import CronJob, ScheduleKind


def _scan(expr: str, after: datetime, tz: ZoneInfo | None, limit_minutes: int) -> datetime | None:
    """The previous implementation, minute by minute, over a bounded window.

    An oracle for ordinary schedules only. #2472 has since given the scan two
    daylight-saving rules of its own -- fire at the end of a spring-forward
    gap, fire once per scheduled local time unless the hour is a wildcard --
    which this plain scan does not have, so the cases that cross a transition
    are pinned against those rules by hand instead.
    """
    parsed = parse_cron(expr)
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(limit_minutes):
        wall = candidate.astimezone(tz) if tz is not None else candidate
        if parsed.matches(wall):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def _job(expr: str, tz: str = "", jitter: float = 0.0) -> CronJob:
    return CronJob(
        id="j",
        name="j",
        schedule_kind=ScheduleKind.CRON,
        cron_expr=expr,
        tz=tz,
        jitter_seconds=jitter,
    )


# ── equivalence with the scan ──────────────────────────────────────────────

_EXPRESSIONS = [
    "* * * * *",
    "*/5 * * * *",
    "0 * * * *",
    "30 9 * * *",
    "0 9 * * 1-5",
    "15,45 8-17 * * MON-FRI",
    "0 0 1 * *",
    "0 0 1,15 * 5",  # POSIX either/or: the 1st, the 15th, or any Friday
    "0 0 * * 0",  # Sunday as 0
    "0 0 * * 7",  # Sunday as 7
    "0 12 29 2 *",
    "@hourly",
    "@daily",
    "@weekly",
    "0 22 31 * *",  # the 31st: only some months have one
]
_STARTS = [
    datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    datetime(2026, 2, 28, 23, 59, 30, tzinfo=UTC),
    datetime(2026, 3, 7, 12, 34, 56, tzinfo=UTC),
    datetime(2026, 12, 31, 23, 58, tzinfo=UTC),
    datetime(2028, 2, 28, 12, 0, tzinfo=UTC),  # a leap year
]
_ZONES = [
    None,
    ZoneInfo("America/New_York"),
    ZoneInfo("Asia/Kolkata"),
    ZoneInfo("Pacific/Auckland"),
]
_WINDOW_MINUTES = 60 * 24 * 45  # the scan is slow; 45 days covers every expression above but two


@pytest.mark.parametrize("zone", _ZONES, ids=lambda z: getattr(z, "key", "UTC"))
@pytest.mark.parametrize("after", _STARTS, ids=lambda d: d.strftime("%Y-%m-%dT%H:%M:%S"))
@pytest.mark.parametrize("expr", _EXPRESSIONS)
def test_field_jump_agrees_with_the_minute_scan(
    expr: str, after: datetime, zone: ZoneInfo | None
) -> None:
    expected = _scan(expr, after, zone, _WINDOW_MINUTES)
    if expected is None:
        pytest.skip("next fire is beyond the scan window this test can afford")

    assert _next_cron_instant(parse_cron(expr), after, zone) == expected


@pytest.mark.parametrize(
    ("expr", "after", "expected"),
    [
        ("0 0 29 2 *", datetime(2028, 3, 1, tzinfo=UTC), datetime(2032, 2, 29, tzinfo=UTC)),
        ("0 0 1 1 *", datetime(2026, 1, 2, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        ("0 0 31 * *", datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 3, 31, tzinfo=UTC)),
    ],
)
def test_sparse_schedules_land_where_the_scan_did(
    expr: str, after: datetime, expected: datetime
) -> None:
    """Too far ahead for the scan to be an affordable oracle; pinned by hand."""
    assert _next_cron_instant(parse_cron(expr), after, None) == expected


# ── daylight saving ────────────────────────────────────────────────────────


def test_a_wall_time_inside_the_spring_forward_gap_fires_when_the_gap_ends() -> None:
    """02:30 does not exist on 2026-03-08 in New York. #2472 fires the job
    once at the first instant the clock does show rather than skipping the
    day, and this search has to land in the same place."""
    zone = ZoneInfo("America/New_York")
    after = datetime(2026, 3, 8, 6, 0, tzinfo=UTC)  # 01:00 EST that morning

    got = _next_cron_instant(parse_cron("30 2 * * *"), after, zone)

    assert got == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)  # 03:00 EDT, where the gap ends
    assert got == _next_run(_job("30 2 * * *", "America/New_York"), after)


def test_the_repeated_hour_on_a_fall_back_night_fires_on_its_first_pass() -> None:
    zone = ZoneInfo("America/New_York")
    after = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # 00:00 EDT

    got = _next_cron_instant(parse_cron("30 1 * * *"), after, zone)

    assert got == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT, the first 01:30
    assert got == _scan("30 1 * * *", after, zone, 60 * 3)


def _chain(expr: str, start: datetime, zone: ZoneInfo, count: int) -> list[datetime]:
    """``count`` consecutive fires, the way the timer drives the scheduler."""
    parsed = parse_cron(expr)
    out: list[datetime] = []
    cursor = start
    for _ in range(count):
        nxt = _next_cron_instant(parsed, cursor, zone)
        assert nxt is not None
        out.append(nxt)
        cursor = nxt
    return out


def _scan_chain(
    expr: str, start: datetime, zone: ZoneInfo, count: int, window_minutes: int = 60 * 6
) -> list[datetime]:
    out: list[datetime] = []
    cursor = start
    for _ in range(count):
        nxt = _scan(expr, cursor, zone, window_minutes)
        assert nxt is not None
        out.append(nxt)
        cursor = nxt
    return out


@pytest.mark.parametrize("expr", ["* * * * *", "*/15 * * * *", "*/30 * * * *", "0 * * * *"])
def test_a_sub_daily_schedule_runs_through_the_repeated_hour(expr: str) -> None:
    """#3099's scope: behaviour for a schedule that fires today is unchanged.
    The walk moves forward on the wall clock, so the second pass of 01:xx --
    which is *behind* the first on that clock -- was skipped, leaving a
    75-minute hole in a `*/15` watcher (review on #3105)."""
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 5, 40, tzinfo=UTC)  # 01:40 EDT, in the first pass

    assert _chain(expr, start, zone, 6) == _scan_chain(expr, start, zone, 6)


def test_the_repeated_hour_is_visited_once_per_pass() -> None:
    """Named instants, so a future change cannot quietly drop or double one."""
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 5, 40, tzinfo=UTC)

    fires = _chain("*/15 * * * *", start, zone, 6)

    assert [f.astimezone(zone).strftime("%H:%M %Z") for f in fires] == [
        "01:45 EDT",
        "01:00 EST",
        "01:15 EST",
        "01:30 EST",
        "01:45 EST",
        "02:00 EST",
    ]
    assert fires == sorted(fires), "instants must advance even when the clock does not"


@pytest.mark.parametrize(
    ("expr", "expected"),
    [("* * * * *", "01:31 EST"), ("*/15 * * * *", "01:45 EST"), ("0 * * * *", "02:00 EST")],
)
def test_an_after_inside_the_second_pass_continues_from_there(expr: str, expected: str) -> None:
    """``after`` at 01:30 EST is itself an ambiguous wall time; the fire after
    it is the next one in that same pass, not the top of the next hour."""
    zone = ZoneInfo("America/New_York")
    after = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1).astimezone(UTC)

    got = _next_cron_instant(parse_cron(expr), after, zone)

    assert got is not None
    assert got.astimezone(zone).strftime("%H:%M %Z") == expected
    assert got == _scan(expr, after, zone, 60 * 6)


def test_a_schedule_naming_an_hour_still_fires_once_on_the_fall_back_night() -> None:
    """The other half of #2472's rule: the repeated-hour search must not hand
    a fixed-hour schedule its second pass. 01:30 happens twice; the job runs
    at the first one only."""
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)  # 00:00 EDT

    fires = _chain("30 1 * * *", start, zone, 2)

    assert [f.astimezone(zone).strftime("%m-%d %H:%M %Z") for f in fires] == [
        "11-01 01:30 EDT",
        "11-02 01:30 EST",
    ]
    assert fires[0] == _next_run(_job("30 1 * * *", "America/New_York"), start)


def test_the_southern_hemisphere_fall_back_behaves_the_same() -> None:
    zone = ZoneInfo("Australia/Sydney")
    start = datetime(2026, 4, 4, 15, 40, tzinfo=UTC)  # 02:40 AEDT, in the first pass

    assert _chain("*/15 * * * *", start, zone, 6) == _scan_chain("*/15 * * * *", start, zone, 6)


def test_a_zone_without_a_transition_is_untouched_by_the_extra_scan() -> None:
    """The repeated-hour check is skipped wherever the wall time is unique."""
    zone = ZoneInfo("Asia/Kolkata")
    start = datetime(2026, 11, 1, 5, 40, tzinfo=UTC)

    assert _chain("*/15 * * * *", start, zone, 6) == _scan_chain("*/15 * * * *", start, zone, 6)


def test_the_local_date_not_the_utc_date_decides_the_day_fields() -> None:
    """23:30 in Auckland on a Monday is Sunday 10:30 UTC; the day-of-week rule
    must see Monday."""
    zone = ZoneInfo("Pacific/Auckland")
    after = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)

    got = _next_cron_instant(parse_cron("30 23 * * 1"), after, zone)

    assert got.astimezone(zone).strftime("%a %H:%M") == "Mon 23:30"
    assert got == _scan("30 23 * * 1", after, zone, 60 * 24 * 8)


# ── the contract of _next_run around the search ────────────────────────────


def test_jitter_is_added_to_the_instant() -> None:
    after = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    assert _next_run(_job("0 9 * * *", jitter=42.0), after) == datetime(
        2026, 1, 1, 9, 0, 42, tzinfo=UTC
    )


def test_after_in_a_non_utc_zone_is_handled() -> None:
    after = datetime(2026, 1, 1, 8, 59, tzinfo=ZoneInfo("Europe/Berlin"))  # 07:59 UTC

    assert _next_run(_job("0 9 * * *"), after) == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def test_a_fire_exactly_at_after_is_not_returned() -> None:
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

    assert _next_run(_job("0 9 * * *"), after) == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def test_seconds_after_the_minute_do_not_skip_the_next_minute() -> None:
    after = datetime(2026, 1, 1, 8, 59, 30, tzinfo=UTC)

    assert _next_run(_job("0 9 * * *"), after) == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


def test_an_impossible_date_is_refused_with_the_same_message() -> None:
    with pytest.raises(ValueError, match=r"No valid next run found for expression '0 0 31 4 \*'"):
        _next_run(_job("0 0 31 4 *"), datetime(2026, 1, 1, tzinfo=UTC))


def test_a_schedule_beyond_the_horizon_is_refused() -> None:
    """The search gives up where the scan did: four years out."""
    assert (
        _next_cron_instant(parse_cron("0 0 30 2 *"), datetime(2026, 1, 1, tzinfo=UTC), None) is None
    )


# ── the cost ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expr", "tz"),
    [
        ("0 0 1 1 *", ""),
        ("0 0 1 1 *", "America/New_York"),
        ("0 0 29 2 *", ""),
        ("0 0 29 2 *", "Asia/Shanghai"),
        ("0 0 31 4 *", ""),
    ],
)
def test_sparse_and_impossible_schedules_are_cheap(expr: str, tz: str) -> None:
    """Yearly took ~1 s, leap-day ~4 s, impossible ~4 s on the scan; each must
    now be well under the tick granularity of anything that calls it."""
    after = datetime(2028, 3, 1, tzinfo=UTC)
    started = time.perf_counter()
    try:
        _next_run(_job(expr, tz), after)
    except ValueError:
        pass
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1, f"{expr} took {elapsed:.2f}s"
