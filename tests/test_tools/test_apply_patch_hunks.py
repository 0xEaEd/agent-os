"""Hunk-splice behaviour of ``apply_patch``, including prepend hunks.

``_parse_hunk_header`` accepts ``old_start == 0`` — it even has a dedicated
default for ``old_count`` in that case — so ``@@@ -0,0 +1,N @@@`` is a
supported way to prepend to a file. ``_apply_hunk`` converted that to
``pos = -1`` and spliced ``result[:-1] + new + result[-1:]``, quietly writing
the new lines *before the last line* instead of the first. These tests assert
on the final file content, which is what protects the reverse-sort ordering
in ``_apply_update`` too.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agentos.tools.builtin import patch as patch_tool
from agentos.tools.types import ToolContext, current_tool_context


def _original_async(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    return fn.__wrapped__.__wrapped__  # type: ignore[attr-defined, no-any-return]


async def _apply(workspace: Path, patch_text: str) -> str:
    token = current_tool_context.set(ToolContext(workspace_dir=str(workspace)))
    try:
        return await _original_async(patch_tool.apply_patch)(patch_text)
    finally:
        current_tool_context.reset(token)


@pytest.mark.asyncio
async def test_prepend_hunk_inserts_at_the_top_of_the_file(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -0,0 +1,1 @@@
+header
*** End Patch""",
    )

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "header\nline1\nline2\nline3\n"


