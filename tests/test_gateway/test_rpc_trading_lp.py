"""``trading.lp.*``: registered, read-only on the agent surface, params reach the engine."""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.trading import lp
from tests.test_gateway.test_rpc_trading import _agent_ctx, call, ctx, stack  # noqa: F401

METHODS = ("trading.lp.pool", "trading.lp.ranges", "trading.lp.position", "trading.lp.positions")


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake(kind: str):
        async def run(service: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append((kind, kwargs))
            return {"version": 1, "kind": kind}

        return run

    for kind in ("pool", "ranges", "position", "positions"):
        monkeypatch.setattr(lp, f"lp_{kind}", fake(kind))
    return calls


def test_methods_are_registered() -> None:
    assert set(METHODS) <= set(get_dispatcher().methods())


async def test_agent_may_read_every_lp_method(
    stack: dict[str, Any],  # noqa: F811
    seen: list[tuple[str, dict[str, Any]]],
) -> None:
    agent = _agent_ctx(stack)
    for method, params in [
        ("trading.lp.pool", {"chainId": 8453, "target": "PEPE", "quote": "WETH"}),
        ("trading.lp.ranges", {"target": "0x" + "ab" * 32}),
        ("trading.lp.position", {"chainId": "robinhood", "tokenId": "48213"}),
        ("trading.lp.positions", {"wallets": ["0x" + "11" * 20], "all": True}),
    ]:
        res = await call(method, params, agent)
        assert res.ok, (method, res.error)
        assert res.payload["kind"] == method.rsplit(".", 1)[1]
    pool, ranges, position, positions = (kwargs for _, kwargs in seen)
    assert pool["chain"].chain_id == 8453 and pool["target"] == "PEPE" and pool["quote"] == "WETH"
    assert ranges["chain"] is None and ranges["target"] == "0x" + "ab" * 32
    assert position["chain"].chain_id == 4663 and position["token_id"] == 48213
    assert positions == {
        "chains": None,
        "wallets": ["0x" + "11" * 20],
        "include_closed": True,
        "budget_s": None,
    }


async def test_param_validation(
    ctx: RpcContext,  # noqa: F811
    seen: list[tuple[str, dict[str, Any]]],
) -> None:
    for method, params in [
        ("trading.lp.pool", {}),
        ("trading.lp.position", {"tokenId": "1"}),
        ("trading.lp.position", {"chainId": 8453, "tokenId": "abc"}),
        ("trading.lp.position", {"chainId": 8453, "tokenId": 0}),
        ("trading.lp.positions", {"wallets": "0x11"}),
        ("trading.lp.positions", {"all": "yes"}),
        ("trading.lp.positions", {"chainIds": "base"}),
        ("trading.lp.positions", {"chainIds": []}),
        ("trading.lp.positions", {"chainIds": [8453, 1]}),
        ("trading.lp.positions", {"chainId": 8453, "chainIds": [4663]}),
        ("trading.lp.positions", {"budgetSeconds": "fast"}),
        ("trading.lp.ranges", {"chainId": 1, "target": "PEPE"}),
    ]:
        res = await call(method, params, ctx)
        assert res.ok is False, (method, params)
    assert seen == []


async def test_engine_errors_keep_their_code(
    ctx: RpcContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.trading.service import TradingError

    async def missing(service: Any, **kwargs: Any) -> dict[str, Any]:
        raise TradingError("trading.lp.not_found", "no Uniswap V4 pool holds it")

    monkeypatch.setattr(lp, "lp_pool", missing)
    res = await call("trading.lp.pool", {"chainId": 8453, "target": "0x" + "12" * 20}, ctx)
    assert res.ok is False and res.error.code == "trading.lp.not_found"


async def test_positions_take_several_chains_and_a_budget(
    ctx: RpcContext,  # noqa: F811
    seen: list[tuple[str, dict[str, Any]]],
) -> None:
    res = await call(
        "trading.lp.positions",
        {"chainIds": [8453, "robinhood", 8453], "budgetSeconds": 40},
        ctx,
    )
    assert res.ok, res.error
    ((_, kwargs),) = seen
    assert [c.key for c in kwargs["chains"]] == ["base", "robinhood"]
    assert kwargs["budget_s"] == 40.0
