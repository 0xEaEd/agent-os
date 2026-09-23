"""A node fault is not evidence about a contract.

``isStockToken`` has three states and SKILL.md is explicit about the third:
``false`` means the node answered and ``uiMultiplier()`` reverted -- "say so
plainly, and do not hand over the address" -- while ``null`` means the check
could not run, "unverified, *not* disproven". The script turns ``false`` into
a withheld price and an accusation in the reading, so a rate-limited public RPC
node must never land there.

No network: every RPC read is stubbed at ``_http_json``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = (
    ROOT
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "robinhood-chain-stocks"
    / "scripts"
    / "chain_stocks.py"
)

_spec = importlib.util.spec_from_file_location("chain_stocks_rpc_faults", _SCRIPT)
assert _spec is not None and _spec.loader is not None
chain_stocks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chain_stocks)

# The real GME Stock Token, per the skill's own SKILL.md.
REAL_GME = "0x1b0e319c6a659f002271b69db8a7df2f911c153e"
FEED: dict[str, Any] = {
    "proxyAddress": "0x" + "cc" * 20,
    "heartbeat": 3600,
    "threshold": 0.5,
}


def _responder(body: Any):
    def _call(_url: str, _timeout: float, _payload: dict[str, Any] | None = None) -> Any:
        return body

    return _call


def _inspect(monkeypatch: pytest.MonkeyPatch, body: Any) -> dict[str, Any]:
    monkeypatch.setattr(chain_stocks, "_http_json", _responder(body))
    return chain_stocks.inspect_token(
        rpc_url="https://rpc.example/x",
        address=REAL_GME,
        timeout=5.0,
        feed=FEED,
        feeds=[],
        now=1_700_000_000,
    )


def test_a_contract_revert_is_still_authoritative(monkeypatch: pytest.MonkeyPatch) -> None:
    # The boundary this fix must not move: a genuine revert stays `false`.
    out = _inspect(monkeypatch, {"error": {"code": 3, "message": "execution reverted"}})

    assert out["isStockToken"] is False
    assert "price" not in out


def test_a_revert_reported_under_the_legacy_code_is_still_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some nodes report a revert as -32000; the message is what names it.
    out = _inspect(
        monkeypatch,
        {"error": {"code": -32000, "message": "execution reverted: not a stock token"}},
    )

    assert out["isStockToken"] is False


def test_a_rate_limited_node_leaves_the_verdict_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _inspect(monkeypatch, {"error": {"code": -32005, "message": "limit exceeded"}})

    assert out["isStockToken"] is None


def test_an_internal_node_error_leaves_the_verdict_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = _inspect(monkeypatch, {"error": {"code": -32603, "message": "internal error"}})

    assert out["isStockToken"] is None


def test_an_unusable_rpc_body_leaves_the_verdict_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A valid eth_call always answers with a hex string; anything else is the
    # node (or a proxy in front of it) failing, not the contract answering.
    out = _inspect(monkeypatch, {"jsonrpc": "2.0", "id": 1, "result": None})

    assert out["isStockToken"] is None


def test_a_node_fault_does_not_withhold_the_price_as_a_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The withheld-price note is reserved for a confirmed impersonator. Under a
    # node fault the price read fails on its own, and the recorded reason has to
    # be the real one so the operator can see it was the node.
    out = _inspect(monkeypatch, {"error": {"code": -32005, "message": "limit exceeded"}})

    price_error = out.get("readErrors", {}).get("price", "")
    assert "failed the Stock Token check" not in price_error
    assert "limit exceeded" in price_error
    assert "notes" not in out or not any("not a Robinhood Stock Token" in n for n in out["notes"])


def test_a_transport_failure_still_leaves_the_verdict_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Guard on the path that already worked, so the new branch cannot regress it.
    def _boom(_url: str, _timeout: float, _payload: dict[str, Any] | None = None) -> Any:
        raise TimeoutError("read timed out")

    monkeypatch.setattr(chain_stocks, "_http_json", _boom)
    out = chain_stocks.inspect_token(
        rpc_url="https://rpc.example/x",
        address=REAL_GME,
        timeout=5.0,
        feed=FEED,
        feeds=[],
        now=1_700_000_000,
    )

    assert out["isStockToken"] is None


def test_a_short_return_blob_still_counts_as_the_contract_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `0x` is a successful eth_call: the node reached the contract and the
    # contract produced no multiplier. That is an answer, not a node fault.
    out = _inspect(monkeypatch, {"jsonrpc": "2.0", "id": 1, "result": "0x"})

    assert out["isStockToken"] is False


def test_a_genuine_multiplier_still_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    # Positive control through the same entry point: a node that answers with a
    # real word must still produce `true`.
    word = "0x" + f"{10**18:064x}"
    out = _inspect(monkeypatch, {"jsonrpc": "2.0", "id": 1, "result": word})

    assert out["isStockToken"] is True
    assert out["uiMultiplier"] == str(10**18)
