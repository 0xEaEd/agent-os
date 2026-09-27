"""Issue #3176: two tools counted lines with ``str.splitlines()``.

``str.splitlines()`` breaks on eleven characters, not just the newline: a
lone ``\\r``, ``\\v``, ``\\f``, ``\\x1c``, ``\\x1d``, ``\\x1e``, ``\\x85``,
``\\u2028`` and ``\\u2029``. ``git diff`` does not, editors do not, and
neither does this repo's own ``read_file``, which numbers lines by iterating
the binary handle.

So a file carrying one of them was numbered differently by different tools:

* ``grep_search`` reported a hit at a line number ``read_file`` disagreed
  with, and the model edits by that number.
* ``apply_patch`` shifted every line after the character against the hunk
  headers, rejecting a correct patch with a bogus context mismatch -- or
  splicing at the wrong offset when the shifted context happened to match.

Both sites are covered here because they are one root cause with one fix.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentos.tools.builtin import filesystem as fs
from agentos.tools.builtin._lines import split_lines, split_lines_keepends
from agentos.tools.builtin.patch import _LINE_BOUNDARIES, Hunk, _updated_text
from agentos.tools.types import CallerKind, ToolContext, current_tool_context

# ``<absolute path>:<lineno>: <text>`` -- split on the line number, not the
# first colon, which on Windows is the drive letter's.
_RESULT_LINE = re.compile(r"^(?P<file>.+?):(?P<lineno>\d+): ")


@contextmanager
def _tool_context(workspace: Path) -> Iterator[None]:
    token = current_tool_context.set(
        ToolContext(
            caller_kind=CallerKind.CLI,
            channel_kind="cli",
            channel_id="cli:test",
            workspace_dir=str(workspace),
            workspace_strict=True,
        )
    )
    try:
        yield
    finally:
        current_tool_context.reset(token)


# ---------------------------------------------------------------------------
# The splitters themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "a", "a\n", "a\nb", "a\nb\n", "a\r\nb\r\n", "a\r\nb", "\n\n", "\n"],
)
def test_split_lines_matches_splitlines_on_ordinary_text(text: str) -> None:
    """Text with no exotic boundary must be split exactly as before, or the
    fix would be a behaviour change for every normal file."""
    assert split_lines(text) == text.splitlines()
    assert split_lines_keepends(text) == text.splitlines(keepends=True)


@pytest.mark.parametrize(
    "boundary",
    ["\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_a_non_newline_boundary_is_content_not_a_line_break(boundary: str) -> None:
    text = f"alpha\nbeta{boundary}gamma\ndelta\n"

    assert split_lines(text) == ["alpha", f"beta{boundary}gamma", "delta"]
    assert len(text.splitlines()) == 4  # what the bug saw


@pytest.mark.parametrize(
    "text",
    ["", "a", "a\n", "a\nb", "a\r\nb\r\n", "a\fb\nc", "a\rb", "\n\n"],
)
def test_split_lines_keepends_round_trips(text: str) -> None:
    """Nothing may be dropped or invented: joining the pieces rebuilds the
    input byte for byte. ``_updated_text`` relies on this."""
    assert "".join(split_lines_keepends(text)) == text


def test_split_lines_drops_the_cr_of_a_crlf_but_keeps_a_lone_cr() -> None:
    assert split_lines("a\r\nb\n") == ["a", "b"]
    assert split_lines("a\rb\n") == ["a\rb"]


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------


def test_a_patch_applies_across_a_form_feed() -> None:
    """The issue's own repro: a form feed inside line 2 shifted line 3."""
    text = "alpha\nbeta\x0cgamma\ndelta\n"
    hunk = Hunk(old_start=3, old_count=1, new_start=3, new_count=1, lines=["-delta", "+DELTA"])

    assert _updated_text(text, [hunk]) == "alpha\nbeta\x0cgamma\nDELTA\n"


def test_a_patch_applies_across_a_lone_carriage_return() -> None:
    text = "alpha\nbeta\rcarriage\ndelta\n"
    hunk = Hunk(old_start=3, old_count=1, new_start=3, new_count=1, lines=["-delta", "+DELTA"])

    assert _updated_text(text, [hunk]) == "alpha\nbeta\rcarriage\nDELTA\n"


