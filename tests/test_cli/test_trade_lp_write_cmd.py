"""``agentos trade lp collect|remove|add``: flags → RPC params, ``--wait``, the card after.

Output is the order JSON (``{"order": …}``) like ``trade revoke``; a *confirmed*
order under ``--json`` is followed by the refreshed position card, written to
``lp-cards/`` and announced by the publish marker as the last stdout line.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agentos.cli import trade_cmd
from agentos.tools.builtin.artifacts import INLINE_ARTIFACT_MARKER_RE

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lp_cards"
WALLET = "0x1111111111111111111111111111111111111111"


def _order(kind: str, status: str, **extra: Any) -> dict[str, Any]:
    op = kind.removeprefix("lp_")
    plan = {
        "op": op,
        "tokenId": "48213" if op != "add" else None,
        "burn": op == "remove",
        "pct": 100.0 if op == "remove" else None,
        "token": {"symbol": "PEPE"},
        "quote": {"symbol": "WETH"},
        "range": {"tickLower": 1, "tickUpper": 2, "priceLower": 1.0, "priceUpper": 2.0},
        "pool": {"poolId": "0xpool", "feePct": "1%"},
        "expected": {"base": {"raw": "5", "human": "5"}, "quote": {"raw": "0", "human": "0"}},
        "bounds": {"base": "4", "quote": "0"},
        "approvals": [],
        "simulation": {"ok": True, "method": "eth_simulateV1"},
        "planHash": "0x1a2b3c4d",
    }
    return {
        "orderId": "ord-1",
        "kind": kind,
        "status": status,
        "chainId": 8453,
        "wallet": WALLET,
        "tokenIn": {"symbol": "PEPE"},
        "tokenOut": {"symbol": "WETH"},
        "plan": plan,
        "tokenId": plan["tokenId"],
        **extra,
    }


class _Client:
    def __init__(self, kind: str = "lp_collect", final: str = "confirmed") -> None:
        self.kind = kind
        self.final = final
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, params: dict | None = None) -> Any:
        self.calls.append((method, dict(params or {})))
        if method in ("trading.lp.collect", "trading.lp.remove", "trading.lp.add"):
            return {"order": _order(self.kind, "awaiting_approval")}
        if method == "trading.orders.wait":
            extra = {"tokenId": "9001"} if self.kind == "lp_add" else {}
            return {"order": _order(self.kind, self.final, txHash="0xabc", **extra)}
        kind = method.rsplit(".", 1)[1]
        return json.loads((FIXTURES / f"{kind}.json").read_text(encoding="utf-8"))


def _use(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: _Client) -> _Client:
    monkeypatch.setattr(
        trade_cmd, "run_gateway_sync", lambda action, **kw: asyncio.run(action(client))
    )
    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.delenv("AGENTOS_SESSION_KEY", raising=False)
    monkeypatch.delenv("AGENTOS_AGENT", raising=False)
    monkeypatch.setattr(trade_cmd.console, "_width", 220)
    monkeypatch.chdir(tmp_path)
    return client


@pytest.mark.parametrize(
    ("args", "method", "params"),
    [
        (
            ["collect", "#48213", "--chain", "base", "--note", "claim", "--client-id", "k1"],
            "trading.lp.collect",
            {"chainId": 8453, "tokenId": "48213", "note": "claim", "clientOrderId": "k1"},
        ),
        (
            ["collect", "48213", "--chain", "base", "--allow-empty"],
            "trading.lp.collect",
            {"chainId": 8453, "tokenId": "48213", "allowEmpty": True},
        ),
        (
            ["remove", "48213", "--chain", "robinhood"],
            "trading.lp.remove",
            {"chainId": 4663, "tokenId": "48213", "pct": 100.0},
        ),
        (
            ["remove", "48213", "--chain", "base", "--pct", "50", "--slippage", "2"],
            "trading.lp.remove",
            {"chainId": 8453, "tokenId": "48213", "pct": 50.0, "slippagePct": 2.0},
        ),
        (
            ["add", "PEPE", "--chain", "base", "--usd", "50", "--range", "mcap:2M-10M"],
            "trading.lp.add",
            {"chainId": 8453, "token": "PEPE", "usd": 50.0, "range": "mcap:2M-10M"},
        ),
        (
            [
                "add",
                "0xpool",
                "--chain",
                "base",
                "--amount-base",
                "1000",
                "--amount-quote",
                "0.1",
                "--wallet",
                "Main",
                "--slippage",
                "1.5",
            ],
            "trading.lp.add",
            {
                "chainId": 8453,
                "token": "0xpool",
                "amountBase": "1000",
                "amountQuote": "0.1",
                "wallet": "Main",
                "slippagePct": 1.5,
            },
        ),
        (
            ["add", "ETH", "--quote", "USDC", "--fee", "0.05", "--chain", "base", "--usd", "5"],
            "trading.lp.add",
            {"chainId": 8453, "token": "ETH", "quote": "USDC", "feePct": "0.05", "usd": 5.0},
        ),
        (
            ["add", "ETH/USDC", "--fee", "500", "--chain", "base", "--usd", "5"],
            "trading.lp.add",
            {"chainId": 8453, "token": "ETH/USDC", "feePct": "500", "usd": 5.0},
        ),
        (
            ["add", "--chain", "base", "--usd", "5", "--to-position", "#48213"],
            "trading.lp.add",
            {"chainId": 8453, "usd": 5.0, "toPosition": "48213"},
        ),
    ],
)
def test_flags_become_rpc_params(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    method: str,
    params: dict[str, Any],
) -> None:
    client = _use(monkeypatch, tmp_path, _Client())
    result = runner.invoke(trade_cmd.app, ["lp", *args, "--json"])
    assert result.exit_code == 0, result.output
    assert client.calls == [(method, {**params, "initiator": "manual"})]
    assert json.loads(result.stdout)["order"]["status"] == "awaiting_approval"
    assert "publish_artifact" not in result.stdout  # nothing confirmed yet: no card


@pytest.mark.parametrize(
    ("kind", "args", "card_method", "card_params"),
    [
        (
            "lp_collect",
            ["collect", "48213", "--chain", "base"],
            "trading.lp.position",
            {"chainId": 8453, "tokenId": "48213"},
        ),
        (
            "lp_remove",
            ["remove", "48213", "--chain", "base"],
            "trading.lp.positions",
            {"chainId": 8453, "wallets": [WALLET]},
        ),
        (
            "lp_add",
            ["add", "PEPE", "--chain", "base", "--usd", "5"],
            "trading.lp.position",
            {"chainId": 8453, "tokenId": "9001"},
        ),
    ],
)
def test_wait_then_the_refreshed_card_is_the_last_line(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
    args: list[str],
    card_method: str,
    card_params: dict[str, Any],
) -> None:
    client = _use(monkeypatch, tmp_path, _Client(kind))
    result = runner.invoke(trade_cmd.app, ["lp", *args, "--wait", "--wait-seconds", "60", "--json"])
    assert result.exit_code == 0, result.output
    methods = [m for m, _ in client.calls]
    assert methods[1] == "trading.orders.wait"
    assert client.calls[1][1] == {"orderId": "ord-1", "timeoutSeconds": 60}
    assert client.calls[2] == (card_method, card_params)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    order = json.loads("\n".join(lines[:-1]))["order"]
    assert order["status"] == "confirmed"
    marker = INLINE_ARTIFACT_MARKER_RE.search(lines[-1])
    assert marker is not None and lines[-1] == marker.group(0)
    card = json.loads((tmp_path / marker.group("path")).read_text(encoding="utf-8"))
    assert card["kind"] == card_method.rsplit(".", 1)[1]


def test_no_card_after_a_failed_order_or_with_no_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _use(monkeypatch, tmp_path, _Client("lp_collect", final="failed"))
    result = runner.invoke(
        trade_cmd.app, ["lp", "collect", "48213", "--chain", "base", "--wait", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert "publish_artifact" not in result.stdout
    assert [m for m, _ in client.calls] == ["trading.lp.collect", "trading.orders.wait"]
    client = _use(monkeypatch, tmp_path, _Client("lp_collect"))
    result = runner.invoke(
        trade_cmd.app,
        ["lp", "collect", "48213", "--chain", "base", "--wait", "--json", "--no-card"],
    )
    assert result.exit_code == 0 and "publish_artifact" not in result.stdout
    assert not (tmp_path / trade_cmd.LP_CARD_DIR).exists()


def test_human_output_describes_the_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _use(monkeypatch, tmp_path, _Client("lp_remove"))
    result = runner.invoke(trade_cmd.app, ["lp", "remove", "48213", "--chain", "base"])
    assert result.exit_code == 0, result.output
    assert "Remove liquidity · #48213 · 100%" in result.stdout
    assert "agentos trade approve ord-1" in result.stdout
    assert "0x1a2b3c4d" in result.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["collect", "abc", "--chain", "base"], "tokenId"),
        (["collect", "1"], "Missing option '--chain'"),
        (["remove", "1", "--chain", "base", "--pct", "0"], "--pct"),
        (["add", "PEPE", "--chain", "base"], "--usd"),
        (["add", "PEPE", "--chain", "base", "--usd", "5", "--amount-base", "1"], "not both"),
        (["add", "--chain", "base", "--usd", "5"], "--to-position"),
        (
            ["add", "--chain", "base", "--usd", "5", "--to-position", "1", "--range", "full"],
            "--range",
        ),
    ],
)
def test_usage_errors_exit_2_as_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str], message: str
) -> None:
    client = _use(monkeypatch, tmp_path, _Client())
    result = runner.invoke(trade_cmd.app, ["lp", *args, "--json"])
    assert result.exit_code == 2 and client.calls == []
    error = json.loads(result.stderr)["error"]
    assert message in error["message"]


@pytest.mark.parametrize(
    ("code", "exit_code"),
    [
        ("trading.lp.range_invalid", 2),
        ("trading.lp.not_owner", 2),
        ("trading.lp.position_closed", 2),
        ("trading.lp.nothing_to_collect", 2),
        ("trading.invalid", 2),
    ],
)
def test_engine_input_errors_exit_2_with_their_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: str, exit_code: int
) -> None:
    from agentos.cli.gateway_client import GatewayRPCError

    client = _use(monkeypatch, tmp_path, _Client())

    async def call(method: str, params: dict | None = None) -> Any:
        raise GatewayRPCError(method, code=code, message="nope")

    monkeypatch.setattr(client, "call", call)
    result = runner.invoke(
        trade_cmd.app, ["lp", "add", "PEPE", "--chain", "base", "--usd", "5", "--json"]
    )
    assert result.exit_code == exit_code
    assert json.loads(result.stderr)["error"]["code"] == code


def test_orders_kind_accepts_lp_kinds_and_legs_read_well(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _use(monkeypatch, tmp_path, _Client())

    async def call(method: str, params: dict | None = None) -> Any:
        client.calls.append((method, dict(params or {})))
        return {"orders": [_order("lp_add", "awaiting_approval")], "pendingApprovals": 1}

    monkeypatch.setattr(client, "call", call)
    result = runner.invoke(trade_cmd.app, ["orders", "--kind", "lp_add"])
    assert result.exit_code == 0, result.output
    assert client.calls[0][1]["kind"] == "lp_add"
    assert "Add liquidity · PEPE/WETH" in result.stdout
    assert trade_cmd._order_legs(_order("lp_collect", "confirmed")) == "Collect fees · #48213"
    bad = runner.invoke(trade_cmd.app, ["orders", "--kind", "lp_swap", "--json"])
    assert bad.exit_code == 2
