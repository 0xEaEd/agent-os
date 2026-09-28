"""LP writes (``docs/lp-write.md``): plans on the fake chain, then the order pipeline.

The planner runs against ``lp_world``'s fake PositionManager, which decodes the
``modifyLiquidities`` calldata it is handed (actions and params) and applies it
-- liquidity, fee payout, NFT mint and burn, Permit2 and ERC-20 allowances --
so a simulation and a mined transaction are the same code. The service tests
link that world to the ``FakeChain`` the engine signs against, and run each
write from order to ledger.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from agentos.trading import guardrails, lp, lp_write
from agentos.trading.chains import BASE, NATIVE_ADDRESS
from agentos.trading.ledger import Ledger
from agentos.trading.service import TradingError, TradingService
from agentos.trading.sync import _lp_moves, lp_note
from tests.test_trading.fakes import FakeChain
from tests.test_trading.lp_world import (
    GAS_PRICE,
    GAS_USED,
    LIB,
    MAX_EXCEEDED,
    OUTSIDER,
    PERMIT2,
    POS_BARE,
    POS_CLOSED,
    POS_ETH,
    POS_OUTSIDER,
    POS_WETH,
    SEL_ERC20_APPROVE,
    SEL_MODIFY,
    SEL_PERMIT2_APPROVE,
    TICK_2000,
    USDC,
    WALLET,
    WETH,
    Revert,
    World,
    env_for,
    eth_usdc_key,
    link,
    pepe_world,
    weth_usdc_key,
    wire,
    write_env,
    write_prices,
    write_world,
)

M = LIB.v4_math
PM = str(LIB.chains.CHAINS["base"]["positionManager"]).lower()


def _pool_id(key: dict[str, Any]) -> str:
    return str(LIB.v4_pool.compute_pool_id(key)).lower()


@pytest.fixture
def world() -> World:
    w = write_world(WALLET)
    w.fund(WALLET, NATIVE_ADDRESS, 10**18)
    w.fund(WALLET, WETH, 2 * 10**18)
    w.fund(WALLET, USDC, 5_000 * 10**6)
    return w


@pytest.fixture
def env(world: World) -> lp.ChainEnv:
    return write_env(world, WALLET)


def _codes(exc: pytest.ExceptionInfo[TradingError]) -> str:
    return exc.value.code


# ── ranges ──────────────────────────────────────────────────────────────────


class TestRanges:
    @pytest.mark.parametrize(
        ("text", "usd"),
        [("2M", 2e6), ("2.5m", 2.5e6), ("750k", 7.5e5), ("1e6", 1e6), ("$3B", 3e9)],
    )
    def test_mcap_units(self, text: str, usd: float) -> None:
        assert lp_write.parse_mcap(text) == usd

    def test_parse_every_form(self) -> None:
        assert lp_write.parse_range(None) == {"kind": "pct", "pct": 20.0, "text": "pct:20"}
        assert lp_write.parse_range("mcap:10M-2M")["lo"] == 2e6  # either order
        assert lp_write.parse_range("full")["kind"] == "full"
        assert lp_write.parse_range("ticks:-201000:-180000") == {
            "kind": "ticks",
            "lo": -201000,
            "hi": -180000,
            "text": "ticks:-201000:-180000",
        }

    @pytest.mark.parametrize(
        "text", ["mcap:abc", "mcap:2M", "mcap:2M-2M", "pct:0", "pct:150", "ticks:1", "wide"]
    )
    def test_bad_ranges_are_range_invalid(self, text: str) -> None:
        with pytest.raises(TradingError) as exc:
            lp_write.parse_range(text)
        assert exc.value.code == "trading.lp.range_invalid"

    def test_pct_full_and_ticks_resolve_on_the_spacing(self, env: lp.ChainEnv) -> None:
        state = lp.pool_states(
            env, [{"poolId": _pool_id(weth_usdc_key()), "poolKey": weth_usdc_key()}]
        )[0]
        side = lp.make_side(env, state["poolKey"])
        lo, hi = lp_write.resolve_ticks(side, state, lp_write.parse_range("pct:20"))
        assert lo % 10 == 0 and hi % 10 == 0 and lo < TICK_2000 < hi
        # ±20 %: ln(0.8)/ln(1.0001) ≈ -2231, ln(1.2)/ln(1.0001) ≈ 1823.
        assert TICK_2000 - lo == pytest.approx(2231, abs=10)
        assert hi - TICK_2000 == pytest.approx(1823, abs=10)
        full = lp_write.resolve_ticks(side, state, lp_write.parse_range("full"))
        assert full == (M.min_usable_tick(10), M.max_usable_tick(10))
        assert lp_write.resolve_ticks(
            side, state, lp_write.parse_range("ticks:-200400:-200200")
        ) == (
            -200400,
            -200200,
        )
        with pytest.raises(TradingError) as exc:
            lp_write.resolve_ticks(side, state, lp_write.parse_range("ticks:-200405:-200200"))
        assert exc.value.code == "trading.lp.range_invalid"
        assert "ticks:-200410:-200200" in str(exc.value)

    def test_pct_orientation_follows_the_base_token(self) -> None:
        """PEPE is currency1: its price rises as the tick falls, and pct:N follows it."""
        world = pepe_world()
        env = env_for(world)
        key = next(
            p.key for p in world.pools.values() if p.key["currency1"].lower().endswith("777d")
        )
        state = lp.pool_states(env, [{"poolId": _pool_id(key), "poolKey": key}])[0]
        side = lp.make_side(env, key, key["currency1"])
        assert side.base_is_currency1
        lo, hi = lp_write.resolve_ticks(side, state, lp_write.parse_range("pct:20"))
        # 1.2x the base price is 1823 ticks *down*; 0.8x is 2231 ticks up.
        assert state["tick"] - lo == pytest.approx(1823, abs=200)
        assert hi - state["tick"] == pytest.approx(2231, abs=200)

    def test_mcap_range_lands_on_the_asked_market_caps(self) -> None:
        world = pepe_world()
        env = env_for(world)
        key = next(
            p.key for p in world.pools.values() if p.key["currency1"].lower().endswith("777d")
        )
        state = lp.pool_states(env, [{"poolId": _pool_id(key), "poolKey": key}])[0]
        side = lp.make_side(env, key, key["currency1"])
        lo, hi = lp_write.resolve_ticks(side, state, lp_write.parse_range("mcap:2M-10M"))
        rng = side.range_json(lo, hi)
        assert rng["mcapLower"] == pytest.approx(2e6, rel=0.03)
        assert rng["mcapUpper"] == pytest.approx(1e7, rel=0.03)

    def test_parse_one_sided(self) -> None:
        assert lp_write.parse_range("above") == {"kind": "above", "pct": 20.0, "text": "above:20"}
        assert lp_write.parse_range("below:") == {"kind": "below", "pct": 20.0, "text": "below:20"}
        assert lp_write.parse_range("Above:35%")["text"] == "above:35"
        assert lp_write.parse_range("above:150")["pct"] == 150.0  # a rise can exceed 100 %
        assert lp_write.parse_range("below:12.5")["text"] == "below:12.5"

    @pytest.mark.parametrize("text", ["above:0", "above:-5", "above:x", "below:100", "below:nan"])
    def test_bad_one_sided_ranges_are_range_invalid(self, text: str) -> None:
        with pytest.raises(TradingError) as exc:
            lp_write.parse_range(text)
        assert exc.value.code == "trading.lp.range_invalid"

    @pytest.mark.parametrize("base_is_currency1", [False, True])
    @pytest.mark.parametrize(("kind", "pct"), [("above", 20.0), ("below", 20.0), ("above", 0.01)])
    def test_one_sided_ticks_sit_off_the_current_tick(
        self, env: lp.ChainEnv, base_is_currency1: bool, kind: str, pct: float
    ) -> None:
        """above:N holds only the base token, below:N only the quote, whichever currency
        the base is; the band never contains the current tick and is on the spacing."""
        if base_is_currency1:
            world = pepe_world()
            env = env_for(world)
            key = next(
                p.key for p in world.pools.values() if p.key["currency1"].lower().endswith("777d")
            )
            side = lp.make_side(env, key, key["currency1"])
        else:
            key = weth_usdc_key()
            side = lp.make_side(env, key)
        state = lp.pool_states(env, [{"poolId": _pool_id(key), "poolKey": key}])[0]
        assert side.base_is_currency1 is base_is_currency1
        spacing, tick = int(key["tickSpacing"]), int(state["tick"])
        lo, hi = lp_write.resolve_ticks(side, state, lp_write.parse_range(f"{kind}:{pct:g}"))
        assert lo % spacing == 0 and hi % spacing == 0 and lo < hi
        # V4's active range is [lower, upper): above the tick is lower > tick,
        # below it upper <= tick.
        range_above_tick = lo > tick
        assert range_above_tick or hi <= tick
        # Base price rises with the tick exactly when the base is currency0.
        assert range_above_tick == ((kind == "above") != base_is_currency1)
        amounts = M.get_amounts_for_liquidity(
            int(state["sqrtPriceX96"]),
            M.get_sqrt_ratio_at_tick(lo),
            M.get_sqrt_ratio_at_tick(hi),
            10**20,
        )
        base_raw, quote_raw = side.split(int(amounts["amount0"]), int(amounts["amount1"]))
        if kind == "above":
            assert base_raw > 0 and quote_raw == 0
        else:
            assert quote_raw > 0 and base_raw == 0
        if pct >= 1:
            # The far edge is N % from the price, within one spacing.
            step = math.log(1.0001)
            width = math.log(1.2) / step if kind == "above" else -math.log(0.8) / step
            far = hi if range_above_tick else lo
            assert abs(abs(far - tick) - width) <= spacing + 1
        else:
            assert hi - lo == spacing  # narrower than a spacing still gets one

    def test_one_sided_add_takes_only_that_token(self, env: lp.ChainEnv) -> None:
        pool = _pool_id(weth_usdc_key())
        above = lp_write.plan_add(env, pool, usd=50, range_spec="above:10", wallet=WALLET)
        assert above["rangeSpec"] == "above:10" and above["rangeDefaulted"] is False
        assert above["oneSided"] == "base" and above["bounds"]["quote"] == "0"
        assert above["expected"]["quote"]["raw"] == "0"
        assert {a["symbol"] for a in above["approvals"]} == {"WETH"}
        assert above["range"]["tickLower"] > TICK_2000
        below = lp_write.plan_add(env, pool, usd=50, range_spec="below", wallet=WALLET)
        assert below["rangeSpec"] == "below:20"
        assert below["oneSided"] == "quote" and below["bounds"]["base"] == "0"
        assert {a["symbol"] for a in below["approvals"]} == {"USDC"}
        assert below["range"]["tickUpper"] <= TICK_2000
        # Sized in the side it holds; the other side is not asked for.
        sized = lp_write.plan_add(env, pool, amount_base="0.01", range_spec="above", wallet=WALLET)
        assert sized["oneSided"] == "base" and sized["expected"]["quote"]["raw"] == "0"
        with pytest.raises(TradingError) as exc:
            lp_write.plan_add(env, pool, amount_quote="10", range_spec="above", wallet=WALLET)
        assert exc.value.code == "trading.invalid" and exc.value.details["oneSided"] == "base"

    def test_mcap_needs_a_supply(self, env: lp.ChainEnv) -> None:
        key = weth_usdc_key()
        state = lp.pool_states(env, [{"poolId": _pool_id(key), "poolKey": key}])[0]
        side = lp.make_side(env, key)
        with pytest.raises(TradingError) as exc:
            lp_write.resolve_ticks(side, state, lp_write.parse_range("mcap:2M-10M"))
        assert exc.value.code == "trading.lp.range_invalid" and "supply" in str(exc.value)


# ── planners ────────────────────────────────────────────────────────────────


class TestCollectAndRemove:
    def test_collect_is_a_zero_decrease_and_a_take(self, env: lp.ChainEnv, world: World) -> None:
        plan = lp_write.plan_collect(env, POS_WETH)
        assert plan["op"] == "collect" and plan["tokenId"] == str(POS_WETH)
        assert plan["actions"] == ["DECREASE_LIQUIDITY", "TAKE_PAIR"]
        assert plan["liquidity"] == "0" and plan["bounds"] == {"base": "0", "quote": "0"}
        assert plan["slippagePct"] is None and plan["approvals"] == [] and plan["oneSided"] is None
        # WETH is currency0 and the base; the fees are what moves.
        assert plan["token"]["symbol"] == "WETH" and plan["quote"]["symbol"] == "USDC"
        assert plan["expected"]["base"]["raw"] == str(10**15)
        assert plan["expected"]["quote"]["raw"] == str(2 * 10**6)
        assert plan["expected"]["usd"] == pytest.approx(4.0)
        assert plan["fees"]["usd"] == pytest.approx(4.0)
        assert plan["positionValueUsd"] > plan["fees"]["usd"]
        assert plan["simulation"] == {
            "ok": True,
            "gasUsed": 200_000,
            "method": "eth_simulateV1",
            "revert": None,
        }
        assert plan["planHash"].startswith("0x") and len(plan["planHash"]) == 10
        assert plan["createdAtBlock"] == world.block and plan["chain"]["id"] == 8453
        assert plan["pool"]["poolKey"]["fee"] == 500 and plan["pool"]["hook"] is None
        # Simulated from the owner, and nothing stuck: the fees are still there.
        (calls,) = world.simulations
        assert calls[0]["from"].lower() == WALLET and calls[0]["to"].lower() == PM
        assert world.positions[POS_WETH].fees0 == 10**15

    def test_not_owner_closed_and_missing(self, env: lp.ChainEnv, world: World) -> None:
        with pytest.raises(TradingError) as exc:
            lp_write.plan_collect(env, POS_OUTSIDER)
        assert exc.value.code == "trading.lp.not_owner"
        assert exc.value.details["owner"].lower() == OUTSIDER
        with pytest.raises(TradingError) as exc:
            lp_write.plan_remove(env, POS_CLOSED)
        assert exc.value.code == "trading.lp.position_closed"
        with pytest.raises(TradingError) as exc:
            lp_write.plan_collect(env, 123456)
        assert exc.value.code == "trading.lp.not_found"
        # A dry run of somebody else's position simulates from its real owner.
        plan = lp_write.plan_collect(env, POS_OUTSIDER, check_owner=False)
        assert plan["wallet"].lower() == OUTSIDER
        assert world.simulations[-1][0]["from"].lower() == OUTSIDER

    def test_remove_all_burns_with_slippage_minimums(self, env: lp.ChainEnv) -> None:
        plan = lp_write.plan_remove(env, POS_WETH, 100, 2.0)
        assert plan["actions"] == ["DECREASE_LIQUIDITY", "BURN_POSITION", "TAKE_PAIR"]
        assert plan["burn"] is True and plan["pct"] == 100 and plan["liquidity"] == str(10**14)
        pos = env.client.positions[POS_WETH]
        sqrt = M.get_sqrt_ratio_at_tick(TICK_2000)
        principal = M.get_amounts_for_liquidity_at_ticks(
            sqrt, pos.tick_lower, pos.tick_upper, 10**14
        )
        assert plan["bounds"] == {
            "base": str(principal["amount0"] * 9800 // 10_000),
            "quote": str(principal["amount1"] * 9800 // 10_000),
        }
        assert plan["expected"]["base"]["raw"] == str(principal["amount0"] + 10**15)
        assert plan["expected"]["quote"]["raw"] == str(principal["amount1"] + 2 * 10**6)
        assert plan["slippagePct"] == 2.0

    def test_remove_half_decreases_and_keeps_the_nft(self, env: lp.ChainEnv) -> None:
        plan = lp_write.plan_remove(env, POS_WETH, 50)
        assert plan["actions"] == ["DECREASE_LIQUIDITY", "TAKE_PAIR"]
        assert plan["burn"] is False and plan["liquidity"] == str(10**14 // 2)
        assert plan["slippagePct"] == lp_write.DEFAULT_SLIPPAGE_PCT
        with pytest.raises(TradingError):
            lp_write.plan_remove(env, POS_WETH, 0)

    def test_a_simulated_revert_refuses_the_plan(
        self, env: lp.ChainEnv, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(self: World, sender: str, data: str, value: int) -> Any:
            raise Revert(MAX_EXCEEDED)

        monkeypatch.setattr(World, "_modify", refuse)
        with pytest.raises(TradingError) as exc:
            lp_write.plan_collect(env, POS_WETH)
        assert exc.value.code == "trading.simulation_failed"
        assert "MaximumAmountExceeded" in str(exc.value)
        assert exc.value.details["simulation"]["ok"] is False

    def test_eth_call_fallback_without_simulate_v1(self, env: lp.ChainEnv, world: World) -> None:
        world.simulate_v1 = False
        plan = lp_write.plan_collect(env, POS_WETH)
        assert plan["simulation"]["method"] == "eth_call" and plan["simulation"]["ok"] is True


class TestAdd:
    def test_usd_deposit_splits_in_range_and_needs_approvals(
        self, env: lp.ChainEnv, world: World
    ) -> None:
        plan = lp_write.plan_add(
            env, _pool_id(weth_usdc_key()), usd=100, range_spec="pct:10", wallet=WALLET
        )
        assert plan["op"] == "add" and plan["tokenId"] is None and plan["increase"] is False
        assert plan["actions"] == ["MINT_POSITION", "SETTLE_PAIR"] and plan["value"] == "0"
        assert plan["oneSided"] is None and plan["status"] == "in-range"
        assert plan["expected"]["usd"] == pytest.approx(100, rel=1e-3)
        assert (
            int(plan["expected"]["base"]["raw"]) > 0 and int(plan["expected"]["quote"]["raw"]) > 0
        )
        assert plan["rangeSpec"] == "pct:10" and plan["rangeDefaulted"] is False
        # Maxima = required + 1 %, rounded up.
        for leg in ("base", "quote"):
            need = int(plan["expected"][leg]["raw"])
            assert int(plan["bounds"][leg]) == need * 10_100 // 10_000 + 1
        steps = [(a["symbol"], a["step"], a["needed"]) for a in plan["approvals"]]
        assert steps == [
            ("WETH", "erc20->permit2", True),
            ("WETH", "permit2->posm", True),
            ("USDC", "erc20->permit2", True),
            ("USDC", "permit2->posm", True),
        ]
        assert [a["amountRaw"] for a in plan["approvals"]] == [
            plan["bounds"]["base"],
            plan["bounds"]["base"],
            plan["bounds"]["quote"],
            plan["bounds"]["quote"],
        ]
        # The pending approvals were simulated first, in one block with the mint.
        calls = world.simulations[-1]
        assert [c["data"][:10] for c in calls] == [
            SEL_ERC20_APPROVE,
            SEL_PERMIT2_APPROVE,
            SEL_ERC20_APPROVE,
            SEL_PERMIT2_APPROVE,
            SEL_MODIFY,
        ]
        assert plan["simulation"]["ok"] is True and 9_000 not in world.positions

    def test_one_sided_ranges_take_one_token(self, env: lp.ChainEnv) -> None:
        above = lp_write.plan_add(
            env,
            _pool_id(weth_usdc_key()),
            usd=50,
            range_spec=f"ticks:{TICK_2000 // 10 * 10 + 200}:{TICK_2000 // 10 * 10 + 1200}",
            wallet=WALLET,
        )
        assert above["oneSided"] == "base" and above["bounds"]["quote"] == "0"
        assert above["expected"]["quote"]["raw"] == "0"
        assert {a["symbol"] for a in above["approvals"]} == {"WETH"}
        below = lp_write.plan_add(
            env,
            _pool_id(weth_usdc_key()),
            usd=50,
            range_spec=f"ticks:{TICK_2000 // 10 * 10 - 1200}:{TICK_2000 // 10 * 10 - 200}",
            wallet=WALLET,
        )
        assert below["oneSided"] == "quote" and below["bounds"]["base"] == "0"
        assert {a["symbol"] for a in below["approvals"]} == {"USDC"}

    def test_token_amounts_size_from_the_binding_side(self, env: lp.ChainEnv) -> None:
        plan = lp_write.plan_add(
            env, _pool_id(weth_usdc_key()), amount_base="0.01", range_spec="pct:10", wallet=WALLET
        )
        assert int(plan["expected"]["base"]["raw"]) == pytest.approx(10**16, rel=1e-6)
        assert int(plan["expected"]["quote"]["raw"]) > 0  # computed, not given
        both = lp_write.plan_add(
            env,
            _pool_id(weth_usdc_key()),
            amount_base="1",
            amount_quote="5",
            range_spec="pct:10",
            wallet=WALLET,
        )
        assert int(both["expected"]["quote"]["raw"]) <= 5 * 10**6  # USDC binds
        with pytest.raises(TradingError) as exc:
            lp_write.plan_add(
                env,
                _pool_id(weth_usdc_key()),
                amount_quote="5",
                range_spec=f"ticks:{TICK_2000 // 10 * 10 + 200}:{TICK_2000 // 10 * 10 + 1200}",
                wallet=WALLET,
            )
        assert exc.value.code == "trading.invalid" and "only WETH" in str(exc.value)

    def test_a_short_wallet_is_refused_naming_the_side(
        self, env: lp.ChainEnv, world: World
    ) -> None:
        world.fund(WALLET, USDC, 0)
        with pytest.raises(TradingError) as exc:
            lp_write.plan_add(
                env, _pool_id(weth_usdc_key()), usd=100, range_spec="pct:10", wallet=WALLET
            )
        assert exc.value.code == "trading.insufficient_balance"
        assert exc.value.details["side"] == "quote" and exc.value.details["symbol"] == "USDC"
        assert "USDC" in str(exc.value) and "Nothing is swapped" in str(exc.value)

    def test_a_buffer_past_the_balance_is_capped_not_refused(
        self, env: lp.ChainEnv, world: World
    ) -> None:
        plan = lp_write.plan_add(
            env, _pool_id(weth_usdc_key()), usd=100, range_spec="pct:10", wallet=WALLET
        )
        need = int(plan["expected"]["quote"]["raw"])
        world.fund(WALLET, USDC, need)
        capped = lp_write.plan_add(
            env, _pool_id(weth_usdc_key()), usd=100, range_spec="pct:10", wallet=WALLET
        )
        assert int(capped["bounds"]["quote"]) == need

    def test_native_deposit_sends_value_and_sweeps(self, env: lp.ChainEnv, world: World) -> None:
        spacing_top = TICK_2000 // 60 * 60
        plan = lp_write.plan_add(
            env,
            _pool_id(eth_usdc_key()),
            usd=0.05,
            range_spec=f"ticks:{spacing_top + 120}:{spacing_top + 1200}",
            wallet=WALLET,
        )
        assert plan["token"]["address"] == NATIVE_ADDRESS and plan["oneSided"] == "base"
        assert plan["actions"] == ["MINT_POSITION", "SETTLE_PAIR", "SWEEP"]
        assert plan["value"] == plan["bounds"]["base"] and plan["approvals"] == []
        assert plan["expected"]["usd"] == pytest.approx(0.05, rel=1e-3)
        # A wallet with ETH for the deposit but not for gas is short.
        world.fund(WALLET, NATIVE_ADDRESS, int(plan["expected"]["base"]["raw"]))
        with pytest.raises(TradingError) as exc:
            lp_write.plan_add(
                env,
                _pool_id(eth_usdc_key()),
                usd=0.05,
                range_spec=f"ticks:{spacing_top + 120}:{spacing_top + 1200}",
                wallet=WALLET,
            )
        assert exc.value.code == "trading.insufficient_balance" and "gas" in str(exc.value)

    def test_pending_approvals_without_simulate_v1_are_skipped_not_refused(
        self, env: lp.ChainEnv, world: World
    ) -> None:
        world.simulate_v1 = False
        plan = lp_write.plan_add(
            env, _pool_id(weth_usdc_key()), usd=10, range_spec="pct:10", wallet=WALLET
        )
        assert plan["simulation"]["ok"] is None and plan["simulation"]["method"] == "skipped"
        native = lp_write.plan_add(
            env,
            _pool_id(eth_usdc_key()),
            usd=0.05,
            range_spec=f"ticks:{TICK_2000 // 60 * 60 + 120}:{TICK_2000 // 60 * 60 + 1200}",
            wallet=WALLET,
        )
        # A single call is eth_call'ed -- with its value, which the deposit needs.
        assert native["simulation"] == {
            "ok": True,
            "gasUsed": None,
            "method": "eth_call",
            "revert": None,
        }

    def test_increase_collects_fees_with_close_currency(self, env: lp.ChainEnv) -> None:
        plan = lp_write.plan_add(env, None, usd=20, to_position=POS_WETH)
        assert plan["increase"] is True and plan["tokenId"] == str(POS_WETH)
        assert plan["actions"] == ["INCREASE_LIQUIDITY", "CLOSE_CURRENCY", "CLOSE_CURRENCY"]
        assert plan["rangeSpec"] == f"position:{POS_WETH}"
        bare = lp_write.plan_add(env, USDC, usd=20, to_position=POS_BARE)
        assert bare["actions"] == ["INCREASE_LIQUIDITY", "SETTLE_PAIR"]
        with pytest.raises(TradingError) as exc:
            lp_write.plan_add(env, None, usd=20, to_position=POS_OUTSIDER)
        assert exc.value.code == "trading.lp.not_owner"
        with pytest.raises(TradingError):
            lp_write.plan_add(env, None, usd=20, to_position=POS_WETH, range_spec="full")


class TestCallAndRevalidation:
    def test_the_call_rebuilds_to_the_approved_hash(self, env: lp.ChainEnv) -> None:
        plan = lp_write.plan_remove(env, POS_WETH, 100)
        to, data, value = lp_write.modify_liquidities_call(plan, 1_760_000_600)
        assert to.lower() == PM and data.startswith(SEL_MODIFY) and value == 0
        tampered = {**plan, "liquidity": str(10**13)}
        with pytest.raises(TradingError) as exc:
            lp_write.modify_liquidities_call(tampered, 1_760_000_600)
        assert exc.value.code == "trading.invalid"

    def test_price_moved_against_an_add_or_a_remove(self, env: lp.ChainEnv, world: World) -> None:
        pool = world.pools[_pool_id(weth_usdc_key())]
        add = lp_write.plan_add(env, pool.pool_id, usd=100, range_spec="pct:10", wallet=WALLET)
        remove = lp_write.plan_remove(env, POS_WETH, 100)
        checked = lp_write.revalidate(env, add)
        assert checked["deadline"] == world.timestamp + lp_write.DEADLINE_S
        assert [s["needed"] for s in checked["approvals"]] == [True] * 4
        pool.tick = TICK_2000 + 400  # WETH up 4 %
        with pytest.raises(TradingError) as exc:
            lp_write.revalidate(env, add)
        assert exc.value.code == "trading.price_moved"
        pool.tick = TICK_2000 + 900
        with pytest.raises(TradingError) as exc:
            lp_write.revalidate(env, remove)
        assert exc.value.code == "trading.price_moved"

    def test_a_position_that_changed_hands_is_not_ours(
        self, env: lp.ChainEnv, world: World
    ) -> None:
        plan = lp_write.plan_collect(env, POS_WETH)
        world.positions[POS_WETH].owner = OUTSIDER
        with pytest.raises(TradingError) as exc:
            lp_write.revalidate(env, plan)
        assert exc.value.code == "trading.lp.not_owner"


# ── guardrails and ledger ───────────────────────────────────────────────────


class TestGuardrails:
    @pytest.mark.parametrize("initiator", ["agent", "manual"])
    @pytest.mark.parametrize("op", ["collect", "remove"])
    def test_collect_and_remove_always_park(self, op: str, initiator: str) -> None:
        verdict = guardrails.evaluate_lp_write(
            op=op, initiator=initiator, value_usd=10**6, daily_cap_usd=0, spent_today_usd=10**6
        )
        assert verdict.decision == "needs_approval"

    def test_add_counts_against_the_agents_cap(self) -> None:
        def decide(initiator: str, value: float | None, cap: float, spent: float) -> str:
            return guardrails.evaluate_lp_write(
                op="add",
                initiator=initiator,
                value_usd=value,
                daily_cap_usd=cap,
                spent_today_usd=spent,
            ).decision

        assert decide("agent", 50, 100, 0) == "needs_approval"
        assert decide("agent", 50, 100, 60) == "blocked_daily_cap"
        assert decide("agent", 1, 0, 0) == "blocked_daily_cap"
        assert decide("agent", None, 100, 0) == "needs_approval"
        assert decide("manual", 5_000, 100, 0) == "needs_approval"

    def test_removals_in_flight_do_not_hold_the_agents_cap(self) -> None:
        ledger = Ledger(":memory:")
        try:
            for i, kind in enumerate(("lp_remove", "lp_collect", "lp_add")):
                ledger.insert_order(
                    {
                        "order_id": f"o{i}",
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "chain_id": 8453,
                        "wallet": WALLET,
                        "token_in": WETH,
                        "token_out": USDC,
                        "amount_raw": "1",
                        "amount_human": "1",
                        "value_usd": 500.0,
                        "status": "awaiting_approval",
                        "initiator": "agent",
                        "kind": kind,
                    }
                )
            assert ledger.open_agent_value_usd(WALLET, now=2.0) == 500.0
        finally:
            ledger.close()


def test_ledger_labels_and_rebuild_moves() -> None:
    plan = {"tokenId": "48213", "pct": 100.0, "token": {"symbol": "PEPE"}}
    assert lp_note("lp_collect", plan) == "Collect fees · #48213"
    assert lp_note("lp_remove", plan) == "Remove liquidity · #48213 · 100%"
    add = {"token": {"symbol": "PEPE"}, "quote": {"symbol": "WETH"}, "expected": {"usd": 50}}
    assert lp_note("lp_add", add) == "Add liquidity · PEPE/WETH · $50.00"
    order = {
        "gas_wei": "7",
        "quote_json": json.dumps(
            {"settlement": {"ins": [[WETH, "5", 1]], "outs": [[NATIVE_ADDRESS, 9, 1_000_000]]}}
        ),
    }
    assert _lp_moves(order) == ([(WETH, 5, 1)], [(NATIVE_ADDRESS, 9, 1_000_000)], 7)


# ── the order pipeline ──────────────────────────────────────────────────────


@pytest.fixture
def lp_service(funded_service: TradingService, base_chain: FakeChain) -> dict[str, Any]:
    """The funded service with an LP world linked to its chain and every tx mined on it."""
    wallet = funded_service.test_wallet.lower()  # type: ignore[attr-defined]
    world = write_world(wallet, block=base_chain.block)
    base_chain.set_erc20(WETH, wallet, 2 * 10**18)
    link(world, base_chain)
    mined = wire(world, base_chain, wallet)
    funded_service.lp_env_factory = lambda spec, loop: env_for(
        world, prices=write_prices(), vault={wallet: "Main"}
    )
    return {
        "service": funded_service,
        "world": world,
        "chain": base_chain,
        "wallet": wallet,
        "mined": mined,
    }


def _events(service: TradingService, name: str) -> list[dict[str, Any]]:
    return [p for e, p in service.events if e == name]  # type: ignore[attr-defined]


def _entries(service: TradingService, kind: str) -> list[dict[str, Any]]:
    return [e for e in service.ledger.list_entries(kind=kind)]


class TestPipeline:
    async def test_collect_parks_for_agent_and_operator_then_settles(
        self, lp_service: dict[str, Any]
    ) -> None:
        service: TradingService = lp_service["service"]
        wallet, world = lp_service["wallet"], lp_service["world"]
        for initiator in ("agent", "manual"):
            order = await service.lp_collect(
                chain=BASE, token_id=POS_WETH, initiator=initiator, session_key=None, note=None
            )
            assert order["status"] == "awaiting_approval" and order["kind"] == "lp_collect"
            assert order["reason"] == "an LP write always waits for you"
        assert lp_service["mined"] == []
        assert len(_events(service, "trading.approval.requested")) == 2
        assert order["plan"]["op"] == "collect" and order["tokenId"] == str(POS_WETH)
        assert order["tokenIn"]["symbol"] == "WETH" and order["tokenOut"]["symbol"] == "USDC"
        assert order["amountInRaw"] == "0" and order["valueUsd"] == pytest.approx(4.0)
        assert order["recipient"].lower() == wallet and order["providerLabel"] == "Uniswap V4"
        assert order["expiresAt"] is not None and order["received"] is None

        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "confirmed", done["reason"]
        assert done["txHash"] and done["explorerUrl"].endswith(done["txHash"])
        assert done["received"]["base"]["raw"] == str(10**15)
        assert done["received"]["quote"]["raw"] == str(2 * 10**6)
        assert done["spent"]["base"]["raw"] == "0" and done["gasUsd"] is not None
        assert world.positions[POS_WETH].fees0 == 0 and world.mined[-1][1] == [
            "DECREASE_LIQUIDITY",
            "TAKE_PAIR",
        ]
        entries = _entries(service, "lp_collect")
        assert sorted(e["token_out"] for e in entries) == sorted([WETH, USDC])
        assert sum(1 for e in entries if e["gas_usd"]) == 1
        assert all(e["note"] == f"Collect fees · #{POS_WETH}" for e in entries)
        assert service.ledger.get_balance(8453, wallet, USDC) == 1_000 * 10**6 + 2 * 10**6
        assert service.ledger.spent_today(wallet) == 0.0
        finished = _events(service, "trading.order.finished")[-1]["order"]
        assert finished["orderId"] == order["orderId"]

    async def test_remove_all_burns_and_books_principal_and_fees(
        self, lp_service: dict[str, Any]
    ) -> None:
        service: TradingService = lp_service["service"]
        world = lp_service["world"]
        order = await service.lp_remove(
            chain=BASE, token_id=POS_WETH, initiator="agent", session_key="s", note=None
        )
        assert order["amountInRaw"] == str(10**14) and order["slippagePct"] == 1.0
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "confirmed", done["reason"]
        assert POS_WETH not in world.positions
        plan = order["plan"]
        assert int(done["received"]["base"]["raw"]) >= int(plan["bounds"]["base"]) + 10**15
        assert done["received"]["base"]["raw"] == plan["expected"]["base"]["raw"]
        entries = _entries(service, "lp_remove")
        assert {e["token_out"] for e in entries} == {WETH, USDC}
        assert all(e["session_key"] == "s" for e in entries)

    async def test_add_runs_exact_approvals_then_mints(self, lp_service: dict[str, Any]) -> None:
        service: TradingService = lp_service["service"]
        world, wallet, mined = lp_service["world"], lp_service["wallet"], lp_service["mined"]
        order = await service.lp_add(
            chain=BASE,
            target=_pool_id(weth_usdc_key()),
            usd=100,
            range_spec="pct:10",
            initiator="agent",
            session_key=None,
            note=None,
        )
        assert order["status"] == "awaiting_approval" and order["tokenId"] is None
        assert order["valueUsd"] == pytest.approx(100, rel=1e-3)
        bounds = order["plan"]["bounds"]
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "confirmed", done["reason"]
        # ERC20.approve(Permit2, max) → Permit2.approve(token, PositionManager, max, now+30 min),
        # per token, then modifyLiquidities.
        assert [tx["data"][:10] for tx in mined] == [
            SEL_ERC20_APPROVE,
            SEL_PERMIT2_APPROVE,
            SEL_ERC20_APPROVE,
            SEL_PERMIT2_APPROVE,
            SEL_MODIFY,
        ]
        codec = LIB.abi_codec
        spender, amount = codec.decode(
            [{"type": "address"}, {"type": "uint256"}], "0x" + mined[0]["data"][10:]
        )
        assert spender.lower() == PERMIT2 and amount == int(bounds["base"])
        token, pm, amount160, expiration = codec.decode(
            [{"type": "address"}, {"type": "address"}, {"type": "uint160"}, {"type": "uint48"}],
            "0x" + mined[1]["data"][10:],
        )
        assert token.lower() == WETH and pm.lower() == PM and amount160 == int(bounds["base"])
        assert expiration == world.timestamp + lp_write.PERMIT2_EXPIRY_S
        assert mined[2]["to"].lower() == USDC
        # The new NFT, from the receipt's Transfer(0x0 → wallet).
        assert done["tokenId"] == "9000" and world.positions[9000].owner == wallet
        assert done["plan"]["tokenId"] == "9000"
        assert done["spent"]["base"]["raw"] == order["plan"]["expected"]["base"]["raw"]
        assert done["approvalTxHash"] and all(a.get("txHash") for a in done["plan"]["approvals"])
        entries = _entries(service, "lp_add")
        assert {e["token_in"] for e in entries} == {WETH, USDC}
        assert service.ledger.spent_today(wallet) == pytest.approx(100, rel=1e-3)
        approvals = _entries(service, "approval")
        assert len(approvals) == 4

    async def test_native_add_sends_value_and_books_the_eth_spent(
        self, lp_service: dict[str, Any]
    ) -> None:
        service: TradingService = lp_service["service"]
        mined, wallet = lp_service["mined"], lp_service["wallet"]
        top = TICK_2000 // 60 * 60
        order = await service.lp_add(
            chain=BASE,
            target=_pool_id(eth_usdc_key()),
            usd=0.05,
            range_spec=f"ticks:{top + 120}:{top + 1200}",
            initiator="manual",
            session_key=None,
            note="dust test",
        )
        assert order["status"] == "awaiting_approval" and order["plan"]["approvals"] == []
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "confirmed", done["reason"]
        assert [tx["data"][:10] for tx in mined] == [SEL_MODIFY]
        assert int(mined[0]["value"]) == int(order["plan"]["value"])
        spent = int(done["spent"]["base"]["raw"])
        assert spent == int(order["plan"]["expected"]["base"]["raw"])  # the sweep refunded the rest
        (entry,) = _entries(service, "lp_add")
        assert entry["token_in"] == NATIVE_ADDRESS and int(entry["amount_in_raw"]) == spent
        assert entry["note"] == "dust test"
        assert service.ledger.spent_today(wallet) == 0.0  # a person's add is not capped

    async def test_collect_with_no_fees_is_refused_unless_allowed(
        self, lp_service: dict[str, Any]
    ) -> None:
        service: TradingService = lp_service["service"]
        # Both sides read zero at plan time: a collect would only pay gas.
        with pytest.raises(TradingError) as refused:
            await service.lp_collect(
                chain=BASE, token_id=POS_BARE, initiator="agent", session_key=None, note=None
            )
        assert refused.value.code == "trading.lp.nothing_to_collect"
        assert f"#{POS_BARE}" in str(refused.value) and "no uncollected fees" in str(refused.value)
        assert service.list_orders()["orders"] == []
        # allow_empty is the explicit override, and it still settles.
        order = await service.lp_collect(
            chain=BASE,
            token_id=POS_BARE,
            initiator="agent",
            session_key=None,
            note=None,
            allow_empty=True,
        )
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "confirmed"
        (entry,) = _entries(service, "lp_collect")
        assert entry["token_in"] is None and entry["token_out"] is None and entry["gas_usd"]

    async def test_increase_takes_fees_home(self, lp_service: dict[str, Any]) -> None:
        service: TradingService = lp_service["service"]
        world = lp_service["world"]
        order = await service.lp_add(
            chain=BASE,
            target=None,
            usd=20,
            to_position=POS_WETH,
            initiator="manual",
            session_key=None,
            note=None,
        )
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "confirmed", done["reason"]
        assert done["tokenId"] == str(POS_WETH) and world.positions[POS_WETH].liquidity > 10**14
        assert world.positions[POS_WETH].fees0 == 0

    async def test_the_agents_cap_refuses_an_add(self, lp_service: dict[str, Any]) -> None:
        service: TradingService = lp_service["service"]
        service.config.daily_cap_usd = 50.0
        kwargs: dict[str, Any] = {
            "chain": BASE,
            "target": _pool_id(weth_usdc_key()),
            "usd": 100,
            "range_spec": "pct:10",
            "session_key": None,
            "note": None,
        }
        refused = await service.lp_add(initiator="agent", **kwargs)
        assert refused["status"] == "rejected" and refused["reason"].startswith("daily cap")
        parked = await service.lp_add(initiator="manual", **kwargs)
        assert parked["status"] == "awaiting_approval"
        service.config.daily_cap_usd = 0.0
        off = await service.lp_add(initiator="agent", **kwargs)
        assert off["status"] == "rejected" and "switched off" in off["reason"]

    async def test_price_moved_fails_the_order_before_anything_is_signed(
        self, lp_service: dict[str, Any]
    ) -> None:
        service: TradingService = lp_service["service"]
        world, mined = lp_service["world"], lp_service["mined"]
        order = await service.lp_add(
            chain=BASE,
            target=_pool_id(weth_usdc_key()),
            usd=100,
            range_spec="pct:10",
            initiator="agent",
            session_key=None,
            note=None,
        )
        world.pools[_pool_id(weth_usdc_key())].tick = TICK_2000 + 400
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "failed" and done["reason"].startswith("trading.price_moved")
        assert mined == []

    async def test_an_expired_approval_is_quote_expired(self, lp_service: dict[str, Any]) -> None:
        service: TradingService = lp_service["service"]
        order = await service.lp_collect(
            chain=BASE, token_id=POS_WETH, initiator="agent", session_key=None, note=None
        )
        service.ledger.update_order(order["orderId"], expires_at=1.0)
        with pytest.raises(TradingError) as exc:
            await service.approve(order["orderId"])
        assert exc.value.code == "trading.quote_expired"
        assert service.get_order(order["orderId"])["status"] == "expired"

    async def test_a_position_given_away_after_parking_is_not_owner(
        self, lp_service: dict[str, Any]
    ) -> None:
        service: TradingService = lp_service["service"]
        world = lp_service["world"]
        order = await service.lp_collect(
            chain=BASE, token_id=POS_WETH, initiator="agent", session_key=None, note=None
        )
        world.positions[POS_WETH].owner = OUTSIDER
        done = await service.approve(order["orderId"], wait=True)
        assert done["status"] == "failed" and done["reason"].startswith("trading.lp.not_owner")

    async def test_refusals_create_no_order(self, lp_service: dict[str, Any]) -> None:
        service: TradingService = lp_service["service"]
        for token_id, code in (
            (POS_OUTSIDER, "trading.lp.not_owner"),
            (POS_CLOSED, "trading.lp.position_closed"),
        ):
            with pytest.raises(TradingError) as exc:
                await service.lp_collect(
                    chain=BASE, token_id=token_id, initiator="agent", session_key=None, note=None
                )
            assert exc.value.code == code
        with pytest.raises(TradingError) as exc:
            await service.lp_remove(
                chain=BASE,
                token_id=POS_WETH,
                slippage_pct=9.0,
                initiator="agent",
                session_key=None,
                note=None,
            )
        assert exc.value.code == "trading.slippage_too_high"
        assert service.list_orders()["orders"] == []

    async def test_client_order_id_returns_the_same_order(self, lp_service: dict[str, Any]) -> None:
        service: TradingService = lp_service["service"]
        first = await service.lp_collect(
            chain=BASE,
            token_id=POS_WETH,
            initiator="agent",
            session_key=None,
            note=None,
            client_order_id="collect-1",
        )
        again = await service.lp_collect(
            chain=BASE,
            token_id=POS_WETH,
            initiator="agent",
            session_key=None,
            note=None,
            client_order_id="collect-1",
        )
        assert again["orderId"] == first["orderId"]
        assert len(service.list_orders(kind="lp_collect")["orders"]) == 1


async def test_native_payout_is_measured_and_a_rebuild_replays_it(
    lp_service: dict[str, Any],
) -> None:
    """ETH leaves the pool by an internal call (no log): the balance says how much.

    A full rebuild of the wallet's history then books the confirmed LP write
    again from its recorded settlement, the same entries it had.
    """
    service: TradingService = lp_service["service"]
    wallet = lp_service["wallet"]
    order = await service.lp_remove(
        chain=BASE, token_id=POS_ETH, initiator="agent", session_key=None, note=None
    )
    assert order["tokenIn"]["native"] is True
    done = await service.approve(order["orderId"], wait=True)
    assert done["status"] == "confirmed", done["reason"]
    assert done["received"]["base"]["raw"] == order["plan"]["expected"]["base"]["raw"]

    def booked() -> list[tuple[str, str | None, str | None]]:
        return sorted(
            (e["kind"], e["token_out"], e["amount_out_raw"])
            for e in service.ledger.list_entries(wallet=wallet)
            if str(e["kind"]).startswith("lp_")
        )

    def ids() -> set[int]:
        return {int(e["id"]) for e in service.ledger.list_entries(wallet=wallet)}

    before, first_ids = booked(), ids()
    assert (
        "lp_remove",
        NATIVE_ADDRESS,
        order["plan"]["expected"]["base"]["raw"],
    ) in before
    await service.syncer.sync(service.vault.get(wallet), BASE, full=True)
    assert booked() == before
    assert not ids() & first_ids  # rebuilt, not left alone


async def test_slow_approvals_do_not_expire_the_main_calls_deadline(
    lp_service: dict[str, Any],
) -> None:
    """Four approvals, each mined a full receipt window later, outlast the deadline.

    The ``modifyLiquidities`` deadline is taken again from the chain once the
    approvals are in, so the main call still carries a future one and lands.
    """
    from agentos.trading.service import RECEIPT_TIMEOUT_S
    from tests.test_trading.fakes import decode_fake_raw

    service: TradingService = lp_service["service"]
    world, chain, mined = lp_service["world"], lp_service["chain"], lp_service["mined"]
    order = await service.lp_add(
        chain=BASE,
        target=_pool_id(weth_usdc_key()),
        usd=100,
        range_spec="pct:10",
        initiator="agent",
        session_key=None,
        note=None,
    )
    send = chain.on_send

    def slow(raw: str) -> str:
        tx_hash = send(raw)
        if str(decode_fake_raw(raw)["data"])[:10] != SEL_MODIFY:
            world.timestamp += int(RECEIPT_TIMEOUT_S)
        return tx_hash

    chain.on_send = slow
    started = world.timestamp
    done = await service.approve(order["orderId"], wait=True)
    assert done["status"] == "confirmed", done["reason"]
    assert [tx["data"][:10] for tx in mined][-1] == SEL_MODIFY and len(mined) == 5
    assert world.timestamp - started > lp_write.DEADLINE_S  # the first deadline had lapsed
    _unlock, deadline = LIB.abi_codec.decode(
        [{"type": "bytes"}, {"type": "uint256"}], "0x" + mined[-1]["data"][10:]
    )
    assert int(deadline) == world.timestamp + lp_write.DEADLINE_S


async def test_increase_paying_out_native_fees_books_the_eth_received(
    lp_service: dict[str, Any],
) -> None:
    """An increase whose uncollected ETH fees exceed its deposit nets ETH *in*.

    INCREASE → CLOSE_CURRENCY × 2 → SWEEP pays the surplus out: the wallet's
    balance rises. That is booked as ETH received -- no phantom ``out`` of the
    plan's deposit -- and the balance cache matches, so a sync books nothing.
    """
    service: TradingService = lp_service["service"]
    world, wallet, mined = lp_service["world"], lp_service["wallet"], lp_service["mined"]
    fees0 = world.positions[POS_ETH].fees0
    order = await service.lp_add(
        chain=BASE,
        target=None,
        usd=2,
        to_position=POS_ETH,
        initiator="manual",
        session_key=None,
        note=None,
    )
    plan = order["plan"]
    assert "CLOSE_CURRENCY" in plan["actions"] and plan["fees"]["base"]["raw"] == str(fees0)
    deposit = int(plan["expected"]["base"]["raw"])
    assert 0 < deposit < fees0
    before = world.native[wallet]
    done = await service.approve(order["orderId"], wait=True)
    assert done["status"] == "confirmed", done["reason"]
    assert mined[-1]["data"][:10] == SEL_MODIFY and int(mined[-1]["value"]) > 0
    received = world.native[wallet] - before + len(mined) * GAS_USED * GAS_PRICE
    assert received == fees0 - deposit
    assert done["received"]["base"]["raw"] == str(received)
    assert done["spent"]["base"]["raw"] == "0"
    entries = _entries(service, "lp_add")
    assert [e["amount_out_raw"] for e in entries if e["token_out"] == NATIVE_ADDRESS] == [
        str(received)
    ]
    assert not [e for e in entries if e["token_in"] == NATIVE_ADDRESS]
    assert service.ledger.get_balance(8453, wallet, NATIVE_ADDRESS) == world.native[wallet]
    await service.syncer.sync(service.vault.get(wallet), BASE)
    moves = [
        e
        for e in service.ledger.list_entries(wallet=wallet)
        if e["kind"] in ("deposit", "withdraw")
        and NATIVE_ADDRESS in (e["token_in"], e["token_out"])
    ]
    assert moves == []


# ── choosing the pool: quote asset and fee tier ─────────────────────────────


class TestPoolChoice:
    @pytest.fixture
    def deep_03(self, world: World) -> str:
        """A second WETH/USDC pool, 0.3 %, far deeper than the 0.05 % one."""
        key = dict(
            LIB.v4_pool.normalize_pool_key(
                {
                    "currency0": WETH,
                    "currency1": USDC,
                    "fee": 3000,
                    "tickSpacing": 60,
                    "hooks": NATIVE_ADDRESS,
                }
            )
        )
        pool = world.add_pool(key, tick=TICK_2000)
        lo, hi = (TICK_2000 - 6_000) // 60 * 60, (TICK_2000 + 6_000) // 60 * 60
        world.add_position(pool, 7100, lo, hi, 10**17, OUTSIDER)
        return _pool_id(key)

    def test_deepest_by_default_and_the_named_tier_on_request(
        self, env: lp.ChainEnv, deep_03: str
    ) -> None:
        state, _ = lp_write._pick_pool(env, WETH)
        assert state["poolId"] == deep_03
        assert [w for w in env.warnings if "V4 pools hold WETH" in w] == [
            "2 V4 pools hold WETH (2 with active liquidity); using the deepest "
            f"(0.3%, {deep_03}) -- pass --quote/--fee or a poolId to choose"
        ]
        # The losing candidate leaves nothing else behind.
        assert env.partial is False
        assert not any("partial scan" in w or "self-check" in w for w in env.warnings)
        state, _ = lp_write._pick_pool(env, WETH, USDC, 500)
        assert state["poolId"] == _pool_id(weth_usdc_key())
        state, _ = lp_write._pick_pool(env, WETH, USDC, lp.parse_fee("0.3%"))
        assert state["poolId"] == deep_03

    def test_a_tier_that_does_not_exist_names_the_ones_that_do(
        self, env: lp.ChainEnv, deep_03: str
    ) -> None:
        with pytest.raises(TradingError) as exc:
            lp_write._pick_pool(env, WETH, USDC, 100)
        assert exc.value.code == "trading.lp.not_found"
        assert "no 0.01% Uniswap V4 pool for WETH/USDC on Base" in str(exc.value)
        assert exc.value.details["tiers"] == ["0.05%", "0.3%"]

    def test_plan_add_goes_into_the_named_tier(self, env: lp.ChainEnv, deep_03: str) -> None:
        plan = lp_write.plan_add(
            env, WETH, quote=USDC, fee=500, usd=50, range_spec="pct:10", wallet=WALLET
        )
        assert plan["pool"]["poolId"] == _pool_id(weth_usdc_key())
        assert plan["pool"]["feePct"] == "0.05%"
        # A poolId on another tier than the one named is refused, not silently used.
        with pytest.raises(TradingError) as exc:
            lp_write.plan_add(env, deep_03, fee=500, usd=50, wallet=WALLET)
        assert exc.value.code == "trading.invalid"

    def test_adding_to_a_position_checks_its_pool(self, env: lp.ChainEnv) -> None:
        # POS_WETH sits in the 0.05 % WETH/USDC pool.
        plan = lp_write.plan_add(env, None, quote=USDC, fee=500, usd=10, to_position=POS_WETH)
        assert plan["tokenId"] == str(POS_WETH)
        with pytest.raises(TradingError) as fee:
            lp_write.plan_add(env, None, fee=3000, usd=10, to_position=POS_WETH)
        assert fee.value.code == "trading.invalid" and "0.05% pool, not 0.3%" in str(fee.value)
        with pytest.raises(TradingError) as quote:
            lp_write.plan_add(env, None, quote=NATIVE_ADDRESS, usd=10, to_position=POS_WETH)
        assert quote.value.code == "trading.invalid"
