"""Strict JSON-RPC gateways want hex QUANTITY fields and must never see their key echoed."""

from __future__ import annotations

import json

import httpx
import pytest

from agentos.trading.evm import EvmClient, EvmTransportError, rpc_tx


def test_rpc_tx_hex_encodes_quantities_only() -> None:
    tx = {"from": "0xabc", "to": "0xdef", "data": "0x", "value": 1, "gas": "21000", "nonce": 0}
    out = rpc_tx(tx)
    assert out["value"] == "0x1"
    assert out["gas"] == "0x5208"
    assert out["nonce"] == "0x0"
    assert out["data"] == "0x"
    assert out["from"] == "0xabc"
    assert rpc_tx({"value": "0x10"})["value"] == "0x10"


@pytest.mark.asyncio
async def test_simulate_and_estimate_send_hex_and_errors_hide_the_key() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["method"] == "eth_estimateGas":
            return httpx.Response(
                400,
                json={"jsonrpc": "2.0", "id": 1, "error": {"message": "Mismatch type string"}},
            )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": "0x"})

    client = EvmClient(
        "https://lb.example.org/base/SECRET-KEY",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await client.simulate({"from": "0xabc", "to": "0xdef", "data": "0x", "value": 5, "gas": 21000})
    call = seen[-1]["params"][0]
    assert call["value"] == "0x5" and call["gas"] == "0x5208"

    with pytest.raises(EvmTransportError) as excinfo:
        await client.estimate_gas({"from": "0xabc", "to": "0xdef", "data": "0x", "value": 5})
    message = str(excinfo.value)
    assert "SECRET-KEY" not in message
    assert "lb.example.org" in message
    assert "HTTP 400" in message and "Mismatch" in message
    await client.aclose()