@pytest.mark.asyncio
async def test_prepend_hunk_with_multiple_lines(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("body\n", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -0,0 +1,2 @@@
+first
+second
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "first\nsecond\nbody\n"


@pytest.mark.asyncio
async def test_prepend_hunk_into_single_line_file(tmp_path: Path) -> None:
    """The one-line file is where the ``-1`` splice looked most like success."""
    target = tmp_path / "test.txt"
    target.write_text("only\n", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -0,0 +1,1 @@@
+header
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "header\nonly\n"


@pytest.mark.asyncio
async def test_prepend_hunk_alongside_a_later_hunk_in_the_same_file(tmp_path: Path) -> None:
    """Hunks apply in reverse ``old_start`` order — the prepend must land last."""
    target = tmp_path / "test.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -0,0 +1,1 @@@
+header
@@@ -3,1 +4,1 @@@
-line3
+LINE3
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "header\nline1\nline2\nLINE3\n"


@pytest.mark.asyncio
async def test_prepend_hunk_on_an_empty_file(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -0,0 +1,1 @@@
+header
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "header\n"


@pytest.mark.asyncio
async def test_ordinary_hunk_positions_are_unchanged(tmp_path: Path) -> None:
    """The clamp must not shift any hunk that already had a 1-indexed start."""
    target = tmp_path / "test.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -2,1 +2,1 @@@
-line2
+LINE2
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "line1\nLINE2\nline3\n"


@pytest.mark.asyncio
async def test_insert_after_first_line_still_uses_one_indexed_start(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("line1\nline2\n", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Update File: test.txt
@@@ -1,1 +1,2 @@@
 line1
+inserted
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "line1\ninserted\nline2\n"


def test_apply_hunk_clamps_a_zero_start_to_position_zero() -> None:
    hunk = patch_tool.Hunk(old_start=0, old_count=0, new_start=1, new_count=1)
    hunk.lines = ["+header"]

    assert patch_tool._apply_hunk(["line1\n", "line2\n"], hunk) == [
        "header\n",
        "line1\n",
        "line2\n",
    ]


def test_apply_hunk_zero_start_with_explicit_old_count_replaces_the_first_line() -> None:
    """``-0,1`` names one existing line from position 0 — the first one."""
    hunk = patch_tool.Hunk(old_start=0, old_count=1, new_start=1, new_count=1)
    hunk.lines = ["-line1", "+LINE1"]

    assert patch_tool._apply_hunk(["line1\n", "line2\n"], hunk) == ["LINE1\n", "line2\n"]


# ── Issue #1577: Blank context lines and newline style matrix ─────────


def test_apply_hunk_with_empty_context_line() -> None:
    """Empty line in hunk lines must be treated as a blank context line (#1577)."""
    hunk = patch_tool.Hunk(
        old_start=1,
        old_count=3,
        new_start=1,
        new_count=3,
        lines=[" foo", "", "-bar", "+baz"],
    )
    file_lines = ["foo\n", "\n", "bar\n"]

    assert patch_tool._apply_hunk(file_lines, hunk) == ["foo\n", "\n", "baz\n"]


@pytest.mark.asyncio
async def test_hunk_with_empty_string_context_line(tmp_path: Path) -> None:
    """An empty line without leading space (``""``) is treated as an empty context line."""
    target = tmp_path / "test.txt"
    target.write_text("foo\n\nbar\n", encoding="utf-8")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: test.txt\n"
        "@@@ -1,3 +1,3 @@@\n"
        " foo\n"
        "\n"
        "-bar\n"
        "+baz\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "foo\n\nbaz\n"


@pytest.mark.asyncio
async def test_hunk_with_space_prefixed_blank_context_line(tmp_path: Path) -> None:
    """A blank context line with leading space (``" "``) is preserved."""
    target = tmp_path / "test.txt"
    target.write_text("foo\n\nbar\n", encoding="utf-8")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: test.txt\n"
        "@@@ -1,3 +1,3 @@@\n"
        " foo\n"
        " \n"
        "-bar\n"
        "+baz\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "foo\n\nbaz\n"


@pytest.mark.asyncio
async def test_hunk_with_several_blanks_in_a_row(tmp_path: Path) -> None:
    """Multiple consecutive blank context lines must advance check_pos and be preserved."""
    target = tmp_path / "test.txt"
    target.write_text("start\n\n\n\nend\n", encoding="utf-8")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: test.txt\n"
        "@@@ -1,5 +1,5 @@@\n"
        " start\n"
        "\n"
        " \n"
        "\n"
        "-end\n"
        "+finished\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "start\n\n\n\nfinished\n"


@pytest.mark.asyncio
async def test_hunk_with_blank_as_first_line_of_hunk(tmp_path: Path) -> None:
    """A blank context line as the very first line of a hunk must match and preserve."""
    target = tmp_path / "test.txt"
    target.write_text("\nheader\nbody\n", encoding="utf-8")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: test.txt\n"
        "@@@ -1,3 +1,3 @@@\n"
        "\n"
        "-header\n"
        "+HEADER\n"
        " body\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "\nHEADER\nbody\n"


@pytest.mark.asyncio
async def test_hunk_with_blank_as_last_line_of_hunk(tmp_path: Path) -> None:
    """A blank context line as the final line of a hunk must match and preserve."""
    target = tmp_path / "test.txt"
    target.write_text("header\nbody\n\n", encoding="utf-8")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: test.txt\n"
        "@@@ -1,3 +1,3 @@@\n"
        " header\n"
        "-body\n"
        "+BODY\n"
        "\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "header\nBODY\n\n"


@pytest.mark.asyncio
async def test_hunk_with_added_and_deleted_blank_lines(tmp_path: Path) -> None:
    """Hunks with '+' and '-' targeting blank lines add and remove blank lines properly."""
    target = tmp_path / "test.txt"
    target.write_text("line1\n\nline2\n", encoding="utf-8")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: test.txt\n"
        "@@@ -1,3 +1,4 @@@\n"
        " line1\n"
        "-\n"
        "+inserted\n"
        "+\n"
        " line2\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    assert target.read_text(encoding="utf-8") == "line1\ninserted\n\nline2\n"


@pytest.mark.asyncio
async def test_hunk_preserves_crlf_newline_style_on_added_lines(tmp_path: Path) -> None:
    """Added lines (both text and blank) in a CRLF file preserve CRLF line endings."""
    target = tmp_path / "crlf.txt"
    target.write_bytes(b"first\r\nsecond\r\n")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: crlf.txt\n"
        "@@@ -1,2 +1,4 @@@\n"
        " first\n"
        "+new_line\n"
        "+\n"
        " second\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    raw = target.read_bytes()
    assert raw == b"first\r\nnew_line\r\n\r\nsecond\r\n"
    # Ensure no lone LF without preceding CR exists
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")


@pytest.mark.asyncio
async def test_hunk_preserves_lf_newline_style_on_added_lines(tmp_path: Path) -> None:
    """Added lines in an LF file preserve LF line endings and contain no CR."""
    target = tmp_path / "lf.txt"
    target.write_bytes(b"first\nsecond\n")

    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: lf.txt\n"
        "@@@ -1,2 +1,4 @@@\n"
        " first\n"
        "+new_line\n"
        "+\n"
        " second\n"
        "*** End Patch"
    )
    result = await _apply(tmp_path, patch_text)

    assert "1 file(s) modified" in result
    raw = target.read_bytes()
    assert raw == b"first\nnew_line\n\nsecond\n"
    assert b"\r" not in raw
