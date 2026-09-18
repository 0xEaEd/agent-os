"""``senior-unilp-manager`` on a Robinhood RPC that caps ``eth_getLogs``.

The drpc Robinhood endpoint answers any ``eth_getLogs`` wider than 100,000 blocks
with HTTP 500 and a JSON-RPC body ``{"error": {"code": 22, "message": "eth_getLogs
range over 100000 blocks is not supported on robinhood, ..."}}``. The skill still
declared Robinhood as ``supportsFullRange`` with 500k-block chunks, so every log
path died — after three pointless retries — with the bare text ``eth_getLogs: HTTP
500``, and nothing on the chain could find the AGENTOS pool: Doppler publishes no
Airlock registry on Robinhood, so ``resolve_launcher`` returns None and the only
remaining route was the log scan that had just failed.

These tests pin the three parts of the fix:

* the JSON-RPC error inside an HTTP 5xx body is surfaced, and a deterministic
  range error is not retried;
* the Robinhood chain entry declares the cap, and the chunked scanner honours it;
* a hook the launcher registry already labels is enough to derive and confirm the
  pool in one multicall, with no log scan at all.
"""

from __future__ import annotations

import email.message
import importlib
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "senior-unilp-manager"
    / "scripts"
)

AGENTOS = "0x6eDA83Fc299C10d474068A7E69771c809Bcbbba3"
WETH = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
DOPPLER_HOOK = "0x4e3468951D49f2EEa976eD0D6e75fFCb44a9a544"
AGENTOS_POOL = "0x1299aa8c4ea0db5b8453757ed129ed8e916561925926a161cb89842e3987401a"

RANGE_ERROR = {
    "code": 22,
    "message": "eth_getLogs range over 100000 blocks is not supported on robinhood, "
    "request a narrower fromBlock/toBlock range",
}
TRANSIENT_ERROR = {
    "code": 19,
    "message": "Temporary internal error. Please retry, trace-id: a4c4fd6c05de56ce",
}


def _load(name: str):
    entry = str(_SCRIPTS)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        return importlib.import_module(name)
    finally:
        if added:
            sys.path.remove(entry)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """``load_env()`` writes the real ``~/.agentos/.env`` into ``os.environ``; keep it out."""
    monkeypatch.setenv("AGENTOS_HOME", str(tmp_path))
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def rpc():
    return _load("unilp.rpc")


@pytest.fixture
def chains():
    return _load("unilp.chains")


@pytest.fixture
def robinhood(chains):
    return chains.CHAINS["robinhood"]


def _http_500(body: dict) -> urllib.error.HTTPError:
    payload = json.dumps({"id": 1, "jsonrpc": "2.0", "error": body}).encode()
    return urllib.error.HTTPError(
        url="https://rpc.example",
        code=500,
        msg="Internal Server Error",
        hdrs=email.message.Message(),
        fp=io.BytesIO(payload),
    )


