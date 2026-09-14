"""The turn-end artifact backstop must not load a file before the size check
(#1760, same defect as ``publish_artifact``)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentos.engine.artifact_delivery import auto_publish_omitted_workspace_artifacts
from agentos.tools.types import CallerKind, ToolContext, current_tool_context
from agentos.tools.write_tracking import record_workspace_file_write


def _forbid_whole_file_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(self: Path) -> bytes:
        raise AssertionError(f"read_bytes() must not be called for {self.name}")

    monkeypatch.setattr(Path, "read_bytes", _boom)


def _ctx_with_written_file(
    tmp_path: Path, *, content: bytes, max_bytes: int | None
) -> tuple[ToolContext, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "report.html"
    target.write_bytes(content)
    ctx = ToolContext(
        caller_kind=CallerKind.WEB,
        workspace_dir=str(workspace),
        artifact_media_root=str(tmp_path / "media"),
        artifact_session_id="session-1",
        session_key="agent:main:webchat:session-1",
        artifact_max_bytes=max_bytes,
    )
    token = current_tool_context.set(ctx)
    try:
        record_workspace_file_write(target)
    finally:
        current_tool_context.reset(token)
    return ctx, target


def test_backstop_rejects_oversize_file_on_stat_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _target = _ctx_with_written_file(tmp_path, content=b"<h1>big</h1>" * 8, max_bytes=16)
    _forbid_whole_file_reads(monkeypatch)

    result = auto_publish_omitted_workspace_artifacts(ctx, final_text="See report.html")

    assert result.artifacts == []
    assert result.failure_summaries == [
        "auto-publish failed for report.html: artifact exceeds per-file budget (96 > 16)"
    ]
    assert ctx.published_artifacts == []


def test_backstop_reads_the_file_once_after_the_budget_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _target = _ctx_with_written_file(tmp_path, content=b"<h1>ok</h1>", max_bytes=None)
    reads: list[str] = []
    real_read_bytes = Path.read_bytes

    def _counting_read_bytes(self: Path) -> bytes:
        reads.append(self.name)
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting_read_bytes)

    result = auto_publish_omitted_workspace_artifacts(ctx, final_text="See report.html")

    assert reads == ["report.html"]
    assert result.failure_summaries == []
    assert [item["name"] for item in result.artifacts] == ["report.html"]
    assert result.artifacts[0]["sha256"] == hashlib.sha256(b"<h1>ok</h1>").hexdigest()
    assert result.artifacts[0]["source"] == "auto_publish_omitted"
