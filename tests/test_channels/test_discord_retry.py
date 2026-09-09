"""Transient-HTTP retries on the Discord adapter's outbound calls.

The file-upload case is the one that used to corrupt data: ``send_file``
opened the handle *outside* ``retry_request``, so the second attempt after a
429/5xx/timeout re-sent an exhausted stream and Discord stored a 0-byte file
without anything raising. These tests assert on the bytes each attempt
actually carried, not merely that the file was reopened.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentos.channels.discord import DiscordChannel, DiscordChannelConfig

_REQUEST = httpx.Request("POST", "https://discord.test/api")


def _resp(
    status_code: int = 200,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body if body is not None else {"id": "42"},
        headers=headers,
        request=_REQUEST,
    )


def _channel() -> DiscordChannel:
    return DiscordChannel(
        config=DiscordChannelConfig(token="bot-test", default_channel_id="C123")
    )


def _attach(channel: DiscordChannel, side_effect: Any) -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(side_effect=side_effect)
    channel._client = client
    return client.post


@pytest.fixture
def no_sleep():
    """Collapse ``retry_request``'s backoff so the assertions stay fast."""
    with patch("agentos.channels._util.asyncio.sleep", new=AsyncMock()) as sleep:
        yield sleep


def _recording_post(
    bodies: list[bytes], statuses: list[int]
) -> Any:
    """Return a ``client.post`` stub that records the uploaded body per attempt."""
    remaining = list(statuses)

    async def _post(url: str, **kwargs: Any) -> httpx.Response:
        bodies.append(kwargs["files"]["file"][1].read())
        status = remaining.pop(0) if remaining else 200
        headers = {"Retry-After": "1"} if status == 429 else None
        return _resp(status, headers=headers)

    return _post


async def test_send_file_rate_limited_retry_uploads_full_body(
    tmp_path: Path, no_sleep
) -> None:
    """429 is the likeliest Discord retry — the second attempt must not be empty."""
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"hello world")

    channel = _channel()
    bodies: list[bytes] = []
    _attach(channel, _recording_post(bodies, [429, 200]))

    result = await channel.send_file("C123", str(sample), content="here")

    assert result.provider_message_id == "42"
    assert bodies == [b"hello world", b"hello world"]


async def test_send_file_server_error_retry_uploads_full_body(
    tmp_path: Path, no_sleep
) -> None:
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"payload-bytes")

    channel = _channel()
    bodies: list[bytes] = []
    _attach(channel, _recording_post(bodies, [503, 200]))

    await channel.send_file("C123", str(sample))

    assert bodies == [b"payload-bytes", b"payload-bytes"]


async def test_send_file_timeout_retry_uploads_full_body(tmp_path: Path, no_sleep) -> None:
    """A timeout raises before the body is recorded, so attempt 2 is the first record."""
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"abc123")

    channel = _channel()
    bodies: list[bytes] = []
    attempts = 0

    async def _post(url: str, **kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        bodies.append(kwargs["files"]["file"][1].read())
        if attempts == 1:
            raise httpx.ConnectTimeout("slow")
        return _resp(200)

    _attach(channel, _post)

    await channel.send_file("C123", str(sample))

    assert attempts == 2
    assert bodies == [b"abc123", b"abc123"]


async def test_send_file_succeeds_first_try_without_retry(tmp_path: Path, no_sleep) -> None:
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"once")

    channel = _channel()
    bodies: list[bytes] = []
    post = _attach(channel, _recording_post(bodies, [200]))

    await channel.send_file("C123", str(sample), content="caption")

    assert post.await_count == 1
    assert bodies == [b"once"]
    no_sleep.assert_not_awaited()


async def test_send_file_passes_content_and_filename(tmp_path: Path, no_sleep) -> None:
    """The closure must keep the same payload shape the direct call had."""
    sample = tmp_path / "report.md"
    sample.write_bytes(b"# report")

    channel = _channel()
    seen: list[dict[str, Any]] = []

    async def _post(url: str, **kwargs: Any) -> httpx.Response:
        seen.append({"url": url, **kwargs})
        return _resp(200)

    _attach(channel, _post)

    await channel.send_file("C999", str(sample), content="see attached")

    assert seen[0]["url"] == "/channels/C999/messages"
    assert seen[0]["data"] == {"content": "see attached"}
    assert seen[0]["files"]["file"][0] == "report.md"
    assert seen[0]["headers"] == {"Authorization": "Bot bot-test"}


async def test_send_file_omits_data_when_no_content(tmp_path: Path, no_sleep) -> None:
    sample = tmp_path / "report.md"
    sample.write_bytes(b"# report")

    channel = _channel()
    seen: list[dict[str, Any]] = []

    async def _post(url: str, **kwargs: Any) -> httpx.Response:
        seen.append(dict(kwargs))
        return _resp(200)

    _attach(channel, _post)

    await channel.send_file("C123", str(sample))

    assert seen[0]["data"] == {}


async def test_send_file_records_sent_message_for_edit_routing(
    tmp_path: Path, no_sleep
) -> None:
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"body")

    channel = _channel()
    bodies: list[bytes] = []
    _attach(channel, _recording_post(bodies, [429, 200]))

    await channel.send_file("C777", str(sample))

    assert channel._sent_messages["42"] == "C777"


async def test_send_file_raises_after_retries_are_exhausted(
    tmp_path: Path, no_sleep
) -> None:
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"body")

    channel = _channel()
    post = _attach(channel, [httpx.ConnectError("down")] * 4)

    with pytest.raises(httpx.ConnectError):
        await channel.send_file("C123", str(sample))

    assert post.await_count == 4  # initial attempt + max_retries=3


async def test_send_file_surfaces_fatal_client_error_without_retry(
    tmp_path: Path, no_sleep
) -> None:
    sample = tmp_path / "note.txt"
    sample.write_bytes(b"body")

    channel = _channel()
    bodies: list[bytes] = []
    post = _attach(channel, _recording_post(bodies, [403]))

    with pytest.raises(httpx.HTTPStatusError):
        await channel.send_file("C123", str(sample))

    assert post.await_count == 1
    no_sleep.assert_not_awaited()
