"""cron.add / cron.update keep the recipient and best-effort flag (#2093).

The CLI's ``_build_delivery_params`` sends the recipient as ``to`` — the same
key the failure-destination block and the target probe already accept — but
``_parse_delivery_overrides`` only read ``channelId``, so a job added with
``--channel telegram --to <chat_id>`` was saved with an empty recipient. The
``bestEffort`` flag was dropped on the same path, and ``cron.update`` crashed
on a job whose stored delivery was ``None``.
"""

from __future__ import annotations

import asyncio

from agentos.gateway.rpc import RpcContext
from agentos.gateway.rpc_cron import (
    _handle_cron_add,
    _handle_cron_update,
    _parse_delivery_overrides,
)
from agentos.scheduler.delivery import infer_delivery
from agentos.scheduler.types import (
    CronJob,
    DeliveryConfig,
    DeliveryMode,
    SessionTarget,
)


class _FakeScheduler:
    def __init__(self, job: CronJob | None = None) -> None:
        self.added: dict | None = None
        self.updated: dict | None = None
        self.job = job

    async def add_job(self, **kwargs) -> CronJob:
        self.added = kwargs
        return CronJob(
            id="job-1",
            name=kwargs["name"],
            cron_expr=kwargs.get("schedule_value") or "",
            schedule_raw=kwargs.get("schedule_value") or "",
            handler_key=kwargs["handler_key"],
            payload=kwargs["payload"],
            session_target=kwargs["session_target"],
            session_key=kwargs["session_key"],
            origin_session_key=kwargs["origin_session_key"],
            delivery=kwargs.get("delivery") or DeliveryConfig(),
        )

    async def update_job(self, job_id, **patch) -> CronJob:
        self.updated = patch
        assert self.job is not None
        for key, value in patch.items():
            setattr(self.job, key, value)
        return self.job

    async def get_job(self, job_id) -> CronJob | None:
        return self.job if self.job and self.job.id == job_id else None


def _add_params(delivery: dict) -> dict:
    return {
        "name": "watchdog",
        "expression": "*/10 * * * *",
        "text": "tick",
        "sessionTarget": "isolated",
        "delivery": delivery,
    }


def _existing_job(delivery: DeliveryConfig | None) -> CronJob:
    return CronJob(
        id="job-1",
        name="watchdog",
        cron_expr="600",
        schedule_raw="600",
        handler_key="script_run",
        payload={"kind": "script", "script": "tick.sh"},
        session_target=SessionTarget.ISOLATED,
        delivery=delivery,  # type: ignore[arg-type]
    )


def _add(delivery: dict) -> DeliveryConfig:
    scheduler = _FakeScheduler()
    asyncio.run(
        _handle_cron_add(_add_params(delivery), RpcContext(conn_id="t", cron_scheduler=scheduler))
    )
    assert scheduler.added is not None
    return scheduler.added["delivery"]


def _update(job: CronJob, delivery: dict) -> DeliveryConfig:
    scheduler = _FakeScheduler(job)
    asyncio.run(
        _handle_cron_update(
            {"id": "job-1", "delivery": delivery},
            RpcContext(conn_id="t", cron_scheduler=scheduler),
        )
    )
    assert scheduler.updated is not None
    return scheduler.updated["delivery"]


# ── _parse_delivery_overrides ───────────────────────────────────────────────


def test_overrides_accept_to_as_the_recipient() -> None:
    overrides = _parse_delivery_overrides(
        {"mode": "channel", "channelName": "telegram", "to": "1245463966"}
    )
    assert overrides is not None
    assert overrides["channel_id"] == "1245463966"


def test_overrides_prefer_channel_id_when_both_are_given() -> None:
    overrides = _parse_delivery_overrides(
        {"channelName": "telegram", "channelId": "111", "to": "222"}
    )
    assert overrides is not None
    assert overrides["channel_id"] == "111"


def test_overrides_accept_channel_as_the_channel_name() -> None:
    overrides = _parse_delivery_overrides({"channel": "telegram", "to": "1245463966"})
    assert overrides is not None
    assert overrides["channel_name"] == "telegram"


