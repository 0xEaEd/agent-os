"""Doctor-payload + health surface for the ``jev`` strategy (registry-driven).

The doctor reports Jev runtime validity from the credential probe, surfaces
the env var name the key is read from (never the key), and short-circuits the
judge-resolution block. The health evaluator turns the missing-credential
reason into its own finding.
"""

from __future__ import annotations

import pytest

from agentos.gateway.config import GatewayConfig
from agentos.gateway.rpc import RpcContext
from agentos.health.evaluator import evaluate_router


def _payload(monkeypatch: pytest.MonkeyPatch, *, api_key: str | None, env_key: str | None = None):
    import agentos.gateway.rpc_doctor as rpc_doctor

    if env_key is None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", env_key)
    config = GatewayConfig(
        agentos_router={"strategy": "jev", "default_tier": "c1", "jev": {"api_key": api_key}}
    )
    return rpc_doctor._router_payload(RpcContext(conn_id="test", config=config))


def test_doctor_reports_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _payload(monkeypatch, api_key=None)
    assert payload["strategy"] == "jev"
    assert payload["runtimeValid"] is False
    assert payload["runtimeInvalidReason"] == "credentials_missing"
    assert "TYPESAFE_API_KEY" in str(payload["error"])
    assert "c1" in str(payload["error"])
    assert payload["credentialEnv"] == "TYPESAFE_API_KEY"
    # A remote-credential strategy never resolves a judge target.
    assert payload["judgeProvider"] is None
    assert payload["judgeModel"] is None
    assert payload["judgeSource"] is None


def test_doctor_reports_valid_with_key_and_never_leaks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload(monkeypatch, api_key="ts-literal-secret")
    assert payload["runtimeValid"] is True
    assert payload["runtimeInvalidReason"] is None
    assert payload["credentialEnv"] == "TYPESAFE_API_KEY"
    assert "ts-literal-secret" not in repr(payload)

    payload = _payload(monkeypatch, api_key=None, env_key="ts-env-secret")
    assert payload["runtimeValid"] is True
    assert "ts-env-secret" not in repr(payload)


def test_health_evaluator_emits_credentials_missing_finding() -> None:
    findings = evaluate_router(
        {
            "enabled": True,
            "rolloutPhase": "full",
            "strategy": "jev",
            "tierProfile": "custom",
            "runtimeValid": False,
            "runtimeInvalidReason": "credentials_missing",
            "credentialEnv": "TYPESAFE_API_KEY",
            "error": "The jev router is selected but has no API key",
        }
    )
    assert findings[0].id == "router.credentials.missing"
    assert findings[0].severity == "warn"
    assert findings[0].title == "Router credentials missing"
    assert "agentos env set TYPESAFE_API_KEY" in findings[0].fix_steps[0].detail
    assert "pilot-v1" in findings[0].fix_steps[0].detail
