"""RPC cron handlers honour the structured schedule contract.

Covers:
- structured ``cron.create`` round-trip (expression on the wire is normalized).
- structured ``cron.create`` validation surfaces a field-named error.
- legacy ``expression`` flat-string CLI shim still works.
- ``cron.update`` CLI shim still accepts ``expression`` and returns the
  normalized value.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import _handle_cron_add, _handle_cron_update, _job_to_wire
from agentos.scheduler.payloads import AGENT_TURN_KIND
from agentos.scheduler.types import CronJob, DeliveryConfig, ScheduleKind


class _FakeScheduler:
    def __init__(self) -> None:
        self.added: dict | None = None
        self.updated: dict | None = None
        self.job: CronJob | None = None

    async def add_job(self, **kwargs) -> CronJob:
        self.added = kwargs
        kind = kwargs.get("schedule_kind") or ScheduleKind.CRON
        value = kwargs.get("schedule_value", "")
        self.job = CronJob(
            id="rpc-strict-1",
            name=kwargs["name"],
            cron_expr=value,
            schedule_raw=value,
            schedule_kind=kind,
            handler_key=kwargs["handler_key"],
            payload=kwargs["payload"],
            session_target=kwargs["session_target"],
            session_key=kwargs.get("session_key", ""),
            origin_session_key=kwargs.get("origin_session_key", ""),
            delivery=kwargs.get("delivery") or DeliveryConfig(),
            tz=kwargs.get("schedule_tz") or kwargs.get("tz", "") or "",
        )
        return self.job

    async def update_job(self, job_id: str, **patch) -> CronJob:
        self.updated = patch
        if self.job is None:
            self.job = CronJob(id=job_id)
        for key, value in patch.items():
            if key == "schedule_value":
                self.job.cron_expr = value
                self.job.schedule_raw = value
            elif key == "schedule_kind":
                self.job.schedule_kind = value
            else:
                setattr(self.job, key, value)
        return self.job

    async def get_job(self, job_id: str) -> CronJob | None:
        return self.job


@pytest.mark.asyncio
async def test_rpc_create_with_structured_cron_returns_normalized_expression() -> None:
    scheduler = _FakeScheduler()

    result = await _handle_cron_add(
        {
            "name": "five",
            "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
            "payloadKind": AGENT_TURN_KIND,
            "text": "ping",
            "agentId": "main",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.added is not None
    assert scheduler.added["schedule_kind"] == ScheduleKind.CRON
    assert scheduler.added["schedule_value"] == "*/5 * * * *"
    assert "creator_is_owner" not in scheduler.added
    assert result["expression"] == "*/5 * * * *"
    assert result["scheduleRaw"] == "*/5 * * * *"
    assert result["scheduleKind"] == "cron"


@pytest.mark.asyncio
async def test_rpc_create_with_natural_language_expr_raises_field_named_error() -> None:
    scheduler = _FakeScheduler()

    with pytest.raises(ValueError, match="schedule.expr"):
        await _handle_cron_add(
            {
                "name": "bad",
                "schedule": {"kind": "cron", "expr": "每5分钟"},
                "payloadKind": AGENT_TURN_KIND,
                "text": "ping",
                "agentId": "main",
            },
            RpcContext(conn_id="test", cron_scheduler=scheduler),
        )


@pytest.mark.asyncio
async def test_rpc_create_with_legacy_expression_string_still_works() -> None:
    """CLI shim: a flat ``expression`` string is wrapped as kind='cron'."""
    scheduler = _FakeScheduler()

    result = await _handle_cron_add(
        {
            "name": "five",
            "expression": "*/5 * * * *",
            "payloadKind": AGENT_TURN_KIND,
            "text": "ping",
            "agentId": "main",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.added["schedule_kind"] == ScheduleKind.CRON
    assert scheduler.added["schedule_value"] == "*/5 * * * *"
    assert result["expression"] == "*/5 * * * *"


@pytest.mark.asyncio
async def test_rpc_create_with_every_schedule() -> None:
    scheduler = _FakeScheduler()

    result = await _handle_cron_add(
        {
            "name": "interval",
            "schedule": {"kind": "every", "every_seconds": 300},
            "payloadKind": AGENT_TURN_KIND,
            "text": "ping",
            "agentId": "main",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.added["schedule_kind"] == ScheduleKind.EVERY
    assert scheduler.added["schedule_value"] == "300"
    assert result["scheduleKind"] == "every"
    assert result["scheduleRaw"] == "300"


@pytest.mark.asyncio
async def test_rpc_create_with_at_schedule() -> None:
    scheduler = _FakeScheduler()

    result = await _handle_cron_add(
        {
            "name": "once",
            "schedule": {"kind": "at", "at": "2026-05-18T09:00:00+08:00"},
            "payloadKind": AGENT_TURN_KIND,
            "text": "ping",
            "agentId": "main",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.added["schedule_kind"] == ScheduleKind.AT
    assert scheduler.added["schedule_value"] == "2026-05-18T09:00:00+08:00"
    assert result["scheduleKind"] == "at"
    assert result["scheduleRaw"] == "2026-05-18T09:00:00+08:00"


@pytest.mark.asyncio
async def test_rpc_update_via_legacy_expression_returns_normalized_wire() -> None:
    scheduler = _FakeScheduler()
    scheduler.job = CronJob(
        id="job-A",
        name="orig",
        cron_expr="*/5 * * * *",
        schedule_raw="*/5 * * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="agent_run",
    )

    result = await _handle_cron_update(
        {
            "id": "job-A",
            "expression": "0 9 * * *",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.updated is not None
    assert scheduler.updated["schedule_kind"] == ScheduleKind.CRON
    assert scheduler.updated["schedule_value"] == "0 9 * * *"
    assert result["expression"] == "0 9 * * *"


@pytest.mark.asyncio
async def test_rpc_update_tz_only_recomputes_existing_cron_schedule() -> None:
    scheduler = _FakeScheduler()
    scheduler.job = CronJob(
        id="job-A",
        name="orig",
        cron_expr="0 9 * * *",
        schedule_raw="0 9 * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="agent_run",
        tz="",
    )

    await _handle_cron_update(
        {
            "id": "job-A",
            "tz": "Asia/Shanghai",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.updated is not None
    assert scheduler.updated["schedule_kind"] == ScheduleKind.CRON
    assert scheduler.updated["schedule_value"] == "0 9 * * *"
    assert scheduler.updated["schedule_tz"] == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_rpc_update_tz_only_can_clear_existing_cron_timezone() -> None:
    scheduler = _FakeScheduler()
    scheduler.job = CronJob(
        id="job-A",
        name="orig",
        cron_expr="0 9 * * *",
        schedule_raw="0 9 * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="agent_run",
        tz="Asia/Shanghai",
    )

    await _handle_cron_update(
        {
            "id": "job-A",
            "tz": "",
        },
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert scheduler.updated is not None
    assert scheduler.updated["schedule_kind"] == ScheduleKind.CRON
    assert scheduler.updated["schedule_value"] == "0 9 * * *"
    assert scheduler.updated["schedule_tz"] == ""


def test_job_to_wire_serializes_normalized_expression() -> None:
    """Direct unit test of the wire mapper: expression must come from cron_expr."""
    job = CronJob(
        id="x",
        name="n",
        cron_expr="*/5 * * * *",
        schedule_raw="每5分钟",  # historical raw text persisted from older versions
        schedule_kind=ScheduleKind.CRON,
        handler_key="agent_run",
    )
    wire = _job_to_wire(job)
    assert wire["expression"] == "*/5 * * * *"
    assert wire["scheduleRaw"] == "每5分钟"
    assert wire["scheduleKind"] == "cron"


def test_job_to_wire_exposes_status_for_cron_countdown_state() -> None:
    job = CronJob(
        id="x",
        name="n",
        cron_expr="60",
        schedule_raw="60",
        schedule_kind=ScheduleKind.EVERY,
        handler_key="agent_run",
        status="running",
    )

    wire = _job_to_wire(job)

    assert wire["status"] == "running"


def test_job_to_wire_exposes_creator_session_as_display_metadata() -> None:
    job = CronJob(
        id="creator-wire",
        name="Creator metadata",
        payload={"kind": "reminder", "text": "hello", "agent_id": "main"},
        creator_session_key="agent:main:telegram:chat-1",
    )

    wire = _job_to_wire(job)

    assert wire["creatorSessionKey"] == "agent:main:telegram:chat-1"
    assert wire["createdFrom"] == "agent:main:telegram:chat-1"


def test_job_to_wire_emits_last_status_error_when_last_error_set() -> None:
    """Failed last run must surface last_status=error so Control UI health is truthful."""
    ran_at = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    job = CronJob(
        id="fail-wire",
        name="fail-demo",
        cron_expr="0 * * * *",
        schedule_raw="0 * * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="script_run",
        last_run_at=ran_at,
        last_error="boom\n",
        error_count=1,
    )
    wire = _job_to_wire(job)
    assert wire["last_run"] is not None
    assert wire["lastResult"] == "boom\n"
    assert wire["last_status"] == "error"
    assert wire["lastStatus"] == "error"


def test_job_to_wire_emits_last_status_ok_after_successful_run() -> None:
    ran_at = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
    job = CronJob(
        id="ok-wire",
        name="ok-demo",
        cron_expr="0 * * * *",
        schedule_raw="0 * * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="script_run",
        last_run_at=ran_at,
        last_error=None,
    )
    wire = _job_to_wire(job)
    assert wire["last_run"] is not None
    assert wire["lastResult"] is None
    assert wire["last_status"] == "ok"
    assert wire["lastStatus"] == "ok"


def test_job_to_wire_emits_last_status_none_when_never_run() -> None:
    job = CronJob(
        id="never-wire",
        name="never-demo",
        cron_expr="0 * * * *",
        schedule_raw="0 * * * *",
        schedule_kind=ScheduleKind.CRON,
        handler_key="script_run",
    )
    wire = _job_to_wire(job)
    assert wire["last_run"] is None
    assert wire["lastResult"] is None
    assert wire["last_status"] is None
    assert wire["lastStatus"] is None
