"""RPC URLs shown to clients never carry the provider key that lives in their path."""

from __future__ import annotations

from agentos.trading.chains import redact_rpc_url


def test_redact_hides_path_and_query() -> None:
    assert redact_rpc_url("https://lb.drpc.live/base/SECRET-KEY") == "https://lb.drpc.live/…"
    assert (
        redact_rpc_url("https://rpc.example.org:8545/?key=abc") == "https://rpc.example.org:8545/…"
    )


def test_redact_keeps_bare_public_hosts() -> None:
    assert redact_rpc_url("https://mainnet.base.org") == "https://mainnet.base.org"
    assert redact_rpc_url("https://mainnet.base.org/") == "https://mainnet.base.org"
    assert redact_rpc_url("") == ""
    assert redact_rpc_url("not a url") == "…"
