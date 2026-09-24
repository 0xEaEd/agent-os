"""Public config views hide what a URL or key would give away.

``trading.rpc_urls`` carry provider keys in the path (dRPC, Alchemy, Infura),
so every public view shows scheme + host only, and a redacted value written
back through ``config.set`` / ``config.apply`` restores the stored URL rather
than replacing it. ``trading.aggregator_base_url`` is pinned to https.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

import agentos.gateway.rpc_config  # noqa: F401  ensures registration
from agentos.cli.main import app
from agentos.gateway.config import GatewayConfig, TradingConfig, redact_public_config
from agentos.gateway.rpc import RpcContext, get_dispatcher

KEYED = "https://lb.drpc.org/ogrpc?network=base&dkey=Ak3ySecretKey"
KEYED_RH = "https://rh-mainnet.g.alchemy.com/v2/AlchemySecret"


def _ctx(config: GatewayConfig) -> RpcContext:
    return RpcContext(conn_id="t", config=config)


def _config(tmp_path: Path) -> GatewayConfig:
    cfg = GatewayConfig(config_path=str(tmp_path / "c.toml"))
    cfg.trading = TradingConfig(rpc_urls={"8453": KEYED, "4663": KEYED_RH})
    return cfg


class TestRpcUrlRedaction:
    def test_redact_public_config_cuts_path_and_query(self) -> None:
        out = redact_public_config({"trading": {"rpc_urls": {"8453": KEYED, "base": ""}}})
        assert out["trading"]["rpc_urls"] == {"8453": "https://lb.drpc.org/…", "base": ""}
        assert "dkey" not in str(out)

    def test_to_public_dict_routes_through_it(self, tmp_path: Path) -> None:
        public = _config(tmp_path).to_public_dict()
        assert public["trading"]["rpc_urls"] == {
            "8453": "https://lb.drpc.org/…",
            "4663": "https://rh-mainnet.g.alchemy.com/…",
        }
        assert "Secret" not in str(public)

    @pytest.mark.asyncio
    async def test_config_snapshot_and_get_are_redacted(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path)
        snapshot = await get_dispatcher().dispatch("r1", "config.snapshot", {}, _ctx(cfg))
        assert snapshot.error is None, snapshot.error
        assert snapshot.payload["config"]["trading"]["rpc_urls"]["8453"] == "https://lb.drpc.org/…"
        assert "Ak3ySecretKey" not in str(snapshot.payload)
        got = await get_dispatcher().dispatch("r2", "config.get", {}, _ctx(cfg))
        assert got.error is None, got.error
        assert "Ak3ySecretKey" not in str(got.payload)
        assert got.payload["trading"]["rpc_urls"]["4663"] == "https://rh-mainnet.g.alchemy.com/…"

    def test_cli_config_get_is_redacted(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path / "state"))
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(f'[trading.rpc_urls]\n"8453" = "{KEYED}"\n', encoding="utf-8")
        result = CliRunner().invoke(
            app, ["config", "get", "trading.rpc_urls", "--config", str(cfg_path)]
        )
        assert result.exit_code == 0, result.output
        assert "lb.drpc.org" in result.output
        assert "Ak3ySecretKey" not in result.output

    @pytest.mark.asyncio
    async def test_redacted_url_written_back_keeps_the_stored_one(self, tmp_path: Path) -> None:
        cfg = _config(tmp_path)
        # What a settings form sends back after editing an unrelated key.
        res = await get_dispatcher().dispatch(
            "r1",
            "config.set",
            {"path": "trading.rpc_urls", "value": {"8453": "https://lb.drpc.org/…", "4663": ""}},
            _ctx(cfg),
        )
        assert res.error is None, res.error
        persisted = tomllib.loads(Path(cfg.config_path).read_text(encoding="utf-8"))
        assert persisted["trading"]["rpc_urls"]["8453"] == KEYED
        assert persisted["trading"]["rpc_urls"]["4663"] == ""
        # A genuinely new URL is written as given.
        res = await get_dispatcher().dispatch(
            "r2",
            "config.patch",
            {"patches": {"trading.rpc_urls.8453": "https://mainnet.base.org"}},
            _ctx(cfg),
        )
        assert res.error is None, res.error
        persisted = tomllib.loads(Path(cfg.config_path).read_text(encoding="utf-8"))
        assert persisted["trading"]["rpc_urls"]["8453"] == "https://mainnet.base.org"


class TestAggregatorUrlPin:
    @pytest.mark.parametrize(
        "url",
        [
            "https://agg.useagentos.dev",
            "https://agg.example.org:8443/v1",
            "http://127.0.0.1:8080",
            "http://localhost:3000",
            "http://[::1]:8080",
            "HTTP://LOCALHOST",
        ],
    )
    def test_accepts_https_or_loopback_http(self, url: str) -> None:
        assert TradingConfig(aggregator_base_url=url).aggregator_base_url == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://evil",
            "http://agg.useagentos.dev",
            "http://127.0.0.1.evil.com",
            "ftp://agg.useagentos.dev",
            "agg.useagentos.dev",
            "",
        ],
    )
    def test_refuses_plain_http_elsewhere(self, url: str) -> None:
        with pytest.raises(ValueError, match="aggregator_base_url"):
            TradingConfig(aggregator_base_url=url)

    def test_validate_assignment_applies(self) -> None:
        cfg = TradingConfig()
        with pytest.raises(ValueError):
            cfg.aggregator_base_url = "http://evil"
        assert cfg.aggregator_base_url == "https://agg.useagentos.dev"
