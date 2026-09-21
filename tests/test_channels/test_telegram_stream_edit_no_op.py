"""Issue #3181: a no-op edit aborted the whole Telegram stream.

Telegram refuses an ``editMessageText`` whose rendered content matches what
is already on screen: ``Bad Request: message is not modified: ...``. That is
not a failure -- the message already says what the edit would have made it
say -- but ``_stream_edit`` re-raised everything that was not a parse-entities
error, and ``send_streaming``'s loop catches only ``TelegramFloodError``. So
the exception left ``send_streaming`` entirely and every later chunk was
dropped: the user saw the reply stop mid-sentence.

It is easy to reach, because Telegram compares the *rendered* text. The
renderer works line by line, so a chunk that is only a newline renders to
exactly the HTML already posted -- and a streaming model emits those
constantly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentos.channels._telegram_formatting import render_telegram_html
from agentos.channels.telegram import (
    TelegramApiError,
    TelegramChannel,
    TelegramChannelConfig,
)

ApiCall = tuple[str, dict[str, Any]]

_NOT_MODIFIED = (
    "Bad Request: message is not modified: specified new message content and "
    "reply markup are exactly the same as a current content and reply markup "
    "of the message"
)


def _channel() -> TelegramChannel:
    return TelegramChannel(TelegramChannelConfig(token="token", default_chat_id="99"))


def _install_fake_api(channel: TelegramChannel, *, fail: Any = None) -> list[ApiCall]:
    calls: list[ApiCall] = []
    next_message_id = [100]

    async def fake_api(method: str, payload: dict[str, Any] | None = None) -> Any:
        calls.append((method, dict(payload or {})))
        if fail is not None:
            fail(method, dict(payload or {}))
        if method == "sendMessage":
            next_message_id[0] += 1
            return {"message_id": next_message_id[0]}
        return True

    channel._api = fake_api  # type: ignore[method-assign]  # noqa: SLF001
    return calls


async def _stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


def test_a_newline_only_chunk_renders_to_the_unchanged_message() -> None:
    """Why the no-op is reachable at all, pinned so the premise cannot rot."""
    assert render_telegram_html("Analysis") == render_telegram_html("Analysis\n")


@pytest.mark.asyncio
async def test_a_no_op_edit_does_not_abort_the_stream() -> None:
    channel = _channel()

    def fail(method: str, payload: dict[str, Any]) -> None:
        if method == "editMessageText":
            raise TelegramApiError(_NOT_MODIFIED)

    calls = _install_fake_api(channel, fail=fail)

    ref = await channel.send_streaming(
        _stream("Analysis", "\n", " complete"), chat_id="99", update_interval_ms=0
    )

    assert ref is not None
    # The stream ran to the end: the final text still reached the user, by
    # edit or by a closing message.
    assert any("complete" in str(payload.get("text", "")) for _m, payload in calls)


@pytest.mark.asyncio
async def test_a_no_op_on_the_plain_text_retry_is_also_survived() -> None:
    """A parse-entities failure falls back to an unformatted edit, and that
    retry can be the no-op just as easily -- dropping the parse mode does not
    make the rendered text different."""
    channel = _channel()

    def fail(method: str, payload: dict[str, Any]) -> None:
        if method != "editMessageText":
            return
        if payload.get("parse_mode") == "HTML":
            raise TelegramApiError("Bad Request: can't parse entities: ...")
        raise TelegramApiError(_NOT_MODIFIED)

    calls = _install_fake_api(channel, fail=fail)

    ref = await channel.send_streaming(
        _stream("one", " two", " three"), chat_id="99", update_interval_ms=0
    )

    assert ref is not None
    assert any("three" in str(payload.get("text", "")) for _m, payload in calls)


@pytest.mark.asyncio
async def test_a_real_edit_failure_still_propagates() -> None:
    """The valve is narrow on purpose: only the no-op is benign. A chat that
    is gone, or a message too old to edit, must not be swallowed."""
    channel = _channel()

    def fail(method: str, payload: dict[str, Any]) -> None:
        if method == "editMessageText":
            raise TelegramApiError("Bad Request: chat not found")

    _install_fake_api(channel, fail=fail)

    with pytest.raises(TelegramApiError, match="chat not found"):
        await channel.send_streaming(
            _stream("one", " two", " three"), chat_id="99", update_interval_ms=0
        )


@pytest.mark.asyncio
async def test_an_ordinary_stream_is_unchanged() -> None:
    channel = _channel()
    calls = _install_fake_api(channel)

    ref = await channel.send_streaming(
        _stream("one", " two", " three"), chat_id="99", update_interval_ms=0
    )

    assert ref is not None
    assert any(name == "editMessageText" for name, _payload in calls)
    assert any("three" in str(payload.get("text", "")) for _m, payload in calls)
