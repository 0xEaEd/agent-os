from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentos.gateway.approval_queue import get_approval_queue, reset_approval_queue
from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.integration import configure_runtime, get_runtime, reset_runtime
from agentos.sandbox.intent_cache import reset_intent_cache
from agentos.sandbox.types import DenialReason
from agentos.tools.builtin import shell
from agentos.tools.types import CallerKind, ToolContext, ToolError, current_tool_context


@pytest.fixture(autouse=True)
def reset_sandbox_and_tool_state(tmp_path: Path):
    reset_approval_queue()
    reset_intent_cache()
    reset_runtime()
    configure_runtime(
        SandboxSettings(sandbox=True, security_grading=False, allow_legacy_mode=True),
        workspace=tmp_path,
    )
    token = current_tool_context.set(
        ToolContext(
            caller_kind=CallerKind.CLI,
            session_key="test-session-1513",
            workspace_dir=str(tmp_path),
        )
    )
    yield
    current_tool_context.reset(token)
    reset_approval_queue()
    reset_intent_cache()
    reset_runtime()


@pytest.mark.asyncio
async def test_denylist_command_records_policy_denied_in_ledger() -> None:
    runtime = get_runtime()
    assert runtime is not None

    denied_cmd = "Clear-Disk -Number 1" if os.name == "nt" else "mkfs.ext4 /dev/sda"
    with pytest.raises(ToolError):
        await shell.exec_command(denied_cmd)

    assert await runtime.ledger.count_session("test-session-1513") == 1
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED


@pytest.mark.asyncio
async def test_sensitive_path_block_records_policy_denied_in_ledger() -> None:
    runtime = get_runtime()
    assert runtime is not None

    result = await shell.exec_command("cat ~/.ssh/id_rsa")
    payload = json.loads(result)
    assert payload["status"] == "blocked"
    assert payload["reason"] == "sensitive_path"

    assert await runtime.ledger.count_session("test-session-1513") == 1
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED


@pytest.mark.asyncio
async def test_workspace_lockdown_block_records_policy_denied_in_ledger(tmp_path: Path) -> None:
    runtime = get_runtime()
    assert runtime is not None

    ctx = current_tool_context.get()
    assert ctx is not None
    ctx.workspace_lockdown = True

    outside_path = "C:/outside_lockdown.txt" if os.name == "nt" else "/outside_lockdown.txt"
    result = await shell.exec_command(f"echo payload > {outside_path}")
    payload = json.loads(result)
    assert payload["status"] == "blocked"
    assert payload["reason"] == "workspace_lockdown"

    assert await runtime.ledger.count_session("test-session-1513") == 1
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED


@pytest.mark.asyncio
async def test_workspace_write_deny_block_records_policy_denied_in_ledger() -> None:
    runtime = get_runtime()
    assert runtime is not None

    ctx = current_tool_context.get()
    assert ctx is not None
    ctx.workspace_write_deny_globs = ["*.secret"]

    result = await shell.exec_command("echo payload > token.secret")
    payload = json.loads(result)
    assert payload["status"] == "blocked"
    assert payload["reason"] == "workspace_write_deny"

    assert await runtime.ledger.count_session("test-session-1513") == 1
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED


@pytest.mark.asyncio
async def test_background_process_hard_blocks_record_policy_denied_in_ledger() -> None:
    runtime = get_runtime()
    assert runtime is not None

    denied_cmd = "Clear-Disk -Number 1" if os.name == "nt" else "mkfs.ext4 /dev/sda"
    with pytest.raises(ToolError):
        await shell.background_process(denied_cmd)

    assert await runtime.ledger.count_session("test-session-1513") == 1
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED

    result = await shell.background_process("cat ~/.ssh/id_rsa")
    payload = json.loads(result)
    assert payload["status"] == "blocked"
    assert await runtime.ledger.count_session("test-session-1513") == 2
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED


@pytest.mark.asyncio
async def test_approval_auto_deny_records_policy_denied_in_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = get_runtime()
    assert runtime is not None
    monkeypatch.setattr(shell, "_sandbox_effectively_off", lambda: True)

    queue = get_approval_queue()
    queue.set_settings("auto-deny")

    warned_cmd = "Remove-Item target.txt" if os.name == "nt" else "rm target.txt"
    result = await shell.exec_command(warned_cmd)
    payload = json.loads(result)
    assert payload["status"] == "approval_denied"

    assert await runtime.ledger.count_session("test-session-1513") == 1
    _, last_reason = await runtime.ledger.last_denial("test-session-1513")
    assert last_reason == DenialReason.POLICY_DENIED


def test_sandbox_request_for_resolves_relative_workdir(tmp_path: Path) -> None:
    built = shell._sandbox_request_for("exec_command", "echo ok", "nested/subfolder")
    assert built is not None
    request, _, session_id = built
    assert session_id == "test-session-1513"
    assert request.cwd == (tmp_path / "nested" / "subfolder").resolve()
