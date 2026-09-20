"""``cron.update`` leaves delivery alone unless the caller changes it.

Two paths rebuilt a job's delivery from whatever the request carried, so a
field the caller did not send was wiped:

* Any payload-related edit (``text``, ``agentId``, ``sessionTarget``, ...) on a
  ``sessionTarget="main"`` job replaced ``delivery`` with an empty
  ``DeliveryConfig()`` -- dropping a webhook, which is valid for main, and a
  failure destination with it.
* A ``delivery`` block is rebuilt from the request, and ``_delivery_to_wire``
  never returns a webhook token. A client that reads a job and saves it back
  (the WebUI edit form does, on every save) cannot echo the token, so the save
  wiped it -- for the primary webhook and for a webhook failure destination.

Every test runs the RPC handlers against a real ``SchedulerEngine`` on an
in-memory ``JobStore`` and reads the job back from the store, so what is
asserted is what was persisted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import _handle_cron_add, _handle_cron_list, _handle_cron_update
from agentos.scheduler.engine import SchedulerEngine
from agentos.scheduler.persistence import JobStore
from agentos.scheduler.types import CronJob, DeliveryMode

HOOK = "https://hooks.example.com/cron"
ALERTS = "https://alerts.example.com/cron-failed"


@pytest.fixture
async def ctx() -> AsyncIterator[RpcContext]:
    async with JobStore(":memory:") as store:
        yield RpcContext(conn_id="test", cron_scheduler=SchedulerEngine(store))


async def _add(ctx: RpcContext, **params: Any) -> str:
    job = await _handle_cron_add({"name": "job", "expression": "*/5 * * * *", **params}, ctx)
    return str(job["id"])


async def _add_main_job(ctx: RpcContext, delivery: dict[str, Any]) -> str:
    return await _add(
        ctx,
        payloadKind="system_event",
        text="old text",
        sessionTarget="main",
        sessionKey="agent:main:main",
        delivery=delivery,
    )


async def _stored(ctx: RpcContext, job_id: str) -> CronJob:
    job = await ctx.cron_scheduler.get_job(job_id)  # type: ignore[union-attr]
    assert job is not None
    return job


async def _wire(ctx: RpcContext, job_id: str) -> dict[str, Any]:
    return next(j for j in await _handle_cron_list({}, ctx) if j["id"] == job_id)


def _webui_delivery(wire: dict[str, Any]) -> dict[str, Any]:
    """What the WebUI edit form sends back for a webhook job it did not touch.

    Mirrors ``buildDelivery`` in ``frontend/src/views/cron/logic.ts``: the form
    is hydrated from the wire job, whose delivery never carries a token, and
    ``webhookToken`` is only added when the user typed one.
    """
    d = wire["delivery"]
    out: dict[str, Any] = {"mode": "webhook", "webhookUrl": d["webhookUrl"]}
    fd = d.get("failureDestination")
    if fd and fd.get("mode") == "webhook":
        out["failureDestination"] = {"mode": "webhook", "webhookUrl": fd["webhookUrl"]}
    return out


# ---------------------------------------------------------------------------
# An omitted delivery block on a main job means unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "edit",
    [
        pytest.param({"text": "new text"}, id="text"),
        pytest.param({"message": "new text"}, id="message_alias"),
        pytest.param({"agentId": "main"}, id="agent_resent"),
        pytest.param({"payloadKind": "system_event"}, id="payload_kind"),
        pytest.param({"sessionTarget": "main"}, id="session_target_resent"),
        pytest.param({"sessionKey": "agent:main:other"}, id="session_key"),
    ],
)
async def test_a_payload_edit_keeps_a_main_jobs_webhook(
    ctx: RpcContext, edit: dict[str, Any]
) -> None:
    job_id = await _add_main_job(
        ctx, {"mode": "webhook", "webhookUrl": HOOK, "webhookToken": "tok"}
    )

    await _handle_cron_update({"id": job_id, **edit}, ctx)

    delivery = (await _stored(ctx, job_id)).delivery
    assert delivery.mode == DeliveryMode.WEBHOOK
    assert delivery.webhook_url == HOOK
    assert delivery.webhook_token == "tok"
    assert (await _wire(ctx, job_id))["delivery"]["webhookUrl"] == HOOK


async def test_a_payload_edit_keeps_a_main_jobs_failure_destination(ctx: RpcContext) -> None:
    """A main job with no primary route may still alert on failure."""
    job_id = await _add_main_job(ctx, {"mode": "none"})
    await _handle_cron_update(
        {
            "id": job_id,
            "delivery": {
                "failureDestination": {
                    "mode": "webhook",
                    "webhookUrl": ALERTS,
                    "webhookToken": "alert-tok",
                }
            },
        },
        ctx,
    )

    await _handle_cron_update({"id": job_id, "text": "new text"}, ctx)

    fd = (await _stored(ctx, job_id)).delivery.failure_destination
    assert fd is not None
    assert fd.webhook_url == ALERTS
    assert fd.webhook_token == "alert-tok"


async def test_moving_a_channel_job_to_main_drops_only_the_channel_route(
    ctx: RpcContext,
) -> None:
    """Channel delivery cannot stay on main; its failure destination can."""
    job_id = await _add(
        ctx,
        payloadKind="agent_turn",
        text="summarise",
        sessionTarget="isolated",
        delivery={
            "mode": "channel",
            "channelName": "telegram",
            "channelId": "123",
            "failureDestination": {"mode": "webhook", "webhookUrl": ALERTS},
        },
    )

    await _handle_cron_update(
        {
            "id": job_id,
            "payloadKind": "system_event",
            "sessionTarget": "main",
            "sessionKey": "agent:main:main",
        },
        ctx,
    )

    delivery = (await _stored(ctx, job_id)).delivery
    assert delivery.mode == DeliveryMode.NONE
    assert delivery.channel_name == ""
    assert delivery.channel_id == ""
    assert delivery.failure_destination is not None
    assert delivery.failure_destination.webhook_url == ALERTS


async def test_an_explicit_none_still_clears_a_main_jobs_webhook(ctx: RpcContext) -> None:
    """Clearing is still possible -- it just has to be asked for."""
    job_id = await _add_main_job(
        ctx, {"mode": "webhook", "webhookUrl": HOOK, "webhookToken": "tok"}
    )

    await _handle_cron_update({"id": job_id, "text": "t", "delivery": {"mode": "none"}}, ctx)

    delivery = (await _stored(ctx, job_id)).delivery
    assert delivery.mode == DeliveryMode.NONE
    assert delivery.webhook_url == ""
    assert delivery.webhook_token == ""


# ---------------------------------------------------------------------------
# A webhook token the caller did not send is kept, for the same URL only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("session_target", ["isolated", "main"])
async def test_saving_a_job_back_as_read_keeps_its_webhook_token(
    ctx: RpcContext, session_target: str
) -> None:
    """The WebUI round trip: read the job, change the schedule, save everything."""
    main = session_target == "main"
    job_id = await _add(
        ctx,
        payloadKind="system_event" if main else "agent_turn",
        text="run",
        sessionTarget=session_target,
        **({"sessionKey": "agent:main:main"} if main else {}),
        delivery={"mode": "webhook", "webhookUrl": HOOK, "webhookToken": "tok"},
    )
    wire = await _wire(ctx, job_id)
    assert "webhookToken" not in wire["delivery"]

    await _handle_cron_update(
        {
            "id": job_id,
            "schedule": {"kind": "cron", "expr": "0 9 * * *"},
            "text": wire["text"],
            "sessionTarget": session_target,
            "delivery": _webui_delivery(wire),
        },
        ctx,
    )

    stored = await _stored(ctx, job_id)
    assert stored.cron_expr == "0 9 * * *"
    assert stored.delivery.webhook_url == HOOK
    assert stored.delivery.webhook_token == "tok"


async def test_saving_a_job_back_as_read_keeps_its_failure_webhook_token(
    ctx: RpcContext,
) -> None:
    job_id = await _add(
        ctx,
        payloadKind="agent_turn",
        text="run",
        sessionTarget="isolated",
        delivery={
            "mode": "webhook",
            "webhookUrl": HOOK,
            "webhookToken": "tok",
            "failureDestination": {
                "mode": "webhook",
                "webhookUrl": ALERTS,
                "webhookToken": "alert-tok",
            },
        },
    )
    wire = await _wire(ctx, job_id)

    await _handle_cron_update({"id": job_id, "delivery": _webui_delivery(wire)}, ctx)

    delivery = (await _stored(ctx, job_id)).delivery
    assert delivery.webhook_token == "tok"
    assert delivery.failure_destination is not None
    assert delivery.failure_destination.webhook_token == "alert-tok"


async def test_a_standalone_failure_destination_patch_keeps_its_token(ctx: RpcContext) -> None:
    job_id = await _add(
        ctx,
        payloadKind="agent_turn",
        text="run",
        sessionTarget="isolated",
        delivery={
            "mode": "channel",
            "channelName": "telegram",
            "channelId": "123",
            "failureDestination": {
                "mode": "webhook",
                "webhookUrl": ALERTS,
                "webhookToken": "alert-tok",
            },
        },
    )

    await _handle_cron_update(
        {
            "id": job_id,
            "delivery": {"failureDestination": {"mode": "webhook", "webhookUrl": ALERTS}},
        },
        ctx,
    )

    fd = (await _stored(ctx, job_id)).delivery.failure_destination
    assert fd is not None
    assert fd.webhook_token == "alert-tok"


async def test_a_token_is_never_carried_to_a_different_url(ctx: RpcContext) -> None:
    """Moving the webhook elsewhere must not hand the old endpoint's secret over."""
    job_id = await _add(
        ctx,
        payloadKind="agent_turn",
        text="run",
        sessionTarget="isolated",
        delivery={
            "mode": "webhook",
            "webhookUrl": HOOK,
            "webhookToken": "tok",
            "failureDestination": {
                "mode": "webhook",
                "webhookUrl": ALERTS,
                "webhookToken": "alert-tok",
            },
        },
    )

    await _handle_cron_update(
        {
            "id": job_id,
            "delivery": {
                "mode": "webhook",
                "webhookUrl": "https://elsewhere.example.com/hook",
                "failureDestination": {
                    "mode": "webhook",
                    "webhookUrl": "https://elsewhere.example.com/alerts",
                },
            },
        },
        ctx,
    )

    delivery = (await _stored(ctx, job_id)).delivery
    assert delivery.webhook_token == ""
    assert delivery.failure_destination is not None
    assert delivery.failure_destination.webhook_token == ""


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        pytest.param({"webhookToken": ""}, "", id="explicit_empty_clears"),
        pytest.param({"token": ""}, "", id="alias_explicit_empty_clears"),
        pytest.param({"webhookToken": "new"}, "new", id="new_token_replaces"),
    ],
)
async def test_a_token_the_caller_does_send_wins(
    ctx: RpcContext, sent: dict[str, str], expected: str
) -> None:
    job_id = await _add(
        ctx,
        payloadKind="agent_turn",
        text="run",
        sessionTarget="isolated",
        delivery={"mode": "webhook", "webhookUrl": HOOK, "webhookToken": "tok"},
    )

    await _handle_cron_update(
        {"id": job_id, "delivery": {"mode": "webhook", "webhookUrl": HOOK, **sent}}, ctx
    )

    assert (await _stored(ctx, job_id)).delivery.webhook_token == expected
