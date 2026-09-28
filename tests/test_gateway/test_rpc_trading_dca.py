"""``trading.dca.*``: an agent may propose and read a DCA mandate; only the operator runs it.

Runs through the real dispatcher and service (the ``stack`` fixture of
``test_rpc_trading``). Param validation is the RPC layer's: a malformed or
missing param is ``trading.dca.invalid`` naming the field -- never the
dispatcher's generic ``INVALID_REQUEST`` -- so the CLI and the card see one
code for "change the input". An unsupported chain stays
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

READS = ("trading.dca.create", "trading.dca.get", "trading.dca.list")
WRITES = (
    "trading.dca.approve",
    "trading.dca.reject",
    "trading.dca.pause",
    "trading.dca.resume",
    "trading.dca.stop",
    "trading.dca.run",
    "trading.dca.update",
)
CREATE = {"chainId": 8453, "token": "WETH", "usdPerRun": 10, "capUsd": 300, "everySeconds": 86400}


async def _ready(ctx: RpcContext, stack: dict[str, Any]) -> str:  # noqa: F811
    await call("wallet.setup", {"password": PASSWORD}, ctx)
    address = (await call("wallet.create", {"label": "Main"}, ctx)).payload["wallet"]["address"]
    stack["base"].set_native(address, 10**18)
    stack["base"].set_erc20(USDC, address, 1_000 * 10**6)
    return str(address)


def test_methods_are_registered() -> None:
    assert set(READS + WRITES) <= set(get_dispatcher().methods())


async def test_agent_proposes_and_the_operator_starts(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    agent = _agent_ctx(stack, "agent:trading:desk:1")
    # The agent may call itself "manual" and name another chat: neither is honoured.
    res = await call(
        "trading.dca.create",
        {**CREATE, "name": "DCA\u202e ETH\n", "initiator": "manual", "sessionKey": "other"},
        agent,
    )
    assert res.ok, res.error
    payload = res.payload
    assert payload["kind"] == "mandate" and payload["version"] == 1
    mandate = payload["mandate"]
    assert mandate["status"] == "awaiting_approval"
    assert mandate["initiator"] == "agent" and mandate["sessionKey"] == "agent:trading:desk:1"
    assert mandate["name"] == "DCA ETH"
    assert mandate["budget"]["usdPerRun"] == 10 and mandate["budget"]["capUsd"] == 300
    assert mandate["schedule"]["everySeconds"] == 86400 and mandate["expiresAt"]
    assert payload["request"] == {"kind": "get", "params": {"mandateId": mandate["id"]}}

    for method in WRITES:
        params: dict[str, Any] = {"mandateId": mandate["id"]}
        if method == "trading.dca.update":
            params["usdPerRun"] = 5
        denied = await call(method, params, agent)
        assert denied.ok is False and denied.error.code == "trading.operator_required", method

    got = await call("trading.dca.get", {"mandateId": mandate["id"]}, agent)
    assert got.ok, got.error
    assert got.payload["mandate"]["status"] == "awaiting_approval"

    approved = await call("trading.dca.approve", {"mandateId": mandate["id"]}, ctx)
    assert approved.ok, approved.error
    assert approved.payload["mandate"]["status"] == "active"
    assert approved.payload["mandate"]["schedule"]["nextRunAt"]


async def test_operator_create_is_active_and_keeps_its_session_key(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    res = await call(
        "trading.dca.create",
        {
            "chain": "base",
            "token": "WETH",
            "usdPerRun": "10",
            "runsMax": 3,
            "everySeconds": 3600.0,
            "maxPriceUsd": 3000,
            "slippagePct": 0.5,
            "startNow": False,
            "sessionKey": "agent:main:cli",
        },
        ctx,
    )
    assert res.ok, res.error
    mandate = res.payload["mandate"]
    assert mandate["status"] == "active" and mandate["initiator"] == "manual"
    assert mandate["sessionKey"] == "agent:main:cli"
    # The cap defaults to usdPerRun x runsMax.
    assert mandate["budget"]["capUsd"] == 30 and mandate["runs"]["max"] == 3
    assert mandate["guards"]["maxPriceUsd"] == 3000 and mandate["guards"]["slippagePct"] == 0.5
    assert mandate["schedule"]["startNow"] is False and mandate["name"] == "DCA WETH"


async def test_list_shapes(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    wallet = await _ready(ctx, stack)
    agent = _agent_ctx(stack)
    empty = await call("trading.dca.list", {}, agent)
    assert empty.ok, empty.error
    assert empty.payload["kind"] == "mandates" and empty.payload["mandates"] == []
    assert empty.payload["request"]["kind"] == "list"

    live = (await call("trading.dca.create", CREATE, ctx)).payload["mandate"]["id"]
    gone = (await call("trading.dca.create", CREATE, ctx)).payload["mandate"]["id"]
    stopped = await call("trading.dca.stop", {"mandateId": gone, "reason": "enough"}, ctx)
    assert stopped.ok, stopped.error
    assert stopped.payload["mandate"]["status"] == "stopped"

    res = await call("trading.dca.list", {}, agent)
    assert [m["id"] for m in res.payload["mandates"]] == [live]
    assert res.payload["totals"]["count"] == 1
    res = await call("trading.dca.list", {"all": True, "wallet": wallet}, agent)
    assert res.ok, res.error
    assert {m["id"] for m in res.payload["mandates"]} == {live, gone}
    assert res.payload["request"]["params"].get("all") is True


async def test_operator_lifecycle_and_update_mapping(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(ctx, stack)
    mandate_id = (await call("trading.dca.create", CREATE, ctx)).payload["mandate"]["id"]

    paused = await call("trading.dca.pause", {"mandateId": mandate_id}, ctx)
    assert paused.ok and paused.payload["mandate"]["status"] == "paused"
    again = await call("trading.dca.pause", {"mandateId": mandate_id}, ctx)
    assert again.ok is False and again.error.code == "trading.dca.bad_state"
    resumed = await call("trading.dca.resume", {"mandateId": mandate_id}, ctx)
    assert resumed.ok and resumed.payload["mandate"]["status"] == "active"

    updated = await call(
        "trading.dca.update",
        {
            "mandateId": mandate_id,
            "usdPerRun": 20,
            "capUsd": 400,
            "runsMax": 0,
            "maxPriceUsd": 2500,
            "name": " Stack ETH ",
        },
        ctx,
    )
    assert updated.ok, updated.error
    mandate = updated.payload["mandate"]
    assert mandate["budget"]["usdPerRun"] == 20 and mandate["budget"]["capUsd"] == 400
    assert mandate["runs"]["max"] is None and mandate["guards"]["maxPriceUsd"] == 2500
    assert mandate["name"] == "Stack ETH"

    # Only the given keys reach the engine, camelCase turned snake_case.
    seen: dict[str, Any] = {}

    async def spy(mid: str, **fields: Any) -> dict[str, Any]:
        seen.update(mandate_id=mid, fields=fields)
        return {"kind": "mandate"}

    monkeypatch.setattr(stack["service"], "dca_update", spy)
    res = await call(
        "trading.dca.update",
        {"mandateId": mandate_id, "everySeconds": 7200, "maxPriceUsd": 0, "capUsd": None},
        ctx,
    )
    assert res.ok, res.error
    assert seen == {
        "mandate_id": mandate_id,
        "fields": {"every_seconds": 7200, "max_price_usd": 0.0},
    }

    run_seen: dict[str, Any] = {}

    async def run_spy(mid: str, *, wait: bool = False) -> dict[str, Any]:
        run_seen.update(mandate_id=mid, wait=wait)
        return {"kind": "mandate", "run": {"n": 1}}

    monkeypatch.setattr(stack["service"], "dca_run_now", run_spy)
    res = await call("trading.dca.run", {"mandateId": mandate_id, "wait": True}, ctx)
    assert res.ok and res.payload["run"] == {"n": 1}
    assert run_seen == {"mandate_id": mandate_id, "wait": True}


async def test_engine_codes(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    res = await call("trading.dca.get", {"mandateId": "dca_00000000"}, ctx)
    assert res.ok is False and res.error.code == "trading.dca.not_found"
    res = await call("trading.dca.create", {**CREATE, "token": "NOPE"}, ctx)
    assert res.ok is False and res.error.code == "trading.token_not_found"
    res = await call("trading.dca.create", {**CREATE, "capUsd": 5}, ctx)
    assert res.ok is False and res.error.code == "trading.dca.invalid"
    res = await call("trading.dca.create", {**CREATE, "chainId": 1}, ctx)
    assert res.ok is False and res.error.code == "trading.unsupported_chain"
    mandate_id = (await call("trading.dca.create", CREATE, ctx)).payload["mandate"]["id"]
    res = await call("trading.dca.approve", {"mandateId": mandate_id}, ctx)
    assert res.ok is False and res.error.code == "trading.dca.bad_state"


@pytest.mark.parametrize(
    ("method", "params", "field"),
    [
        ("trading.dca.create", {**CREATE, "chainId": None}, "chainId"),
        ("trading.dca.create", {**CREATE, "token": None}, "token"),
        ("trading.dca.create", {**CREATE, "usdPerRun": None}, "usdPerRun"),
        ("trading.dca.create", {**CREATE, "usdPerRun": "ten"}, "usdPerRun"),
        ("trading.dca.create", {**CREATE, "usdPerRun": True}, "usdPerRun"),
        ("trading.dca.create", {**CREATE, "capUsd": None}, "capUsd or params.runsMax"),
        ("trading.dca.create", {**CREATE, "runsMax": 2.5}, "runsMax"),
        ("trading.dca.create", {**CREATE, "everySeconds": None}, "everySeconds"),
        ("trading.dca.create", {**CREATE, "everySeconds": 59}, "everySeconds"),
        ("trading.dca.create", {**CREATE, "everySeconds": "1d"}, "everySeconds"),
        ("trading.dca.create", {**CREATE, "startNow": "yes"}, "startNow"),
        ("trading.dca.create", {**CREATE, "maxPriceUsd": "nan"}, "maxPriceUsd"),
        ("trading.dca.create", {**CREATE, "initiator": "robot"}, "initiator"),
        ("trading.dca.get", {}, "mandateId"),
        ("trading.dca.list", {"all": "yes"}, "all"),
        ("trading.dca.approve", {"mandateId": " "}, "mandateId"),
        ("trading.dca.run", {"mandateId": "dca_1", "wait": 1}, "wait"),
        ("trading.dca.update", {"mandateId": "dca_1"}, "nothing to update"),
        ("trading.dca.update", {"mandateId": "dca_1", "everySeconds": 30}, "everySeconds"),
        ("trading.dca.update", {"mandateId": "dca_1", "runsMax": "x"}, "runsMax"),
        ("trading.dca.update", {"mandateId": "dca_1", "name": 5}, "name"),
    ],
)
async def test_bad_params_are_dca_invalid_naming_the_field(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    method: str,
    params: dict[str, Any],
    field: str,
) -> None:
    await _ready(ctx, stack)
    res = await call(method, params, ctx)
    assert res.ok is False, (method, params)
    assert res.error.code == "trading.dca.invalid", res.error
    assert field in res.error.message
    assert stack["service"].ledger.list_mandates() == []
