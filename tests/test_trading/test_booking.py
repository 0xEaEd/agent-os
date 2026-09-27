"""How a swap is valued, how an airdrop is booked, and what a rebuild trusts.

* A swap is worth its most reliable priced leg: a stablecoin to the cent,
  then the native coin, then a listed token, then whatever a thin pool says.
* An unlisted token that arrives from a stranger cost nothing.
* A native receipt below the quote's minimum that was not a short fill is a
  mis-measurement; the rebuild uses the quote's expected amount instead.
"""

# ruff: noqa: F811  (fixtures imported from test_ledger_sync are shadowed by their parameters)
from __future__ import annotations

import pytest

from agentos.trading.chains import BASE, NATIVE_ADDRESS
from agentos.trading.ledger import Ledger
from agentos.trading.pnl import holding_from_lots
from agentos.trading.prices import TokenMeta, native_token
from agentos.trading.sync import TxGroup, WalletSyncer, _order_legs, swap_value
from tests.test_trading.fakes import AAPL, OTHER, USDC, WALLET, WETH, FakeChain, FakePrices
from tests.test_trading.test_ledger_sync import (  # noqa: F401  (fixtures)
    _wallet,
    chain,
    prices_fake,
    syncer,
)

ETH = native_token(BASE)
USDC_META = TokenMeta(8453, USDC, "USDC", "USD Coin", 6, verified=True)
USDT_META = TokenMeta(8453, OTHER, "USDT", "Tether", 6, verified=True)
WETH_META = TokenMeta(8453, WETH, "WETH", "Wrapped Ether", 18, verified=True)
JUNK_A = TokenMeta(8453, AAPL, "AAPL", "Apple Fan Coin", 18)
JUNK_B = TokenMeta(8453, "0x" + "b" * 40, "BBB", "Bee", 18)


def _value(**kw: object) -> tuple[float | None, str]:
    base: dict[str, object] = {
        "meta_in": ETH,
        "amount_in": 10**16,
        "price_in": 2000.0,
        "meta_out": USDC_META,
        "amount_out": 19_500_000,
        "price_out": 1.0,
    }
    base.update(kw)
    return swap_value(BASE, **base)  # type: ignore[arg-type]


class TestSwapValue:
    def test_stable_out_leg_beats_native_in(self) -> None:
        # 0.01 ETH at $2000 is $20 by the feed; the 19.5 USDC that arrived is the trade.
        assert _value() == (pytest.approx(19.5), "out")

    def test_stable_in_leg_beats_native_out(self) -> None:
        value, leg = _value(
            meta_in=USDC_META,
            amount_in=50_000_000,
            price_in=1.0,
            meta_out=ETH,
            amount_out=24 * 10**15,
            price_out=2000.0,
        )
        assert (value, leg) == (pytest.approx(50.0), "in")

    def test_native_beats_a_listed_token(self) -> None:
        value, leg = _value(meta_out=WETH_META, amount_out=10**16, price_out=1990.0)
        assert (value, leg) == (pytest.approx(20.0), "in")

    def test_listed_beats_unlisted_and_unlisted_ties_go_to_the_in_leg(self) -> None:
        value, leg = _value(
            meta_in=JUNK_A,
            amount_in=10**18,
            price_in=3.0,
            meta_out=WETH_META,
            amount_out=10**15,
            price_out=2000.0,
        )
        assert (value, leg) == (pytest.approx(2.0), "out")
        value, leg = _value(
            meta_in=JUNK_A,
            amount_in=10**18,
            price_in=3.0,
            meta_out=JUNK_B,
            amount_out=10**18,
            price_out=7.0,
        )
        assert (value, leg) == (pytest.approx(3.0), "in")

    def test_stable_by_symbol_or_by_chain_address(self) -> None:
        value, leg = _value(meta_out=USDT_META, amount_out=19_000_000)
        assert (value, leg) == (pytest.approx(19.0), "out")
        # The chain's own USDC counts even when the list did not flag it.
        unlisted_usdc = TokenMeta(8453, USDC, "", "", 6)
        value, leg = _value(meta_out=unlisted_usdc, amount_out=19_250_000)
        assert (value, leg) == (pytest.approx(19.25), "out")
        # An unlisted token calling itself USDC is not a stablecoin.
        fake_usdc = TokenMeta(8453, AAPL, "USDC", "", 6)
        value, leg = _value(meta_out=fake_usdc, amount_out=999_000_000)
        assert (value, leg) == (pytest.approx(20.0), "in")

    def test_only_a_priced_leg_counts(self) -> None:
        assert _value(price_out=None) == (pytest.approx(20.0), "in")
        assert _value(price_in=None) == (pytest.approx(19.5), "out")
        assert _value(price_in=None, price_out=None) == (None, "in")


