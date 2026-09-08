"""``apply_patch`` is all-or-nothing across the operations in one patch.

``_apply_ops`` used to write each op to disk as it iterated, so a patch whose
second op had a bad context left the first one committed — and because the
exception escaped ``apply_patch`` before its post-write hooks ran, the runtime's
record of what had been written disagreed with the filesystem. Every op is now
resolved and dry-run in memory first.
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
async def test_add_is_not_committed_when_a_later_update_fails(tmp_path: Path) -> None:
    (tmp_path / "file2.txt").write_text("real content\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Context mismatch"):
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Add File: file1.txt
+new file content
*** Update File: file2.txt
@@@ -1,1 +1,1 @@@
-nonexistent context
+replacement
*** End Patch""",
        )

    assert not (tmp_path / "file1.txt").exists()
    assert (tmp_path / "file2.txt").read_text(encoding="utf-8") == "real content\n"


@pytest.mark.asyncio
async def test_add_is_not_committed_when_a_later_update_targets_a_missing_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="File not found for update"):
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Add File: file1.txt
+new file content
*** Update File: missing.txt
@@@ -1,1 +1,1 @@@
-a
+b
*** End Patch""",
        )

    assert not (tmp_path / "file1.txt").exists()


@pytest.mark.asyncio
async def test_earlier_update_is_rolled_back_when_a_later_delete_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "keep.txt"
    target.write_text("original\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="File not found for deletion"):
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Update File: keep.txt
@@@ -1,1 +1,1 @@@
-original
+changed
*** Delete File: gone.txt
*** End Patch""",
        )

    assert target.read_text(encoding="utf-8") == "original\n"


@pytest.mark.asyncio
async def test_delete_is_not_committed_when_a_later_add_collides(tmp_path: Path) -> None:
    doomed = tmp_path / "doomed.txt"
    doomed.write_text("still here\n", encoding="utf-8")
    (tmp_path / "taken.txt").write_text("occupied\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="File already exists"):
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Delete File: doomed.txt
*** Add File: taken.txt
+clobber
*** End Patch""",
        )

    assert doomed.read_text(encoding="utf-8") == "still here\n"
    assert (tmp_path / "taken.txt").read_text(encoding="utf-8") == "occupied\n"


@pytest.mark.asyncio
async def test_failure_message_names_the_operation_that_failed(tmp_path: Path) -> None:
    (tmp_path / "file2.txt").write_text("real content\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Add File: file1.txt
+new file content
*** Update File: file2.txt
@@@ -1,1 +1,1 @@@
-nonexistent context
+replacement
*** End Patch""",
        )

    message = str(excinfo.value)
    assert message.startswith("Update File: file2.txt: ")
    assert "Context mismatch" in message


@pytest.mark.asyncio
async def test_no_directory_is_left_behind_by_a_failed_nested_add(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Add File: nested/deep/file1.txt
+content
*** Delete File: gone.txt
*** End Patch""",
        )

    assert not (tmp_path / "nested").exists()


@pytest.mark.asyncio
async def test_a_fully_valid_multi_op_patch_still_applies_every_operation(
    tmp_path: Path,
) -> None:
    (tmp_path / "update_me.txt").write_text("before\n", encoding="utf-8")
    (tmp_path / "delete_me.txt").write_text("bye\n", encoding="utf-8")

    result = await _apply(
        tmp_path,
        """*** Begin Patch
*** Add File: nested/added.txt
+added
*** Update File: update_me.txt
@@@ -1,1 +1,1 @@@
-before
+after
*** Delete File: delete_me.txt
*** End Patch""",
    )

    assert "1 file(s) added" in result
    assert "1 file(s) modified" in result
    assert "1 file(s) deleted" in result
    assert (tmp_path / "nested" / "added.txt").read_text(encoding="utf-8") == "added"
    assert (tmp_path / "update_me.txt").read_text(encoding="utf-8") == "after\n"
    assert not (tmp_path / "delete_me.txt").exists()


@pytest.mark.asyncio
async def test_an_add_then_update_of_the_same_file_still_composes(tmp_path: Path) -> None:
    """Planning is sequential: the update must see what the add would write."""
    result = await _apply(
        tmp_path,
        """*** Begin Patch
*** Add File: staged.txt
+first
*** Update File: staged.txt
@@@ -1,1 +1,1 @@@
-first
+second
*** End Patch""",
    )

    assert "1 file(s) added" in result
    assert (tmp_path / "staged.txt").read_text(encoding="utf-8") == "second\n"


@pytest.mark.asyncio
async def test_an_add_after_a_delete_of_the_same_path_is_allowed(tmp_path: Path) -> None:
    target = tmp_path / "swap.txt"
    target.write_text("old\n", encoding="utf-8")

    await _apply(
        tmp_path,
        """*** Begin Patch
*** Delete File: swap.txt
*** Add File: swap.txt
+new
*** End Patch""",
    )

    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.asyncio
async def test_an_update_after_a_delete_of_the_same_path_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "swap.txt"
    target.write_text("old\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="File not found for update"):
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Delete File: swap.txt
*** Update File: swap.txt
@@@ -1,1 +1,1 @@@
-old
+new
*** End Patch""",
        )

    assert target.read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
async def test_write_tracking_matches_disk_after_a_successful_patch(tmp_path: Path) -> None:
    """The post-write hooks only ever describe a batch that actually landed."""
    recorded: list[str] = []
    original = patch_tool.record_workspace_file_write

    def _spy(path: str) -> None:
        recorded.append(path)

    patch_tool.record_workspace_file_write = _spy  # type: ignore[assignment]
    try:
        await _apply(
            tmp_path,
            """*** Begin Patch
*** Add File: tracked.txt
+tracked
*** End Patch""",
        )
        before_failure = list(recorded)
        recorded.clear()

        with pytest.raises(FileNotFoundError):
            await _apply(
                tmp_path,
                """*** Begin Patch
*** Add File: untracked.txt
+untracked
*** Delete File: gone.txt
*** End Patch""",
            )
    finally:
        patch_tool.record_workspace_file_write = original  # type: ignore[assignment]

    assert before_failure  # the successful patch was recorded
    # The failed patch recorded nothing and wrote nothing.
    assert recorded == []
    assert not (tmp_path / "untracked.txt").exists()


def test_plan_ops_writes_nothing_on_its_own(tmp_path: Path) -> None:
    ops = patch_tool._parse_patch(
        """*** Begin Patch
*** Add File: planned.txt
+planned
*** End Patch"""
    )

    staged, counts = patch_tool._plan_ops(ops, tmp_path)

    assert counts == (1, 0, 0)
    assert [item.label for item in staged] == ["Add File: planned.txt"]
    assert not (tmp_path / "planned.txt").exists()
