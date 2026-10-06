"""``trading.trigger.*``: an agent may propose and read a price trigger; only the operator runs it.

Runs through the real dispatcher and service (the ``stack`` fixture of
``test_rpc_trading``). Structural param validation is the RPC layer's: a
malformed or missing param, a condition without its number, an action with
the wrong size is ``trading.trigger.invalid`` naming the field -- never the
dispatcher's generic ``INVALID_REQUEST``. An unsupported chain stays
``trading.unsupported_chain``.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher
from tests.test_gateway.test_rpc_trading import (  # noqa: F401
    PASSWORD,
    _agent_ctx,
    call,
    ctx,
    stack,
)
from tests.test_trading.fakes import USDC

READS = ("trading.trigger.create", "trading.trigger.get", "trading.trigger.list")
WRITES = (
    "trading.trigger.approve",
    "trading.trigger.reject",
    "trading.trigger.pause",
    "trading.trigger.resume",
    "trading.trigger.stop",
    "trading.trigger.fire",
)
#: A stop-loss far under the fake WETH price (2000): it stays armed.
CREATE = {
    "chainId": 8453,
    "kind": "sell",
    "token": "WETH",
    "direction": "below",
    "price": "1000",
    "amountPct": 50,
}


async def _ready(ctx: RpcContext, stack: dict[str, Any]) -> str:  # noqa: F811
    await call("wallet.setup", {"password": PASSWORD}, ctx)
    address = (await call("wallet.create", {"label": "Main"}, ctx)).payload["wallet"]["address"]
    stack["base"].set_native(address, 10**18)
    stack["base"].set_erc20(USDC, address, 1_000 * 10**6)
    return str(address)


def test_methods_are_registered() -> None:
    assert set(READS + WRITES) <= set(get_dispatcher().methods())


async def test_agent_proposes_and_the_operator_arms(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    agent = _agent_ctx(stack, "agent:trading:desk:1")
    # The agent may call itself "manual" and name another chat: neither is honoured.
    res = await call(
        "trading.trigger.create",
        {**CREATE, "name": "Stop\u202e WETH\n", "initiator": "manual", "sessionKey": "other"},
        agent,
    )
    assert res.ok, res.error
    payload = res.payload
    assert payload["kind"] == "trigger" and payload["version"] == 1
    trigger = payload["trigger"]
    assert trigger["id"].startswith("trg_")
    assert trigger["status"] == "awaiting_approval"
    assert trigger["initiator"] == "agent" and trigger["sessionKey"] == "agent:trading:desk:1"
    assert trigger["name"] == "Stop WETH"
    assert trigger["kind"] == "sell" and trigger["condition"]["direction"] == "below"
    assert trigger["condition"]["priceUsd"] == 1000 and trigger["action"]["amountPct"] == 50
    assert trigger["expiresAt"]
    assert payload["request"] == {"kind": "get", "params": {"triggerId": trigger["id"]}}

    for method in WRITES:
        denied = await call(method, {"triggerId": trigger["id"]}, agent)
        assert denied.ok is False and denied.error.code == "trading.operator_required", method

    got = await call("trading.trigger.get", {"triggerId": trigger["id"]}, agent)
    assert got.ok, got.error
    assert got.payload["trigger"]["status"] == "awaiting_approval"
    assert got.payload["request"] == {"kind": "get", "params": {"triggerId": trigger["id"]}}

    approved = await call("trading.trigger.approve", {"triggerId": trigger["id"]}, ctx)
    assert approved.ok, approved.error
    assert approved.payload["trigger"]["status"] == "armed"
    assert approved.payload["trigger"]["armedAt"]


async def test_operator_create_is_armed_and_keeps_its_session_key(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    res = await call(
        "trading.trigger.create",
        {
            "chain": "base",
            "kind": "alert",
            "token": "WETH",
            "direction": "above",
            "price": 5000,
            "validForSeconds": 3600.0,
            "sessionKey": "agent:main:cli",
        },
        ctx,
    )
    assert res.ok, res.error
    trigger = res.payload["trigger"]
    assert trigger["status"] == "armed" and trigger["initiator"] == "manual"
    assert trigger["sessionKey"] == "agent:main:cli"
    assert trigger["kind"] == "alert" and trigger["condition"]["priceUsd"] == 5000
    assert trigger["validUntil"]


async def test_create_forwards_params_as_given(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``price`` goes through untouched (the engine resolves ``-10%``); ``amount`` as text."""
    seen: list[dict[str, Any]] = []

    async def spy(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"kind": "trigger"}

    monkeypatch.setattr(stack["service"], "trigger_create", spy, raising=False)
    res = await call(
        "trading.trigger.create",
        {
            **CREATE,
            "kind": "SELL",
            "price": " -10% ",
            "amountPct": None,
            "amount": 0.00001,
            "quote": "USDC",
            "wallet": "Main",
            "slippagePct": "1",
            "name": "  Cut   losses ",
            "validForSeconds": "86400",
        },
        ctx,
    )
    assert res.ok, res.error
    call_args = seen[-1]
    chain = call_args.pop("chain")
    assert chain.chain_id == 8453
    assert call_args == {
        "kind": "sell",
        "token": "WETH",
        "quote": "USDC",
        "direction": "below",
        "price": "-10%",
        "trail_pct": None,
        "amount_usd": None,
        "amount_pct": None,
        "amount": "0.00001",
        "wallet": "Main",
        "slippage_pct": 1.0,
        "name": "Cut losses",
        "valid_for_seconds": 86_400,
        "initiator": "manual",
        "session_key": None,
    }

    res = await call(
        "trading.trigger.create",
        {**CREATE, "price": 3800.5, "amountPct": None, "amount": "0.05"},
        ctx,
    )
    assert res.ok, res.error
    assert seen[-1]["price"] == 3800.5 and seen[-1]["amount"] == "0.05"

    res = await call(
        "trading.trigger.create",
        {**CREATE, "direction": "trail", "price": None, "trailPct": 10},
        ctx,
    )
    assert res.ok, res.error
    assert seen[-1]["direction"] == "trail" and seen[-1]["trail_pct"] == 10.0
    assert seen[-1]["price"] is None and seen[-1]["amount_pct"] == 50.0

    agent = _agent_ctx(stack, "agent:trading:desk:2")
    res = await call("trading.trigger.create", {**CREATE, "initiator": "manual"}, agent)
    assert res.ok, res.error
    assert seen[-1]["initiator"] == "agent" and seen[-1]["session_key"] == "agent:trading:desk:2"


