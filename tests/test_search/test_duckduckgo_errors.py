"""DuckDuckGo failures are structured errors, not an empty result list."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from agentos.search.providers.duckduckgo import DuckDuckGoProvider
from agentos.search.types import SearchProviderError
from agentos.tools.builtin import web


@pytest.fixture(autouse=True)
def clean_search_runtime():
    web.reset_search_runtime()
    yield
    web.reset_search_runtime()


def _mock_transport(monkeypatch, error: Exception) -> None:
    mock_client = AsyncMock()
    mock_client.post.side_effect = error
    monkeypatch.setattr("httpx.AsyncClient.__aenter__", AsyncMock(return_value=mock_client))


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://html.duckduckgo.com/html")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("HTTP Status Error", request=request, response=response)


def test_duckduckgo_diagnostics_default_is_on() -> None:
    assert DuckDuckGoProvider()._diagnostics is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_kind,retryable",
    [
        (401, "auth", False),
        (403, "auth", False),
        (429, "rate_limit", True),
        (500, "http", True),
        (503, "http", True),
    ],
)
async def test_duckduckgo_http_status_errors(
    monkeypatch, status_code: int, expected_kind: str, retryable: bool
) -> None:
    _mock_transport(monkeypatch, _status_error(status_code))

    with pytest.raises(SearchProviderError) as exc_info:
        await DuckDuckGoProvider().search("hello")

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.status_code == status_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.provider == "duckduckgo"


@pytest.mark.asyncio
async def test_duckduckgo_timeout_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://html.duckduckgo.com/html")
    _mock_transport(monkeypatch, httpx.TimeoutException("Timeout Error", request=request))

    with pytest.raises(SearchProviderError) as exc_info:
        await DuckDuckGoProvider().search("hello")

    assert exc_info.value.kind == "timeout"
    assert exc_info.value.retryable is True
    assert exc_info.value.status_code is None


@pytest.mark.asyncio
async def test_duckduckgo_network_error(monkeypatch) -> None:
    _mock_transport(monkeypatch, httpx.HTTPError("HTTP Error"))

    with pytest.raises(SearchProviderError) as exc_info:
        await DuckDuckGoProvider().search("hello")

    assert exc_info.value.kind == "network"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _status_error(429),
        httpx.TimeoutException("Timeout Error"),
        httpx.HTTPError("HTTP Error"),
    ],
)
async def test_duckduckgo_opt_out_still_swallows(monkeypatch, error: Exception) -> None:
    """``diagnostics=False`` keeps the historical never-raises contract."""
    _mock_transport(monkeypatch, error)

    assert await DuckDuckGoProvider(diagnostics=False).search("hello") == []


def test_search_kwargs_no_longer_force_diagnostics_off() -> None:
    """The tool boundary must not re-bury what the provider now reports."""
    web.configure_search("duckduckgo", diagnostics=False)

    assert "diagnostics" not in web._search_provider_kwargs("duckduckgo")


def test_search_kwargs_still_enable_diagnostics() -> None:
    web.configure_search("duckduckgo", diagnostics=True)

    assert web._search_provider_kwargs("duckduckgo")["diagnostics"] is True


@pytest.mark.asyncio
async def test_web_search_payload_reports_rate_limit(monkeypatch) -> None:
    """End-to-end: an upstream 429 surfaces as a classified tool failure."""
    web.configure_search("duckduckgo")
    _mock_transport(monkeypatch, _status_error(429))

    payload = await web.run_web_search_payload("hello")

    assert payload["ok"] is False
    assert payload["error"]["kind"] == "rate_limit"
    assert payload["error"]["retryable"] is True
    assert payload["results"] == []