class TestSwapBooking:
    async def test_eth_to_usdc_proceeds_are_the_usdc_received(
        self, syncer: WalletSyncer, chain: FakeChain, ledger: Ledger
    ) -> None:
        wallet = _wallet(created_block=8_000)
        tx = chain.add_transfer(
            token=USDC, sender=OTHER, recipient=WALLET, amount=248_936, block=9_990
        )
        ledger.insert_order(
            {
                "order_id": "ord_sell",
                "created_at": 1_700_099_900.0,
                "updated_at": 1_700_099_990.0,
                "chain_id": 8453,
                "wallet": WALLET,
                "token_in": NATIVE_ADDRESS,
                "token_out": USDC,
                "amount_raw": str(10**14),
                "amount_human": "0.0001",
                "status": "confirmed",
                "initiator": "agent",
                "tx_hash": tx,
                "spent_in_raw": str(10**14),
                "received_out_raw": "248936",
                "gas_wei": str(10**12),
                "delivered_token": USDC,
            }
        )
        chain.set_erc20(USDC, WALLET, 248_936)
        chain.set_native(WALLET, 10**14)
        await syncer.sync(wallet, BASE, full=True)
        swap = ledger.list_entries(wallet=WALLET, kind="swap")[0]
        # 0.0001 ETH at $2000 is $0.20 by the feed; the trade was $0.248936.
        assert swap["value_usd"] == pytest.approx(0.248936)
        assert swap["cost_basis_source"] == "spot:out"
        assert swap["price_in_usd"] == 2000.0 and swap["price_out_usd"] == 1.0
        realized = ledger._conn.execute(
            "SELECT proceeds_usd, cost_usd FROM realized WHERE token = ?", (NATIVE_ADDRESS,)
        ).fetchone()
        assert realized["proceeds_usd"] == pytest.approx(0.248936)
        assert realized["cost_usd"] == pytest.approx(0.2)  # the opening lot at spot
        positions = {p.token: p for p in ledger.positions(WALLET)}
        assert positions[USDC].cost_usd == pytest.approx(0.248936)

    async def test_usdc_to_eth_lot_costs_the_usdc_spent(
        self, syncer: WalletSyncer, chain: FakeChain, ledger: Ledger
    ) -> None:
        wallet = _wallet(created_block=8_000)
        tx = chain.add_transfer(
            token=USDC, sender=WALLET, recipient=OTHER, amount=50_000_000, block=9_990
        )
        ledger.insert_order(
            {
                "order_id": "ord_buy",
                "created_at": 1_700_099_900.0,
                "updated_at": 1_700_099_990.0,
                "chain_id": 8453,
                "wallet": WALLET,
                "token_in": USDC,
                "token_out": NATIVE_ADDRESS,
                "amount_raw": "50000000",
                "amount_human": "50",
                "status": "confirmed",
                "initiator": "manual",
                "tx_hash": tx,
                "spent_in_raw": "50000000",
                "received_out_raw": str(24 * 10**15),
                "gas_wei": str(10**12),
                "delivered_token": NATIVE_ADDRESS,
            }
        )
        chain.set_erc20(USDC, WALLET, 0)
        chain.set_native(WALLET, 24 * 10**15)
        await syncer.sync(wallet, BASE, full=True)
        swap = ledger.list_entries(wallet=WALLET, kind="swap")[0]
        assert swap["value_usd"] == pytest.approx(50.0)  # not 0.024 ETH × $2000 = $48
        assert swap["cost_basis_source"] == "spot:in"
        positions = {p.token: p for p in ledger.positions(WALLET)}
        assert positions[NATIVE_ADDRESS].amount_raw == 24 * 10**15
        assert positions[NATIVE_ADDRESS].cost_usd == pytest.approx(50.0)
        assert USDC not in positions


