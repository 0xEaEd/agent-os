from __future__ import annotations

import json
from typing import Any

import pytest

from agentos.tools.builtin.control import gateway, set_gateway_config
from agentos.tools.types import ToolError


class FakeGatewayConfig:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.patched: list[dict[str, Any]] = []

    def to_toml_dict(self) -> dict[str, Any]:
        return self._data

    async def patch(self, delta: dict[str, Any]) -> None:
        self.patched.append(delta)


@pytest.mark.asyncio
async def test_gateway_config_get_with_none_value() -> None:
    config_data = {
        "gateway": {
            "host": "127.0.0.1",
            "port": 8000,
            "auth_token": None,
        },
        "llm": {
            "default_model": "gpt-4",
            "fallback_model": None,
            "params": {"temperature": 0.0, "seed": None},
        },
    }
    fake_config = FakeGatewayConfig(config_data)
    set_gateway_config(fake_config)

    # Key with string value
    res_str = await gateway(action="config_get", key="gateway.host")
    parsed_str = json.loads(res_str)
    assert parsed_str["action"] == "config_get"
    assert parsed_str["key"] == "gateway.host"
    assert parsed_str["value"] == "127.0.0.1"

    # Key with None value
    res_none = await gateway(action="config_get", key="gateway.auth_token")
    parsed_none = json.loads(res_none)
    assert parsed_none["action"] == "config_get"
    assert parsed_none["key"] == "gateway.auth_token"
    assert parsed_none["value"] is None

    # Nested key with None value
    res_nested = await gateway(action="config_get", key="llm.params.seed")
    parsed_nested = json.loads(res_nested)
    assert parsed_nested["value"] is None

    # Truly non-existent key raises ToolError
    with pytest.raises(ToolError, match="Config key not found: gateway.missing"):
        await gateway(action="config_get", key="gateway.missing")

    # Non-dict leaf traversal raises ToolError
    with pytest.raises(ToolError, match="Config key not found: gateway.port.sub"):
        await gateway(action="config_get", key="gateway.port.sub")


@pytest.mark.asyncio
async def test_gateway_validation_errors() -> None:
    set_gateway_config(FakeGatewayConfig({}))

    with pytest.raises(ToolError, match="Invalid action: invalid"):
        await gateway(action="invalid")

    with pytest.raises(ToolError, match="'key' required for config_get"):
        await gateway(action="config_get")

    with pytest.raises(ToolError, match="'value' required for config_set"):
        await gateway(action="config_set", key="some.key")

    with pytest.raises(ToolError, match="'value' must be valid JSON"):
        await gateway(action="config_set", key="some.key", value="not-valid-json{")
