"""Issue #1595: a relative ``cwd`` must resolve against the workspace root.

``_resolve_workspace`` used to accept ``cwd`` only when it was already
absolute and otherwise fell through to the workspace root, so every relative
``workdir`` collapsed onto one path. ``action_fingerprint`` hashes ``cwd``, and
for tools with a fixed ``argv_factory`` (``git_status``) it is the *only*
discriminator -- so a human denial in ``repoA`` made ``post_denial_guard``
auto-deny a never-reviewed call in ``repoB`` as ``REPEATED_SAME_INTENT``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.governance import DenialReason, DenialResult, action_fingerprint
from agentos.sandbox.integration import (
    _resolve_workspace,
    configure_runtime,
    gate_action,
    reset_runtime,
)
from agentos.tools.types import ToolContext, current_tool_context

_ARGV = ("git", "status", "--short", "--branch")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    reset_runtime()
    configure_runtime(SandboxSettings(sandbox=False, denial_threshold=10), workspace=ws)
    token = current_tool_context.set(
        ToolContext(workspace_dir=str(ws), session_key="agent:main:test")
    )
    yield ws
    current_tool_context.reset(token)
    reset_runtime()


def test_relative_cwd_is_joined_onto_the_workspace(workspace: Path) -> None:
    from agentos.sandbox.integration import get_runtime

    rt = get_runtime()
    assert rt is not None

    assert _resolve_workspace(rt, "repoA") == workspace / "repoA"
    assert _resolve_workspace(rt, "repoB") == workspace / "repoB"
    assert _resolve_workspace(rt, "nested/deeper") == workspace / "nested" / "deeper"
    # The pre-existing contracts are untouched.
    assert _resolve_workspace(rt, None) == workspace
    assert _resolve_workspace(rt, "") == workspace
    assert _resolve_workspace(rt, str(workspace / "abs")) == workspace / "abs"


def test_relative_cwd_prefers_the_tool_context_over_the_runtime(
    workspace: Path, tmp_path: Path
) -> None:
    """The context's workspace is what ``shell._effective_workdir`` anchors
    on; the runtime workspace is only the fallback when no context is set."""
    from agentos.sandbox.integration import get_runtime

    rt = get_runtime()
    assert rt is not None
    other = tmp_path / "other"
    token = current_tool_context.set(ToolContext(workspace_dir=str(other)))
    try:
        assert _resolve_workspace(rt, "repoA") == other / "repoA"
    finally:
        current_tool_context.reset(token)

    token = current_tool_context.set(ToolContext(workspace_dir=None))
    try:
        assert _resolve_workspace(rt, "repoA") == workspace / "repoA"
    finally:
        current_tool_context.reset(token)


def test_tilde_cwd_is_expanded(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.sandbox.integration import get_runtime

    home = workspace.parent / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    rt = get_runtime()
    assert rt is not None

    assert _resolve_workspace(rt, "~/repo") == home / "repo"


@pytest.mark.asyncio
async def test_different_relative_workdirs_produce_different_fingerprints(
    workspace: Path,
) -> None:
    _, _, req_a = await gate_action(action_kind="git_status", argv=_ARGV, cwd=Path("repoA"))
    _, _, req_b = await gate_action(action_kind="git_status", argv=_ARGV, cwd=Path("repoB"))

    assert req_a.cwd == workspace / "repoA"
    assert req_b.cwd == workspace / "repoB"
    assert action_fingerprint(req_a) != action_fingerprint(req_b)


@pytest.mark.asyncio
async def test_denial_in_one_relative_workdir_does_not_block_another(workspace: Path) -> None:
    """The reported false positive: deny ``repoA``, and the never-reviewed
    ``repoB`` call must reach the gate as a fresh intent. A genuine blind
    retry of ``repoA`` is still caught."""
    from agentos.sandbox.integration import get_runtime

    rt = get_runtime()
    assert rt is not None
    session = "agent:main:test"

    _, _, req_a = await gate_action(
        action_kind="git_status", argv=_ARGV, cwd=Path("repoA"), session_id=session
    )
    await rt.ledger.record_denial(session, action_fingerprint(req_a), DenialReason.HUMAN_REJECTED)

    decision_b, _, _ = await gate_action(
        action_kind="git_status", argv=_ARGV, cwd=Path("repoB"), session_id=session
    )
    assert not isinstance(decision_b, DenialResult)

    decision_a, _, _ = await gate_action(
        action_kind="git_status", argv=_ARGV, cwd=Path("repoA"), session_id=session
    )
    assert isinstance(decision_a, DenialResult)
    assert decision_a.reason is DenialReason.REPEATED_SAME_INTENT
