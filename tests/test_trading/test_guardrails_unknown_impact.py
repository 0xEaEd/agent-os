"""A5: an agent swap whose price impact could not be computed fails closed."""

from __future__ import annotations

from agentos.trading import guardrails


def _eval(**kw: object) -> guardrails.GuardVerdict:
    base: dict[str, object] = {
        "initiator": "agent",
        "value_usd": 50.0,
        "threshold_usd": 100.0,
        "daily_cap_usd": 1000.0,
        "spent_today_usd": 0.0,
        "price_impact_pct": 1.0,
    }
    base.update(kw)
    return guardrails.evaluate(**base)  # type: ignore[arg-type]


def test_unknown_impact_parks_an_agent_order() -> None:
    verdict = _eval(price_impact_pct=None)
    assert verdict.decision == "needs_approval"
    assert verdict.reason == "price impact unknown (no reference price for one side)"


def test_known_impact_under_the_ceiling_is_allowed() -> None:
    assert _eval(price_impact_pct=1.0).decision == "allow"
    assert _eval(price_impact_pct=0.0).decision == "allow"


def test_manual_orders_are_not_affected() -> None:
    assert _eval(initiator="manual", price_impact_pct=None).decision == "allow"


def test_the_cap_and_threshold_still_come_first() -> None:
    assert _eval(price_impact_pct=None, value_usd=150.0).reason.endswith("threshold")
    assert _eval(price_impact_pct=None, spent_today_usd=990.0).decision == "blocked_daily_cap"
