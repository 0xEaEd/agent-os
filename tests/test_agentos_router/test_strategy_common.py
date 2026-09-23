"""Shared remote-strategy helpers (strategy_common) — contract tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentos.agentos_router import llm_judge
from agentos.agentos_router.strategy_common import (
    DEFAULT_ROUTING_TIMEOUT_SECONDS,
    DEFAULT_SHORT_CIRCUIT_ALLOWLIST,
    ELISION_MARKER,
    find_valid_tier_prefer_higher,
    match_short_circuit,
    resolve_inner_timeout,
    run_coro_blocking,
    truncate_body,
)


def test_default_budget_matches_config_default() -> None:
    from agentos.gateway.config import AgentOSRouterConfig

    assert DEFAULT_ROUTING_TIMEOUT_SECONDS == AgentOSRouterConfig().routing_timeout_seconds


@pytest.mark.parametrize("budget", [0.05, 0.2, 0.5, 1.0, 3.0, 10.0, 60.0])
@pytest.mark.parametrize("explicit", [None, 0.01, 5.0, 100.0])
def test_inner_timeout_always_strictly_below_budget(budget: float, explicit: float | None) -> None:
    cfg = SimpleNamespace(routing_timeout_seconds=budget)
    timeout = resolve_inner_timeout(cfg, explicit=explicit)
    assert 0.0 < timeout < budget


def test_inner_timeout_honours_explicit_sub_budget_value() -> None:
    cfg = SimpleNamespace(routing_timeout_seconds=10.0)
    assert resolve_inner_timeout(cfg, explicit=3.0) == 3.0


def test_inner_timeout_defaults_when_cfg_lacks_budget() -> None:
    timeout = resolve_inner_timeout(SimpleNamespace(), explicit=None)
    assert 0.0 < timeout < DEFAULT_ROUTING_TIMEOUT_SECONDS


def test_judge_resolve_timeout_delegates_to_shared_helper() -> None:
    cfg = SimpleNamespace(routing_timeout_seconds=10.0, judge_timeout_seconds=2.5)
    assert llm_judge.LLMJudgeStrategy._resolve_timeout(cfg) == resolve_inner_timeout(
        cfg, explicit=2.5
    )


def test_truncate_body_head_tail_split() -> None:
    text = "a" * 3000 + "b" * 3000
    out = truncate_body(text, 4000)
    assert out.startswith("a" * 800)
    assert out.endswith("b" * 1200)
    assert ELISION_MARKER.format(omitted=4000) in out


def test_truncate_body_small_budget_hard_truncates() -> None:
    text = "x" * 5000
    out = truncate_body(text, 1000)
    assert out == "x" * 1000


def test_truncate_body_passthrough_under_budget() -> None:
    assert truncate_body("short", 1000) == "short"


@pytest.mark.parametrize("message", ["hi", "Hello!", "  cảm ơn nhé.  ", "谢谢", "OK"])
def test_short_circuit_matches_greetings(message: str) -> None:
    assert match_short_circuit(message, DEFAULT_SHORT_CIRCUIT_ALLOWLIST)


@pytest.mark.parametrize("message", ["", "hi there, can you help me deploy?", "x" * 21])
def test_short_circuit_rejects_non_greetings(message: str) -> None:
    assert not match_short_circuit(message, DEFAULT_SHORT_CIRCUIT_ALLOWLIST)


def test_judge_aliases_point_at_shared_helpers() -> None:
    assert llm_judge._truncate_body is truncate_body
    assert llm_judge._run_coro_blocking is run_coro_blocking
    assert llm_judge._find_valid_tier is find_valid_tier_prefer_higher
    assert llm_judge._DEFAULT_SHORT_CIRCUIT_ALLOWLIST is DEFAULT_SHORT_CIRCUIT_ALLOWLIST


def test_find_valid_tier_prefers_higher_then_clamps_to_highest() -> None:
    assert find_valid_tier_prefer_higher("c1", ["c0", "c2", "c3"]) == "c2"
    assert find_valid_tier_prefer_higher("c3", ["c0", "c1"]) == "c1"
    assert find_valid_tier_prefer_higher("c0", ["c0", "c1"]) == "c0"
    assert find_valid_tier_prefer_higher("c2", []) == "c1"


def test_run_coro_blocking_without_loop() -> None:
    async def _coro() -> int:
        return 7

    assert run_coro_blocking(_coro()) == 7


async def test_run_coro_blocking_inside_running_loop() -> None:
    async def _coro() -> int:
        return 9

    assert run_coro_blocking(_coro()) == 9
