"""``git_log`` on a repository with no commits yet.

``git log`` exits 128 on an unborn branch ("your current branch 'main' does
not have any commits yet"), and ``_run_git`` turns any non-zero exit into a
RuntimeError, so the raw error escaped the tool and took the turn with it.
The other read tools treat the same state as ordinary: ``git_status``
prints ``## No commits yet on main`` and ``git_diff`` returns empty.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.integration import configure_runtime, reset_runtime
from agentos.tools.builtin import git
from agentos.tools.types import ToolContext, current_tool_context


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)


@pytest.fixture
def workspace(tmp_path: Path):
    """A configured runtime, or @sandboxed denies the call fail-closed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    reset_runtime()
    configure_runtime(SandboxSettings(sandbox=False, denial_threshold=10), workspace=ws)
    token = current_tool_context.set(
        ToolContext(workspace_dir=str(ws), session_key="agent:main:test")
    )
    try:
        yield ws
    finally:
        current_tool_context.reset(token)
        reset_runtime()


@pytest.fixture
def empty_repo(workspace: Path) -> Path:
    repo = workspace / "repo"
    repo.mkdir()
    _init_repo(repo)
    return repo


@pytest.mark.asyncio
async def test_git_log_reports_an_empty_repository_cleanly(empty_repo: Path) -> None:
    assert await git.git_log(workdir=str(empty_repo)) == "(no commits yet)"


@pytest.mark.asyncio
async def test_git_log_still_raises_on_a_real_failure(workspace: Path) -> None:
    """Only the unborn-branch case is swallowed."""
    not_a_repo = workspace / "plain"
    not_a_repo.mkdir()

    with pytest.raises(RuntimeError):
        await git.git_log(workdir=str(not_a_repo))


@pytest.mark.asyncio
async def test_git_log_count_cannot_request_unlimited_history(
    empty_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """git reads --max-count=-1 as unlimited; clamp before it gets there."""
    captured: list[tuple[str, ...]] = []

    async def _fake_run_git(*args: str, cwd: str | None = None) -> str:
        captured.append(args)
        return ""

    monkeypatch.setattr(git, "_run_git", _fake_run_git)

    await git.git_log(count=-1, workdir=str(empty_repo))

    assert "--max-count=1" in captured[0]
