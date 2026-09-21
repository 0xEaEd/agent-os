"""Tests: web_fetch routes non-HTML error responses through the error path.

The non-HTML early return used to run before the ``status >= 400`` handling,
so a 4xx/5xx body served as JSON or plain text (an API endpoint, a plain-text
error page) was returned as ``extractor: "raw"`` success with no ``error``
hint, and cached for 15 minutes — while the same status served as HTML was
correctly reported as empty with a hint, and transient statuses were never
cached. See issue #3231.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.integration import configure_runtime, reset_runtime
from agentos.tools.builtin import web_fetch as wf

_URL = "https://example.com/data.json"


@pytest.fixture
def sandbox_off(tmp_path: Path) -> Any:
    """Configure a sandbox-off runtime so the @sandboxed tool runs inline."""

    configure_runtime(
        SandboxSettings(sandbox=False, security_grading=False, allow_legacy_mode=True),
        workspace=tmp_path,
    )
    wf._cache.clear()
    yield
    wf._cache.clear()
    reset_runtime()


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Serve every web_fetch request through a mocked transport."""

    real_async_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real_async_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    monkeypatch.setattr(wf.httpx, "AsyncClient", fake_async_client)


async def _fetch(**kwargs: Any) -> dict[str, Any]:
    return json.loads(await wf.web_fetch(url=_URL, **kwargs))


@pytest.mark.asyncio
async def test_non_html_error_status_returns_error_not_raw_body(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """A 404 served as application/json must not come back as raw success."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            headers={"content-type": "application/json"},
            content=b'{"error": "not found"}',
        )

    _install_transport(monkeypatch, handler)

    result = await _fetch()

    assert result["status"] == 404
    assert result["extractor"] == "none"
    assert result["text"] == ""
    assert result["error"]
    # The cached entry is the error result, not the raw error body.
    cached = wf._cache[(_URL, "markdown")]
    assert cached["extractor"] == "none"


@pytest.mark.asyncio
async def test_non_html_transient_error_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """A 503 served as text/plain is reported as an error and not cached."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            headers={"content-type": "text/plain"},
            content=b"upstream unavailable",
        )

    _install_transport(monkeypatch, handler)
    monkeypatch.setattr(wf, "_RETRY_DELAY_SECONDS", 0)

    result = await _fetch()

    assert result["status"] == 503
    assert result["extractor"] == "none"
    assert result["text"] == ""
    assert result["error"]
    assert (_URL, "markdown") not in wf._cache


@pytest.mark.asyncio
async def test_non_html_success_still_returns_raw_body(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """Anti-drift: a 200 non-HTML body still comes back raw, without an error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
        )

    _install_transport(monkeypatch, handler)

    result = await _fetch()

    assert result["status"] == 200
    assert result["extractor"] == "raw"
    assert '{"ok": true}' in result["text"]
    assert "error" not in result


@pytest.mark.asyncio
async def test_html_error_status_still_returns_empty_with_hint(
    monkeypatch: pytest.MonkeyPatch, sandbox_off: Any
) -> None:
    """Anti-drift: the HTML error path is unchanged by the reorder."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            headers={"content-type": "text/html"},
            content=b"<html><body>nope</body></html>",
        )

    _install_transport(monkeypatch, handler)

    result = await _fetch()

    assert result["status"] == 404
    assert result["extractor"] == "none"
    assert result["text"] == ""
    assert result["error"]
