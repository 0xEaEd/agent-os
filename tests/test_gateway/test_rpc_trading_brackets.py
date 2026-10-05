"""``trading.bracket.*``: an agent may propose and read a bracket; only the operator runs it.

Runs through the real dispatcher (the ``stack`` fixture of ``test_rpc_trading``)
with the service's ``bracket_*`` methods replaced by a recording fake, so the
RPC layer is tested on its own: who may call what, the params → kwargs
mapping, and the structural validation (``takeProfit`` required, exactly one
of ``stopLoss`` / ``trailPct``, one size at most, ``tpPct`` only with
``amountPct``, no size on an alert) answered as ``trading.bracket.invalid``
naming the field, before the engine is reached. One test runs the real engine
when it has brackets.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.trading.service import TradingError, TradingService
from tests.test_gateway.test_rpc_trading import (  # noqa: F401
    PASSWORD,
    _agent_ctx,
    call,
    ctx,
    stack,
)
from tests.test_trading.fakes import USDC

READS = ("trading.bracket.create", "trading.bracket.get", "trading.bracket.list")
WRITES = (
    "trading.bracket.approve",
    "trading.bracket.reject",
    "trading.bracket.pause",
    "trading.bracket.resume",
    "trading.bracket.stop",
    "trading.bracket.fire",
)
#: Lines either side of the fake WETH price (2000): it stays armed.
CREATE = {
    "chainId": 8453,
    "token": "WETH",
    "takeProfit": "3000",
    "stopLoss": "1000",
    "amountPct": 50,
}


class FakeBrackets:
    """The engine's ``bracket_*`` surface, recording every call and keeping a status."""

    METHODS = (
        "bracket_create",
        "bracket_get",
        "bracket_list",
        "bracket_approve",
        "bracket_reject",
        "bracket_pause",
        "bracket_resume",
        "bracket_stop",
        "bracket_fire_now",
    )

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.status: dict[str, str] = {}

    def install(self, service: TradingService, monkeypatch: pytest.MonkeyPatch) -> FakeBrackets:
        for name in self.METHODS:
            monkeypatch.setattr(service, name, getattr(self, name), raising=False)
        return self

    def _payload(self, bracket_id: str, **extra: Any) -> dict[str, Any]:
        if bracket_id not in self.status:
            raise TradingError("trading.bracket.not_found", f"no bracket {bracket_id}")
        return {
            "version": 1,
            "kind": "bracket",
            "warnings": [],
            "request": {"kind": "get", "params": {"bracketId": bracket_id}},
            "bracket": {"id": bracket_id, "status": self.status[bracket_id]},
            **extra,
        }

    def _move(self, bracket_id: str, frm: tuple[str, ...], to: str, op: str) -> dict[str, Any]:
        status = self.status.get(bracket_id)
        if status is None:
            raise TradingError("trading.bracket.not_found", f"no bracket {bracket_id}")
        if status not in frm:
            raise TradingError("trading.bracket.bad_state", f"cannot {op} bracket: it is {status}")
        self.status[bracket_id] = to
        return self._payload(bracket_id)

    async def bracket_create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("create", kwargs))
        bracket_id = f"brk_{len(self.status) + 1:08x}"
        agent = kwargs["initiator"] == "agent"
        self.status[bracket_id] = "awaiting_approval" if agent else "armed"
        payload = self._payload(bracket_id)
        payload["bracket"].update(initiator=kwargs["initiator"], sessionKey=kwargs["session_key"])
        return payload

    async def bracket_get(self, bracket_id: str) -> dict[str, Any]:
        self.calls.append(("get", {"bracket_id": bracket_id}))
        return self._payload(bracket_id)

    async def bracket_list(self, *, all: bool = False, wallet: str | None = None) -> dict[str, Any]:
        self.calls.append(("list", {"all": all, "wallet": wallet}))
        params: dict[str, Any] = {"all": True} if all else {}
        return {
            "version": 1,
            "kind": "brackets",
            "request": {"kind": "list", "params": params},
            "brackets": [{"id": k, "status": v} for k, v in self.status.items()],
            "totals": {"count": len(self.status)},
        }

    async def bracket_approve(self, bracket_id: str) -> dict[str, Any]:
        self.calls.append(("approve", {"bracket_id": bracket_id}))
        return self._move(bracket_id, ("awaiting_approval",), "armed", "approve")

    async def bracket_reject(self, bracket_id: str, reason: str | None = None) -> dict[str, Any]:
        self.calls.append(("reject", {"bracket_id": bracket_id, "reason": reason}))
        return self._move(bracket_id, ("awaiting_approval",), "rejected", "reject")

    async def bracket_pause(self, bracket_id: str) -> dict[str, Any]:
        self.calls.append(("pause", {"bracket_id": bracket_id}))
        return self._move(bracket_id, ("armed",), "paused", "pause")

    async def bracket_resume(self, bracket_id: str) -> dict[str, Any]:
        self.calls.append(("resume", {"bracket_id": bracket_id}))
        return self._move(bracket_id, ("paused",), "armed", "resume")

    async def bracket_stop(self, bracket_id: str, reason: str | None = None) -> dict[str, Any]:
        self.calls.append(("stop", {"bracket_id": bracket_id, "reason": reason}))
        live = ("awaiting_approval", "armed", "paused", "triggered")
        return self._move(bracket_id, live, "stopped", "stop")

    async def bracket_fire_now(
        self, bracket_id: str, *, leg: str | None = None, wait: bool = False
    ) -> dict[str, Any]:
        self.calls.append(("fire", {"bracket_id": bracket_id, "leg": leg, "wait": wait}))
        payload = self._move(bracket_id, ("armed", "paused"), "triggered", "fire")
        payload["fire"] = {"n": 1, "status": "pending", "orderId": "ord_1"}
        return payload


