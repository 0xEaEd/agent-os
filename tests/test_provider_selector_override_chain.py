"""``override_model(fallbacks=...)`` must rebase the selector onto the new chain.

Regression for #1616: ``override_model`` rebuilt ``_chain`` but left
``_index`` and the held breaker admission (``_admitted_index``) pointing at
positions computed against the *old* chain. A selector that had already
advanced onto a fallback (because the primary's breaker was open) then either
crashed with ``IndexError`` on the next ``resolve()`` when the new chain was
shorter, or silently kept serving whatever now sat at the stale index --
without ever asking the breaker about it.
"""

from __future__ import annotations

from agentos.provider.circuit_breaker import (
    BreakerSettings,
    ProviderCircuitBreaker,
)
from agentos.provider.failures import ProviderFailureKind
from agentos.provider.selector import ModelSelector, ProviderConfig, SelectorConfig


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(clock: FakeClock) -> ProviderCircuitBreaker:
    return ProviderCircuitBreaker(
        BreakerSettings(failure_threshold=1, cooldown_seconds=60.0), clock=clock
    )


def _trip(breaker: ProviderCircuitBreaker, provider: str) -> None:
    breaker.record_failure(provider, ProviderFailureKind.PROVIDER_OVERLOADED, "503")
    assert breaker.allow(provider) is False


def _selector(breaker: ProviderCircuitBreaker) -> ModelSelector:
    return ModelSelector(
        SelectorConfig(
            primary=ProviderConfig("openrouter", "anthropic/claude-3.5-sonnet", api_key="k"),
            fallbacks=[
                ProviderConfig("ollama", "llama3"),
                ProviderConfig("ollama", "mistral"),
            ],
        ),
        breaker=breaker,
    )


def _on_first_fallback(breaker: ProviderCircuitBreaker) -> ModelSelector:
    """A selector that ``resolve()`` already moved past an open primary."""
    _trip(breaker, "openrouter")
    selector = _selector(breaker)
    selector.resolve()
    assert selector.active_provider_id == "ollama"
    assert selector.current_config.model == "llama3"
    return selector


def test_shorter_chain_after_advancing_does_not_raise() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    selector = _on_first_fallback(breaker)

    selector.override_model("gpt-4o", fallbacks=[])

    # The issue's repro: every read of the active link used to raise
    # ``IndexError: list index out of range``.
    assert selector.active_provider_id == "openrouter"
    assert selector.current_config.model == "gpt-4o"
    assert selector.has_fallback() is False
    # Nothing else admits, so the primary is served even in cooldown --
    # "a provider in cooldown still beats no provider at all".
    selector.resolve()
    assert selector.active_provider_id == "openrouter"
    assert selector.current_config.model == "gpt-4o"


def test_rebased_chain_re_asks_the_breaker_instead_of_trusting_the_stale_slot() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    selector = _on_first_fallback(breaker)
    _trip(breaker, "deepseek")

    # The slot the old admission pointed at (index 1) now names a provider
    # whose breaker is open. The stale admission must not vouch for it.
    selector.override_model(
        "gpt-4o",
        fallbacks=[
            ProviderConfig("deepseek", "deepseek-chat", api_key="k"),
            ProviderConfig("ollama", "llama3"),
        ],
    )
    selector.resolve()

    assert selector.active_provider_id == "ollama"
    assert selector.current_config.model == "llama3"


def test_admitted_provider_is_kept_when_it_moves_to_a_later_slot() -> None:
    """The admission follows its provider, not its index: ``ollama`` moved
    from slot 1 to slot 2 and this turn keeps it. A primary that recovered
    in between is picked up by the next turn's fresh selector -- the same
    rule the model-only override already applies via ``_admitted_index``."""
    clock = FakeClock()
    breaker = _breaker(clock)
    selector = _on_first_fallback(breaker)

    breaker.record_success("openrouter")
    selector.override_model(
        "gpt-4o",
        fallbacks=[
            ProviderConfig("deepseek", "deepseek-chat", api_key="k"),
            ProviderConfig("ollama", "llama3"),
        ],
    )
    selector.resolve()

    assert selector.active_provider_id == "ollama"
    assert selector.current_config.model == "llama3"
    assert selector.has_fallback() is False

    fresh = selector.clone()
    fresh.resolve()
    assert fresh.active_provider_id == "openrouter"


