"""``agentos trade markets``: RPC params, the card file, the marker, and the human tables."""

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
NVDA = "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"


def _payload() -> dict[str, Any]:
    row = {
        "poolAddress": "0xcbdf",
        "dex": {"id": "bankr-robinhood", "label": "Bankr", "version": None},
        "launcher": "Bankr",
        "viaUniswap": False,
        "feePct": None,
        "counterparty": {"symbol": "AI", "stockToken": False, "lookalike": False},
        "tvlUsd": 4_732_293.0,
        "volume24hUsd": 806_473.0,
        "priceUsd": 0.1131,
        "priceInToken": 0.000471,
        "createdAt": "2026-07-25T00:52:36Z",
    }
    base = {
        **row,
        "pair": "NVDA/USDG",
        "side": "base",
        "dex": {"id": "uniswap-v4-robinhood", "label": "Uniswap", "version": "v4"},
        "launcher": None,
        "viaUniswap": True,
        "feePct": 0.01,
        "counterparty": {"symbol": "USDG", "stockToken": False, "lookalike": False},
        "priceUsd": 240.30,
        "priceInToken": 234.1,
    }
    return {
        "version": 1,
        "kind": "markets",
        "chain": {"id": 4663, "key": "robinhood", "name": "Robinhood Chain"},
        "partial": True,
        "warnings": ["GeckoTerminal rate limit: showing the first 100 pools"],
        "token": {
            "address": NVDA,
            "symbol": "NVDA",
            "name": "NVIDIA • Robinhood Token",
            "priceUsd": 240.30,
            "stockToken": True,
            "oracle": {"usd": 239.74, "stale": False, "paused": False},
        },
        "counts": {
            "scanned": 100,
            "shown": 2,
            "belowMinTvl": 61,
            "hiddenLookalikes": 5,
            "limited": 12,
            "pages": 5,
            "pageCap": 5,
            "pageCapHit": False,
        },
        "sections": {"quote": [{**row, "pair": "AI/NVDA", "side": "quote"}], "base": [base]},
        "request": {"kind": "markets", "params": {"side": "all", "minTvlUsd": 10000}},
    }


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, params: dict | None = None) -> Any:
        self.calls.append((method, dict(params or {})))
        return _payload()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Client:
    fake = _Client()
    monkeypatch.setattr(
        trade_cmd, "run_gateway_sync", lambda action, **kw: asyncio.run(action(fake))
    )
    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.setattr(trade_cmd.console, "_width", 220)
    monkeypatch.chdir(tmp_path)
    return fake


def test_defaults_to_robinhood_and_documented_params(client: _Client) -> None:
    result = runner.invoke(trade_cmd.app, ["markets", "NVDA", "--json", "--no-card"])
    assert result.exit_code == 0, result.output
    assert client.calls == [
        (
            "trading.markets",
            {
                "target": "NVDA",
                "chainId": 4663,
                "side": "all",
                "minTvlUsd": 10000.0,
                "limit": 50,
                "lookalikes": False,
                "deep": False,
            },
        )
    ]
    assert "publish_artifact" not in result.stdout


def test_flags_reach_the_gateway(client: _Client) -> None:
    result = runner.invoke(
        trade_cmd.app,
        [
            "markets",
            "0xabc",
            "--chain",
            "base",
            "--side",
            "Quote",
            "--min-tvl",
            "0",
            "--limit",
            "200",
            "--lookalikes",
            "--deep",
            "--json",
            "--no-card",
        ],
    )
    assert result.exit_code == 0, result.output
    ((_, params),) = client.calls
    assert params == {
        "target": "0xabc",
        "chainId": 8453,
        "side": "quote",
        "minTvlUsd": 0.0,
        "limit": 200,
        "lookalikes": True,
        "deep": True,
    }


