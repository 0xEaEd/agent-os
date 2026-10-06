"""``trading.markets``: registered, read-only on the agent surface, params reach the engine."""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.trading import markets
from tests.test_gateway.test_rpc_trading import _agent_ctx, call, ctx, stack  # noqa: F401

NVDA = "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake(service: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"version": 1, "kind": "markets", "token": {"address": NVDA}}

    monkeypatch.setattr(markets, "markets", fake)
    return calls


def test_method_is_registered() -> None:
    assert "trading.markets" in set(get_dispatcher().methods())


async def test_agent_may_read_markets_with_the_documented_defaults(
    stack: dict[str, Any],  # noqa: F811
    seen: list[dict[str, Any]],
) -> None:
    res = await call("trading.markets", {"target": "NVDA"}, _agent_ctx(stack))
    assert res.ok, res.error
    ((kwargs),) = seen
    assert kwargs["chain"].chain_id == 4663
    assert kwargs["target"] == "NVDA"
    assert kwargs["side"] == "all"
    assert kwargs["min_tvl_usd"] == 10_000.0
    assert kwargs["limit"] == 50
    assert kwargs["lookalikes"] is False and kwargs["deep"] is False
    # The echo names the resolved address, so a refresh cannot turn ambiguous.
    assert res.payload["request"] == {
        "kind": "markets",
        "params": {
            "target": NVDA,
            "chainId": 4663,
            "side": "all",
            "minTvlUsd": 10_000.0,
            "limit": 50,
            "lookalikes": False,
            "deep": False,
        },
    }


async def test_params_reach_the_engine(
    ctx: RpcContext,  # noqa: F811
    seen: list[dict[str, Any]],
) -> None:
    res = await call(
        "trading.markets",
        {
            "token": "0x" + "ab" * 20,
            "chainId": 8453,
            "side": "QUOTE",
            "minTvlUsd": "0",
            "limit": 200,
            "lookalikes": True,
            "deep": True,
        },
        ctx,
    )
    assert res.ok, res.error
    ((kwargs),) = seen
    assert kwargs["chain"].chain_id == 8453 and kwargs["target"] == "0x" + "ab" * 20
    assert kwargs["side"] == "quote" and kwargs["min_tvl_usd"] == 0.0 and kwargs["limit"] == 200
    assert kwargs["lookalikes"] is True and kwargs["deep"] is True


async def test_param_validation(
    ctx: RpcContext,  # noqa: F811
    seen: list[dict[str, Any]],
) -> None:
    for params in [
        {},
        {"target": "NVDA", "side": "both"},
        {"target": "NVDA", "minTvlUsd": -1},
        {"target": "NVDA", "minTvlUsd": "lots"},
        {"target": "NVDA", "minTvlUsd": True},
        {"target": "NVDA", "limit": 0},
        {"target": "NVDA", "limit": 201},
        {"target": "NVDA", "limit": "many"},
        {"target": "NVDA", "lookalikes": "yes"},
        {"target": "NVDA", "deep": 1},
    ]:
        res = await call("trading.markets", params, ctx)
        assert res.ok is False, params
        assert res.error.code == "trading.invalid", (params, res.error)
    res = await call("trading.markets", {"target": "NVDA", "chainId": 1}, ctx)
    assert res.ok is False and res.error.code == "trading.unsupported_chain"
    assert seen == []


async def test_engine_errors_keep_their_code(
    ctx: RpcContext,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.trading.service import TradingError

    async def down(service: Any, **kwargs: Any) -> dict[str, Any]:
        raise TradingError("trading.markets.unavailable", "GeckoTerminal did not answer")

    monkeypatch.setattr(markets, "markets", down)
    res = await call("trading.markets", {"target": "NVDA"}, ctx)
    assert res.ok is False and res.error.code == "trading.markets.unavailable"
