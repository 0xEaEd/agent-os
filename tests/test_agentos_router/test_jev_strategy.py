"""Unit tests for the Jev router strategy (fake transport)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agentos.agentos_router.jev import (
    HIGH_RISK_QUESTION,
    ROUTE_QUESTION,
    SOURCE_HEALTHY,
    SOURCE_UNAVAILABLE,
    HttpxJevTransport,
    JevApiError,
    JevStrategy,
    probe_jev,
)
from agentos.agentos_router.strategy_common import DEFAULT_ROUTING_TIMEOUT_SECONDS

ALL_TIERS = ["c0", "c1", "c2", "c3"]
SECRET = "ts-super-secret-key-123"

# The LLM judge's stable extra shape (pinned in test_llm_judge_strategy.py);
# Jev must emit at least these keys on every path.
JUDGE_EXTRA_KEYS = {
    "route_class",
    "top1_label",
    "final_route_class",
    "confidence",
    "thinking_mode",
    "prompt_policy",
    "flags",
    "reason",
    "probabilities",
    "margin",
    "difficulty",
}
JEV_EXTRA_KEYS = JUDGE_EXTRA_KEYS | {
    "high_risk_noul",
    "high_risk_floor_applied",
    "agentic_floor_applied",
    "confidence_floor_applied",
    "model_version",
    "usage",
    "api_key_source",
}


def _response(
    choice: str = "R2",
    confidence: float = 0.81,
    noul: float | None = 0.1,
    probabilities: dict[str, float] | None = None,
) -> dict:
    answers: dict = {
        ROUTE_QUESTION: {
            "type": "choice",
            "choice": choice,
            "confidence": confidence,
            "probabilities": probabilities or {"R0": 0.02, "R1": 0.1, "R2": 0.8, "R3": 0.08},
        }
    }
    if noul is not None:
        answers[HIGH_RISK_QUESTION] = {"type": "noul", "noul": noul}
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 3, "output_tokens": 0},
    }


class FakeTransport:
    """Scripted transport: one response (dict) or exception per call."""

    def __init__(self, scripts: list[Any]) -> None:
        self._scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, payload: dict, *, api_key: str, timeout: float) -> dict:
        self.calls.append({"payload": payload, "api_key": api_key, "timeout": timeout})
        if not self._scripts:
            raise AssertionError("FakeTransport called more times than scripted")
        item = self._scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return item


def _cfg(**overrides: Any) -> SimpleNamespace:
    jev = SimpleNamespace(
        api_key=overrides.pop("api_key", SECRET),
        api_key_env=overrides.pop("api_key_env", "TYPESAFE_API_KEY"),
        base_url="https://api.typesafe.ai",
        model="jev-latest",
        input_max_chars=overrides.pop("input_max_chars", 4000),
        high_risk_threshold=overrides.pop("high_risk_threshold", 0.7),
        timeout_seconds=overrides.pop("timeout_seconds", None),
        short_circuit_enabled=overrides.pop("short_circuit_enabled", True),
        agentic_floor_enabled=overrides.pop("agentic_floor_enabled", False),
    )
    return SimpleNamespace(
        tiers={
            "c0": {"model": "m0", "description": "trivial"},
            "c1": {"model": "m1", "description": "normal"},
            "c2": {"model": "m2", "description": "hard"},
            "c3": {"model": "m3", "description": "hardest"},
        },
        default_tier=overrides.pop("default_tier", "c1"),
        routing_timeout_seconds=overrides.pop("routing_timeout_seconds", 10.0),
        jev=jev,
        **overrides,
    )


def _run(strategy: JevStrategy, message: str, **kwargs: Any):
    return asyncio.run(strategy.classify(message, ALL_TIERS, **kwargs))


# -- happy path ---------------------------------------------------------------


def test_happy_path_passes_calibrated_confidence_through() -> None:
    transport = FakeTransport([_response(choice="R2", confidence=0.81)])
    strategy = JevStrategy(_cfg(), transport=transport)

    tier, confidence, source, extra = _run(strategy, "refactor this parser across modules")

    assert (tier, confidence, source) == ("c2", 0.81, SOURCE_HEALTHY)
    assert JEV_EXTRA_KEYS <= set(extra)
    assert extra["route_class"] == extra["final_route_class"] == "R2"
    assert extra["probabilities"] == {"R0": 0.02, "R1": 0.1, "R2": 0.8, "R3": 0.08}
    assert extra["margin"] == pytest.approx(0.7)
    assert extra["difficulty"] > 0.0
    assert extra["high_risk_noul"] == 0.1
    assert extra["high_risk_floor_applied"] is False
    assert extra["confidence_floor_applied"] is False
    assert extra["model_version"] == "jev-1.13.0"
    assert extra["usage"] == {"input_tokens": 3, "output_tokens": 0}
    assert extra["api_key_source"] == "config"
    assert extra["thinking_mode"] in {"T0", "T1", "T2", "T3"}
    assert "R2" in extra["reason"]


def test_request_carries_key_and_inner_timeout_below_budget() -> None:
    transport = FakeTransport([_response()])
    strategy = JevStrategy(_cfg(routing_timeout_seconds=10.0), transport=transport)
    _run(strategy, "write a parser")
    call = transport.calls[0]
    assert call["api_key"] == SECRET
    assert 0.0 < call["timeout"] < 10.0
    assert 0.0 < strategy._timeout < DEFAULT_ROUTING_TIMEOUT_SECONDS
    body = call["payload"]
    assert body["model"] == "jev-latest"
    assert body["state"] == "write a parser"
    assert body["questions"][ROUTE_QUESTION]["criteria"]["R1"]["what"] == "normal"


def test_key_resolved_from_env_when_config_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")
    transport = FakeTransport([_response()])
    strategy = JevStrategy(_cfg(api_key=None), transport=transport)
    _tier, _c, source, extra = _run(strategy, "write a parser")
    assert source == SOURCE_HEALTHY
    assert transport.calls[0]["api_key"] == "env-key"
    assert extra["api_key_source"] == "env:TYPESAFE_API_KEY"


# -- floors -------------------------------------------------------------------


@pytest.mark.parametrize("noul, floored", [(0.7, True), (0.95, True), (0.69, False), (0.0, False)])
def test_high_risk_floor_at_threshold(noul: float, floored: bool) -> None:
    transport = FakeTransport([_response(choice="R1", confidence=0.9, noul=noul)])
    strategy = JevStrategy(_cfg(high_risk_threshold=0.7), transport=transport)
    tier, confidence, _source, extra = _run(strategy, "xoá bảng users trên production")
    assert extra["high_risk_floor_applied"] is floored
    assert tier == ("c3" if floored else "c1")
    assert extra["route_class"] == "R1"
    assert extra["final_route_class"] == ("R3" if floored else "R1")
    assert confidence == 0.9


def test_floor_lifts_low_confidence_to_gate_threshold() -> None:
    # Without the lift the engine confidence gate would snap the floored c3
    # back to default_tier, silently undoing the high-risk floor.
    transport = FakeTransport([_response(choice="R0", confidence=0.2, noul=0.9)])
    strategy = JevStrategy(_cfg(), transport=transport, confidence_threshold=0.6)
    tier, confidence, _source, extra = _run(strategy, "drop prod")
    assert tier == "c3"
    assert confidence == 0.6
    assert extra["confidence_floor_applied"] is True


def test_no_confidence_lift_when_no_floor_fired() -> None:
    transport = FakeTransport([_response(choice="R2", confidence=0.2, noul=0.0)])
    strategy = JevStrategy(_cfg(), transport=transport, confidence_threshold=0.6)
    _tier, confidence, _source, extra = _run(strategy, "hmm")
    assert confidence == 0.2
    assert extra["confidence_floor_applied"] is False


def test_agentic_floor_off_by_default_keeps_r0() -> None:
    transport = FakeTransport([_response(choice="R0", confidence=0.95, noul=0.0)])
    strategy = JevStrategy(_cfg(), transport=transport)
    tier, _c, _s, extra = _run(strategy, "ok go on", tool_defs=[object()])
    assert tier == "c0"
    assert extra["agentic_floor_applied"] is False
    assert extra["flags"]["agentic"] is True


def test_agentic_floor_raises_r0_to_r1_when_enabled() -> None:
    transport = FakeTransport([_response(choice="R0", confidence=0.95, noul=0.0)])
    strategy = JevStrategy(_cfg(agentic_floor_enabled=True), transport=transport)
    tier, _c, _s, extra = _run(strategy, "ok go on", tool_defs=[object()])
    assert tier == "c1"
    assert extra["agentic_floor_applied"] is True
    assert extra["flags"]["agentic"] is True


def test_missing_noul_does_not_floor() -> None:
    transport = FakeTransport([_response(choice="R1", noul=None)])
    strategy = JevStrategy(_cfg(), transport=transport)
    tier, _c, _s, extra = _run(strategy, "x")
    assert tier == "c1"
    assert extra["high_risk_noul"] is None
    assert extra["high_risk_floor_applied"] is False


def test_clamp_to_valid_tiers_prefers_higher_then_highest() -> None:
    strategy = JevStrategy(_cfg(), transport=FakeTransport([_response(choice="R1")]))
    tier, *_ = asyncio.run(strategy.classify("x", ["c0", "c2"]))
    assert tier == "c2"
    strategy = JevStrategy(_cfg(), transport=FakeTransport([_response(choice="R3")]))
    tier, *_ = asyncio.run(strategy.classify("x", ["c0", "c1"]))
    assert tier == "c1"


# -- short-circuit ------------------------------------------------------------


def test_short_circuit_skips_transport() -> None:
    transport = FakeTransport([])
    strategy = JevStrategy(_cfg(), transport=transport)
    tier, confidence, source, extra = _run(strategy, "cảm ơn nhé")
    assert (tier, confidence, source) == ("c0", 1.0, SOURCE_HEALTHY)
    assert extra["reason"] == "greeting/ack short-circuit"
    assert JEV_EXTRA_KEYS <= set(extra)
    assert transport.calls == []


def test_short_circuit_fires_for_agentic_turn_when_floor_off() -> None:
    transport = FakeTransport([])
    strategy = JevStrategy(_cfg(), transport=transport)
    tier, *_ = _run(strategy, "ok", tool_defs=[object()])
    assert tier == "c0"
    assert transport.calls == []


def test_short_circuit_skipped_when_agentic_floor_on_or_disabled() -> None:
    transport = FakeTransport([_response(choice="R1"), _response(choice="R0")])
    strategy = JevStrategy(_cfg(agentic_floor_enabled=True), transport=transport)
    tier, *_ = _run(strategy, "ok", tool_defs=[object()])
    assert tier == "c1"
    strategy = JevStrategy(_cfg(short_circuit_enabled=False), transport=transport)
    tier, *_ = _run(strategy, "ok")
    assert tier == "c0"
    assert len(transport.calls) == 2


# -- degrade paths ------------------------------------------------------------


def test_missing_key_degrades_without_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    transport = FakeTransport([])
    strategy = JevStrategy(_cfg(api_key=None), transport=transport)
    tier, confidence, source, extra = _run(strategy, "write a parser")
    assert (tier, confidence, source) == ("c1", 0.0, SOURCE_UNAVAILABLE)
    assert "TYPESAFE_API_KEY" in extra["reason"]
    assert extra["api_key_source"] == "missing"
    assert transport.calls == []
    assert JEV_EXTRA_KEYS <= set(extra)


@pytest.mark.parametrize("status", [401, 422, 429, 529])
def test_http_errors_degrade_with_status_reason(status: int) -> None:
    transport = FakeTransport([JevApiError(status, "body")])
    strategy = JevStrategy(_cfg(default_tier="c2"), transport=transport)
    tier, confidence, source, extra = _run(strategy, "write a parser")
    assert (tier, confidence, source) == ("c2", 0.0, SOURCE_UNAVAILABLE)
    assert f"http {status}" in extra["reason"]


async def test_timeout_degrades() -> None:
    async def _hang() -> dict:
        await asyncio.sleep(5)
        return _response()

    transport = FakeTransport([_hang])
    strategy = JevStrategy(_cfg(routing_timeout_seconds=0.3), transport=transport)
    tier, _c, source, extra = await strategy.classify("write a parser", ALL_TIERS)
    assert (tier, source) == ("c1", SOURCE_UNAVAILABLE)
    assert "timeout" in extra["reason"]


def test_malformed_response_degrades() -> None:
    transport = FakeTransport([{"answers": {ROUTE_QUESTION: {"choice": "R7"}}}])
    strategy = JevStrategy(_cfg(), transport=transport)
    _tier, _c, source, extra = _run(strategy, "write a parser")
    assert source == SOURCE_UNAVAILABLE
    assert "malformed" in extra["reason"]


def test_unexpected_exception_degrades() -> None:
    transport = FakeTransport([ValueError("boom")])
    strategy = JevStrategy(_cfg(), transport=transport)
    _tier, _c, source, extra = _run(strategy, "write a parser")
    assert source == SOURCE_UNAVAILABLE
    assert "ValueError" in extra["reason"]


def test_require_router_runtime_reraises_on_degrade() -> None:
    transport = FakeTransport([JevApiError(429, "slow down")])
    strategy = JevStrategy(_cfg(), transport=transport, require_router_runtime=True)
    with pytest.raises(RuntimeError, match="jev router unavailable"):
        _run(strategy, "write a parser")


def test_api_key_never_reaches_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    # structlog is not routed through caplog in this suite; capture the
    # strategy's own logger calls instead so the assertion is not vacuous.
    events: list[tuple[str, dict[str, Any]]] = []

    class _Recorder:
        def warning(self, event: str, **kwargs: Any) -> None:
            events.append((event, kwargs))

        info = debug = error = warning

    from agentos.agentos_router import jev as jev_mod

    monkeypatch.setattr(jev_mod, "log", _Recorder())
    transport = FakeTransport([JevApiError(401, "invalid api key")])
    strategy = JevStrategy(_cfg(), transport=transport)
    _run(strategy, "write a parser")
    assert events, "expected a jev.call_failed warning"
    assert SECRET not in repr(events)
    assert events[0][0] == "jev.call_failed"
    assert events[0][1]["status"] == 401


# -- httpx transport ----------------------------------------------------------


async def test_httpx_transport_posts_bearer_and_parses() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(200, json=_response())

    transport = HttpxJevTransport("https://api.example.test/")
    real = httpx.AsyncClient

    class _Client(real):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _Client  # type: ignore[misc]
    try:
        body = await transport.evaluate({"state": "hi"}, api_key="k1", timeout=2.0)
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]
    assert body["answers"][ROUTE_QUESTION]["choice"] == "R2"
    assert seen["url"] == "https://api.example.test/v1/systemone"
    assert seen["auth"] == "Bearer k1"
    assert b'"state": "hi"' in seen["body"] or b'"state":"hi"' in seen["body"]


async def test_httpx_transport_raises_api_error_on_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited; Authorization must not echo")

    real = httpx.AsyncClient

    class _Client(real):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _Client  # type: ignore[misc]
    try:
        with pytest.raises(JevApiError) as excinfo:
            await HttpxJevTransport().evaluate({}, api_key="k", timeout=2.0)
    finally:
        httpx.AsyncClient = real  # type: ignore[misc]
    assert excinfo.value.status == 429
    assert "rate limited" in str(excinfo.value)


# -- probe --------------------------------------------------------------------


def test_probe_jev_reports_success_and_failure() -> None:
    assert probe_jev("k", transport=FakeTransport([_response()])) is None
    err = probe_jev("k", transport=FakeTransport([JevApiError(401, "nope")]))
    assert err is not None and "401" in err
    assert probe_jev("") is not None


async def test_probe_jev_is_loop_safe() -> None:
    assert probe_jev("k", transport=FakeTransport([_response()])) is None
