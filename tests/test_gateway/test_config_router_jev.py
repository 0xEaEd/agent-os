"""Config-surface tests for the Jev cloud classifier strategy (``strategy="jev"``).

Jev settings live in the ``[agentos_router.jev]`` sub-table backed by a typed
``JevConfig``. The API key is redacted on public surfaces and never frozen into
``config.toml`` when it merely mirrors ``$TYPESAFE_API_KEY``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentos.gateway.config import AgentOSRouterConfig, GatewayConfig, JevConfig


def test_router_strategy_accepts_jev() -> None:
    assert AgentOSRouterConfig(strategy="jev").strategy == "jev"


def test_jev_config_defaults() -> None:
    cfg = AgentOSRouterConfig()
    assert isinstance(cfg.jev, JevConfig)
    assert cfg.jev.api_key is None
    assert cfg.jev.api_key_env == "TYPESAFE_API_KEY"
    assert cfg.jev.base_url == "https://api.typesafe.ai"
    assert cfg.jev.model == "jev-latest"
    assert cfg.jev.input_max_chars == 4000
    assert cfg.jev.high_risk_threshold == 0.7
    assert cfg.jev.timeout_seconds is None
    assert cfg.jev.short_circuit_enabled is True
    assert cfg.jev.agentic_floor_enabled is False


def test_jev_config_reads_sub_table() -> None:
    gw = GatewayConfig(
        agentos_router={
            "strategy": "jev",
            "jev": {"api_key": "ts-secret", "high_risk_threshold": 0.9, "model": "jev-1.13.0"},
        }
    )
    assert gw.agentos_router.strategy == "jev"
    assert gw.agentos_router.jev.api_key == "ts-secret"
    assert gw.agentos_router.jev.high_risk_threshold == 0.9
    assert gw.agentos_router.jev.model == "jev-1.13.0"


@pytest.mark.parametrize(
    "field, value",
    [
        ("high_risk_threshold", 1.5),
        ("high_risk_threshold", -0.1),
        ("input_max_chars", 500),
        ("timeout_seconds", 0.0),
    ],
)
def test_jev_config_rejects_out_of_range(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        JevConfig(**{field: value})


def test_jev_api_key_redacted_on_public_dict() -> None:
    gw = GatewayConfig(agentos_router={"strategy": "jev", "jev": {"api_key": "ts-secret"}})
    public = gw.to_public_dict()
    assert public["agentos_router"]["jev"]["api_key"] != "ts-secret"
    assert "ts-secret" not in repr(public)


def test_jev_api_key_dropped_from_toml_when_env_sourced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-secret")
    gw = GatewayConfig(agentos_router={"strategy": "jev", "jev": {"api_key": "ts-secret"}})
    data = gw.to_toml_dict()
    assert "api_key" not in data["agentos_router"]["jev"]
    assert data["agentos_router"]["jev"]["api_key_env"] == "TYPESAFE_API_KEY"


def test_jev_api_key_kept_in_toml_when_literal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    gw = GatewayConfig(agentos_router={"strategy": "jev", "jev": {"api_key": "ts-literal"}})
    assert gw.to_toml_dict()["agentos_router"]["jev"]["api_key"] == "ts-literal"