@pytest.fixture
def fake(stack: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> FakeBrackets:  # noqa: F811
    return FakeBrackets().install(stack["service"], monkeypatch)


async def _ready(ctx: RpcContext, stack: dict[str, Any]) -> str:  # noqa: F811
    await call("wallet.setup", {"password": PASSWORD}, ctx)
    address = (await call("wallet.create", {"label": "Main"}, ctx)).payload["wallet"]["address"]
    stack["base"].set_native(address, 10**18)
    stack["base"].set_erc20(USDC, address, 1_000 * 10**6)
    return str(address)


def test_methods_are_registered() -> None:
    assert set(READS + WRITES) <= set(get_dispatcher().methods())


async def test_agent_proposes_reads_and_cannot_write(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    fake: FakeBrackets,
) -> None:
    agent = _agent_ctx(stack, "agent:trading:desk:1")
    # The agent may call itself "manual" and name another chat: neither is honoured.
    res = await call(
        "trading.bracket.create",
        {**CREATE, "initiator": "manual", "sessionKey": "other"},
        agent,
    )
    assert res.ok, res.error
    bracket = res.payload["bracket"]
    assert bracket["status"] == "awaiting_approval"
    assert bracket["initiator"] == "agent" and bracket["sessionKey"] == "agent:trading:desk:1"
    bracket_id = bracket["id"]

    got = await call("trading.bracket.get", {"bracketId": bracket_id}, agent)
    assert got.ok and got.payload["bracket"]["status"] == "awaiting_approval"
    listed = await call("trading.bracket.list", {}, agent)
    assert listed.ok and [b["id"] for b in listed.payload["brackets"]] == [bracket_id]

    before = len(fake.calls)
    for method in WRITES:
        denied = await call(method, {"bracketId": bracket_id}, agent)
        assert denied.ok is False, method
        assert denied.error.code == "trading.operator_required", method
        assert method.split(".")[-1] in denied.error.message
    assert len(fake.calls) == before  # refused before the engine is reached

    approved = await call("trading.bracket.approve", {"bracketId": bracket_id}, ctx)
    assert approved.ok, approved.error
    assert approved.payload["bracket"]["status"] == "armed"
    assert fake.calls[-1] == ("approve", {"bracket_id": bracket_id})


async def test_create_forwards_params_as_kwargs(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    fake: FakeBrackets,
) -> None:
    """Lines go through untouched (the engine resolves ``+20%``); ``amount`` as text."""
    res = await call(
        "trading.bracket.create",
        {
            "chain": "base",
            "kind": "SELL",
            "token": " WETH ",
            "takeProfit": " +20% ",
            "stopLoss": "-10%",
            "amountPct": "60",
            "tpPct": 30,
            "quote": "USDC",
            "wallet": "Main",
            "slippagePct": "1",
            "name": "  Guard\u202e   ETH\n",
            "validForSeconds": "86400",
            "sessionKey": "agent:main:cli",
        },
        ctx,
    )
    assert res.ok, res.error
    _, kwargs = fake.calls[-1]
    chain = kwargs.pop("chain")
    assert chain.chain_id == 8453
    assert kwargs == {
        "kind": "sell",
        "token": "WETH",
        "quote": "USDC",
        "take_profit": "+20%",
        "stop_loss": "-10%",
        "trail_pct": None,
        "amount_usd": None,
        "amount_pct": 60.0,
        "amount": None,
        "tp_pct": 30.0,
        "wallet": "Main",
        "slippage_pct": 1.0,
        "name": "Guard ETH",
        "valid_for_seconds": 86_400,
        "initiator": "manual",
        "session_key": "agent:main:cli",
    }

    # Defaults: kind sell, no size (the engine makes it 100 %), numbers as numbers.
    res = await call(
        "trading.bracket.create",
        {"chainId": 8453, "token": "WETH", "takeProfit": 3000.5, "trailPct": 10},
        ctx,
    )
    assert res.ok, res.error
    _, kwargs = fake.calls[-1]
    assert kwargs["kind"] == "sell" and kwargs["take_profit"] == 3000.5
    assert kwargs["stop_loss"] is None and kwargs["trail_pct"] == 10.0
    assert kwargs["amount_pct"] is None and kwargs["amount"] is None
    assert kwargs["amount_usd"] is None and kwargs["tp_pct"] is None

    # tpPct with no size: the engine defaults the size to 100 %.
    res = await call("trading.bracket.create", {**CREATE, "amountPct": None, "tpPct": 50}, ctx)
    assert res.ok, res.error
    assert fake.calls[-1][1]["tp_pct"] == 50.0 and fake.calls[-1][1]["amount_pct"] is None

    res = await call(
        "trading.bracket.create",
        {**CREATE, "amountPct": None, "amount": 0.00001},
        ctx,
    )
    assert res.ok, res.error
    assert fake.calls[-1][1]["amount"] == "0.00001"

    res = await call("trading.bracket.create", {**CREATE, "amountPct": None, "amountUsd": 25}, ctx)
    assert res.ok, res.error
    assert fake.calls[-1][1]["amount_usd"] == 25.0

    res = await call(
        "trading.bracket.create",
        {"chainId": 8453, "kind": "alert", "token": "WETH", "takeProfit": 3000, "stopLoss": 1000},
        ctx,
    )
    assert res.ok, res.error
    assert fake.calls[-1][1]["kind"] == "alert"

    agent = _agent_ctx(stack, "agent:trading:desk:2")
    res = await call("trading.bracket.create", {**CREATE, "initiator": "manual"}, agent)
    assert res.ok, res.error
    assert fake.calls[-1][1]["initiator"] == "agent"
    assert fake.calls[-1][1]["session_key"] == "agent:trading:desk:2"


async def test_operator_lifecycle_forwards_ids_and_options(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    fake: FakeBrackets,
) -> None:
    bracket_id = (await call("trading.bracket.create", CREATE, ctx)).payload["bracket"]["id"]
    assert fake.status[bracket_id] == "armed"

    paused = await call("trading.bracket.pause", {"bracketId": f" {bracket_id} "}, ctx)
    assert paused.ok and paused.payload["bracket"]["status"] == "paused"
    again = await call("trading.bracket.pause", {"bracketId": bracket_id}, ctx)
    assert again.ok is False and again.error.code == "trading.bracket.bad_state"
    resumed = await call("trading.bracket.resume", {"bracketId": bracket_id}, ctx)
    assert resumed.ok and resumed.payload["bracket"]["status"] == "armed"

    fired = await call(
        "trading.bracket.fire", {"bracketId": bracket_id, "leg": "TP", "wait": True}, ctx
    )
    assert fired.ok, fired.error
    assert fired.payload["fire"]["orderId"] == "ord_1"
    assert fake.calls[-1] == ("fire", {"bracket_id": bracket_id, "leg": "tp", "wait": True})

    stopped = await call(
        "trading.bracket.stop", {"bracketId": bracket_id, "reason": "too\ntight"}, ctx
    )
    assert stopped.ok and stopped.payload["bracket"]["status"] == "stopped"
    assert fake.calls[-1] == ("stop", {"bracket_id": bracket_id, "reason": "too tight"})

    other = (await call("trading.bracket.create", CREATE, ctx)).payload["bracket"]["id"]
    res = await call("trading.bracket.fire", {"bracketId": other}, ctx)
    assert res.ok, res.error
    assert fake.calls[-1] == ("fire", {"bracket_id": other, "leg": None, "wait": False})

    res = await call("trading.bracket.list", {"all": True, "wallet": "Main"}, ctx)
    assert res.ok and res.payload["kind"] == "brackets"
    assert fake.calls[-1] == ("list", {"all": True, "wallet": "Main"})
    res = await call("trading.bracket.list", None, ctx)
    assert res.ok and fake.calls[-1] == ("list", {"all": False, "wallet": None})

    agent = _agent_ctx(stack)
    proposed = (await call("trading.bracket.create", CREATE, agent)).payload["bracket"]["id"]
    rejected = await call(
        "trading.bracket.reject", {"bracketId": proposed, "reason": "no\u202e"}, ctx
    )
    assert rejected.ok and rejected.payload["bracket"]["status"] == "rejected"
    assert fake.calls[-1] == ("reject", {"bracket_id": proposed, "reason": "no"})


async def test_engine_codes_pass_through(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
    fake: FakeBrackets,
) -> None:
    res = await call("trading.bracket.get", {"bracketId": "brk_00000000"}, ctx)
    assert res.ok is False and res.error.code == "trading.bracket.not_found"
    bracket_id = (await call("trading.bracket.create", CREATE, ctx)).payload["bracket"]["id"]
    res = await call("trading.bracket.approve", {"bracketId": bracket_id}, ctx)
    assert res.ok is False and res.error.code == "trading.bracket.bad_state"
    res = await call("trading.bracket.create", {**CREATE, "chainId": 1}, ctx)
    assert res.ok is False and res.error.code == "trading.unsupported_chain"


SELL = {k: v for k, v in CREATE.items() if k != "amountPct"}
ALERT = {**SELL, "kind": "alert"}


@pytest.mark.parametrize(
    ("method", "params", "field"),
    [
        ("trading.bracket.create", {**CREATE, "chainId": None}, "chainId"),
        ("trading.bracket.create", {**CREATE, "token": None}, "token"),
        ("trading.bracket.create", {**CREATE, "token": 7}, "token"),
        ("trading.bracket.create", {**CREATE, "kind": "buy"}, "kind"),
        ("trading.bracket.create", {**CREATE, "kind": 3}, "kind"),
        ("trading.bracket.create", {**CREATE, "takeProfit": None}, "takeProfit"),
        ("trading.bracket.create", {**CREATE, "takeProfit": "  "}, "takeProfit"),
        ("trading.bracket.create", {**CREATE, "takeProfit": True}, "takeProfit"),
        ("trading.bracket.create", {**CREATE, "takeProfit": [3000]}, "takeProfit"),
        ("trading.bracket.create", {**CREATE, "takeProfit": float("inf")}, "takeProfit"),
        ("trading.bracket.create", {**CREATE, "stopLoss": None}, "params.stopLoss or"),
        ("trading.bracket.create", {**CREATE, "stopLoss": {"p": 1}}, "stopLoss"),
        ("trading.bracket.create", {**CREATE, "trailPct": 10}, "exclude each other"),
        ("trading.bracket.create", {**SELL, "stopLoss": None, "trailPct": "x"}, "trailPct"),
        ("trading.bracket.create", {**CREATE, "amountUsd": 10}, "one size at most"),
        ("trading.bracket.create", {**SELL, "amount": "1", "amountUsd": 10}, "one size at most"),
        ("trading.bracket.create", {**CREATE, "amountPct": "half"}, "amountPct"),
        ("trading.bracket.create", {**SELL, "amount": True}, "amount"),
        ("trading.bracket.create", {**SELL, "amountUsd": 10, "tpPct": 50}, "tpPct"),
        ("trading.bracket.create", {**SELL, "amount": "1", "tpPct": 50}, "tpPct"),
        ("trading.bracket.create", {**CREATE, "tpPct": "some"}, "tpPct"),
        ("trading.bracket.create", {**ALERT, "amountPct": 50}, "amountPct"),
        ("trading.bracket.create", {**ALERT, "amountUsd": 5}, "amountUsd"),
        ("trading.bracket.create", {**ALERT, "tpPct": 50}, "tpPct"),
        ("trading.bracket.create", {**CREATE, "validForSeconds": 59}, "validForSeconds"),
        ("trading.bracket.create", {**CREATE, "validForSeconds": "1d"}, "validForSeconds"),
        ("trading.bracket.create", {**CREATE, "slippagePct": "nan"}, "slippagePct"),
        ("trading.bracket.create", {**CREATE, "initiator": "robot"}, "initiator"),
        ("trading.bracket.create", {**CREATE, "quote": 5}, "quote"),
        ("trading.bracket.get", {}, "bracketId"),
        ("trading.bracket.get", {"bracketId": 7}, "bracketId"),
        ("trading.bracket.list", {"all": "yes"}, "all"),
        ("trading.bracket.list", {"wallet": 1}, "wallet"),
        ("trading.bracket.approve", {"bracketId": " "}, "bracketId"),
        ("trading.bracket.reject", {}, "bracketId"),
        ("trading.bracket.pause", {"bracketId": None}, "bracketId"),
        ("trading.bracket.resume", {"bracketId": ""}, "bracketId"),
        ("trading.bracket.stop", {"bracketId": 7}, "bracketId"),
        ("trading.bracket.fire", {"bracketId": "brk_1", "leg": "both"}, "leg"),
        ("trading.bracket.fire", {"bracketId": "brk_1", "wait": 1}, "wait"),
    ],
)
async def test_bad_params_are_bracket_invalid_naming_the_field(
    ctx: RpcContext,  # noqa: F811
    fake: FakeBrackets,
    method: str,
    params: dict[str, Any],
    field: str,
) -> None:
    res = await call(method, params, ctx)
    assert res.ok is False, (method, params)
    assert res.error.code == "trading.bracket.invalid", res.error
    assert field in res.error.message, res.error.message
    assert fake.calls == []  # the engine never saw it


@pytest.mark.skipif(
    not hasattr(TradingService, "bracket_create"), reason="the engine has no brackets yet"
)
async def test_real_engine_agent_proposes_and_the_operator_arms(
    ctx: RpcContext,  # noqa: F811
    stack: dict[str, Any],  # noqa: F811
) -> None:
    await _ready(ctx, stack)
    agent = _agent_ctx(stack, "agent:trading:desk:1")
    res = await call("trading.bracket.create", CREATE, agent)
    assert res.ok, res.error
    payload = res.payload
    assert payload["kind"] == "bracket" and payload["version"] == 1
    bracket = payload["bracket"]
    assert bracket["id"].startswith("brk_") and bracket["status"] == "awaiting_approval"
    assert bracket["takeProfit"]["status"] == bracket["stopLoss"]["status"]
    assert bracket["initiator"] == "agent"
    for method in WRITES:
        denied = await call(method, {"bracketId": bracket["id"]}, agent)
        assert denied.ok is False and denied.error.code == "trading.operator_required", method
    listed = await call("trading.bracket.list", {}, agent)
    assert listed.ok and [b["id"] for b in listed.payload["brackets"]] == [bracket["id"]]
    triggers = await call("trading.trigger.list", {"all": True}, agent)
    assert triggers.ok and triggers.payload["triggers"] == []  # legs are not listed
    approved = await call("trading.bracket.approve", {"bracketId": bracket["id"]}, ctx)
    assert approved.ok, approved.error
    assert approved.payload["bracket"]["status"] == "armed"
