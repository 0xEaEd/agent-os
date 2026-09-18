"""``senior-unilp-manager``: fewer round trips per mint.

A live mint on Robinhood (session agent:main:b8e53394, 2026-09-18) took eleven
model round trips, four of them wasted on avoidable errors:

* ``ticks --pool <id>`` and ``mint --pool <id>`` both failed with "the PoolKey
  cannot be looked up" until ``--token`` was added — even though ``pools``
  had just derived and printed that very PoolKey. A PoolKey the skill has
  already confirmed on chain is now remembered on disk and found by poolId.
* "all AGENTOS" needed a hand-written ``balanceOf`` script, then the mint was
  refused because the +100 bps buffer exceeded the balance, with a message
  that told the agent to run ``approve``. ``--amount1 max`` sizes from the
  wallet and caps the buffer at the balance, and a balance shortfall caused
  by the buffer says so instead of pointing at approvals.
"""

from __future__ import annotations

import importlib
import os
import sys
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
AGENTOS_KEY = {
    "currency0": WETH,
    "currency1": AGENTOS,
    "fee": 8388608,
    "tickSpacing": 200,
    "hooks": DOPPLER_HOOK,
}
BALANCE = 961_316_825_381_778_821_086_676_585


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
    """Keep the skill's dotenv loader away from the real ``~/.agentos/.env``.

    ``state_root()`` calls ``load_env()``, which writes every key of that file into
    ``os.environ`` for the rest of the process — and a leaked ``POOLSFUN_PRIVATE_KEY``
    flips an unrelated skill to "configured" in later prompt-contract tests.
    """
    monkeypatch.setenv("AGENTOS_HOME", str(tmp_path))
    monkeypatch.setenv("UNILP_STATE_DIR", str(tmp_path))
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path


@pytest.fixture
def robinhood():
    return _load("unilp.chains").CHAINS["robinhood"]


class _NoChain:
    """A client that must not be touched — the answer has to come from disk."""

    def __getattr__(self, name):  # pragma: no cover - the assertion is the point
        raise AssertionError(f"unexpected RPC call {name}: the PoolKey should come from the cache")


class _SlotClient:
    def __init__(self, live_pool: str) -> None:
        self.live = live_pool.lower()

    def multicall(self, calls, allow_failure=True, **kwargs):
        out = []
        for call in calls:
            pool_id = call["args"][0].lower()
            price = 2146980011035521529512487118669094 if pool_id == self.live else 0
            out.append({"status": "success", "result": (price, 204155, 0, 7000)})
        return out


# ---------------------------------------------------------------------------
# Pool cache
# ---------------------------------------------------------------------------


def test_remembered_pool_key_is_found_by_pool_id(state_dir, robinhood) -> None:
    poolcache = _load("unilp.poolcache")

    poolcache.remember(robinhood, [{"poolId": AGENTOS_POOL, "poolKey": AGENTOS_KEY}])

    assert poolcache.lookup(robinhood, AGENTOS_POOL) == AGENTOS_KEY
    assert poolcache.lookup(robinhood, AGENTOS_POOL.upper()) == AGENTOS_KEY, "case-insensitive"
    assert poolcache.lookup(robinhood, "0x" + "ab" * 32) is None
    assert (state_dir / "pools" / "robinhood.json").exists()


def test_cache_is_per_chain(state_dir) -> None:
    chains = _load("unilp.chains").CHAINS
    poolcache = _load("unilp.poolcache")

    poolcache.remember(chains["robinhood"], [{"poolId": AGENTOS_POOL, "poolKey": AGENTOS_KEY}])

    assert poolcache.lookup(chains["base"], AGENTOS_POOL) is None


def test_pool_key_for_id_answers_from_cache_without_token(state_dir, robinhood) -> None:
    lp_read = _load("lp_read")
    _load("unilp.poolcache").remember(robinhood, [{"poolId": AGENTOS_POOL, "poolKey": AGENTOS_KEY}])

    key = lp_read.pool_key_for_id(_NoChain(), robinhood, AGENTOS_POOL, {})

    assert key == AGENTOS_KEY


def test_explicit_pool_key_still_wins_over_cache(state_dir, robinhood) -> None:
    lp_read = _load("lp_read")
    _load("unilp.poolcache").remember(
        robinhood, [{"poolId": AGENTOS_POOL, "poolKey": {**AGENTOS_KEY, "tickSpacing": 60}}]
    )
    args = {
        "currency0": WETH,
        "currency1": AGENTOS,
        "fee": "8388608",
        "tick-spacing": "200",
        "hooks": DOPPLER_HOOK,
    }

    key = lp_read.pool_key_for_id(_NoChain(), robinhood, AGENTOS_POOL, args)

    assert key["tickSpacing"] == 200, "a PoolKey spelled out on the command line is authoritative"


def test_discovery_populates_the_cache(state_dir, robinhood) -> None:
    lp_read = _load("lp_read")
    poolcache = _load("unilp.poolcache")

    found = lp_read.discover_via_launcher(_SlotClient(AGENTOS_POOL), robinhood, AGENTOS)

    assert found is not None
    cached = poolcache.lookup(robinhood, AGENTOS_POOL)
    assert cached is not None
    assert cached["hooks"].lower() == DOPPLER_HOOK.lower()
    assert cached["tickSpacing"] == 200


