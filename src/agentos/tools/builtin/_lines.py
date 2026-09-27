"""Line splitting that counts lines the way the rest of the world counts them.

``str.splitlines()`` splits on eleven characters, not just the newline: ``\\r``
on its own, ``\\v``, ``\\f``, ``\\x1c``, ``\\x1d``, ``\\x1e``, ``\\x85``,
``\\u2028`` and ``\\u2029`` are all line boundaries to it. Nothing else in the
toolchain agrees. ``git diff`` counts newlines; so does every editor the user
reads the file in; so does ``read_file``, which numbers lines by iterating the
binary handle.

A file carrying one of those characters is therefore numbered differently by
different tools -- a lone ``\\r`` inside a line (log captures, progress output,
CSV exports) or an ``\\f`` page break (common in Python sources) is enough. The
tool reports a line number the user and the next tool cannot use, and nothing
in either result says the two were counted differently.

These helpers split on newlines only. ``\\r\\n`` is one terminator, as it is
everywhere else; every other character is ordinary content.
"""

from __future__ import annotations

__all__ = ["split_lines", "split_lines_keepends"]


def split_lines(text: str) -> list[str]:
    """Lines of *text* without their terminators, split on newlines only.

    The ``\\r`` of a CRLF is dropped with the ``\\n`` -- a caller displaying a
    matched line should not be handed a stray carriage return -- and a trailing
    newline does not produce a final empty line, both matching
    ``str.splitlines()`` for text that contains no other boundary character.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def split_lines_keepends(text: str) -> list[str]:
    """Lines of *text* with their terminators, split on newlines only.

    The keepends counterpart of :func:`split_lines`: ``"a\\nb"`` is
    ``["a\\n", "b"]`` and ``"a\\r\\nb"`` is ``["a\\r\\n", "b"]``, so a line's
    own ending survives a round trip through ``"".join``.
    """
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines
