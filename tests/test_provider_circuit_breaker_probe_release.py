"""A request-shaped failure on the half-open probe must release the probe slot.

Regression for #1602: ``record_failure`` ignores non-tripping kinds
(``MODEL_NOT_FOUND``, ``BAD_REQUEST``, ...) because they say something about
the request, not the provider. But when the ignored failure *was* the
half-open probe, returning early left ``probe_started_at`` set, so ``allow()``
kept reporting "a probe is in flight" and blocked every other caller for a
full cooldown window -- parking a provider nothing has shown to be unhealthy.
"""

from __future__ import annotations

import pytest

from agentos.provider.circuit_breaker import (
    TRIPPING_FAILURE_KINDS,
    BreakerSettings,
    BreakerState,
    ProviderCircuitBreaker,
)
from agentos.provider.failures import ProviderFailureKind

NON_TRIPPING_KINDS = sorted(
    (kind for kind in ProviderFailureKind if kind not in TRIPPING_FAILURE_KINDS),
    key=str,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _half_open_breaker(clock: FakeClock) -> ProviderCircuitBreaker:
    """A breaker whose probe for ``openrouter`` has just been admitted."""
    breaker = ProviderCircuitBreaker(
        BreakerSettings(failure_threshold=1, cooldown_seconds=60.0, max_cooldown_seconds=600.0),
        clock=clock,
    )
    breaker.record_failure("openrouter", ProviderFailureKind.RATE_LIMITED, "429")
    assert breaker.allow("openrouter") is False
    clock.advance(60.0)
    assert breaker.allow("openrouter") is True
    assert breaker.state("openrouter") is BreakerState.HALF_OPEN
    return breaker


@pytest.mark.parametrize("kind", NON_TRIPPING_KINDS, ids=str)
def test_request_shaped_probe_failure_releases_the_probe_slot(
    kind: ProviderFailureKind,
) -> None:
    clock = FakeClock()
    breaker = _half_open_breaker(clock)

    # The probe request itself failed for a reason unrelated to provider
    # health (e.g. a wrong model id). The breaker stays half-open: the
    # provider is still unproven, so the next caller becomes the new probe.
    assert breaker.record_failure("openrouter", kind, "bad model") is BreakerState.HALF_OPEN

    assert breaker.allow("openrouter") is True
    assert breaker.state("openrouter") is BreakerState.HALF_OPEN


def test_released_slot_still_admits_only_one_probe_at_a_time() -> None:
    clock = FakeClock()
    breaker = _half_open_breaker(clock)
    breaker.record_failure("openrouter", ProviderFailureKind.MODEL_NOT_FOUND, "bad model")

    assert breaker.allow("openrouter") is True  # the replacement probe
    # A second concurrent caller still waits on that probe's outcome.
    assert breaker.allow("openrouter") is False
    clock.advance(60.0)
    assert breaker.allow("openrouter") is True


def test_request_shaped_probe_failure_leaves_the_failure_history_alone() -> None:
    clock = FakeClock()
    breaker = _half_open_breaker(clock)
    before = breaker.status("openrouter")

    breaker.record_failure("openrouter", ProviderFailureKind.BAD_REQUEST, "400")

    after = breaker.status("openrouter")
    assert after.state is BreakerState.HALF_OPEN
    assert after.consecutive_failures == before.consecutive_failures
    assert after.total_failures == before.total_failures
    assert after.total_trips == before.total_trips
    assert after.last_failure_kind == str(ProviderFailureKind.RATE_LIMITED)


def test_replacement_probe_outcome_still_drives_the_breaker() -> None:
    clock = FakeClock()
    breaker = _half_open_breaker(clock)
    breaker.record_failure("openrouter", ProviderFailureKind.MODEL_NOT_FOUND, "bad model")
    assert breaker.allow("openrouter") is True

    # The replacement probe's *provider-shaped* failure reopens with backoff...
    state = breaker.record_failure("openrouter", ProviderFailureKind.PROVIDER_OVERLOADED, "503")
    assert state is BreakerState.OPEN
    clock.advance(60.0)
    assert breaker.allow("openrouter") is False
    clock.advance(60.0)
    assert breaker.allow("openrouter") is True

    # ...and a success closes it.
    breaker.record_success("openrouter")
    assert breaker.state("openrouter") is BreakerState.CLOSED


def test_request_shaped_failure_outside_a_probe_is_still_a_no_op() -> None:
    clock = FakeClock()
    breaker = ProviderCircuitBreaker(
        BreakerSettings(failure_threshold=1, cooldown_seconds=60.0), clock=clock
    )

    # Closed and untracked: nothing to release, nothing recorded.
    assert (
        breaker.record_failure("openrouter", ProviderFailureKind.MODEL_NOT_FOUND)
        is BreakerState.CLOSED
    )
    assert breaker.snapshot() == []

    # Open with the cooldown still running: the window is not shortened.
    breaker.record_failure("openrouter", ProviderFailureKind.RATE_LIMITED, "429")
    clock.advance(30.0)
    assert (
        breaker.record_failure("openrouter", ProviderFailureKind.MODEL_NOT_FOUND)
        is BreakerState.OPEN
    )
    assert breaker.allow("openrouter") is False
    assert breaker.status("openrouter").cooldown_remaining_seconds == 30.0
