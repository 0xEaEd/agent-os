"""Workspace write-deny globs must not lose the dot that makes a dotfile.

Regression for the ``lstrip("./")`` character-set/prefix confusion: it stripped
every leading ``.`` and ``/`` from both the pattern and the candidate, so a rule
written for ``.env`` also matched ``environment.md``.
"""

from __future__ import annotations

from pathlib import Path

from agentos.tools.types import ToolContext
from agentos.tools.write_policy import match_workspace_write_deny


def _ctx(workspace: Path, *patterns: str) -> ToolContext:
    return ToolContext(
        workspace_dir=str(workspace),
        workspace_write_deny_globs=list(patterns),
    )


def _denied(workspace: Path, ctx: ToolContext, name: str) -> bool:
    return match_workspace_write_deny(workspace / name, original_path=name, ctx=ctx) is not None


def test_dotfile_glob_does_not_block_names_sharing_its_stem(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, ".env*")

    assert _denied(tmp_path, ctx, ".env")
    assert _denied(tmp_path, ctx, ".env.local")
    assert not _denied(tmp_path, ctx, "environment.md")
    assert not _denied(tmp_path, ctx, "envoy.yaml")
    assert not _denied(tmp_path, ctx, "env_setup.py")


def test_plain_glob_still_matches_dot_slash_spelling(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "secrets.json")

    assert _denied(tmp_path, ctx, "secrets.json")
    assert _denied(tmp_path, ctx, "./secrets.json")
    assert _denied(tmp_path, ctx, ".//secrets.json")


def test_absolute_candidate_still_matches_workspace_relative_glob(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "generated/**")
    target = tmp_path / "generated" / "out.txt"

    assert match_workspace_write_deny(target, original_path=str(target), ctx=ctx) is not None


def test_glob_written_with_dot_slash_still_matches(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "./build/*")

    assert _denied(tmp_path, ctx, "build/app.js")


def test_dot_directory_glob_matches_only_that_directory(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, ".github/*")

    assert _denied(tmp_path, ctx, ".github/workflows.yml")
    assert not _denied(tmp_path, ctx, "github/workflows.yml")
