"""``cron.update`` treats ``schedule.timezone`` as the alias of ``schedule.tz``.

Companion to the #1603 normalizer fix: once the alias resolves inside
``schedule``, an explicit ``"timezone": ""`` must clear the job's timezone the
way ``"tz": ""`` already does, instead of being read as "not supplied".
"""

from __future__ import annotations

import pytest

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import _handle_cron_update
from agentos.scheduler.types import CronJob, ScheduleKind


class _FakeScheduler:
    def __init__(self, job: CronJob) -> None:
        self.job = job
        self.updated: dict | None = None

    async def update_job(self, job_id: str, **patch) -> CronJob:
        self.updated = patch
        for key, value in patch.items():
            if key == "schedule_value":
                self.job.cron_expr = value
                self.job.schedule_raw = value
            elif key == "schedule_kind":
                self.job.schedule_kind = value
            elif key == "schedule_tz":
                # ``ops.update`` folds the structured tz into ``job.tz``.
                self.job.tz = value
            else:
                setattr(self.job, key, value)
        return self.job

    async def get_job(self, job_id: str) -> CronJob | None:
        return self.job


def _shanghai_job() -> CronJob:
    return CronJob(
        id="job-A",
        name="orig",
        cron_expr="0 9 * * *",
        schedule_raw="0 9 * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="agent_run",
        tz="Asia/Shanghai",
    )


async def _update(scheduler: _FakeScheduler, schedule: dict) -> None:
    await _handle_cron_update(
        {"id": "job-A", "schedule": schedule},
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )


@pytest.mark.asyncio
async def test_update_sets_the_timezone_through_the_alias() -> None:
    scheduler = _FakeScheduler(_shanghai_job())

    await _update(
        scheduler, {"kind": "cron", "expr": "0 10 * * *", "timezone": "America/Los_Angeles"}
    )

    assert scheduler.updated is not None
    assert scheduler.updated["schedule_value"] == "0 10 * * *"
    assert scheduler.updated["schedule_tz"] == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_update_clears_the_timezone_through_the_alias() -> None:
    scheduler = _FakeScheduler(_shanghai_job())

    await _update(scheduler, {"kind": "cron", "expr": "0 9 * * *", "timezone": ""})

    assert scheduler.updated is not None
    assert scheduler.updated["schedule_tz"] == ""
    assert scheduler.job.tz == ""


@pytest.mark.asyncio
async def test_update_without_either_key_leaves_the_timezone_alone() -> None:
    scheduler = _FakeScheduler(_shanghai_job())

    await _update(scheduler, {"kind": "cron", "expr": "0 10 * * *"})

    assert scheduler.updated is not None
    assert "schedule_tz" not in scheduler.updated
    assert scheduler.job.tz == "Asia/Shanghai"