def test_json_writes_the_card_and_the_marker_last(client: _Client, tmp_path: Path) -> None:
    result = runner.invoke(trade_cmd.app, ["markets", "NVDA", "--json"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert json.loads(lines[0])["kind"] == "markets"
    marker = INLINE_ARTIFACT_MARKER_RE.search(lines[-1])
    assert marker is not None and lines[-1] == marker.group(0)
    assert marker.group("mime") == "application/vnd.agentos.markets+json"
    path = marker.group("path")
    assert path.startswith("markets-cards/markets-NVDA-") and path.endswith(".json")
    assert json.loads((tmp_path / path).read_text(encoding="utf-8")) == json.loads(lines[0])


def test_only_the_newest_cards_are_kept(client: _Client, tmp_path: Path) -> None:
    folder = tmp_path / trade_cmd.MARKETS_CARD_DIR
    folder.mkdir()
    for index in range(25):
        (folder / f"markets-OLD-{index:02d}.json").write_text("{}", encoding="utf-8")
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    result = runner.invoke(trade_cmd.app, ["markets", "NVDA", "--json"])
    assert result.exit_code == 0, result.output
    cards = [p for p in folder.iterdir() if p.name.startswith("markets-")]
    assert len(cards) == trade_cmd.MARKETS_CARDS_KEPT
    assert (folder / "notes.txt").exists()


def test_human_output_has_both_tables_and_the_counts(client: _Client, tmp_path: Path) -> None:
    result = runner.invoke(trade_cmd.app, ["markets", "NVDA"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "oracle $239.74" in out and "premium +0.23%" in out
    assert "Priced in NVDA" in out and "NVDA priced in" in out
    for column in ("PAIR", "DEX", "TVL", "VOL 24H", "PRICE", "IN NVDA", "AGE", "FLAGS"):
        assert column in out
    assert "IN QUOTE" in out  # the base section's ratio is NVDA priced in the counterparty
    assert "AI/NVDA" in out and "$4.7M" in out and "Bankr" in out
    assert "Uniswap v4 0.01%" in out and "uni" in out
    assert (
        "2 of 100 pools shown · 61 under $10k · 5 lookalikes hidden"
        " · 12 more over the limit · partial"
    ) in out
    assert not (tmp_path / trade_cmd.MARKETS_CARD_DIR).exists()


def test_bad_side_is_a_usage_error(client: _Client) -> None:
    result = runner.invoke(trade_cmd.app, ["markets", "NVDA", "--side", "both", "--json"])
    assert result.exit_code == 2
    assert client.calls == []


def test_error_envelope_keeps_the_engine_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ambiguous symbol: the gateway's ``error.details`` reaches the JSON envelope."""
    from agentos.cli import gateway_client, gateway_rpc

    candidates = [{"address": "0x" + "d1" * 20}, {"address": "0x" + "d2" * 20}]

    class _Ws:
        def __init__(self, owner: gateway_client.GatewayClient) -> None:
            self.owner = owner

        async def send(self, raw: str) -> None:
            frame = json.loads(raw)
            # The shape the gateway's ResFrame/ErrorShape serialises to.
            self.owner._pending[frame["id"]].set_result(
                {
                    "type": "res",
                    "id": frame["id"],
                    "ok": False,
                    "error": {
                        "code": "trading.invalid",
                        "message": "Ambiguous token symbol DUP",
                        "details": {"candidates": candidates},
                        "retryable": False,
                    },
                }
            )

    class _Client(gateway_client.GatewayClient):
        async def connect(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
            self._ws = _Ws(self)  # type: ignore[assignment]

        async def close(self) -> None:
            self._ws = None

    monkeypatch.setattr(gateway_client, "GatewayClient", _Client)
    monkeypatch.setattr(gateway_rpc, "_target_gateway_url", lambda **kw: "ws://127.0.0.1:1/ws")
    monkeypatch.setattr(gateway_rpc, "default_gateway_token", lambda *a: None)
    monkeypatch.setattr(gateway_rpc, "_apply_version_skew_policy", lambda *a, **kw: None)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(trade_cmd.app, ["markets", "DUP", "--json"])
    assert result.exit_code != 0
    envelope = json.loads(result.stderr.strip().splitlines()[-1])
    assert envelope["error"]["code"] == "trading.invalid"
    assert envelope["error"]["details"] == {"candidates": candidates}
    assert not (tmp_path / trade_cmd.MARKETS_CARD_DIR).exists()