def test_the_boundary_character_survives_the_round_trip() -> None:
    """Counting it as content is only correct if it is still written back."""
    text = "a\nb\x0cc\nd\n"
    hunk = Hunk(old_start=1, old_count=1, new_start=1, new_count=1, lines=["-a", "+A"])

    assert "\x0c" in _updated_text(text, [hunk])


def test_an_ordinary_lf_patch_is_unchanged() -> None:
    text = "one\ntwo\nthree\n"
    hunk = Hunk(old_start=2, old_count=1, new_start=2, new_count=1, lines=["-two", "+TWO"])

    assert _updated_text(text, [hunk]) == "one\nTWO\nthree\n"


def test_an_ordinary_crlf_patch_keeps_its_endings() -> None:
    text = "one\r\ntwo\r\nthree\r\n"
    hunk = Hunk(old_start=2, old_count=1, new_start=2, new_count=1, lines=["-two", "+TWO"])

    assert _updated_text(text, [hunk]) == "one\r\nTWO\r\nthree\r\n"


# ---------------------------------------------------------------------------
# grep_search vs read_file
# ---------------------------------------------------------------------------


async def _grep_lineno(repo: Path, pattern: str) -> int:
    with _tool_context(repo):
        out = await fs.grep_search(pattern, path=str(repo))
    assert not out.startswith("No matches"), out
    match = _RESULT_LINE.match(out.splitlines()[0])
    assert match is not None, out
    return int(match["lineno"])


async def _read_file_lineno(repo: Path, name: str, needle: str) -> int:
    with _tool_context(repo):
        out = await fs.read_file(str(repo / name))
    for row in out.split("\n"):
        number, _, text = row.partition("\t")
        if needle in text:
            return int(number)
    raise AssertionError(f"{needle!r} not found in read_file output: {out!r}")


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["\r", "\f", "\x85"])
async def test_grep_search_and_read_file_agree_on_the_line_number(
    tmp_path: Path, boundary: str
) -> None:
    """The silent half of the bug: grep_search named a line the model then
    edited, and read_file showed something else there -- or nothing at all."""
    (tmp_path / "sample.txt").write_text(
        f"alpha\nbeta{boundary}carriage\ngamma NEEDLE\n", encoding="utf-8"
    )

    assert await _grep_lineno(tmp_path, "NEEDLE") == await _read_file_lineno(
        tmp_path, "sample.txt", "NEEDLE"
    )


@pytest.mark.asyncio
async def test_grep_search_still_numbers_an_ordinary_file_correctly(
    tmp_path: Path,
) -> None:
    (tmp_path / "plain.txt").write_text("one\ntwo\nthree NEEDLE\n", encoding="utf-8")

    assert await _grep_lineno(tmp_path, "NEEDLE") == 3


# ---------------------------------------------------------------------------
# _LINE_BOUNDARIES follows the same rule as the splitter
# ---------------------------------------------------------------------------


def test_only_a_newline_terminates_a_line() -> None:
    """The two have to agree. While _LINE_BOUNDARIES still listed every
    str.splitlines() boundary, a last line ending in a form feed counted as
    already terminated even though the splitter no longer breaks there."""
    assert _LINE_BOUNDARIES == ("\n",)


def test_a_final_line_ending_in_a_boundary_character_is_terminated() -> None:
    """Appending after a last line that ends in a form feed must put a newline
    between them. Treating the form feed as the terminator merged the two into
    one line, so the file came back with a different line count than read_file
    would report for it."""
    text = "alpha\nbeta\ngamma\x0c"
    hunk = Hunk(
        old_start=3,
        old_count=1,
        new_start=3,
        new_count=2,
        lines=[" gamma\x0c", "+new"],
    )

    updated = _updated_text(text, [hunk])

    assert updated == "alpha\nbeta\ngamma\x0c\nnew\n"
    assert len(split_lines(updated)) == 4


def test_a_final_line_without_any_terminator_still_gets_one() -> None:
    """The unchanged half of the same rule."""
    text = "alpha\nbeta"
    hunk = Hunk(old_start=2, old_count=1, new_start=2, new_count=2, lines=[" beta", "+new"])

    assert _updated_text(text, [hunk]) == "alpha\nbeta\nnew\n"