def _client(rpc, monkeypatch, robinhood, errors: list[dict]):
    """An RpcClient whose transport raises HTTP 500 with the given bodies in turn."""
    attempts: list[int] = []

    def fake_urlopen(request, timeout=None):
        attempts.append(1)
        raise _http_500(errors[min(len(attempts), len(errors)) - 1])

    monkeypatch.setattr(rpc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(rpc.time, "sleep", lambda _s: None)
    client = rpc.RpcClient(robinhood, rpc_url="https://rpc.example")
    return client, attempts


# ---------------------------------------------------------------------------
# Transport: the error inside an HTTP 5xx body must reach the caller
# ---------------------------------------------------------------------------


def test_range_error_in_http_500_body_is_surfaced_without_retry(
    rpc, monkeypatch, robinhood
) -> None:
    client, attempts = _client(rpc, monkeypatch, robinhood, [RANGE_ERROR])

    with pytest.raises(rpc.RpcError) as excinfo:
        client.get_logs({"fromBlock": "0x0", "toBlock": "latest"})

    assert "range over 100000 blocks" in str(excinfo.value)
    assert excinfo.value.code == 22
    assert len(attempts) == 1, "a deterministic range error must not be retried"


def test_transient_error_in_http_500_body_is_retried_then_surfaced(
    rpc, monkeypatch, robinhood
) -> None:
    client, attempts = _client(rpc, monkeypatch, robinhood, [TRANSIENT_ERROR])

    with pytest.raises(rpc.RpcError) as excinfo:
        client.get_logs({"fromBlock": "0x0", "toBlock": "latest"})

    assert "Temporary internal error" in str(excinfo.value)
    assert len(attempts) == 4, "3 retries + the first attempt"


# ---------------------------------------------------------------------------
# Chain registry: Robinhood declares the cap, the scanner honours it
# ---------------------------------------------------------------------------


def test_robinhood_declares_the_getlogs_cap(robinhood) -> None:
    scan = robinhood["logScan"]
    assert scan["supportsFullRange"] is False
    assert 0 < scan["chunkBlocks"] <= 100_000
    # The v4 contracts deploy within the first ~10k blocks; scanning from 0 is the
    # same cost, but the number must not lie about where the deployment is.
    assert scan["fromBlock"] <= 9_070
    # Reserves default to the tick-bitmap walk, which needs no logs.
    assert robinhood["rangeMode"] == "ticks"


def test_get_logs_chunked_never_exceeds_the_cap(robinhood) -> None:
    v4_pool = _load("unilp.v4_pool")
    windows: list[tuple[int, int]] = []

    class Client:
        def block_number(self):
            return 350_000

        def get_logs(self, params):
            lo, hi = int(params["fromBlock"], 16), int(params["toBlock"], 16)
            assert params["toBlock"] != "latest"
            windows.append((lo, hi))
            return []

    v4_pool.get_logs_chunked(
        Client(), robinhood, "0x" + "11" * 20, ["0x" + "22" * 32], from_block=0
    )

    assert windows, "the scanner must issue chunked requests"
    cap = robinhood["logScan"]["chunkBlocks"]
    assert all(hi - lo + 1 <= cap for lo, hi in windows)
    assert windows[0][0] == 0 and windows[-1][1] == 350_000


# ---------------------------------------------------------------------------
# Discovery: a labelled hook is a registry of one
# ---------------------------------------------------------------------------


class _SlotClient:
    """Answers getSlot0 for exactly one initialized pool; refuses any log scan."""

    def __init__(self, live_pool: str) -> None:
        self.live = live_pool.lower()
        self.calls: list[list[dict]] = []

    def multicall(self, calls, allow_failure=True, **kwargs):
        self.calls.append(calls)
        out = []
        for call in calls:
            assert call["functionName"] == "getSlot0", call
            pool_id = call["args"][0].lower()
            price = 2146980011035521529512487118669094 if pool_id == self.live else 0
            out.append({"status": "success", "result": (price, 204155, 0, 7000)})
        return out

    def get_logs(self, params):  # pragma: no cover - the assertion is the point
        raise AssertionError("discovery must not scan logs")

    def block_number(self):  # pragma: no cover
        raise AssertionError("discovery must not scan logs")


def test_labelled_hook_derives_the_agentos_pool_without_a_registry(robinhood) -> None:
    lp_read = _load("lp_read")
    client = _SlotClient(AGENTOS_POOL)

    found = lp_read.discover_via_launcher(client, robinhood, AGENTOS)

    assert found is not None, "a labelled hook must be tried before giving up"
    assert found["launcher"]["hook"].lower() == DOPPLER_HOOK.lower()
    assert found["launcher"]["name"] == "Doppler (Bankr)"
    assert [i["poolId"].lower() for i in found["inits"]] == [AGENTOS_POOL.lower()]
    key = found["inits"][0]["poolKey"]
    assert key["currency0"].lower() == WETH.lower()
    assert key["currency1"].lower() == AGENTOS.lower()
    assert key["tickSpacing"] == 200
    assert len(client.calls) == 1, "one multicall confirms every candidate"


def test_labelled_hook_with_no_live_pool_falls_through(robinhood) -> None:
    lp_read = _load("lp_read")
    client = _SlotClient(live_pool="0x" + "00" * 32)

    assert lp_read.discover_via_launcher(client, robinhood, WETH) is None
