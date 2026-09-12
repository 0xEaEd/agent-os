"""cron.remove must refuse missing jobs the same way status/update do.

Regression for https://github.com/use-agent-os/agent-os/issues/1598 —
scheduler.remove_job already returns False when the id is absent, but the
RPC handler used to discard that bool and always report success. The CLI
then invented ``{"removed": true}``.
"""

from __future__ import annotations

import pytest

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import _handle_cron_remove


class _FakeScheduler:
    def __init__(self, *, existing: set[str] | None = None) -> None:
        self.existing = set(existing or ())
        self.removed: list[str] = []

    async def remove_job(self, job_id: str) -> bool:
        self.removed.append(job_id)
        if job_id not in self.existing:
            return False
        self.existing.discard(job_id)
        return True


@pytest.mark.asyncio
async def test_cron_remove_missing_job_raises_not_found() -> None:
    scheduler = _FakeScheduler(existing=set())

    with pytest.raises(KeyError, match="Cron job not found: missing-job"):
        await _handle_cron_remove(
            {"id": "missing-job"},
            RpcContext(conn_id="test", cron_scheduler=scheduler),
        )

    assert scheduler.removed == ["missing-job"]


@pytest.mark.asyncio
async def test_cron_remove_existing_job_succeeds() -> None:
    scheduler = _FakeScheduler(existing={"job-1"})

    result = await _handle_cron_remove(
        {"id": "job-1"},
        RpcContext(conn_id="test", cron_scheduler=scheduler),
    )

    assert result is None
    assert scheduler.removed == ["job-1"]
    assert "job-1" not in scheduler.existing


@pytest.mark.asyncio
async def test_cron_remove_requires_id() -> None:
    scheduler = _FakeScheduler(existing={"job-1"})

    with pytest.raises(ValueError, match="params.id is required"):
        await _handle_cron_remove(
            {},
            RpcContext(conn_id="test", cron_scheduler=scheduler),
        )

    assert scheduler.removed == []
