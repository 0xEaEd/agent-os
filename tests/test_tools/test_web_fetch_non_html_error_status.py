"""Issue #3231: a non-HTML error response was packaged as a success.

``web_fetch`` decided on the *content type* before the *status*::

    # --- Non-HTML: return as-is ---
    if not is_html:
        ...
        _cache[cache_key] = result
        return ...

    # --- Error HTTP status: return empty ---
    if status >= 400:
        ...

So a JSON API's 404, or a plain-text 502, never reached the error branch. Two
consequences, both reported:

1. the result carried no ``error`` hint, so the model read an upstream failure
   as a successful ``raw`` extraction whose body happened to be an error;
2. it was cached for the full TTL -- including transient statuses (500, 502,
   503, 429, ...), which the HTML path explicitly refuses to cache, so a
   momentary outage stuck for fifteen minutes.

A status is a property of the response, not of its body, so it is decided
first.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.integration import configure_runtime, reset_runtime
from agentos.tools.builtin import web_fetch as wf


@pytest.fixture
def sandbox_off(tmp_path: Any) -> Any:
    from pathlib import Path

    configure_runtime(
        SandboxSettings(sandbox=False, security_grading=False, allow_legacy_mode=True),
        workspace=Path(tmp_path),
    )
    yield
    reset_runtime()


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    wf._cache.clear()
    yield
    wf._cache.clear()


def _e2e_resolver(addr: str) -> Any:
    def resolver(host: Any, port: Any, **_kw: Any) -> list[tuple[Any, ...]]:
        return [(2, 1, 6, "", (addr, 0))]

    return resolver


def _install(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_async_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("socket.getaddrinfo", _e2e_resolver("93.184.216.34"))
    monkeypatch.setattr(wf.httpx, "AsyncClient", fake_async_client)


def _responder(status: int, content_type: str, body: bytes) -> Any:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, headers={"content-type": content_type}, content=body)

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/json", "text/plain"])
async def test_a_non_html_error_carries_the_error_hint(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any, content_type: str
) -> None:
    """The issue's repro."""
    _install(monkeypatch, _responder(404, content_type, b'{"error": "not found"}'))

    result = json.loads(await wf.web_fetch("https://api.example.test/missing.json"))

    assert result["status"] == 404
    assert result["error"]
    assert result["extractor"] == "none"


@pytest.mark.asyncio
async def test_a_transient_non_html_error_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """The half that outlives the request: a 502 used to stick for the full
    TTL, so a momentary outage looked permanent to the agent."""
    handler = _responder(502, "application/json", b'{"error": "bad gateway"}')
    _install(monkeypatch, handler)

    url = "https://api.example.test/flaky.json"
    await wf.web_fetch(url)
    await wf.web_fetch(url)

    assert handler.calls["n"] >= 2  # refetched, not served from cache
    assert not wf._cache


@pytest.mark.asyncio
async def test_a_permanent_non_html_error_is_still_cached(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """Caching is not the bug -- caching a *transient* status was. A 404 is
    worth remembering, exactly as the HTML path already does."""
    _install(monkeypatch, _responder(404, "application/json", b"{}"))

    await wf.web_fetch("https://api.example.test/gone.json")

    assert wf._cache


@pytest.mark.asyncio
async def test_a_successful_non_html_response_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """The reordering must not disturb the path it was moved past."""
    _install(monkeypatch, _responder(200, "application/json", b'{"ok": true}'))

    result = json.loads(await wf.web_fetch("https://api.example.test/ok.json"))

    assert result["status"] == 200
    assert result["extractor"] == "raw"
    assert "error" not in result
    assert '{"ok": true}' in result["text"]


@pytest.mark.asyncio
async def test_an_html_error_still_behaves_as_before(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    _install(monkeypatch, _responder(404, "text/html", b"<html><body>gone</body></html>"))

    result = json.loads(await wf.web_fetch("https://example.test/missing"))

    assert result["status"] == 404
    assert result["error"]
    assert result["text"] == ""


@pytest.mark.asyncio
async def test_a_non_html_error_body_is_not_presented_as_content(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """An upstream error page must not read as the document that was asked
    for -- that is what let a 404 pass for content."""
    _install(
        monkeypatch,
        _responder(500, "text/plain", b"Internal Server Error: database unreachable"),
    )

    result = json.loads(await wf.web_fetch("https://api.example.test/boom"))

    assert result["text"] == ""
    assert result["length"] == 0
