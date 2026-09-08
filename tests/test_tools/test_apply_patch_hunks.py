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
