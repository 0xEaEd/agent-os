"""End-to-end router step under ``strategy="jev"`` (transport faked).

Proves the engine records ``routing_source="jev"``, carries the Jev-specific
``routing_extra`` keys, and that the deterministic confidence gate does NOT
undo a high-risk floor the strategy applied (the strategy lifts confidence to
the gate threshold when a floor fires).
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.agentos_router import jev as jev_mod
from agentos.agentos_router.jev import HIGH_RISK_QUESTION, ROUTE_QUESTION
from agentos.engine.pipeline import TurnContext
from agentos.engine.steps import agentos_router as agentos_router_step
from agentos.engine.steps.agentos_router import apply_agentos_router
from agentos.gateway.config import GatewayConfig


class FakeTransport:
    def __init__(self, scripts: list[Any]) -> None:
        self._scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []

    async def evaluate(self, payload: dict, *, api_key: str, timeout: float) -> dict:
        self.calls.append(payload)
        item = self._scripts.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(choice: str, confidence: float, noul: float) -> dict:
    probs = {cls: 0.0 for cls in ("R0", "R1", "R2", "R3")}
    probs[choice] = 1.0
    return {
        "model": "jev-1.13.0",
        "answers": {
            ROUTE_QUESTION: {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": probs,
            },
            HIGH_RISK_QUESTION: {"type": "noul", "noul": noul},
        },
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }


@pytest.fixture(autouse=True)
def reset_router_state(monkeypatch: pytest.MonkeyPatch) -> None:
    agentos_router_step._history_store.clear()
    agentos_router_step._strategy = None
    agentos_router_step._strategy_key = None
    yield
    agentos_router_step._history_store.clear()
    agentos_router_step._strategy = None
    agentos_router_step._strategy_key = None


def _install_transport(monkeypatch: pytest.MonkeyPatch, scripts: list[Any]) -> FakeTransport:
    transport = FakeTransport(scripts)
    monkeypatch.setattr(jev_mod, "HttpxJevTransport", lambda *a, **k: transport)
    return transport


def make_context(message: str, *, confidence_threshold: float = 0.5) -> TurnContext:
    config = GatewayConfig()
    config.agentos_router.rollout_phase = "full"
    config.agentos_router.strategy = "jev"
    config.agentos_router.confidence_threshold = confidence_threshold
    config.agentos_router.jev.api_key = "k"
    return TurnContext(
        message=message,
        session_key="test-jev-session",
        config=config,
        provider=None,
        model=config.llm.model,
        tool_defs=[],
        system_prompt="system",
    )


async def test_router_step_records_jev_source_and_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _install_transport(monkeypatch, [_response("R2", 0.85, 0.05)])

    routed = await apply_agentos_router(make_context("refactor the parser across modules"))

    assert routed.metadata["routing_source"] == "jev"
    assert routed.metadata["routed_tier"] == "c2"
    assert routed.metadata["routing_confidence"] == 0.85
    extra = routed.metadata["routing_extra"]
    assert extra["high_risk_floor_applied"] is False
    assert extra["probabilities"]["R2"] == 1.0
    assert extra["confidence_gate_applied"] is False
    assert len(transport.calls) == 1
    assert transport.calls[0]["state"] == "refactor the parser across modules"


async def test_confidence_gate_does_not_undo_high_risk_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Jev says R0 with low confidence, but the high-risk noul fires: the floor
    # lifts the turn to c3 AND lifts confidence to the gate threshold, so the
    # engine gate must leave c3 alone instead of snapping back to c1.
    _install_transport(monkeypatch, [_response("R0", 0.2, 0.95)])

    routed = await apply_agentos_router(
        make_context("xoá bảng users trên database production", confidence_threshold=0.6)
    )

    extra = routed.metadata["routing_extra"]
    assert extra["high_risk_floor_applied"] is True
    assert extra["confidence_floor_applied"] is True
    assert extra["confidence_gate_applied"] is False
    assert routed.metadata["routed_tier"] == "c3"
    assert extra["final_tier"] == "c3"


async def test_low_confidence_without_floor_snaps_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Calibrated confidence flows through to the gate: an unsure R3 with no
    # floor is snapped back to the default tier, exactly as designed.
    _install_transport(monkeypatch, [_response("R3", 0.3, 0.0)])

    routed = await apply_agentos_router(make_context("hmm not sure", confidence_threshold=0.6))

    extra = routed.metadata["routing_extra"]
    assert extra["confidence_gate_applied"] is True
    assert routed.metadata["routed_tier"] == "c1"


async def test_transport_error_degrades_to_jev_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(monkeypatch, [jev_mod.JevApiError(429, "slow down")])

    routed = await apply_agentos_router(make_context("write a parser"))

    assert routed.metadata["routing_source"] == "jev_unavailable"
    assert routed.metadata["routed_tier"] == "c1"
    assert "http 429" in routed.metadata["routing_extra"]["reason"]