async def test_list_shapes(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    wallet = await _ready(ctx, stack)
    agent = _agent_ctx(stack)
    empty = await call("trading.trigger.list", {}, agent)
    assert empty.ok, empty.error
    assert empty.payload["kind"] == "triggers" and empty.payload["triggers"] == []
    assert empty.payload["request"]["kind"] == "list"
    assert empty.payload["totals"]["count"] == 0

    live = (await call("trading.trigger.create", CREATE, ctx)).payload["trigger"]["id"]
    gone = (await call("trading.trigger.create", CREATE, ctx)).payload["trigger"]["id"]
    stopped = await call("trading.trigger.stop", {"triggerId": gone, "reason": "enough"}, ctx)
    assert stopped.ok, stopped.error
    assert stopped.payload["trigger"]["status"] == "stopped"

    res = await call("trading.trigger.list", {}, agent)
    assert [t["id"] for t in res.payload["triggers"]] == [live]
    assert res.payload["totals"]["count"] == 1 and res.payload["totals"]["armed"] == 1
    res = await call("trading.trigger.list", {"all": True, "wallet": wallet}, agent)
    assert res.ok, res.error
    assert {t["id"] for t in res.payload["triggers"]} == {live, gone}
    assert res.payload["request"]["params"].get("all") is True


async def test_operator_lifecycle(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(ctx, stack)
    trigger_id = (await call("trading.trigger.create", CREATE, ctx)).payload["trigger"]["id"]

    paused = await call("trading.trigger.pause", {"triggerId": trigger_id}, ctx)
    assert paused.ok and paused.payload["trigger"]["status"] == "paused"
    again = await call("trading.trigger.pause", {"triggerId": trigger_id}, ctx)
    assert again.ok is False and again.error.code == "trading.trigger.bad_state"
    resumed = await call("trading.trigger.resume", {"triggerId": trigger_id}, ctx)
    assert resumed.ok and resumed.payload["trigger"]["status"] == "armed"

    fire_seen: dict[str, Any] = {}

    async def fire_spy(tid: str, *, wait: bool = False) -> dict[str, Any]:
        fire_seen.update(trigger_id=tid, wait=wait)
        return {"kind": "trigger", "fire": {"n": 1}}

    monkeypatch.setattr(stack["service"], "trigger_fire_now", fire_spy)
    res = await call("trading.trigger.fire", {"triggerId": trigger_id, "wait": True}, ctx)
    assert res.ok and res.payload["fire"] == {"n": 1}
    assert fire_seen == {"trigger_id": trigger_id, "wait": True}

    reject_seen: dict[str, Any] = {}

    async def reject_spy(tid: str, reason: str | None = None) -> dict[str, Any]:
        reject_seen.update(trigger_id=tid, reason=reason)
        return {"kind": "trigger"}

    monkeypatch.setattr(stack["service"], "trigger_reject", reject_spy)
    res = await call(
        "trading.trigger.reject", {"triggerId": f" {trigger_id} ", "reason": "too\ntight"}, ctx
    )
    assert res.ok, res.error
    assert reject_seen == {"trigger_id": trigger_id, "reason": "too tight"}


async def test_engine_codes(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    res = await call("trading.trigger.get", {"triggerId": "trg_00000000"}, ctx)
    assert res.ok is False and res.error.code == "trading.trigger.not_found"
    res = await call("trading.trigger.create", {**CREATE, "token": "NOPE"}, ctx)
    assert res.ok is False and res.error.code == "trading.token_not_found"
    res = await call("trading.trigger.create", {**CREATE, "amountPct": 150}, ctx)
    assert res.ok is False and res.error.code == "trading.trigger.invalid"
    res = await call("trading.trigger.create", {**CREATE, "chainId": 1}, ctx)
    assert res.ok is False and res.error.code == "trading.unsupported_chain"
    trigger_id = (await call("trading.trigger.create", CREATE, ctx)).payload["trigger"]["id"]
    res = await call("trading.trigger.approve", {"triggerId": trigger_id}, ctx)
    assert res.ok is False and res.error.code == "trading.trigger.bad_state"


SELL = {k: v for k, v in CREATE.items() if k != "amountPct"}


@pytest.mark.parametrize(
    ("method", "params", "field"),
    [
        ("trading.trigger.create", {**CREATE, "chainId": None}, "chainId"),
        ("trading.trigger.create", {**CREATE, "token": None}, "token"),
        ("trading.trigger.create", {**CREATE, "kind": None}, "kind"),
        ("trading.trigger.create", {**CREATE, "kind": "hold"}, "kind"),
        ("trading.trigger.create", {**CREATE, "direction": None}, "direction"),
        ("trading.trigger.create", {**CREATE, "direction": "sideways"}, "direction"),
        ("trading.trigger.create", {**CREATE, "price": None}, "price"),
        ("trading.trigger.create", {**CREATE, "price": "  "}, "price"),
        ("trading.trigger.create", {**CREATE, "price": True}, "price"),
        ("trading.trigger.create", {**CREATE, "price": [3800]}, "price"),
        ("trading.trigger.create", {**CREATE, "trailPct": 10}, "trailPct"),
        ("trading.trigger.create", {**CREATE, "direction": "trail", "price": None}, "trailPct"),
        ("trading.trigger.create", {**CREATE, "direction": "trail", "trailPct": 5}, "price"),
        ("trading.trigger.create", {**CREATE, "direction": "trail", "trailPct": "x"}, "trailPct"),
        ("trading.trigger.create", SELL, "exactly one size"),
        ("trading.trigger.create", {**CREATE, "amountUsd": 10}, "exactly one size"),
        ("trading.trigger.create", {**CREATE, "amountPct": "half"}, "amountPct"),
        ("trading.trigger.create", {**SELL, "amount": True}, "amount"),
        ("trading.trigger.create", {**SELL, "kind": "buy"}, "amountUsd"),
        ("trading.trigger.create", {**CREATE, "kind": "buy"}, "amountUsd"),
        ("trading.trigger.create", {**SELL, "kind": "buy", "amountUsd": 5, "amount": 1}, "only"),
        (
            "trading.trigger.create",
            {
                **SELL,
                "kind": "buy",
                "amountUsd": 5,
                "direction": "trail",
                "price": None,
                "trailPct": 10,
            },
            "trail",
        ),
        ("trading.trigger.create", {**CREATE, "kind": "alert"}, "amountPct"),
        ("trading.trigger.create", {**SELL, "kind": "alert", "amountUsd": 5}, "amountUsd"),
        ("trading.trigger.create", {**CREATE, "validForSeconds": 59}, "validForSeconds"),
        ("trading.trigger.create", {**CREATE, "validForSeconds": "1d"}, "validForSeconds"),
        ("trading.trigger.create", {**CREATE, "validForSeconds": 90.5}, "validForSeconds"),
        ("trading.trigger.create", {**CREATE, "slippagePct": "nan"}, "slippagePct"),
        ("trading.trigger.create", {**CREATE, "initiator": "robot"}, "initiator"),
        ("trading.trigger.create", {**CREATE, "quote": 5}, "quote"),
        ("trading.trigger.get", {}, "triggerId"),
        ("trading.trigger.list", {"all": "yes"}, "all"),
        ("trading.trigger.approve", {"triggerId": " "}, "triggerId"),
        ("trading.trigger.stop", {"triggerId": 7}, "triggerId"),
        ("trading.trigger.fire", {"triggerId": "trg_1", "wait": 1}, "wait"),
    ],
)
async def test_bad_params_are_trigger_invalid_naming_the_field(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    method: str,
    params: dict[str, Any],
    field: str,
) -> None:
    await _ready(ctx, stack)
    res = await call(method, params, ctx)
    assert res.ok is False, (method, params)
    assert res.error.code == "trading.trigger.invalid", res.error
    assert field in res.error.message, res.error.message
    listed = await call("trading.trigger.list", {"all": True}, ctx)
    assert listed.ok, listed.error
    assert listed.payload["triggers"] == []
