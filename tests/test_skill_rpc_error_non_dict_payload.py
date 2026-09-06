"""``RpcError`` must survive a non-object JSON-RPC ``error`` payload.

The spec says ``error`` is an object, but nodes and proxies really do answer
with a bare string (``{"jsonrpc": "2.0", "id": 1, "error": "rate limit
exceeded"}``) or null. ``senior-unilp-manager`` and
``poolsdotfun-token-launcher`` read it as a dict unconditionally, so such a
response raised ``AttributeError: 'str' object has no attribute 'get'`` out of
the exception constructor and took the whole command down instead of raising
the ``RpcError`` every caller already handles.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED = _REPO_ROOT / "src" / "agentos" / "skills" / "bundled"
_SKILLS = (
    ("unilp", _BUNDLED / "senior-unilp-manager" / "scripts"),
    ("poolsfun", _BUNDLED / "poolsdotfun-token-launcher" / "scripts"),
)


def _load(package: str, scripts_dir: Path):
    """Import ``<package>.rpc`` from a bundled skill without touching PATH."""
    entry = str(scripts_dir)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        return importlib.import_module(f"{package}.rpc")
    finally:
        if added:
            sys.path.remove(entry)


@pytest.fixture(params=_SKILLS, ids=[name for name, _ in _SKILLS])
def rpc(request: pytest.FixtureRequest):
    package, scripts_dir = request.param
    return _load(package, scripts_dir)


@pytest.mark.parametrize(
    "payload",
    ["rate limit exceeded", 429, None, ["rate limited"], True],
    ids=["string", "int", "null", "list", "bool"],
)
def test_non_dict_error_payload_raises_rpc_error(rpc, payload: object) -> None:
    error = rpc.RpcError("eth_call", payload)

    assert isinstance(error, RuntimeError)
    assert str(error) == f"eth_call: {payload}"
    assert error.code is None
    assert error.data is None
    assert error.raw == payload


def test_dict_error_payload_is_unchanged(rpc) -> None:
    error = rpc.RpcError(
        "eth_call",
        {"code": -32000, "message": "execution reverted", "data": "0xdeadbeef"},
    )

    assert str(error) == "eth_call: execution reverted"
    assert error.code == -32000
    assert error.data == "0xdeadbeef"
    assert error.raw == {"code": -32000, "message": "execution reverted", "data": "0xdeadbeef"}


def test_dict_without_message_falls_back_to_the_whole_object(rpc) -> None:
    error = rpc.RpcError("eth_call", {"code": -32000})

    assert str(error) == "eth_call: {'code': -32000}"
    assert error.code == -32000
    assert error.data is None


def test_request_raises_rpc_error_for_a_string_error(rpc, monkeypatch) -> None:
    client = rpc.RpcClient.__new__(rpc.RpcClient)
    client._next_id = 0
    monkeypatch.setattr(
        rpc.RpcClient,
        "_post",
        lambda _self, _payload, _label: {
            "jsonrpc": "2.0",
            "id": 1,
            "error": "rate limit exceeded",
        },
    )

    with pytest.raises(rpc.RpcError) as excinfo:
        client.request("eth_blockNumber")

    assert "rate limit exceeded" in str(excinfo.value)
    assert excinfo.value.raw == "rate limit exceeded"


def test_batch_fallback_records_a_string_error_without_crashing(rpc, monkeypatch) -> None:
    """A per-call error must land in the result slot, not abort the sweep."""
    client = rpc.RpcClient.__new__(rpc.RpcClient)
    client._next_id = 0
    client._allow_batch = False
    monkeypatch.setattr(
        rpc.RpcClient,
        "_post",
        lambda _self, _payload, _label: {"jsonrpc": "2.0", "id": 1, "error": "boom"},
    )

    out = client.batch([{"method": "eth_call", "params": []}])

    assert out == [{"error": "boom"}]
