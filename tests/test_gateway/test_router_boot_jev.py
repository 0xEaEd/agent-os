"""Boot-preflight surface for the ``jev`` strategy (registry-driven).

``validate_agentos_router_runtime`` runs the registry ``credential_probe`` for
a strategy that ``requires_remote_credentials``. A missing key warns (and
degrades at runtime) unless ``require_router_runtime`` is set; a present key
logs ready. The key itself never appears in a log event.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.gateway.boot import validate_agentos_router_runtime
from agentos.gateway.config import GatewayConfig


def _config(**jev: Any) -> GatewayConfig:
    config = GatewayConfig()
    config.agentos_router.strategy = "jev"
    for key, value in jev.items():
        setattr(config.agentos_router.jev, key, value)
    return config


def test_jev_boot_warns_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    warnings: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agentos.gateway.boot.log.warning",
        lambda event, **kwargs: warnings.append({"event": event, **kwargs}),
    )

    validate_agentos_router_runtime(_config(api_key=None))

    events = [
        w for w in warnings if w["event"] == "build_services.agentos_router_credentials_missing"
    ]
    assert events
    assert events[0]["strategy"] == "jev"
    assert "TYPESAFE_API_KEY" in events[0]["problem"]


def test_jev_boot_raises_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = _config(api_key=None)
    config.agentos_router.require_router_runtime = True

    with pytest.raises(RuntimeError, match="jev router credentials missing"):
        validate_agentos_router_runtime(config)


def test_jev_boot_ready_with_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-secret")
    infos: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "agentos.gateway.boot.log.info",
        lambda event, **kwargs: infos.append({"event": event, **kwargs}),
    )
    config = _config(api_key=None)
    config.agentos_router.require_router_runtime = True

    validate_agentos_router_runtime(config)

    ready = [i for i in infos if i["event"] == "build_services.agentos_router_ready"]
    assert ready and ready[0]["strategy"] == "jev"
    assert "ts-secret" not in repr(infos)


def test_jev_boot_skips_judge_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []
    monkeypatch.setattr(
        "agentos.gateway.boot._log_resolved_judge", lambda *a, **k: called.append(a)
    )
    validate_agentos_router_runtime(_config(api_key="k"))
    assert called == []
