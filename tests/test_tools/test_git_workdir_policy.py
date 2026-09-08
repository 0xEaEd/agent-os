from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agentos.tools.builtin import git
from agentos.tools.types import ToolContext, ToolError, current_tool_context


def test_git_effective_workdir_resolves_context_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    token = current_tool_context.set(ToolContext(workspace_dir=str(workspace)))
    try:
        assert git._effective_workdir(None) == str(workspace.resolve())
    finally:
        current_tool_context.reset(token)


def test_git_effective_workdir_rejects_foreign_posix_absolute_path_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(git, "os", SimpleNamespace(name="nt"), raising=False)
    token = current_tool_context.set(ToolContext(workspace_dir=str(workspace)))
    try:
        with pytest.raises(ToolError, match="foreign_host_path"):
            git._effective_workdir("/Users/a1/Desktop/repo")
    finally:
        current_tool_context.reset(token)


def test_git_rejects_foreign_diff_path_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "os", SimpleNamespace(name="nt"), raising=False)

    with pytest.raises(ToolError, match="foreign_host_path"):
        git._reject_foreign_git_path("/Users/a1/Desktop/repo/file.py")


def test_git_rejects_foreign_commit_file_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git, "os", SimpleNamespace(name="nt"), raising=False)

    with pytest.raises(ToolError, match="foreign_host_path"):
        git._reject_foreign_git_path("/Users/a1/Desktop/repo/file.py")


def test_git_diff_argv_unstaged_without_path() -> None:
    assert git._git_diff_argv({}) == ("git", "diff")
    assert git._git_diff_argv({"staged": False, "path": None}) == ("git", "diff")


def test_git_diff_argv_staged_without_path() -> None:
    assert git._git_diff_argv({"staged": True}) == ("git", "diff", "--cached")
    assert git._git_diff_argv({"staged": True, "path": None}) == ("git", "diff", "--cached")


def test_git_diff_argv_unstaged_with_path() -> None:
    assert git._git_diff_argv({"path": "src/main.py"}) == (
        "git",
        "diff",
        "--",
        "src/main.py",
    )


def test_git_diff_argv_staged_with_path() -> None:
    assert git._git_diff_argv({"staged": True, "path": "src/main.py"}) == (
        "git",
        "diff",
        "--cached",
        "--",
        "src/main.py",
    )


def _git_commit_impl() -> Any:
    """``git_commit`` with the ``@tool``/``@sandboxed`` wrappers peeled off.

    The staging decision under test lives in the plain coroutine; the sandbox
    wrapper denies fail-closed when no runtime is configured.
    """
    return git.git_commit.__wrapped__.__wrapped__  # type: ignore[attr-defined]


class _RecordingRunGit:
    """Stand-in for ``git._run_git`` that records argv instead of running git."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, *args: str, cwd: str | None = None) -> str:
        self.calls.append(args)
        return ""


async def test_git_commit_with_empty_files_stages_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``files=[]`` is an explicit "stage nothing", not "stage everything".

    Regression for the ``if files:`` fall-through that ran ``git add -A`` and
    swept untracked files the caller never named into the commit.
    """
    recorder = _RecordingRunGit()
    monkeypatch.setattr(git, "_run_git", recorder)

    await _git_commit_impl()(message="commit staged", files=[])

    assert recorder.calls == [("commit", "-m", "commit staged")]


async def test_git_commit_without_files_stages_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting ``files`` keeps the documented ``git add -A`` behavior."""
    recorder = _RecordingRunGit()
    monkeypatch.setattr(git, "_run_git", recorder)

    await _git_commit_impl()(message="commit all")

    assert recorder.calls == [("add", "-A"), ("commit", "-m", "commit all")]


async def test_git_commit_with_named_files_stages_only_those(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RecordingRunGit()
    monkeypatch.setattr(git, "_run_git", recorder)

    await _git_commit_impl()(message="commit one", files=["file1.txt"])

    assert recorder.calls == [
        ("add", "--", "file1.txt"),
        ("commit", "-m", "commit one"),
    ]
