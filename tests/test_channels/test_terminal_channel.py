"""``TerminalChannel`` stdin reads and stdout ordering.

``_get_reader`` hands ``sys.stdin`` to ``loop.connect_read_pipe``. On the
default Windows ``ProactorEventLoop`` that registers the handle with IOCP,
which only accepts overlapped handles -- a console handle is not one, so the
transport dies with ``OSError: [WinError 6] The handle is invalid`` and every
``receive()`` fails. ``send``/``edit`` already run blocking stdio in a thread;
these tests pin ``receive`` to the same approach on Windows.

They also cover the adapter's two shared resources. Overlapping reads take
turns on both platforms -- a second ``StreamReader.readline()`` entered
while the first is still waiting raises ``RuntimeError`` -- and concurrent
writes to the one stdout keep call order rather than completion order.
"""

from __future__ import annotations

import asyncio
import io
import time

import pytest

from agentos.channels import terminal as terminal_module
from agentos.channels.terminal import TerminalChannel
from agentos.channels.types import OutgoingMessage


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
async def test_posix_overlapping_receives_take_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entering readline() twice at once raises; the lock must serialize.

    A caller that wraps ``receive()`` in a timeout and retries lands here:
    the cancelled read can still be waiting when the next one starts.
    """
    monkeypatch.setattr(terminal_module, "_ON_WINDOWS", False)
    reader = asyncio.StreamReader()

    async def _reader(self: TerminalChannel) -> asyncio.StreamReader:
        return reader

    monkeypatch.setattr(TerminalChannel, "_get_reader", _reader)
    channel = TerminalChannel()

    first = asyncio.create_task(channel.receive())
    await asyncio.sleep(0)  # let the first read reach readline()
    second = asyncio.create_task(channel.receive())
    await asyncio.sleep(0)

    reader.feed_data(b"one\ntwo\n")
    messages = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

    assert [m.content for m in messages] == ["one", "two"]


@pytest.mark.asyncio
async def test_concurrent_sends_keep_call_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concurrent deliveries share one stdout; call order must survive."""
    written: list[int] = []

    def _slow_write(text: str) -> None:
        # Earlier messages take longest, so an unserialized write lets the
        # later ones finish first -- exactly the reordering being fixed.
        index = int(text)
        time.sleep(0.02 * (5 - index) if index < 5 else 0)
        written.append(index)

    monkeypatch.setattr(TerminalChannel, "_write_stdout", staticmethod(_slow_write))
    channel = TerminalChannel()

    await asyncio.gather(*[channel.send(OutgoingMessage(content=str(i))) for i in range(8)])

    assert written == list(range(8))


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
