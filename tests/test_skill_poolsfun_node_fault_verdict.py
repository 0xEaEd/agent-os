"""Tests for the poolsdotfun node-fault / contract-revert verdict (#3309).

A JSON-RPC error is raised both when the contract answers with a revert and
when the node refuses the call (a bare-string rate limit, a transient internal
error). Only the first is evidence about the contract, so only the first may be
reported as ``startTickFor reverted`` / ``simulation reverted``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "poolsdotfun-token-launcher"
    / "scripts"
)

WETH = "0x4200000000000000000000000000000000000006"
_ERROR_STRING_REVERT = (
    "0x08c379a0" + "00" * 31 + "20" + "00" * 31 + "08" + "6e6f742077657468" + "00" * 24
)


def _module(name: str) -> Any:
    entry = str(_SCRIPTS_DIR)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        return importlib.import_module(name)
    finally:
        if added:
            sys.path.remove(entry)


class _RaisingClient:
    """Stands in for RpcClient; every call raises the given RpcError."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read(self, *args: Any, **kwargs: Any) -> Any:
        raise self._exc

    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise self._exc


class _SequencedClient:
    """Raises each error in turn: the first call, then the retry."""

    def __init__(self, errors: list[Exception]) -> None:
        self._errors = list(errors)

    def request(self, *args: Any, **kwargs: Any) -> Any:
        raise self._errors.pop(0)


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        ("rate limit exceeded", "rate limit exceeded"),
        (
            {"code": 19, "message": "Temporary internal error. Please retry"},
            "Temporary internal error",
        ),
        ({"code": -32005, "message": "rate limit exceeded"}, "rate limit exceeded"),
    ],
)
def test_a_node_fault_is_not_reported_as_a_start_tick_revert(error: Any, cause: str) -> None:
    """A refused call must not be stated as ``startTickFor reverted for ...``."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")

    with pytest.raises(RuntimeError) as caught:
        plan.read_start_tick(_RaisingClient(rpc.RpcError("startTickFor", error)), WETH)

    message = str(caught.value)
    assert "startTickFor reverted" not in message
    assert cause in message


def test_a_real_revert_still_reads_as_a_revert() -> None:
    """A revert blob keeps its decoded sentence (anti-drift)."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    error = {"code": 3, "message": "execution reverted", "data": _ERROR_STRING_REVERT}

    with pytest.raises(RuntimeError) as caught:
        plan.read_start_tick(_RaisingClient(rpc.RpcError("startTickFor", error)), WETH)

    assert str(caught.value) == "reverted: not weth"


def test_a_revert_named_only_by_its_code_still_reads_as_a_revert() -> None:
    """Code 3 without a blob is still the contract answering (anti-drift)."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    error = {"code": 3, "message": "execution reverted"}

    with pytest.raises(RuntimeError) as caught:
        plan.read_start_tick(_RaisingClient(rpc.RpcError("startTickFor", error)), WETH)

    assert str(caught.value) == f"startTickFor reverted for {WETH}"


def _simulate_args() -> dict[str, Any]:
    return dict(
        factory="0x" + "11" * 20,
        name="N",
        symbol="S",
        metadata_uri="uri",
        salt="0x" + "00" * 32,
        paired_asset="0x" + "22" * 20,
        start_tick=0,
        deadline=9999999999,
        creator="0x" + "33" * 20,
        fee_recipient="0x" + "33" * 20,
        dev_buy_amount_in=0,
        value_wei=0,
        from_address="0x" + "44" * 20,
    )


def test_a_node_fault_is_not_reported_as_a_reverted_simulation() -> None:
    """The simulation's no-override failure path must not claim a revert."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    error = {"code": 19, "message": "Temporary internal error. Please retry"}

    with pytest.raises(RuntimeError) as caught:
        plan.simulate_launch(_RaisingClient(rpc.RpcError("eth_call", error)), **_simulate_args())

    message = str(caught.value)
    assert "simulation reverted" not in message
    assert "Temporary internal error" in message


def test_a_node_fault_after_the_override_retry_is_not_a_reverted_simulation() -> None:
    """The retry failure path is the same claim, and gets the same treatment."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    first = rpc.RpcError(
        "eth_call", {"code": -32602, "message": "invalid params: unknown override parameter"}
    )
    second = rpc.RpcError(
        "eth_call", {"code": 19, "message": "Temporary internal error. Please retry"}
    )

    with pytest.raises(RuntimeError) as caught:
        plan.simulate_launch(_SequencedClient([first, second]), **_simulate_args())

    message = str(caught.value)
    assert "simulation reverted" not in message
    assert "Temporary internal error" in message


def test_a_retry_revert_keeps_its_decoded_reason() -> None:
    """The retry path decodes the blob instead of dropping to the raw error."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    first = rpc.RpcError(
        "eth_call", {"code": -32602, "message": "invalid params: unknown override parameter"}
    )
    second = rpc.RpcError(
        "eth_call",
        {"code": 3, "message": "execution reverted", "data": _ERROR_STRING_REVERT},
    )

    with pytest.raises(RuntimeError) as caught:
        plan.simulate_launch(_SequencedClient([first, second]), **_simulate_args())

    assert str(caught.value) == "reverted: not weth"


def test_a_revert_blob_under_a_vm_exception_message_still_reads_as_a_revert() -> None:
    """A blob is contract output even when the message does not say 'revert'."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    first = rpc.RpcError(
        "eth_call", {"code": -32602, "message": "invalid params: unknown override parameter"}
    )
    second = rpc.RpcError(
        "eth_call",
        {"code": -32000, "message": "VM Exception", "data": _ERROR_STRING_REVERT},
    )

    with pytest.raises(RuntimeError) as caught:
        plan.simulate_launch(_SequencedClient([first, second]), **_simulate_args())

    assert str(caught.value) == "reverted: not weth"


def test_a_revert_blob_a_node_cannot_decode_still_counts_as_answered() -> None:
    """An undecodable blob is still the contract answering, not a node fault."""
    rpc = _module("poolsfun.rpc")
    plan = _module("poolsfun.plan")
    # A custom-error selector `explain_revert` does not know.
    error = {"code": -32000, "message": "VM Exception", "data": "0xdeadbeef" + "00" * 32}

    with pytest.raises(RuntimeError) as caught:
        plan.read_start_tick(_RaisingClient(rpc.RpcError("startTickFor", error)), WETH)

    message = str(caught.value)
    assert "node fault" not in message
    assert message == f"startTickFor reverted for {WETH}"
