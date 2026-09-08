"""Tests for the updates.check gateway RPC handler."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.cli import update_notice
from agentos.gateway.access import CONTROL_ONLY
from agentos.gateway.config import GatewayConfig, UpdatesConfig
from agentos.gateway.rpc import RpcContext, get_dispatcher


@pytest.fixture(autouse=True)
def _state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTOS_NO_UPDATE_NOTICE", raising=False)
    for var in update_notice._CI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock_latest(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr("agentos.compat.pypi_client.latest_version", lambda timeout=2.0: value)


@pytest.mark.asyncio
async def test_updates_check_control_only() -> None:
    entry = get_dispatcher().get_entry("updates.check")
    assert entry is not None
    assert entry.audiences == CONTROL_ONLY


@pytest.mark.asyncio
async def test_updates_check_outdated(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_latest(monkeypatch, "2099.1.1")
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(),
    )

    response = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response.ok is True
    assert response.payload["latest"] == "2099.1.1"
    assert response.payload["status"] == "outdated"


@pytest.mark.asyncio
async def test_updates_check_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_latest(monkeypatch, "0.0.0+unknown")
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(),
    )

    response = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response.ok is True
    assert response.payload["latest"] == "0.0.0+unknown"
    assert response.payload["status"] == "up-to-date"


@pytest.mark.asyncio
async def test_updates_check_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_latest(monkeypatch, None)
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(),
    )

    response = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response.ok is True
    assert response.payload["latest"] is None
    assert response.payload["status"] == "offline"


@pytest.mark.asyncio
async def test_updates_check_respects_notify_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_latest(monkeypatch, "2099.1.1")
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(updates=UpdatesConfig(notify=False)),
    )

    response = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response.ok is True
    assert response.payload["latest"] is None
    assert response.payload["status"] == "offline"


@pytest.mark.asyncio
async def test_updates_check_respects_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_latest(monkeypatch, "2099.1.1")
    monkeypatch.setenv("AGENTOS_NO_UPDATE_NOTICE", "1")
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(),
    )

    response = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response.ok is True
    assert response.payload["latest"] is None
    assert response.payload["status"] == "offline"


@pytest.mark.asyncio
async def test_updates_check_throttling_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_latest(monkeypatch, "2099.1.1")
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(),
    )

    response1 = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response1.ok is True
    assert response1.payload["latest"] == "2099.1.1"
    assert response1.payload["status"] == "outdated"

    # Mock a newer version on PyPI, but check should hit cache and still return 2099.1.1
    _mock_latest(monkeypatch, "2099.2.2")
    response2 = await get_dispatcher().dispatch("req-2", "updates.check", {}, ctx)
    assert response2.ok is True
    assert response2.payload["latest"] == "2099.1.1"


@pytest.mark.asyncio
async def test_updates_check_namespaced_from_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from agentos.compat.pypi_client import notice_state_path, read_state

    _mock_latest(monkeypatch, "2099.1.1")
    ctx = RpcContext(
        conn_id="test",
        config=GatewayConfig(),
    )

    # 1. Run web UI update check.
    response = await get_dispatcher().dispatch("req-1", "updates.check", {}, ctx)
    assert response.ok is True
    assert response.payload["latest"] == "2099.1.1"

    # Verify state has "webui" namespace and "latest" at root.
    path = notice_state_path()
    state = read_state(path)
    assert "webui" in state
    assert "last_checked" in state["webui"]
    assert state["latest"] == "2099.1.1"

    # 2. Run CLI update check immediately after.
    # Even though Web UI just checked, CLI check is still due since it's namespaced separately.
    _mock_latest(monkeypatch, "2099.2.2")  # mock a newer one to verify it actually checks
    monkeypatch.setattr(update_notice, "_stderr_is_tty", lambda: True)
    msg = update_notice.maybe_emit_update_notice(current_version="2026.7.18")
    assert msg is not None
    assert "2099.2.2" in msg

    # State should now contain both "webui" and "cli" namespaces, with shared "latest".
    state = read_state(path)
    assert "cli" in state
    assert "last_checked" in state["cli"]
    assert "webui" in state
    assert state["latest"] == "2099.2.2"
