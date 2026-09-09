"""``_OPENAI_COMPAT_PROVIDERS`` must not drift from the provider registry.

The hand-kept literal this replaces went stale twice: a provider was registered
with ``failure_family="openai_compat"`` and never added to the classifier, so
its 401/402/429 fell through to ``UNKNOWN``. These tests assert the structural
relationship rather than the two names that happened to be missing.
"""

from __future__ import annotations

import pytest

from agentos.provider.failures import (
    _OPENAI_COMPAT_PROVIDERS,
    ProviderFailureKind,
    classify_provider_error,
)
from agentos.provider.registry import list_provider_specs


def _registry_compat_ids() -> set[str]:
    return {
        spec.provider_id for spec in list_provider_specs() if spec.failure_family == "openai_compat"
    }


def test_every_openai_compat_spec_is_classified() -> None:
    missing = sorted(_registry_compat_ids() - set(_OPENAI_COMPAT_PROVIDERS))

    assert missing == [], (
        "providers registered with failure_family='openai_compat' but unknown to "
        f"classify_provider_error: {missing}"
    )


def test_no_classifier_entry_without_a_registered_spec() -> None:
    extra = sorted(set(_OPENAI_COMPAT_PROVIDERS) - _registry_compat_ids())

    assert extra == [], f"classifier lists providers the registry does not register: {extra}"


def test_non_openai_families_are_not_swept_in() -> None:
    """anthropic- and ollama-family providers keep their own classification."""
    for spec in list_provider_specs():
        if spec.failure_family != "openai_compat":
            assert spec.provider_id not in _OPENAI_COMPAT_PROVIDERS


@pytest.mark.parametrize("provider", ["bankr", "openai_responses"])
@pytest.mark.parametrize(
    "status_code,message,expected",
    [
        (401, "invalid api key", ProviderFailureKind.AUTH_INVALID),
        (403, "", ProviderFailureKind.AUTH_INVALID),
        (402, "", ProviderFailureKind.INSUFFICIENT_CREDITS),
        (429, "", ProviderFailureKind.RATE_LIMITED),
        (400, "", ProviderFailureKind.BAD_REQUEST),
        (404, "model not found", ProviderFailureKind.MODEL_NOT_FOUND),
        (503, "", ProviderFailureKind.PROVIDER_OVERLOADED),
    ],
)
def test_reported_providers_classify_like_their_siblings(
    provider: str, status_code: int, message: str, expected: ProviderFailureKind
) -> None:
    assert classify_provider_error(provider, status_code=status_code, message=message) == expected


@pytest.mark.parametrize("status_code", [401, 402, 429, 400])
def test_every_compat_provider_classifies_the_core_statuses(status_code: int) -> None:
    """No provider in the family may answer UNKNOWN for a status its siblings map."""
    for provider in sorted(_OPENAI_COMPAT_PROVIDERS):
        kind = classify_provider_error(provider, status_code=status_code)
        assert kind is not ProviderFailureKind.UNKNOWN, f"{provider} @ {status_code}"
