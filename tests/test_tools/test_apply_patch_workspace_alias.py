from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from agentos.tools.builtin.patch import _validate_path, apply_patch
from agentos.tools.types import ToolContext, current_tool_context


def test_validate_path_resolves_workspace_alias_to_the_patch_root(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    assert _validate_path("/workspace/src/app.py", root) == root / "src" / "app.py"
    assert _validate_path("/workspace/test.txt", root) == root / "test.txt"


def test_validate_path_keeps_relative_and_in_root_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    assert _validate_path("src/app.py", root) == root / "src" / "app.py"
    assert _validate_path(str(root / "a.txt"), root) == root / "a.txt"


def test_validate_path_still_rejects_traversal_through_the_alias(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("/workspace/../../etc/passwd", tmp_path.resolve())
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("/etc/passwd", tmp_path.resolve())


def _original_async(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    return fn.__wrapped__.__wrapped__  # type: ignore[attr-defined, no-any-return]


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path.resolve()
    token = current_tool_context.set(ToolContext(workspace_dir=str(root)))
    try:
        yield root
    finally:
        current_tool_context.reset(token)


async def test_apply_patch_adds_a_file_through_the_workspace_alias(workspace: Path) -> None:
    patch_text = "*** Begin Patch\n*** Add File: /workspace/new.txt\n+hello\n*** End Patch\n"

    result = await _original_async(apply_patch)(patch_text)

    assert "Applied patch" in result
    assert (workspace / "new.txt").read_text(encoding="utf-8").strip() == "hello"


async def test_apply_patch_updates_a_file_through_the_workspace_alias(workspace: Path) -> None:
    (workspace / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8", newline="\n")
    patch_text = (
        "*** Begin Patch\n*** Update File: /workspace/a.txt\n@@@ -0,0 +1,1 @@@\n+header\n"
        "*** End Patch\n"
    )

    result = await _original_async(apply_patch)(patch_text)

    assert "Applied patch" in result
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "header\none\ntwo\nthree\n"