def test_admission_on_an_unchanged_fallback_slot_survives() -> None:
    """The held admission may be a half-open probe. When the rebuilt chain
    keeps the same provider at the active slot, dropping it would make the
    second ``resolve()`` see its own probe as "already in flight" and skip
    past it -- the very thing ``_admitted_index`` exists to prevent."""
    clock = FakeClock()
    breaker = _breaker(clock)
    _trip(breaker, "openrouter")
    _trip(breaker, "ollama")
    clock.advance(60.0)
    # openrouter's probe goes to a concurrent turn; ollama's to this one.
    assert breaker.allow("openrouter") is True
    selector = _selector(breaker)
    selector.resolve()
    assert selector.active_provider_id == "ollama"  # probe granted

    selector.override_model(
        "gpt-4o",
        fallbacks=[ProviderConfig("ollama", "mistral"), ProviderConfig("deepseek", "x")],
    )
    selector.resolve()

    assert selector.active_provider_id == "ollama"
    assert selector.current_config.model == "mistral"


def test_primary_probe_survives_a_chain_rebuild() -> None:
    """Same guarantee for the primary (``test_repeated_resolve_keeps_the_probe_
    this_selector_was_granted`` covers the model-only override)."""
    clock = FakeClock()
    breaker = _breaker(clock)
    _trip(breaker, "openrouter")
    clock.advance(60.0)
    selector = _selector(breaker)
    selector.resolve()
    assert selector.active_provider_id == "openrouter"  # probe granted

    selector.override_model("gpt-4o", fallbacks=[ProviderConfig("ollama", "mistral")])
    selector.resolve()

    assert selector.active_provider_id == "openrouter"
    assert selector.current_config.model == "gpt-4o"


def test_model_only_override_leaves_the_position_alone() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    selector = _on_first_fallback(breaker)

    selector.override_model("gpt-4o")

    assert selector.active_provider_id == "ollama"
    assert selector.current_config.model == "llama3"
    assert selector._chain[0].model == "gpt-4o"


def test_next_fallback_walks_the_rebuilt_chain() -> None:
    clock = FakeClock()
    breaker = _breaker(clock)
    selector = _on_first_fallback(breaker)

    selector.override_model(
        "gpt-4o",
        fallbacks=[
            ProviderConfig("deepseek", "deepseek-chat", api_key="k"),
            ProviderConfig("anthropic", "claude-opus-5", api_key="k"),
        ],
    )
    selector.resolve()
    assert selector.active_provider_id == "deepseek"
    assert selector.has_fallback() is True

    selector.next_fallback()
    assert selector.active_provider_id == "anthropic"
    assert selector.has_fallback() is False


def test_admission_follows_its_provider_to_a_new_slot() -> None:
    """The admitted provider may survive the rebuild at a *different* index.
    Resetting there would discard this turn's probe: the next ``resolve()``
    sees it "in flight", skips the provider, and with every other link
    denied falls through to the dead primary while the probed provider sits
    half-open with nobody to close it."""
    clock = FakeClock()
    breaker = _breaker(clock)
    selector = ModelSelector(
        SelectorConfig(
            primary=ProviderConfig("openrouter", "a", api_key="k"),
            fallbacks=[ProviderConfig("ollama", "llama3"), ProviderConfig("deepseek", "d")],
        ),
        breaker=breaker,
    )
    for provider in ("openrouter", "ollama", "deepseek"):
        _trip(breaker, provider)
    clock.advance(60.0)
    assert breaker.allow("openrouter") is True  # probes held by other turns
    assert breaker.allow("deepseek") is True
    selector.resolve()
    assert selector.active_provider_id == "ollama"  # this turn's probe

    selector.override_model(
        "a2",
        fallbacks=[ProviderConfig("deepseek", "d2"), ProviderConfig("ollama", "mistral")],
    )
    selector.resolve()

    assert selector.active_provider_id == "ollama"
    assert selector.current_config.model == "mistral"
    assert selector.has_fallback() is False