def test_pool_key_for_id_survives_a_corrupt_cache_file(state_dir, robinhood) -> None:
    lp_read = _load("lp_read")
    (state_dir / "pools").mkdir()
    (state_dir / "pools" / "robinhood.json").write_text("{not json")
    args = {
        "currency0": WETH,
        "currency1": AGENTOS,
        "fee": "8388608",
        "tick-spacing": "200",
        "hooks": DOPPLER_HOOK,
    }

    key = lp_read.pool_key_for_id(_NoChain(), robinhood, AGENTOS_POOL, args)

    assert key["tickSpacing"] == 200


# ---------------------------------------------------------------------------
# --amount max
# ---------------------------------------------------------------------------


class _BalanceClient:
    def __init__(self, balances: dict[str, int]) -> None:
        self.balances = {k.lower(): v for k, v in balances.items()}

    def multicall(self, calls, allow_failure=True, **kwargs):
        out = []
        for call in calls:
            assert call["functionName"] == "balanceOf", call
            out.append({"status": "success", "result": self.balances[call["address"].lower()]})
        return out


def test_amount_max_resolves_to_the_wallet_balance(robinhood) -> None:
    lp_write = _load("lp_write")
    args = {"amount1": "max"}
    client = _BalanceClient({AGENTOS: BALANCE})

    maxed = lp_write.resolve_max_amounts(client, robinhood, "0x" + "11" * 20, args, AGENTOS_KEY)

    assert args["amount1"] == f"{BALANCE}w", "rewritten as raw base units for size_liquidity"
    assert maxed == {"amount1": BALANCE}


@pytest.mark.parametrize("word", ["max", "MAX", "all"])
def test_amount_max_accepts_both_spellings(robinhood, word) -> None:
    lp_write = _load("lp_write")
    args = {"amount0": word}

    maxed = lp_write.resolve_max_amounts(
        _BalanceClient({WETH: 5}), robinhood, "0x" + "11" * 20, args, AGENTOS_KEY
    )

    assert args["amount0"] == "5w"
    assert maxed == {"amount0": 5}


def test_amount_max_is_refused_for_the_native_currency(robinhood) -> None:
    lp_write = _load("lp_write")
    native_key = {**AGENTOS_KEY, "currency0": "0x" + "00" * 20}

    with pytest.raises(RuntimeError, match="native"):
        lp_write.resolve_max_amounts(
            _BalanceClient({}), robinhood, "0x" + "11" * 20, {"amount0": "max"}, native_key
        )


def test_plain_amounts_are_left_alone(robinhood) -> None:
    lp_write = _load("lp_write")
    args = {"amount1": "961316825.38"}

    maxed = lp_write.resolve_max_amounts(_NoChain(), robinhood, "0x" + "11" * 20, args, AGENTOS_KEY)

    assert maxed == {}
    assert args == {"amount1": "961316825.38"}


def test_slippage_buffer_is_capped_at_the_balance_when_maxed() -> None:
    lp_write = _load("lp_write")

    capped, note = lp_write.cap_at_balance(lp_write.with_slippage_up(BALANCE, 100), BALANCE)
    untouched, no_note = lp_write.cap_at_balance(BALANCE // 2, BALANCE)

    assert capped == BALANCE
    assert "capped at balance" in note
    assert (untouched, no_note) == (BALANCE // 2, "")


# ---------------------------------------------------------------------------
# The approvals gate must not blame approvals for a sizing problem
# ---------------------------------------------------------------------------


def _row(balance: int) -> dict:
    return {
        "currency": AGENTOS,
        "native": False,
        "erc20ToPermit2": 2**256 - 1,
        "permit2ToPosm": 2**160 - 1,
        "permit2Expiration": 4_000_000_000,
        "balance": balance,
    }


def test_buffer_shortfall_is_reported_as_sizing_not_approval() -> None:
    lp_write = _load("lp_write")
    required = BALANCE
    needed = lp_write.with_slippage_up(required, 100)

    problem = lp_write.allowance_problem(_row(BALANCE), needed, 1_700_000_000, required=required)

    assert problem is not None
    assert problem.kind == "balance"
    assert "buffer" in problem.message
    assert "--amount1 max" in problem.message or "--slippage-bps" in problem.message
    assert "approval" not in problem.message.lower()


def test_real_balance_shortfall_names_the_gap() -> None:
    lp_write = _load("lp_write")

    problem = lp_write.allowance_problem(_row(10), 20, 1_700_000_000, required=20)

    assert problem is not None
    assert problem.kind == "balance"
    assert "10" in problem.message and "20" in problem.message


def test_expired_permit2_is_still_an_approval_problem() -> None:
    lp_write = _load("lp_write")
    row = {**_row(BALANCE), "permit2Expiration": 1}

    problem = lp_write.allowance_problem(row, 5, 1_700_000_000, required=5)

    assert problem is not None
    assert problem.kind == "approval"
    assert "expired" in problem.message
