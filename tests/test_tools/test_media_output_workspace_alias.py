from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agentos.tools.builtin.media import (
    _resolve_generated_audio_path,
    _resolve_generated_image_path,
)
from agentos.tools.types import ToolContext, ToolError, current_tool_context


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path.resolve()
    token = current_tool_context.set(ToolContext(workspace_dir=str(root)))
    try:
        yield root
    finally:
        current_tool_context.reset(token)


def _audio(path: str) -> Path:
    return _resolve_generated_audio_path(
        path, response_format="mp3", mime_type="audio/mpeg", prefix="speech"
    )


def test_image_output_resolves_workspace_alias(workspace: Path) -> None:
    assert _resolve_generated_image_path("/workspace/out.png", "png") == workspace / "out.png"
    assert _resolve_generated_image_path("/workspace/a/b", "png") == workspace / "a" / "b.png"


def test_audio_output_resolves_workspace_alias(workspace: Path) -> None:
    assert _audio("/workspace/out.mp3") == workspace / "out.mp3"
    assert _audio("/workspace/a/b") == workspace / "a" / "b.mp3"


def test_output_paths_keep_relative_behaviour_and_still_reject_escapes(workspace: Path) -> None:
    assert _resolve_generated_image_path("x.png", "png") == workspace / "x.png"
    assert _audio("x.mp3") == workspace / "x.mp3"
    # No message match: on Windows a POSIX ``/etc/...`` path is refused earlier, by the
    # foreign-host-path check, with a different message.
    for bad in ("/etc/out.png", "/workspace/../../etc/out.png"):
        with pytest.raises(ToolError):
            _resolve_generated_image_path(bad, "png")
    for bad in ("/etc/out.mp3", "/workspace/../../etc/out.mp3"):
        with pytest.raises(ToolError):
            _audio(bad)