class TestOrderLegs:
    def _order(self, **fields: object) -> dict[str, object]:
        return {
            "order_id": "ord_x",
            "status": "confirmed",
            "token_in": USDC,
            "token_out": NATIVE_ADDRESS,
            "delivered_token": NATIVE_ADDRESS,
            "amount_raw": "50000",
            "spent_in_raw": "50000",
            "expected_out_raw": "18941114315080",
            "min_out_raw": "18846408743505",
            "received_out_raw": "920556473576",
            "gas_wei": "1263084300000",
            "reason": None,
            **fields,
        }

    def test_implausible_native_receipt_falls_back_to_expected(self) -> None:
        spent, received, token_in, token_out, gas = _order_legs(self._order())
        assert (spent, token_in, token_out, gas) == (50000, USDC, NATIVE_ADDRESS, 1263084300000)
        assert received == 18941114315080

    def test_a_declared_short_fill_is_believed(self) -> None:
        order = self._order(reason="short fill: received 920556473576 below min 18846408743505")
        assert _order_legs(order)[1] == 920556473576

    def test_erc20_receipts_and_sane_native_receipts_stand(self) -> None:
        order = self._order(token_out=USDC, delivered_token=USDC, received_out_raw="90000")
        assert _order_legs(order)[1] == 90000
        assert _order_legs(self._order(received_out_raw="18900000000000"))[1] == 18900000000000
        # No quote to fall back on: the number stays, wrong or not.
        assert _order_legs(self._order(expected_out_raw=None))[1] == 920556473576


class TestAirdrops:
    async def test_unlisted_token_from_a_stranger_costs_nothing(
        self, syncer: WalletSyncer, chain: FakeChain, ledger: Ledger, prices_fake: FakePrices
    ) -> None:
        prices_fake.spot[("base", AAPL)] = 3.0
        chain.add_transfer(token=AAPL, sender=OTHER, recipient=WALLET, amount=10**18, block=9_999)
        await syncer.sync(_wallet(), BASE)
        entry = ledger.list_entries(wallet=WALLET, kind="deposit")[0]
        assert entry["token_out"] == AAPL
        assert entry["value_usd"] is None
        assert entry["price_out_usd"] == 3.0  # informational
        assert entry["cost_basis_source"] == "airdrop"
        lots = ledger.open_lots(8453, WALLET, AAPL)
        assert [lot.cost_usd for lot in lots] == [0.0]
        holding = holding_from_lots(lots, decimals=18, price_usd=3.0, realized_usd=0.0)
        assert holding.value_usd == pytest.approx(3.0)
        assert holding.unrealized_usd == pytest.approx(3.0)
        assert holding.unrealized_pct is None

    async def test_listed_token_from_a_stranger_is_bought_at_spot(
        self, syncer: WalletSyncer, chain: FakeChain, ledger: Ledger
    ) -> None:
        chain.add_transfer(
            token=USDC, sender=OTHER, recipient=WALLET, amount=100 * 10**6, block=9_999
        )
        await syncer.sync(_wallet(), BASE)
        entry = ledger.list_entries(wallet=WALLET, kind="deposit")[0]
        assert entry["value_usd"] == pytest.approx(100.0)
        assert entry["cost_basis_source"] != "airdrop"
        assert ledger.positions(WALLET)[0].cost_usd == pytest.approx(100.0)

    async def test_a_self_sent_unlisted_token_is_bought_at_spot(
        self, syncer: WalletSyncer, ledger: Ledger, prices_fake: FakePrices
    ) -> None:
        prices_fake.spot[("base", AAPL)] = 3.0
        now = syncer.clock["now"]  # type: ignore[attr-defined]
        group = TxGroup(
            tx_hash="0x" + "5" * 64,
            block=9_999,
            log_index=0,
            ts=now,
            ins={AAPL: 10**18},
            senders={WALLET, OTHER},
        )
        assert await syncer._book_group(_wallet(), BASE, group) is True
        entry = ledger.list_entries(wallet=WALLET, kind="deposit")[0]
        assert entry["value_usd"] == pytest.approx(3.0)
        assert entry["cost_basis_source"] == "spot"
        assert ledger.positions(WALLET)[0].cost_usd == pytest.approx(3.0)

    async def test_native_diff_deposits_are_not_airdrops(
        self, syncer: WalletSyncer, chain: FakeChain, ledger: Ledger
    ) -> None:
        wallet = _wallet()
        chain.set_native(WALLET, 10**16)
        await syncer.sync(wallet, BASE)
        chain.set_native(WALLET, 2 * 10**16)
        await syncer.sync(wallet, BASE)
        deposits = ledger.list_entries(wallet=WALLET, kind="deposit")
        assert all(d["cost_basis_source"] != "airdrop" for d in deposits)
        assert all(d["value_usd"] == pytest.approx(20.0) for d in deposits)
