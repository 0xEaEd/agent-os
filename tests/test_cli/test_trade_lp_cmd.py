"""``agentos trade lp …``: RPC params, the card file, and the publish marker last on stdout.

The card is written only with ``--json`` (the agent always passes it); a person
running the command gets the table and nothing in the working directory.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agentos.cli import trade_cmd
from agentos.cli.output import emit_error
from agentos.tools.builtin.artifacts import INLINE_ARTIFACT_MARKER_RE, INLINE_ARTIFACT_MIME_PREFIX

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lp_cards"


def _fixture(kind: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{kind}.json").read_text(encoding="utf-8"))


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, method: str, params: dict | None = None) -> Any:
        self.calls.append((method, dict(params or {})))
        return _fixture(method.rsplit(".", 1)[1])


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


def _last_line(output: str) -> str:
    return [line for line in output.splitlines() if line.strip()][-1]


@pytest.mark.parametrize(
    ("args", "method", "params"),
    [
        (
            ["pool", "PEPE", "--chain", "base"],
            "trading.lp.pool",
            {"target": "PEPE", "chainId": 8453},
        ),
        (
            ["pool", "0xabc", "--quote", "WETH"],
            "trading.lp.pool",
            {"target": "0xabc", "quote": "WETH"},
        ),
        (["ranges", "PEPE"], "trading.lp.ranges", {"target": "PEPE"}),
        (
            ["position", "#48213", "--chain", "robinhood"],
            "trading.lp.position",
            {"chainId": 4663, "tokenId": "48213"},
        ),
        (["positions"], "trading.lp.positions", {}),
        (
            ["positions", "--wallet", "0x1", "--wallet", "Main", "--chain", "base", "--all"],
            "trading.lp.positions",
            {"wallets": ["0x1", "Main"], "chainId": 8453, "all": True},
        ),
        # A repeated --chain used to keep only the last one (Robinhood alone).
        (
            ["positions", "--chain", "base", "--chain", "robinhood"],
            "trading.lp.positions",
            {"chainIds": [8453, 4663]},
        ),
        (
            ["positions", "--chain", "base", "--chain", "8453"],
            "trading.lp.positions",
            {"chainId": 8453},
        ),
        (
            ["positions", "--budget-seconds", "40"],
            "trading.lp.positions",
            {"budgetSeconds": 40.0},
        ),
    ],
)
def test_json_prints_payload_then_the_marker_last(
    client: _Client, args: list[str], method: str, params: dict[str, Any], tmp_path: Path
) -> None:
    result = runner.invoke(trade_cmd.app, ["lp", *args, "--json"])
    assert result.exit_code == 0, result.output
    assert client.calls == [(method, params)]
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert json.loads(lines[0])["kind"] == method.rsplit(".", 1)[1]
    marker = INLINE_ARTIFACT_MARKER_RE.search(lines[-1])
    assert marker is not None and lines[-1] == marker.group(0)
    assert marker.group("mime") == "application/vnd.agentos.lp+json"
    assert marker.group("mime").startswith(INLINE_ARTIFACT_MIME_PREFIX)
    written = tmp_path / marker.group("path")
    assert json.loads(written.read_text(encoding="utf-8")) == json.loads(lines[0])
    assert not Path(marker.group("path")).is_absolute()


def test_no_card_writes_nothing(client: _Client, tmp_path: Path) -> None:
    result = runner.invoke(trade_cmd.app, ["lp", "pool", "PEPE", "--json", "--no-card"])
    assert result.exit_code == 0, result.output
    assert "publish_artifact" not in result.stdout
    assert json.loads(result.stdout)["kind"] == "pool"
    assert not (tmp_path / trade_cmd.LP_CARD_DIR).exists()


@pytest.mark.parametrize("kind", ["pool", "ranges", "position", "positions"])
def test_human_output_renders_the_table_and_writes_no_card(
    client: _Client, kind: str, tmp_path: Path
) -> None:
    args = {
        "pool": ["pool", "PEPE"],
        "ranges": ["ranges", "PEPE"],
        "position": ["position", "48213", "--chain", "base"],
        "positions": ["positions"],
    }[kind]
    result = runner.invoke(trade_cmd.app, ["lp", *args])
    assert result.exit_code == 0, result.output
    assert "publish_artifact" not in result.stdout
    assert not (tmp_path / trade_cmd.LP_CARD_DIR).exists()
    assert "as of block 21044901" in _last_line(result.stdout)
    if kind == "positions":
        assert "#48213" in result.stdout and "below-range" in result.stdout
    if kind == "pool":
        assert "Clanker v4.1" in result.stdout and "LP locked" in result.stdout


def test_empty_positions_is_still_a_card(client: _Client, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = _fixture("positions")
    empty.update(
        positions=[], totals={"valueUsd": 0.0, "feesUsd": 0.0, "count": 0, "outOfRange": 0}
    )

    async def call(method: str, params: dict | None = None) -> Any:
        return empty

    monkeypatch.setattr(client, "call", call)
    result = runner.invoke(trade_cmd.app, ["lp", "positions"])
    assert result.exit_code == 0, result.output
    assert "No Uniswap V4 positions in Main" in result.stdout
    result = runner.invoke(trade_cmd.app, ["lp", "positions", "--json"])
    assert result.exit_code == 0, result.output
    assert _last_line(result.stdout).startswith("publish_artifact path=lp-cards/positions-")


def test_card_directory_keeps_only_the_newest_cards(client: _Client, tmp_path: Path) -> None:
    cards = tmp_path / trade_cmd.LP_CARD_DIR
    cards.mkdir()
    for i in range(trade_cmd.LP_CARDS_KEPT + 5):
        old = cards / f"pool-OLD{i:02d}-base-20260101T0000{i:02d}Z.json"
        old.write_text("{}", encoding="utf-8")
        os.utime(old, (1_700_000_000 + i, 1_700_000_000 + i))
    unrelated = cards / "notes.json"
    unrelated.write_text("{}", encoding="utf-8")
    os.utime(unrelated, (1, 1))
    result = runner.invoke(trade_cmd.app, ["lp", "pool", "PEPE", "--json"])
    assert result.exit_code == 0, result.output
    name = INLINE_ARTIFACT_MARKER_RE.search(_last_line(result.stdout)).group("path")  # type: ignore[union-attr]
    kept = sorted(p.name for p in cards.iterdir() if p.name != "notes.json")
    assert len(kept) == trade_cmd.LP_CARDS_KEPT
    assert Path(name).name in kept
    # The oldest go first; the newest old ones survive; foreign files are never touched.
    assert "pool-OLD00-base-20260101T000000Z.json" not in kept
    assert "pool-OLD24-base-20260101T000024Z.json" in kept
    assert unrelated.exists()


def test_error_is_json_nonzero_and_no_card(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def failing(action: Any, *, json_output: bool = False, **kw: Any) -> Any:
        emit_error(
            "no Uniswap V4 pool holds it", json_output=json_output, code="trading.lp.not_found"
        )
        raise typer.Exit(1)

    monkeypatch.setattr(trade_cmd, "run_gateway_sync", failing)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(trade_cmd.app, ["lp", "pool", "NOPE", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "trading.lp.not_found"
    assert "publish_artifact" not in result.stdout
    assert not (tmp_path / trade_cmd.LP_CARD_DIR).exists()


def test_bad_token_id_is_rejected_before_the_gateway(client: _Client) -> None:
    result = runner.invoke(trade_cmd.app, ["lp", "position", "abc", "--chain", "base", "--json"])
    assert result.exit_code == 2 and client.calls == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["position", "48213"], "Missing option '--chain'"),
        (["pool", "PEPE", "--chain", "eth"], "unknown chain 'eth'"),
        (["positions", "--bogus"], "No such option: --bogus"),
        (["positions", "--chain", "base", "--chain", "eth"], "unknown chain 'eth'"),
        (["positions", "--budget-seconds", "1"], "--budget-seconds"),
        (["pool", "PEPE", "--chain", "base", "--chain", "robinhood"], "takes one --chain"),
        (["ranges", "PEPE", "--chain", "base", "--chain", "base"], "takes one --chain"),
        (["position", "1", "--chain", "base", "--chain", "robinhood"], "takes one --chain"),
    ],
)
def test_usage_errors_under_json_are_json(client: _Client, args: list[str], message: str) -> None:
    result = runner.invoke(trade_cmd.app, ["lp", *args, "--json"])
    assert result.exit_code == 2 and client.calls == []
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "INVALID_ARGUMENT" and message in error["message"]
    assert result.stdout == ""


def test_usage_error_without_json_keeps_the_usage_panel(client: _Client) -> None:
    result = runner.invoke(trade_cmd.app, ["lp", "position", "48213"])
    assert result.exit_code == 2
    assert "Usage:" in result.stderr and not result.stderr.lstrip().startswith("{")


@pytest.mark.parametrize(
    "code", ["trading.lp.not_a_wallet", "trading.lp.pool_key_unknown", "trading.invalid"]
)
def test_input_the_engine_rejects_exits_2_with_its_code(
    client: _Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: str
) -> None:
    from agentos.cli.gateway_client import GatewayRPCError

    async def call(method: str, params: dict | None = None) -> Any:
        raise GatewayRPCError(method, code=code, message="0xTOKEN is a token contract (boar)")

    monkeypatch.setattr(client, "call", call)
    result = runner.invoke(trade_cmd.app, ["lp", "positions", "--wallet", "0xTOKEN", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.stderr)["error"]["code"] == code
    assert "publish_artifact" not in result.stdout
    assert not (tmp_path / trade_cmd.LP_CARD_DIR).exists()
