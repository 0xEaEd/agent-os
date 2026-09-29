"""``trading.lp.collect|remove|add``: an agent may create, only the operator approves.

Runs through the real dispatcher and service, with the planner pointed at
``lp_world``'s fake PositionManager (see ``tests/test_trading/test_lp_write.py``).
Also: every phase-1 read echoes ``request: {kind, params}`` so a card can
re-run itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.trading import lp
from tests.test_gateway.test_rpc_trading import _agent_ctx, call, ctx, stack  # noqa: F401
from tests.test_trading.conftest import PASSWORD
from tests.test_trading.lp_world import (
    POS_BARE,
    POS_OUTSIDER,
    POS_WETH,
    USDC,
    WETH,
    env_for,
    link,
    weth_usdc_key,
    wire,
    write_prices,
    write_world,
)

WRITES = ("trading.lp.collect", "trading.lp.remove", "trading.lp.add")


async def _lp_ready(ctx: RpcContext, stack: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    await call("wallet.setup", {"password": PASSWORD}, ctx)
    address = (await call("wallet.create", {"label": "Main"}, ctx)).payload["wallet"]["address"]
    wallet = address.lower()
    chain = stack["base"]
    chain.set_native(wallet, 10**18)
    chain.set_erc20(USDC, wallet, 1_000 * 10**6)
    chain.set_erc20(WETH, wallet, 10**18)
    world = write_world(wallet, block=chain.block)
    link(world, chain)
    wire(world, chain, wallet)
    stack["service"].lp_env_factory = lambda spec, loop: env_for(
        world, prices=write_prices(), vault={wallet: "Main"}
    )
    return {"wallet": wallet, "world": world}


def test_write_methods_are_registered() -> None:
    assert set(WRITES) <= set(get_dispatcher().methods())


async def test_agent_creates_but_only_the_operator_approves(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    ready = await _lp_ready(ctx, stack)
    agent = _agent_ctx(stack, "agent:trading:desk:1")
    res = await call(
        "trading.lp.collect",
        {"chainId": 8453, "tokenId": f"#{POS_WETH}", "note": "claim", "clientOrderId": "c-1"},
        agent,
    )
    assert res.ok, res.error
    order = res.payload["order"]
    assert order["status"] == "awaiting_approval" and order["kind"] == "lp_collect"
    assert order["initiator"] == "agent" and order["sessionKey"] == "agent:trading:desk:1"
    assert order["plan"]["op"] == "collect" and order["clientOrderId"] == "c-1"
    assert order["note"] == "claim"

    denied = await call("trading.orders.approve", {"orderId": order["orderId"]}, agent)
    assert denied.ok is False and denied.error.code == "trading.operator_required"

    approved = await call(
        "trading.orders.approve", {"orderId": order["orderId"], "wait": True}, ctx
    )
    assert approved.ok, approved.error
    done = approved.payload["order"]
    assert done["status"] == "confirmed", done["reason"]
    assert done["received"]["base"]["raw"] == str(10**15)
    assert ready["world"].positions[POS_WETH].fees0 == 0


async def test_remove_and_add_params_reach_the_engine(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _lp_ready(ctx, stack)
    agent = _agent_ctx(stack)
    res = await call(
        "trading.lp.remove",
        {"chainId": "base", "tokenId": POS_WETH, "pct": 50, "slippagePct": 2},
        agent,
    )
    assert res.ok, res.error
    plan = res.payload["order"]["plan"]
    assert plan["pct"] == 50 and plan["slippagePct"] == 2 and plan["burn"] is False

    pool_id = str(lp.unilp().v4_pool.compute_pool_id(weth_usdc_key())).lower()
    res = await call(
        "trading.lp.add",
        {"chainId": 8453, "token": pool_id, "usd": 20, "range": "pct:10", "slippagePct": 1.5},
        agent,
    )
    assert res.ok, res.error
    order = res.payload["order"]
    assert order["kind"] == "lp_add" and order["status"] == "awaiting_approval"
    assert order["plan"]["rangeSpec"] == "pct:10" and order["plan"]["slippagePct"] == 1.5
    # One-sided shorthand: all base token, from just above the price.
    res = await call(
        "trading.lp.add", {"chainId": 8453, "token": pool_id, "usd": 20, "range": "above:5"}, ctx
    )
    assert res.ok, res.error
    plan = res.payload["order"]["plan"]
    assert plan["rangeSpec"] == "above:5" and plan["oneSided"] == "base"
    assert plan["bounds"]["quote"] == "0"
    res = await call(
        "trading.lp.add",
        {"chainId": 8453, "toPosition": POS_WETH, "amountBase": "0.001"},
        ctx,
    )
    assert res.ok, res.error
    assert res.payload["order"]["plan"]["increase"] is True
    assert res.payload["order"]["initiator"] == "manual"


async def test_engine_codes_and_param_validation(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _lp_ready(ctx, stack)
    res = await call("trading.lp.collect", {"chainId": 8453, "tokenId": POS_OUTSIDER}, ctx)
    assert res.ok is False and res.error.code == "trading.lp.not_owner"
    res = await call(
        "trading.lp.add",
        {"chainId": 8453, "token": WETH, "usd": 20, "range": "mcap:lots"},
        ctx,
    )
    assert res.ok is False and res.error.code == "trading.lp.range_invalid"
    for method, params in [
        ("trading.lp.collect", {"chainId": 8453}),
        ("trading.lp.collect", {"tokenId": "1"}),
        ("trading.lp.collect", {"chainId": 8453, "tokenId": "abc"}),
        ("trading.lp.collect", {"chainId": 8453, "tokenId": 0}),
        ("trading.lp.remove", {"chainId": 8453, "tokenId": 1, "pct": "all"}),
        ("trading.lp.add", {"chainId": 8453, "usd": 5}),
        ("trading.lp.add", {"chainId": 8453, "token": "WETH", "amountBase": True}),
        ("trading.lp.add", {"chainId": 1, "token": "WETH", "usd": 5}),
    ]:
        res = await call(method, params, ctx)
        assert res.ok is False, (method, params)
    assert stack["service"].list_orders()["orders"] == []


async def test_reads_echo_their_request(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = {"id": 8453, "key": "base", "name": "Base", "explorer": "https://basescan.org"}

    def fake(kind: str, payload_chain: dict[str, Any] | None):
        async def run(service: Any, **kwargs: Any) -> dict[str, Any]:
            return {"version": 1, "kind": kind, "chain": payload_chain}

        return run

    monkeypatch.setattr(lp, "lp_pool", fake("pool", chain))
    monkeypatch.setattr(lp, "lp_ranges", fake("ranges", chain))
    monkeypatch.setattr(lp, "lp_position", fake("position", chain))
    monkeypatch.setattr(lp, "lp_positions", fake("positions", None))
    agent = _agent_ctx(stack)
    cases = [
        (
            "trading.lp.pool",
            {"target": "PEPE", "quote": "WETH"},
            {"target": "PEPE", "chainId": 8453, "quote": "WETH"},
        ),
        (
            "trading.lp.ranges",
            {"target": "PEPE", "chainId": 8453},
            {"target": "PEPE", "chainId": 8453},
        ),
        (
            "trading.lp.position",
            {"chainId": "base", "tokenId": "48213"},
            {"tokenId": "48213", "chainId": 8453},
        ),
        ("trading.lp.positions", {}, {}),
        (
            "trading.lp.positions",
            {"chainIds": [8453], "wallets": ["0x" + "11" * 20], "all": True, "budgetSeconds": 40},
            {"chainIds": [8453], "wallets": ["0x" + "11" * 20], "all": True, "budgetSeconds": 40.0},
        ),
    ]
    for method, params, echoed in cases:
        res = await call(method, params, agent)
        assert res.ok, (method, res.error)
        kind = method.rsplit(".", 1)[1]
        assert res.payload["request"] == {"kind": kind, "params": echoed}, method


async def test_add_takes_a_pair_and_a_fee_tier(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _lp_ready(ctx, stack)
    pool_id = str(lp.unilp().v4_pool.compute_pool_id(weth_usdc_key())).lower()
    for params in (
        {"token": "WETH/USDC", "feePct": "0.05"},
        {"token": "WETH", "quote": "USDC", "feePct": "0.05%"},
        {"token": "WETH", "quote": "USDC", "feePct": 500},
    ):
        res = await call(
            "trading.lp.add", {"chainId": 8453, "usd": 20, "range": "pct:10", **params}, ctx
        )
        assert res.ok, (params, res.error)
        pool = res.payload["order"]["plan"]["pool"]
        assert pool["poolId"] == pool_id and pool["feePct"] == "0.05%"
    res = await call(
        "trading.lp.add",
        {"chainId": 8453, "token": "WETH", "quote": "USDC", "feePct": "0.3", "usd": 20},
        ctx,
    )
    assert res.ok is False and res.error.code == "trading.lp.not_found"
    assert "tiers that exist: 0.05%" in res.error.message
    res = await call(
        "trading.lp.add", {"chainId": 8453, "token": "WETH", "feePct": "fast", "usd": 20}, ctx
    )
    assert res.ok is False and res.error.code == "trading.invalid"


async def test_collecting_nothing_needs_allow_empty(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _lp_ready(ctx, stack)
    res = await call("trading.lp.collect", {"chainId": 8453, "tokenId": POS_BARE}, ctx)
    assert res.ok is False and res.error.code == "trading.lp.nothing_to_collect"
    assert f"#{POS_BARE}" in res.error.message
    res = await call(
        "trading.lp.collect", {"chainId": 8453, "tokenId": POS_BARE, "allowEmpty": "yes"}, ctx
    )
    assert res.ok is False and "allowEmpty must be a boolean" in res.error.message
    assert stack["service"].list_orders()["orders"] == []
    res = await call(
        "trading.lp.collect", {"chainId": 8453, "tokenId": POS_BARE, "allowEmpty": True}, ctx
    )
    assert res.ok, res.error
    assert res.payload["order"]["kind"] == "lp_collect"
