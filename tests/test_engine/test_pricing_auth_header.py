"""Issue #3230: PricingCache sent an empty bearer token.

``refresh`` built its headers unconditionally::

    headers = {
        "Authorization": f"Bearer {self._api_key}",
        "Content-Type": "application/json",
    }

With no OpenRouter key configured, ``self._api_key`` is ``""`` and the header
goes out as ``Authorization: Bearer `` -- which is not the same as sending no
header. OpenRouter reads it as a credential, fails to find it, and answers
401, on an endpoint whose model list is public and would have answered.

So every deployment without an OpenRouter key -- a local Ollama setup, direct
provider keys -- lost live pricing entirely, and the log line said
"unauthorized" rather than "no key configured".
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agentos.engine.pricing import PricingCache

_MODELS_BODY = {
    "data": [
        {
            "id": "vendor/model",
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
    ]
}


def _install(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_async_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("agentos.engine.pricing.httpx.AsyncClient", fake_async_client)


def _recorder(status: int = 200) -> Any:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        return httpx.Response(status, json=_MODELS_BODY)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("api_key", ["", "   "])
async def test_no_authorization_header_without_a_key(
    monkeypatch: pytest.MonkeyPatch, api_key: str
) -> None:
    """The issue's repro. Whitespace is included because the constructor
    strips paste boundaries, so a blank-looking key arrives as ``""``."""
    handler = _recorder()
    _install(monkeypatch, handler)

    await PricingCache(api_key=api_key).refresh()

    assert "authorization" not in handler.seen["headers"]


@pytest.mark.asyncio
async def test_an_empty_bearer_is_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stated directly, since the empty bearer -- not the missing key -- is
    what OpenRouter rejects."""
    handler = _recorder()
    _install(monkeypatch, handler)

    await PricingCache(api_key="").refresh()

    assert handler.seen["headers"].get("authorization", "").strip() != "Bearer"
    assert not any(
        value.strip() in {"Bearer", "Bearer "} for value in handler.seen["headers"].values()
    )


@pytest.mark.asyncio
async def test_pricing_is_populated_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the fix: the public endpoint answers, so the cache fills."""
    _install(monkeypatch, _recorder())
    cache = PricingCache(api_key="")

    await cache.refresh()

    assert cache.get_price_sync("vendor/model") is not None


@pytest.mark.asyncio
async def test_a_configured_key_is_still_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _recorder()
    _install(monkeypatch, handler)

    await PricingCache(api_key="sk-or-test-key").refresh()

    assert handler.seen["headers"]["authorization"] == "Bearer sk-or-test-key"


@pytest.mark.asyncio
async def test_the_other_headers_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Content-Type and the OpenRouter app headers must still go out with or
    without a key -- dropping the key must not drop its neighbours."""
    handler = _recorder()
    _install(monkeypatch, handler)

    await PricingCache(api_key="").refresh()

    assert handler.seen["headers"]["content-type"] == "application/json"
