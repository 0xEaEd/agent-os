"""A process without an approval surface must not write into a live gateway's queue.

Regression for the 2026-09-27 incident: a probe script imported the shell
tool and drove ``_check_exec_approval`` through a matrix that included
``bash -c "rm -rf /etc"``. The gate persisted every command as a pending row
in the default ``~/.agentos/state/approval_queue.sqlite`` — the running
desktop app's queue — and the app popped "Approval needed for exec: rm -rf
/etc" at a user who never issued it. One click on Allow would have run it.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import IO

import pytest

from agentos.application import approval_queue as aq
from agentos.gateway import pidlock
from agentos.sandbox.integration import reset_runtime
from agentos.sandbox.intent_cache import reset_intent_cache
from agentos.tools.builtin import shell
from agentos.tools.types import CallerKind, ToolContext, current_tool_context

FOREIGN_PID = 4_000_000  # never ours; only the lock decides liveness anyway


def _write_pid_file(state_dir: Path, pid: int) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "gateway.pid").write_text(
        json.dumps({"pid": pid, "start_ts": "2026-09-27T06:41:02+00:00"})
    )


def _hold_gateway_lock(state_dir: Path) -> IO[bytes]:
    """Hold ``gateway.pid.lock`` the way a live gateway does.

    A second open of the same file in this process gets its own open-file
    description, so the queue's non-blocking probe contends with it exactly as
    it would with another process.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(str(state_dir / "gateway.pid.lock"), "a+b")
    assert pidlock._try_lock(fh)
    return fh


def _rows(db_path: Path) -> int:
    # ``with sqlite3.connect()`` commits on exit but does not close, and an
    # open handle makes the later ``reset_approval_queue()`` unlink fail on
    # Windows (WinError 32), so close explicitly.
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM approval_queue").fetchone()[0])
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _no_surface_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Importing the CLI package claims the surface for the whole process;
    # these tests are about the unclaimed (library / script) case.
    monkeypatch.setattr(aq, "_LOCAL_APPROVAL_SURFACE", False)


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def queue(state_dir: Path) -> Iterator[aq.ApprovalQueue]:
    q = aq.ApprovalQueue(db_path=str(state_dir / "approval_queue.sqlite"))
    yield q
    q.close()


# ── live_gateway_pid ────────────────────────────────────────────────────────


def test_no_pid_file_means_no_owner(state_dir: Path) -> None:
    state_dir.mkdir()
    assert pidlock.live_gateway_pid(state_dir) is None


def test_own_pid_is_not_a_foreign_owner(state_dir: Path) -> None:
    _write_pid_file(state_dir, os.getpid())
    fh = _hold_gateway_lock(state_dir)
    try:
        assert pidlock.live_gateway_pid(state_dir) is None
    finally:
        pidlock._unlock(fh)
        fh.close()


def test_stale_pid_file_without_lock_holder_is_not_an_owner(state_dir: Path) -> None:
    _write_pid_file(state_dir, FOREIGN_PID)
    (state_dir / "gateway.pid.lock").touch()
    assert pidlock.live_gateway_pid(state_dir) is None


def test_foreign_pid_with_held_lock_is_the_owner(state_dir: Path) -> None:
    _write_pid_file(state_dir, FOREIGN_PID)
    fh = _hold_gateway_lock(state_dir)
    try:
        assert pidlock.live_gateway_pid(state_dir) == FOREIGN_PID
    finally:
        pidlock._unlock(fh)
        fh.close()


def test_probe_does_not_steal_or_keep_the_lock(state_dir: Path) -> None:
    _write_pid_file(state_dir, FOREIGN_PID)
    (state_dir / "gateway.pid.lock").touch()
    assert pidlock.live_gateway_pid(state_dir) is None
    # The probe released what it briefly took: a gateway can still start.
    fh = _hold_gateway_lock(state_dir)
    pidlock._unlock(fh)
    fh.close()


# ── ApprovalQueue.request ───────────────────────────────────────────────────


def test_request_without_gateway_still_works(queue: aq.ApprovalQueue) -> None:
    approval_id = queue.request("exec", {"command": "rm -rf ./dist"})
    assert queue.get(approval_id).params["command"] == "rm -rf ./dist"


def test_request_refused_when_live_foreign_gateway_owns_state_dir(
    queue: aq.ApprovalQueue, state_dir: Path
) -> None:
    _write_pid_file(state_dir, FOREIGN_PID)
    fh = _hold_gateway_lock(state_dir)
    try:
        with pytest.raises(aq.ApprovalQueueOwnedByGatewayError) as excinfo:
            queue.request("exec", {"command": 'bash -c "rm -rf /etc"'})
    finally:
        pidlock._unlock(fh)
        fh.close()
    err = excinfo.value
    assert err.gateway_pid == FOREIGN_PID
    assert err.namespace == "exec"
    assert "AGENTOS_STATE_DIR" in str(err)
    assert _rows(queue._db_path) == 0
    assert queue.list_pending("exec") == []


def test_request_allowed_over_stale_pid_file(queue: aq.ApprovalQueue, state_dir: Path) -> None:
    _write_pid_file(state_dir, FOREIGN_PID)
    (state_dir / "gateway.pid.lock").touch()
    assert queue.request("plugin", {"name": "x"})


def test_claimed_surface_may_share_state_dir_with_live_gateway(
    queue: aq.ApprovalQueue, state_dir: Path
) -> None:
    _write_pid_file(state_dir, FOREIGN_PID)
    fh = _hold_gateway_lock(state_dir)
    try:
        aq.claim_local_approval_surface()
        assert queue.request("exec", {"command": "rm -rf ./dist"})
    finally:
        pidlock._unlock(fh)
        fh.close()


# ── through the real entry point: the exec gate ─────────────────────────────


@pytest.mark.asyncio
async def test_exec_gate_does_not_leak_into_live_gateway_queue(
    monkeypatch: pytest.MonkeyPatch, state_dir: Path
) -> None:
    """The incident shape: the shell gate driven from a process that is not the gateway."""
    db_path = state_dir / "approval_queue.sqlite"
    monkeypatch.setattr(aq, "_DEFAULT_APPROVAL_QUEUE_PATH", db_path)
    aq.reset_approval_queue()
    reset_intent_cache()
    reset_runtime()
    monkeypatch.setattr(shell, "_sandbox_effectively_off", lambda: True)
    elevate_token = shell._elevate_current_call.set(False)
    ctx_token = current_tool_context.set(
        ToolContext(caller_kind=CallerKind.CLI, session_key="agent:main:probe")
    )
    _write_pid_file(state_dir, FOREIGN_PID)
    fh = _hold_gateway_lock(state_dir)
    try:
        with pytest.raises(aq.ApprovalQueueOwnedByGatewayError):
            # The incident's command is a sensitive-path hard block on a fixed
            # main and never reaches the queue; this one is merely warned and
            # forced to prompt, which is the row that leaked.
            await shell._check_exec_approval(
                "exec_command",
                "rm -rf ./dist",
                str(state_dir),
                "command requires approval",
                None,
                False,
            )
        assert _rows(db_path) == 0
    finally:
        pidlock._unlock(fh)
        fh.close()
        current_tool_context.reset(ctx_token)
        shell._elevate_current_call.reset(elevate_token)
        aq.reset_approval_queue()
        reset_intent_cache()
        reset_runtime()
