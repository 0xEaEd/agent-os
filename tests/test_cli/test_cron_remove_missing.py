"""CLI cron remove must not invent success when the gateway says NOT_FOUND.

Pairs with gateway/rpc_cron.py fix for https://github.com/use-agent-os/agent-os/issues/1598.
"""

from __future__ import annotations

import json
from typing import Any

from typer.testing import CliRunner

from agentos.cli.main import app

runner = CliRunner()


class _NotFoundOnRemoveGateway:
    calls: list[tuple[str, dict[str, Any]]] = []

    def __init__(self) -> None:
        pass

    async def connect(self, url: str, *, token=None) -> None:
        return None

    async def close(self) -> None:
        return None

    async def call(self, method: str, params: dict | None = None) -> Any:
        from agentos.cli.gateway_client import GatewayRPCError

        type(self).calls.append((method, params or {}))
        if method == "cron.remove":
            raise GatewayRPCError(
                method,
                code="NOT_FOUND",
                message=f"Cron job not found: {(params or {}).get('id')}",
            )
        return {}


def test_cron_remove_missing_job_exits_nonzero(monkeypatch) -> None:
    _NotFoundOnRemoveGateway.calls = []
    monkeypatch.setattr(
        "agentos.cli.gateway_client.GatewayClient",
        _NotFoundOnRemoveGateway,
    )

    result = runner.invoke(
        app,
        ["cron", "remove", "missing-job-xyz", "--yes", "--json"],
    )

    assert result.exit_code != 0
    err = json.loads(result.stderr)
    assert err["error"]["code"] == "NOT_FOUND"
    assert "missing-job-xyz" in err["error"]["message"]
    # Must not emit the invented success payload on stdout.
    assert "removed" not in (result.stdout or "")
    assert ("cron.remove", {"id": "missing-job-xyz"}) in _NotFoundOnRemoveGateway.calls


def test_cron_remove_success_still_reports_removed(monkeypatch) -> None:
    class _OkRemoveGateway(_NotFoundOnRemoveGateway):
        async def call(self, method: str, params: dict | None = None) -> Any:
            type(self).calls.append((method, params or {}))
            return None

    _OkRemoveGateway.calls = []
    monkeypatch.setattr("agentos.cli.gateway_client.GatewayClient", _OkRemoveGateway)

    result = runner.invoke(
        app,
        ["cron", "remove", "job-1", "--yes", "--json"],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload == {"id": "job-1", "removed": True}
