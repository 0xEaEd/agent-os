"""Refire-gap guard — MIN_REFIRE_GAP_SECONDS must not throttle a job below
its own configured EVERY interval.

The guard exists to debounce a nudge landing right after an ordinary tick
already ran the job (see ``SchedulerTimer._tick``'s comment), not to cap a
job's own schedule. An EVERY job with ``every_seconds`` under the flat
2.0s floor -- only ``every_seconds=1`` is legal, since ``ops.py`` rejects
anything below 1 -- was silently throttled to the floor's ~2s cadence on
every ordinary tick, not just a nudge race (#3345).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from agentos.scheduler.ops import SchedulerOps
from agentos.scheduler.persistence import JobStore
from agentos.scheduler.timer import MIN_REFIRE_GAP_SECONDS, SchedulerTimer, _refire_gap_seconds
from agentos.scheduler.types import CronJob, ScheduleKind, SessionTarget


def _job(schedule_kind: ScheduleKind, cron_expr: str) -> CronJob:
    return CronJob(
        id="job-1",
        cron_expr=cron_expr,
        handler_key="agent_run",
        payload={"kind": "agent_turn", "task": "x", "agent_id": "main"},
        session_target=SessionTarget.ISOLATED,
        schedule_kind=schedule_kind,
    )


# --- pure _refire_gap_seconds -------------------------------------------


def test_every_interval_under_the_floor_uses_the_interval() -> None:
    assert _refire_gap_seconds(_job(ScheduleKind.EVERY, "1")) == 1.0


def test_every_interval_at_or_above_the_floor_uses_the_floor() -> None:
    assert _refire_gap_seconds(_job(ScheduleKind.EVERY, "2")) == MIN_REFIRE_GAP_SECONDS
    assert _refire_gap_seconds(_job(ScheduleKind.EVERY, "60")) == MIN_REFIRE_GAP_SECONDS


def test_cron_and_at_jobs_keep_the_flat_floor() -> None:
    assert _refire_gap_seconds(_job(ScheduleKind.CRON, "* * * * *")) == MIN_REFIRE_GAP_SECONDS
    at_job = _job(ScheduleKind.AT, datetime.now(UTC).isoformat())
    assert _refire_gap_seconds(at_job) == MIN_REFIRE_GAP_SECONDS


def test_every_with_a_non_numeric_expression_keeps_the_flat_floor() -> None:
    # cron_expr.isdigit() guards against a malformed/legacy EVERY row.
    assert _refire_gap_seconds(_job(ScheduleKind.EVERY, "not-a-number")) == MIN_REFIRE_GAP_SECONDS


# --- live tick loop -------------------------------------------------------


@pytest.mark.asyncio
async def test_every_one_second_job_fires_at_roughly_its_own_interval() -> None:
    """Regression for #3345: under the old flat floor this fired at ~2.0s
    gaps. Asserting the second gap is well under the old floor distinguishes
    a real fix from noise -- the buggy behavior could not produce this."""
    fire_times: list[float] = []

    async def handler(job: CronJob) -> str:
        fire_times.append(time.monotonic())
        return "ok"

    async with JobStore(":memory:") as store:
        ops = SchedulerOps(store, max_jitter=0.0)
        await ops.add(
            name="every-1s",
            schedule_kind=ScheduleKind.EVERY,
            schedule_value="1",
            handler_key="agent_run",
            payload={"kind": "agent_turn", "task": "noop", "agent_id": "main"},
            session_target=SessionTarget.ISOLATED,
            timeout_seconds=5,
        )
        timer = SchedulerTimer(store=store, handlers={"agent_run": handler}, max_concurrent=3)

        start = time.monotonic()
        while time.monotonic() - start < 4.0 and len(fire_times) < 3:
            await timer._tick()
            await asyncio.sleep(0.05)
        await asyncio.gather(*list(timer._running.values()), return_exceptions=True)

    assert len(fire_times) >= 3, f"expected >=3 fires in 4s, got {len(fire_times)}"
    gaps = [b - a for a, b in zip(fire_times, fire_times[1:])]
    assert all(gap < MIN_REFIRE_GAP_SECONDS - 0.5 for gap in gaps), gaps


@pytest.mark.asyncio
async def test_a_longer_interval_job_is_still_debounced_against_a_double_tick() -> None:
    """The original guard behavior must survive: a job whose last_run_at is
    seconds-fresh relative to a *longer* interval is skipped, not re-run."""
    async with JobStore(":memory:") as store:
        ops = SchedulerOps(store, max_jitter=0.0)
        job = await ops.add(
            name="every-30s",
            schedule_kind=ScheduleKind.EVERY,
            schedule_value="30",
            handler_key="agent_run",
            payload={"kind": "agent_turn", "task": "noop", "agent_id": "main"},
            session_target=SessionTarget.ISOLATED,
            timeout_seconds=5,
        )
        now = datetime.now(UTC)
        job.last_run_at = now - timedelta(seconds=0.5)
        job.next_run_at = now
        await store.save(job, write_reservation=False)

        fired = False

        async def handler(_job: CronJob) -> str:
            nonlocal fired
            fired = True
            return "ok"

        timer = SchedulerTimer(store=store, handlers={"agent_run": handler}, max_concurrent=3)
        await timer._tick()
        await asyncio.gather(*list(timer._running.values()), return_exceptions=True)

    assert fired is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("last_run_ago", "expect_fired"),
    [
        (0.5, False),  # inside the job's own 1s interval -> still debounced
        (1.1, True),  # past the 1s interval -> fires (was skipped under the flat 2s floor)
    ],
)
async def test_every_one_second_job_in_a_nudge_race(
    last_run_ago: float, expect_fired: bool
) -> None:
    """The per-job cap must still debounce a nudge landing inside an
    every_seconds=1 job's own interval, while no longer skipping it once
    that interval has elapsed (#3345)."""
    async with JobStore(":memory:") as store:
        ops = SchedulerOps(store, max_jitter=0.0)
        job = await ops.add(
            name="every-1s",
            schedule_kind=ScheduleKind.EVERY,
            schedule_value="1",
            handler_key="agent_run",
            payload={"kind": "agent_turn", "task": "noop", "agent_id": "main"},
            session_target=SessionTarget.ISOLATED,
            timeout_seconds=5,
        )
        now = datetime.now(UTC)
        job.last_run_at = now - timedelta(seconds=last_run_ago)
        job.next_run_at = now
        await store.save(job, write_reservation=False)

        fired = False

        async def handler(_job: CronJob) -> str:
            nonlocal fired
            fired = True
            return "ok"

        timer = SchedulerTimer(store=store, handlers={"agent_run": handler}, max_concurrent=3)
        timer.nudge()
        await timer._tick()
        await asyncio.gather(*list(timer._running.values()), return_exceptions=True)

    assert fired is expect_fired
