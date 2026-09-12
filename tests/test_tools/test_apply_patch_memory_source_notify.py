"""``apply_patch`` must recognise the same memory sources as the filesystem tools.

``filesystem._memory_source_rel_path`` walks every memory root (workspace and
``memory_source_dir``) and treats ``MEMORY.md``, ``memory.md``, ``USER.md`` and
``memory/*.md`` as sources whose write must refresh the frozen memory
snapshot. ``patch._memory_source_rel_path`` had its own copy of that rule
that only knew ``MEMORY.md`` under the single patch root, so patching
``USER.md`` or ``memory.md`` never fired ``on_memory_source_write`` and the
change stayed invisible to the model for the rest of the session -- while the
very same edit through ``write_file`` refreshed the snapshot. The two helpers
must not drift again, so the patch tool now delegates to the filesystem one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agentos.tools.builtin import filesystem
from agentos.tools.builtin import patch as patch_tool
from agentos.tools.types import ToolContext, current_tool_context


def _original_async(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    return fn.__wrapped__.__wrapped__  # type: ignore[attr-defined, no-any-return]


def _update(path: str, old: str, new: str) -> str:
    return f"*** Update File: {path}\n@@@ -1,1 +1,1 @@@\n-{old}\n+{new}\n"


async def _patch_in(
    workspace: Path,
    sections: str,
    *,
    memory_source_dir: Path | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    memory_calls: list[tuple[str, str]] = []
    bootstrap_calls: list[tuple[str, str]] = []
    token = current_tool_context.set(
        ToolContext(
            agent_id="main",
            workspace_dir=str(workspace),
            memory_source_dir=str(memory_source_dir or workspace),
            on_memory_source_write=lambda agent_id, path: memory_calls.append((agent_id, path)),
            on_bootstrap_source_write=lambda agent_id, path: bootstrap_calls.append(
                (agent_id, path)
            ),
        )
    )
    apply_patch = _original_async(patch_tool.apply_patch)
    try:
        result = await apply_patch(f"*** Begin Patch\n{sections}*** End Patch")
    finally:
        current_tool_context.reset(token)
    assert result.startswith("Applied patch:"), result
    return memory_calls, bootstrap_calls


@pytest.mark.asyncio
async def test_patching_user_md_refreshes_the_memory_snapshot(tmp_path: Path) -> None:
    """The regression this file exists for: USER.md is a curated memory store."""
    (tmp_path / "USER.md").write_text("Name:\n", encoding="utf-8")

    memory_calls, bootstrap_calls = await _patch_in(
        tmp_path, _update("USER.md", "Name:", "Name: Alice")
    )

    assert memory_calls == [("main", "USER.md")]
    # It is also a bootstrap file, so both snapshots are still invalidated.
    assert bootstrap_calls == [("main", "USER.md")]


@pytest.mark.asyncio
async def test_patching_legacy_memory_md_refreshes_the_memory_snapshot(tmp_path: Path) -> None:
    """Kept deliberately: runtime.py still falls back to reading memory.md."""
    (tmp_path / "memory.md").write_text("old\n", encoding="utf-8")

    memory_calls, _ = await _patch_in(tmp_path, _update("memory.md", "old", "new"))

    assert memory_calls == [("main", "memory.md")]


@pytest.mark.asyncio
async def test_patching_a_memory_source_dir_inside_the_workspace(tmp_path: Path) -> None:
    """A ``memory_source_dir`` nested under the workspace is a memory root too.

    Only the workspace root used to be considered, so a patch to
    ``state/MEMORY.md`` looked like a stray file named ``MEMORY.md`` two
    levels down and never notified.
    """
    state = tmp_path / "state"
    (state / "memory").mkdir(parents=True)
    (state / "MEMORY.md").write_text("# MEMORY\n", encoding="utf-8")
    (state / "memory" / "2026-09-12.md").write_text("old\n", encoding="utf-8")

    memory_calls, bootstrap_calls = await _patch_in(
        tmp_path,
        _update("state/MEMORY.md", "# MEMORY", "# MEMORY v2")
        + _update("state/memory/2026-09-12.md", "old", "new"),
        memory_source_dir=state,
    )

    assert memory_calls == [("main", "MEMORY.md"), ("main", "memory/2026-09-12.md")]
    # Bootstrap files are only recognised at the workspace root.
    assert bootstrap_calls == []


@pytest.mark.asyncio
async def test_other_root_files_do_not_refresh_the_memory_snapshot(tmp_path: Path) -> None:
    """Widening past MEMORY.md must not sweep in every root-level file."""
    for name in ("SOUL.md", "notes.md", "USER.txt"):
        (tmp_path / name).write_text("old\n", encoding="utf-8")

    memory_calls, bootstrap_calls = await _patch_in(
        tmp_path,
        _update("SOUL.md", "old", "new")
        + _update("notes.md", "old", "new")
        + _update("USER.txt", "old", "new"),
    )

    assert memory_calls == []
    assert bootstrap_calls == [("main", "SOUL.md")]


@pytest.mark.asyncio
async def test_each_memory_source_is_notified_once_per_patch(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text("a\nb\n", encoding="utf-8")

    memory_calls, _ = await _patch_in(
        tmp_path,
        "*** Update File: USER.md\n@@@ -1,1 +1,1 @@@\n-a\n+A\n@@@ -2,1 +2,1 @@@\n-b\n+B\n",
    )

    assert memory_calls == [("main", "USER.md")]


@pytest.mark.parametrize(
    "rel",
    [
        "MEMORY.md",
        "memory.md",
        "USER.md",
        "memory/2026-09-12.md",
        "memory/nested/note.md",
        "SOUL.md",
        "AGENTS.md",
        "notes.md",
        "USER.txt",
        "memory/README.txt",
        "docs/MEMORY.md",
    ],
)
def test_patch_helper_agrees_with_the_filesystem_helper(tmp_path: Path, rel: str) -> None:
    """Parity guard: the two tools must classify every path identically."""
    token = current_tool_context.set(
        ToolContext(
            agent_id="main",
            workspace_dir=str(tmp_path),
            memory_source_dir=str(tmp_path),
        )
    )
    try:
        via_patch = patch_tool._memory_source_rel_path(rel, tmp_path.resolve())
        via_filesystem = filesystem._memory_source_rel_path(tmp_path / rel)
    finally:
        current_tool_context.reset(token)

    assert via_patch == via_filesystem
