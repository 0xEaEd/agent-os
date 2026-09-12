"""``TerminalChannel`` stdin reads on Windows.

``_get_reader`` hands ``sys.stdin`` to ``loop.connect_read_pipe``. On the
default Windows ``ProactorEventLoop`` that registers the handle with IOCP,
which only accepts overlapped handles -- a console handle is not one, so the
transport dies with ``OSError: [WinError 6] The handle is invalid`` and every
``receive()`` fails. ``send``/``edit`` already run blocking stdio in a thread;
these tests pin ``receive`` to the same approach on Windows and leave the
POSIX ``StreamReader`` path untouched.
"""

from __future__ import annotations

import asyncio
import io

import pytest

from agentos.channels import terminal as terminal_module
from agentos.channels.terminal import TerminalChannel


def _binary_stdin(data: bytes) -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(data), encoding="utf-8")


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal_module, "_ON_WINDOWS", True)

    async def _never(self: TerminalChannel) -> asyncio.StreamReader:
        raise AssertionError("connect_read_pipe path must not be used on Windows")

    monkeypatch.setattr(TerminalChannel, "_get_reader", _never)


@pytest.mark.asyncio
async def test_windows_receive_reads_a_line_without_connect_read_pipe(
    windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", _binary_stdin(b"hello\nworld\n"))
    channel = TerminalChannel()

    first = await channel.receive()
    second = await channel.receive()

    assert (first.content, second.content) == ("hello", "world")
    assert first.sender_id == "user"
    assert first.channel_id == "terminal"


@pytest.mark.asyncio
async def test_windows_receive_reports_eof_as_empty_content(
    windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", _binary_stdin(b""))

    message = await TerminalChannel().receive()

    assert message.content == ""


@pytest.mark.asyncio
async def test_windows_receive_strips_a_console_crlf_terminator(
    windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", _binary_stdin(b"hello\r\n"))

    message = await TerminalChannel().receive()

    assert message.content == "hello"


@pytest.mark.asyncio
async def test_windows_receive_replaces_undecodable_bytes_like_posix(
    windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both platform branches share one decoding policy."""
    monkeypatch.setattr("sys.stdin", _binary_stdin(b"caf\xff\n"))

    message = await TerminalChannel().receive()

    assert message.content == "caf�"


@pytest.mark.asyncio
async def test_windows_receive_copes_with_a_text_only_stdin(
    windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replaced ``sys.stdin`` (tests, embedders) may have no ``.buffer``."""
    monkeypatch.setattr("sys.stdin", io.StringIO("typed\n"))

    message = await TerminalChannel().receive()

    assert message.content == "typed"


@pytest.mark.asyncio
async def test_posix_receive_still_uses_the_stream_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(terminal_module, "_ON_WINDOWS", False)
    monkeypatch.setattr("sys.stdin", _binary_stdin(b"must not be read\n"))
    reader = asyncio.StreamReader()
    reader.feed_data(b"from reader\n")
    reader.feed_eof()

    async def _reader(self: TerminalChannel) -> asyncio.StreamReader:
        return reader

    monkeypatch.setattr(TerminalChannel, "_get_reader", _reader)

    message = await TerminalChannel().receive()

    assert message.content == "from reader"
