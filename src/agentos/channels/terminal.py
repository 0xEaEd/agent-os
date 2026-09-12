"""TerminalChannel: interactive stdin/stdout channel adapter."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field

import structlog

from agentos.channels.types import IncomingMessage, OutgoingMessage

log = structlog.get_logger(__name__)

# ``loop.connect_read_pipe(sys.stdin)`` cannot work on Windows: the default
# ProactorEventLoop hands ``sys.stdin.fileno()`` -- a CRT fd, not a Win32
# handle -- to IOCP registration, which fails with ``OSError: [WinError 6]
# The handle is invalid`` (and a console handle would not be overlapped
# anyway). The failure surfaces from the first ``readline()``, not from
# ``connect_read_pipe`` itself, so it cannot be caught and retried there; a
# SelectorEventLoop raises NotImplementedError instead. Reads go through a
# thread on Windows, the same way ``send``/``edit`` already write.
_ON_WINDOWS = os.name == "nt"


@dataclass
class TerminalChannel:
    """Channel adapter for interactive terminal (stdin/stdout)."""

    channel_id: str = "terminal"
    sender_id: str = "user"
    _reader: asyncio.StreamReader | None = field(default=None, init=False, repr=False)
    _reader_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    async def _get_reader(self) -> asyncio.StreamReader:
        async with self._reader_lock:
            if self._reader is None:
                loop = asyncio.get_running_loop()
                reader = asyncio.StreamReader()
                protocol = asyncio.StreamReaderProtocol(reader)
                await loop.connect_read_pipe(lambda: protocol, sys.stdin)
                self._reader = reader
            return self._reader

    async def _read_line(self) -> bytes:
        """Return one raw line from stdin, ``b""`` at EOF.

        On Windows the read runs in the default executor under
        ``_reader_lock`` so overlapping ``receive()`` calls take turns
        instead of racing for lines. Cancelling the awaiting task does not
        unblock the worker thread: it keeps waiting for the next line and
        discards it, and interpreter exit joins it, so wrap ``receive()`` in
        a timeout only with that in mind.
        """
        if _ON_WINDOWS:
            loop = asyncio.get_running_loop()
            async with self._reader_lock:
                return await loop.run_in_executor(None, self._blocking_readline)
        reader = await self._get_reader()
        return await reader.readline()

    async def receive(self) -> IncomingMessage:
        """Read one line from stdin and return as IncomingMessage."""
        line_bytes = await self._read_line()
        content = line_bytes.decode(errors="replace").removesuffix("\n").removesuffix("\r")
        log.debug("terminal.receive", content=content[:80])
        return IncomingMessage(
            sender_id=self.sender_id,
            channel_id=self.channel_id,
            content=content,
        )

    async def send(self, message: OutgoingMessage) -> None:
        """Write message content to stdout."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_stdout, message.content)
        log.debug("terminal.send", content=message.content[:80])

    async def edit(self, message_id: str, content: str) -> None:
        """Edit is not supported on terminal; re-print with prefix."""
        prefix = f"[edit:{message_id}] "
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_stdout, prefix + content)
        log.debug("terminal.edit", message_id=message_id)

    async def delete(self, message_id: str) -> None:
        """Delete is not supported on terminal; print a notice."""
        notice = f"[deleted:{message_id}]\n"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_stdout, notice)
        log.debug("terminal.delete", message_id=message_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _blocking_readline() -> bytes:
        """Read one raw line from stdin; ``b""`` at EOF.

        Reads the underlying byte stream so both platform branches decode
        with the same UTF-8 ``errors="replace"`` policy (a Windows console
        yields UTF-8 bytes; redirected input is assumed UTF-8, as on POSIX).
        A replaced ``sys.stdin`` (a ``StringIO`` in tests or an embedder) has
        no ``buffer``; its text is re-encoded so the caller sees bytes either
        way.
        """
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is not None:
            return bytes(buffer.readline())
        return sys.stdin.readline().encode("utf-8", errors="replace")

    @staticmethod
    def _write_stdout(text: str) -> None:
        if not text.endswith("\n"):
            text += "\n"
        sys.stdout.write(text)
        sys.stdout.flush()
