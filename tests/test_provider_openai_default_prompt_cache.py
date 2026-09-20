"""Issue #3183: the default OpenAI endpoint reported no prompt caching.

``provider_context_capabilities`` claimed ``PromptCacheSupport.AUTOMATIC``
for ``openai`` only when ``"api.openai.com" in base_url``. ``base_url``
defaults to ``""``, which is not an unknown host -- it is the OpenAI SDK's
own default, ``https://api.openai.com/v1``. Asking about the standard
OpenAI provider without naming a URL therefore reported
``PromptCacheSupport.NONE``.

The host check earns its keep: this provider is also how people reach
OpenAI-compatible servers (vLLM, Ollama, LM Studio, a gateway), which do not
cache automatically. But those are configured *by setting* a base_url, never
by omitting one -- so an empty value is the one case the check should not
have been withholding the claim from.

Every other branch in the function already decides on ``provider`` alone;
``openai`` was the one that needed a base_url to recognise its own endpoint.
"""

from __future__ import annotations

import pytest

from agentos.provider.context_capabilities import (
    PromptCacheSupport,
    provider_context_capabilities,
)


def test_the_default_endpoint_supports_automatic_prompt_caching() -> None:
    """The issue's repro: no base_url means the SDK default."""
    caps = provider_context_capabilities(provider_kind="openai", model="gpt-4o")

    assert caps.prompt_cache == PromptCacheSupport.AUTOMATIC


@pytest.mark.parametrize("base_url", ["", "   ", None])
def test_an_absent_base_url_in_any_spelling_is_the_default_endpoint(
    base_url: str | None,
) -> None:
    kwargs = {} if base_url is None else {"base_url": base_url}
    caps = provider_context_capabilities(provider_kind="openai", model="gpt-4o", **kwargs)

    assert caps.prompt_cache == PromptCacheSupport.AUTOMATIC


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://api.openai.com",
        "HTTPS://API.OPENAI.COM/v1",
    ],
)
def test_an_explicit_openai_base_url_is_unchanged(base_url: str) -> None:
    caps = provider_context_capabilities(provider_kind="openai", model="gpt-4o", base_url=base_url)

    assert caps.prompt_cache == PromptCacheSupport.AUTOMATIC


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:11434/v1",
        "http://localhost:8000/v1",
        "https://api.together.xyz/v1",
        "https://openrouter.ai/api/v1",
    ],
)
def test_an_openai_compatible_server_still_claims_nothing(base_url: str) -> None:
    """The reason the host check exists, and the line the fix must not cross:
    a third-party server does not cache automatically, and is always reached
    by setting a base_url."""
    caps = provider_context_capabilities(
        provider_kind="openai", model="some-local-model", base_url=base_url
    )

    assert caps.prompt_cache == PromptCacheSupport.NONE


def test_an_unknown_provider_without_a_base_url_still_claims_nothing() -> None:
    """The empty-base_url allowance is scoped to ``openai`` and must not leak
    into the fall-through."""
    caps = provider_context_capabilities(provider_kind="mystery", model="m")

    assert caps.prompt_cache == PromptCacheSupport.NONE


def test_the_openai_branch_still_reports_non_portable_state() -> None:
    """The rest of the branch's payload is unchanged."""
    caps = provider_context_capabilities(provider_kind="openai", model="gpt-4o")

    assert caps.provider == "openai"
    assert caps.model == "gpt-4o"
    assert caps.state_portable_across_providers is False
