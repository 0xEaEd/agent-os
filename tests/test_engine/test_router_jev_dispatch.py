"""Dispatch + cache-key surfaces for the ``jev`` strategy.

These exercise the real engine seam (``_get_strategy`` / ``_strategy_cache_key``)
— not a mock of it — proving that a config with ``strategy="jev"`` builds a
``JevStrategy`` with the live confidence threshold, that hot edits to any
``[agentos_router.jev]`` field (or a tier edit, which changes the criteria)
rebuild the cached strategy, and that a construction failure degrades with the
right telemetry tag.
"""

from __future__ import annotations

import pytest

from agentos.agentos_router.jev import JevStrategy
from agentos.engine.steps import agentos_router as step
from agentos.gateway.config import AgentOSRouterConfig


@pytest.fixture(autouse=True)
def _clear_strategy_cache() -> None:
    step._strategy = None
    step._strategy_key = None
    yield
    step._strategy = None
    step._strategy_key = None


def test_dispatch_builds_jev_strategy_with_live_threshold() -> None:
    cfg = AgentOSRouterConfig(
        strategy="jev",
        confidence_threshold=0.7,
        jev={"api_key": "k", "high_risk_threshold": 0.8, "model": "jev-1.13.0"},
    )

    strategy = step._get_strategy(cfg)

    assert isinstance(strategy, JevStrategy)
    assert strategy._confidence_threshold == 0.7
    assert strategy._high_risk_threshold == 0.8
    assert strategy._model == "jev-1.13.0"
    assert strategy._criteria["R1"]["what"] == cfg.tiers["c1"]["description"]
    assert 0.0 < strategy._timeout < cfg.routing_timeout_seconds


def test_dispatch_returns_cached_strategy_for_same_config() -> None:
    cfg = AgentOSRouterConfig(strategy="jev", jev={"api_key": "k"})
    first = step._get_strategy(cfg)
    second = step._get_strategy(cfg)
    assert first is second


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: setattr(c.jev, "api_key", "k2"),
        lambda c: setattr(c.jev, "api_key_env", "OTHER_KEY"),
        lambda c: setattr(c.jev, "base_url", "https://proxy.example"),
        lambda c: setattr(c.jev, "model", "jev-preview"),
        lambda c: setattr(c.jev, "input_max_chars", 6000),
        lambda c: setattr(c.jev, "high_risk_threshold", 0.9),
        lambda c: setattr(c.jev, "timeout_seconds", 3.0),
        lambda c: setattr(c.jev, "short_circuit_enabled", False),
        lambda c: setattr(c.jev, "agentic_floor_enabled", True),
        lambda c: setattr(c, "routing_timeout_seconds", 20.0),
        lambda c: setattr(c, "confidence_threshold", 0.9),
        lambda c: setattr(c, "default_tier", "c2"),
        lambda c: setattr(c, "require_router_runtime", True),
        lambda c: c.tiers["c1"].__setitem__("description", "edited"),
    ],
)
def test_cache_key_perturbs_on_every_jev_field(mutate) -> None:
    cfg = AgentOSRouterConfig(strategy="jev", jev={"api_key": "k"})
    before = step._strategy_cache_key(cfg)
    mutate(cfg)
    assert step._strategy_cache_key(cfg) != before


def test_construction_failure_degrades_with_jev_source(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.agentos_router import jev as jev_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("bad jev config")

    monkeypatch.setattr(jev_mod, "JevStrategy", _boom)
    cfg = AgentOSRouterConfig(strategy="jev", jev={"api_key": "k"})

    strategy = step._get_strategy(cfg)

    assert isinstance(strategy, step._UnavailableJudgeStrategy)
    assert strategy.source == "jev_unavailable"


def test_construction_failure_raises_when_runtime_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agentos.agentos_router import jev as jev_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("bad jev config")

    monkeypatch.setattr(jev_mod, "JevStrategy", _boom)
    cfg = AgentOSRouterConfig(strategy="jev", jev={"api_key": "k"}, require_router_runtime=True)

    with pytest.raises(RuntimeError, match="bad jev config"):
        step._get_strategy(cfg)
