"""Media tools reject a directory instead of letting the OS error escape.

A directory named like a media file (``photo.png/``, ``sample.wav/``) got
past the ``exists()`` guard and straight into ``read_bytes`` /
``pdfplumber.open``, which raise ``IsADirectoryError`` on POSIX and
``PermissionError`` on Windows -- raw OS errors crossing the tool
boundary. ``filesystem.read_file`` and friends already validate
``is_file()``; these do too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.tools.builtin import media
from agentos.tools.types import SafeToolError, ToolContext, current_tool_context


@pytest.fixture
def workspace(tmp_path: Path):
    token = current_tool_context.set(ToolContext(workspace_dir=str(tmp_path)))
    try:
        yield tmp_path
    finally:
        current_tool_context.reset(token)


def _media_shaped_dir(workspace: Path, name: str) -> Path:
    target = workspace / name
    target.mkdir()
    return target


@pytest.mark.asyncio
async def test_image_reader_rejects_a_directory(workspace: Path) -> None:
    target = _media_shaped_dir(workspace, "photo.png")

    with pytest.raises(SafeToolError, match="is a directory"):
        await media._read_image_file(str(target))


@pytest.mark.asyncio
async def test_audio_resolver_rejects_a_directory(workspace: Path) -> None:
    target = _media_shaped_dir(workspace, "sample.wav")

    with pytest.raises(SafeToolError, match="is a directory"):
        await media._resolve_supported_audio_file_for_tool(
            tool_name="voice_clone", path=str(target)
        )


@pytest.mark.asyncio
async def test_image_reader_still_accepts_a_real_file(workspace: Path) -> None:
    """The guard rejects directories only."""
    target = workspace / "real.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n")

    data, media_type = await media._read_image_file(str(target))

    assert data.startswith(b"\x89PNG")
    assert media_type == "image/png"


@pytest.mark.asyncio
async def test_missing_path_still_reports_not_found(workspace: Path) -> None:
    with pytest.raises(SafeToolError, match="not accessible|not found"):
        await media._read_image_file(str(workspace / "absent.png"))