def test_overrides_carry_best_effort_under_either_spelling() -> None:
    camel = _parse_delivery_overrides({"channelName": "telegram", "bestEffort": True})
    snake = _parse_delivery_overrides({"channelName": "telegram", "best_effort": True})
    unset = _parse_delivery_overrides({"channelName": "telegram"})
    assert camel is not None and camel["best_effort"] is True
    assert snake is not None and snake["best_effort"] is True
    assert unset is not None and unset["best_effort"] is False


def test_infer_delivery_keeps_best_effort_from_overrides() -> None:
    delivery = asyncio.run(
        infer_delivery(
            session_storage=None,
            session_key="agent:main",
            user_overrides={"channel_name": "telegram", "channel_id": "1", "best_effort": True},
        )
    )
    assert delivery.mode == DeliveryMode.CHANNEL
    assert delivery.best_effort is True


# ── cron.add ────────────────────────────────────────────────────────────────


def test_add_keeps_the_recipient_sent_as_to() -> None:
    delivery = _add(
        {"mode": "channel", "channelName": "telegram", "to": "1245463966", "bestEffort": True}
    )
    assert delivery.mode == DeliveryMode.CHANNEL
    assert delivery.channel_name == "telegram"
    assert delivery.channel_id == "1245463966"
    assert delivery.best_effort is True


def test_add_keeps_the_cli_announce_shape() -> None:
    delivery = _add(
        {"mode": "announce", "channelName": "telegram", "to": "1245463966", "bestEffort": True}
    )
    assert delivery.channel_id == "1245463966"
    assert delivery.best_effort is True


def test_add_defaults_best_effort_to_false() -> None:
    delivery = _add({"mode": "channel", "channelName": "telegram", "channelId": "1245463966"})
    assert delivery.channel_id == "1245463966"
    assert delivery.best_effort is False


# ── cron.update ─────────────────────────────────────────────────────────────


def test_update_accepts_channel_and_to_aliases() -> None:
    delivery = _update(
        _existing_job(DeliveryConfig(ws_topic="cron:job-1")),
        {"mode": "channel", "channel": "telegram", "to": "1245463966", "best_effort": True},
    )
    assert delivery.mode == DeliveryMode.CHANNEL
    assert delivery.channel_name == "telegram"
    assert delivery.channel_id == "1245463966"
    assert delivery.best_effort is True
    assert delivery.ws_topic == "cron:job-1"


def test_update_survives_a_job_with_no_stored_delivery() -> None:
    delivery = _update(
        _existing_job(None),
        {"mode": "channel", "channelName": "telegram", "channelId": "1245463966"},
    )
    assert delivery.channel_id == "1245463966"
    assert delivery.ws_topic == ""


def test_update_to_webhook_survives_a_job_with_no_stored_delivery() -> None:
    delivery = _update(
        _existing_job(None),
        {"mode": "webhook", "webhookUrl": "https://hooks.example.com/x"},
    )
    assert delivery.mode == DeliveryMode.WEBHOOK
    assert delivery.ws_topic == ""


def test_update_failure_destination_only_survives_a_job_with_no_stored_delivery() -> None:
    delivery = _update(
        _existing_job(None),
        {"failureDestination": {"mode": "channel", "channelName": "slack", "to": "C123"}},
    )
    assert delivery.mode == DeliveryMode.NONE
    assert delivery.failure_destination is not None
    assert delivery.failure_destination.channel_id == "C123"


def test_add_keeps_best_effort_on_an_inferred_delivery() -> None:
    class _Node:
        last_channel = "telegram"
        last_to = "1245463966"
        last_account_id = ""
        last_thread_id = ""

    class _Storage:
        async def get_session(self, session_key: str) -> _Node:
            return _Node()

    class _SessionManager:
        _storage = _Storage()

    scheduler = _FakeScheduler()
    ctx = RpcContext(conn_id="t", cron_scheduler=scheduler)
    ctx.session_manager = _SessionManager()
    params = _add_params({"mode": "announce", "bestEffort": True})
    params["sessionKey"] = "agent:main:telegram:direct:1245463966"
    asyncio.run(_handle_cron_add(params, ctx))

    assert scheduler.added is not None
    delivery = scheduler.added["delivery"]
    assert delivery.mode == DeliveryMode.ORIGIN
    assert delivery.channel_name == "telegram"
    assert delivery.best_effort is True
