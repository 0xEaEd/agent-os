"""A patch body indented as a block, and a patch that parses to nothing.

A patch quoted inside a Markdown list, blockquote or indented block carries the
same indentation on every line. ``_marker_span`` found the markers, but no
section directive matched, ``_parse_patch`` returned ``[]`` and ``apply_patch``
answered ``Applied patch: no changes`` with the workspace untouched.

Two things are pinned here: the block is shifted back by the indentation of its
first section directive, and -- whatever the indentation handling does -- a
patch whose markers enclose no recognised operation is an error, never a
success.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agentos.tools.builtin import patch as patch_tool
from agentos.tools.types import ToolContext, current_tool_context

NOTHING_APPLIED = r"No operations found between '\*\*\* Begin Patch' and '\*\*\* End Patch'"


def _original_async(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    return fn.__wrapped__.__wrapped__  # type: ignore[attr-defined, no-any-return]


async def _apply(workspace: Path, patch_text: str) -> str:
    token = current_tool_context.set(ToolContext(workspace_dir=str(workspace)))
    try:
        return await _original_async(patch_tool.apply_patch)(patch_text)
    finally:
        current_tool_context.reset(token)


def _indent(text: str, prefix: str) -> str:
    """Indent every line of *text*, the way a Markdown list item nests a block."""
    return "".join(prefix + line if line.strip() else line for line in text.splitlines(True))


# ---------------------------------------------------------------------------
# The reported direction: an indented block now applies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reported_indented_add_file_is_created(tmp_path: Path) -> None:
    result = await _apply(
        tmp_path,
        "    *** Begin Patch\n    *** Add File: sample.txt\n    +hello world\n    *** End Patch\n",
    )

    assert result == "Applied patch: 1 file(s) added"
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "hello world"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("  ", id="two_spaces"),
        pytest.param("    ", id="four_spaces"),
        pytest.param("\t", id="tab"),
    ],
)
async def test_an_indented_block_applies_add_update_and_delete(tmp_path: Path, prefix: str) -> None:
    (tmp_path / "app.py").write_text(
        "def run():\n    if ready:\n        old_val = 1\n    return old_val\n", encoding="utf-8"
    )
    (tmp_path / "obsolete.txt").write_text("gone\n", encoding="utf-8")
    patch_text = _indent(
        "*** Begin Patch\n"
        "*** Add File: notes/new.md\n"
        "+# Title\n"
        "+    indented body line\n"
        "*** Update File: app.py\n"
        "@@@ -1,4 +1,4 @@@\n"
        " def run():\n"
        "     if ready:\n"
        "-        old_val = 1\n"
        "+        new_val = 2\n"
        "     return old_val\n"
        "*** Delete File: obsolete.txt\n"
        "*** End Patch\n",
        prefix,
    )

    result = await _apply(tmp_path, patch_text)

    assert result == "Applied patch: 1 file(s) added, 1 file(s) modified, 1 file(s) deleted"
    assert (tmp_path / "notes" / "new.md").read_text(encoding="utf-8") == (
        "# Title\n    indented body line"
    )
    # Only the block's indentation is removed; the code's own indentation stays.
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == (
        "def run():\n    if ready:\n        new_val = 2\n    return old_val\n"
    )
    assert not (tmp_path / "obsolete.txt").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blank_context",
    [
        pytest.param("     ", id="block_indent_plus_space"),
        pytest.param("    ", id="block_indent_only"),
        pytest.param("", id="bare"),
    ],
)
async def test_an_empty_context_line_in_an_indented_hunk_is_still_context(
    tmp_path: Path, blank_context: str
) -> None:
    """However an editor trimmed it, an empty source line stays a context line."""
    (tmp_path / "a.txt").write_text("first\n\nthird\n", encoding="utf-8")
    patch_text = (
        "    *** Begin Patch\n"
        "    *** Update File: a.txt\n"
        "    @@@ -1,3 +1,3 @@@\n"
        "     first\n"
        f"{blank_context}\n"
        "    -third\n"
        "    +THIRD\n"
        "    *** End Patch\n"
    )

    result = await _apply(tmp_path, patch_text)

    assert result == "Applied patch: 1 file(s) modified"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "first\n\nTHIRD\n"


@pytest.mark.asyncio
async def test_a_block_indented_past_flush_markers_applies(tmp_path: Path) -> None:
    """The column comes from the first directive, not from the markers."""
    result = await _apply(
        tmp_path,
        "*** Begin Patch\n  *** Add File: sample.txt\n  +hello\n*** End Patch\n",
    )

    assert result == "Applied patch: 1 file(s) added"
    assert (tmp_path / "sample.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_a_line_indented_less_than_the_block_is_rejected_not_guessed(
    tmp_path: Path,
) -> None:
    """A stray line outside the block's column is an error, and nothing is written.

    Shrinking the block indentation to fit it would unmatch every directive and
    fall back to a silent no-op; stripping it would guess at the content.
    """
    patch_text = (
        "    *** Begin Patch\n"
        "    *** Add File: sample.txt\n"
        "    +hello\n"
        "  +world\n"
        "    *** End Patch\n"
    )

    with pytest.raises(ValueError, match=r"expected a '\+' prefix"):
        await _apply(tmp_path, patch_text)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# The floor: markers with no recognised operation are an error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch_text",
    [
        pytest.param("*** Begin Patch\n*** End Patch\n", id="empty_body"),
        pytest.param(
            "*** Begin Patch\nI would add sample.txt with hello.\n*** End Patch\n",
            id="prose_only",
        ),
        pytest.param(
            "*** Begin Patch\n*** Create File: sample.txt\n+hello\n*** End Patch\n",
            id="unknown_directive",
        ),
        pytest.param(
            "*** Begin Patch\n+++ b/sample.txt\n+hello\n*** End Patch\n",
            id="unified_diff_header",
        ),
    ],
)
async def test_markers_without_an_operation_raise_instead_of_reporting_no_changes(
    tmp_path: Path, patch_text: str
) -> None:
    with pytest.raises(ValueError, match=NOTHING_APPLIED):
        await _apply(tmp_path, patch_text)
    assert list(tmp_path.iterdir()) == []


def test_the_floor_error_names_the_directives_the_parser_accepts() -> None:
    """The message is what the model reads when it retries."""
    with pytest.raises(ValueError) as excinfo:
        patch_tool._parse_patch("*** Begin Patch\n*** End Patch\n")

    message = str(excinfo.value)
    for directive in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
        assert directive in message
    assert "Nothing was applied." in message


# ---------------------------------------------------------------------------
# Guards: what already worked keeps working (pass either way by design)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_drifted_begin_marker_does_not_strip_a_flush_body(tmp_path: Path) -> None:
    """Dedenting by the ``*** Begin Patch`` line would eat this context line's spaces."""
    (tmp_path / "a.py").write_text("def f():\n  x = 1\n  return x\n", encoding="utf-8")
    patch_text = (
        "  *** Begin Patch\n"
        "*** Update File: a.py\n"
        "@@@ -1,3 +1,3 @@@\n"
        " def f():\n"
        "   x = 1\n"
        "-  return x\n"
        "+  return x + 1\n"
        "*** End Patch\n"
    )

    result = await _apply(tmp_path, patch_text)

    assert result == "Applied patch: 1 file(s) modified"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == (
        "def f():\n  x = 1\n  return x + 1\n"
    )


def test_a_flush_patch_parses_to_the_same_operations() -> None:
    patch_text = (
        "*** Begin Patch\n"
        "*** Add File: a.txt\n"
        "+  keep my indent\n"
        "*** Update File: b.txt\n"
        "@@@ -1 +1 @@@\n"
        "-    old\n"
        "+    new\n"
        "*** End Patch\n"
    )

    ops = patch_tool._parse_patch(patch_text)

    assert ops[0] == patch_tool.AddFile(path="a.txt", content="  keep my indent")
    assert isinstance(ops[1], patch_tool.UpdateFile)
    assert ops[1].hunks[0].lines == ["-    old", "+    new"]
