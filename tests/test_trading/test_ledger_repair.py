"""Schema version 6: the native-receipt repair and the full-sync nag it leaves behind.

The fixture rebuilds, from the schema helpers, the shape of the rows the
live ledger held on 2026-09-19: two USDC→ETH swaps whose settlement wrote a
stray number as the receipt while the real ETH was booked seconds later as
an external deposit, and an ETH→USDC swap followed by a phantom withdrawal
equal to what it spent plus gas.
"""

# ruff: noqa: F811  (fixtures imported from test_ledger_sync are shadowed by their parameters)
from __future__ import annotations

from pathlib import Path

import pytest

from agentos.trading.chains import BASE, NATIVE_ADDRESS
from agentos.trading.ledger import (
    REPAIR_PENDING_MESSAGE,
    SCHEMA_VERSION,
    Ledger,
)
from agentos.trading.sync import WalletSyncer
from tests.test_trading.fakes import USDC, WALLET, FakeChain
from tests.test_trading.test_ledger_sync import (  # noqa: F401  (fixtures)
    _wallet,
    chain,
    prices_fake,
    syncer,
)

# ord_d2cf1a1ca0c8 / entries 164+165 on the live ledger.
SWAP_A_TS = 1_789_825_657.0883
SWAP_A = {
    "order_id": "ord_a",
    "tx_hash": "0x" + "a" * 64,
    "expected_out_raw": "18941114315080",
    "min_out_raw": "18846408743505",
    "received_out_raw": "920556473576",
    "gas_wei": "1263084300000",
}
DEPOSIT_A = 17_667_859_434_242
# ord_55686eedd72b / entries 167+168.
SWAP_B_TS = 1_789_825_986.8333
SWAP_B = {
    "order_id": "ord_b",
    "tx_hash": "0x" + "b" * 64,
    "expected_out_raw": "18939803222520",
    "min_out_raw": "18845104206407",
    "received_out_raw": "1263084300000",
    "gas_wei": "1263084300000",
}
DEPOSIT_B = 17_672_305_326_941
# ord_63b64f603ce9 / entries 146+147: ETH→USDC, then a phantom withdrawal.
SELL_TS = 1_789_548_832.8936
SELL_SPENT = 41_773_537_299_591
SELL_GAS = 836_041_600_000
PHANTOM_WITHDRAW = 42_612_513_230_860


def _order(order_id: str, ts: float, **fields: object) -> dict[str, object]:
    return {
        "order_id": order_id,
        "created_at": ts - 5,
        "updated_at": ts,
        "chain_id": 8453,
        "wallet": WALLET,
        "amount_human": "x",
        "status": "confirmed",
        "initiator": "manual",
        "kind": "swap",
        **fields,
    }


def _book_buy(ledger: Ledger, spec: dict[str, str], ts: float, deposit: int) -> tuple[int, int]:
    """One USDC→ETH swap as the buggy settlement left it; returns (swap id, deposit id)."""
    ledger.insert_order(
        _order(
            spec["order_id"],
            ts,
            token_in=USDC,
            token_out=NATIVE_ADDRESS,
            amount_raw="50000",
            expected_out_raw=spec["expected_out_raw"],
            min_out_raw=spec["min_out_raw"],
            tx_hash=spec["tx_hash"],
            received_out_raw=spec["received_out_raw"],
            spent_in_raw="50000",
            gas_wei=spec["gas_wei"],
            delivered_token=NATIVE_ADDRESS,
        )
    )
    swap_id = ledger.insert_entry(
        ts=ts,
        chain_id=8453,
        wallet=WALLET,
        kind="swap",
        tx_hash=spec["tx_hash"],
        log_index=0,
        token_in=USDC,
        amount_in_raw=50000,
        token_out=NATIVE_ADDRESS,
        amount_out_raw=int(spec["received_out_raw"]),
        value_usd=0.05,
        cost_basis_source="spot",
        initiator="manual",
        order_id=spec["order_id"],
    )
    assert swap_id is not None
    ledger.add_lot(
        8453,
        WALLET,
        NATIVE_ADDRESS,
        amount_raw=int(spec["received_out_raw"]),
        cost_usd_per_raw=0.05 / int(spec["received_out_raw"]),
        acquired_at=ts,
        entry_id=swap_id,
    )
    deposit_ts = ts + 1.65
    deposit_id = ledger.insert_entry(
        ts=deposit_ts,
        chain_id=8453,
        wallet=WALLET,
        kind="deposit",
        tx_hash=None,
        log_index=int(deposit_ts),
        token_out=NATIVE_ADDRESS,
        amount_out_raw=deposit,
        value_usd=0.0465,
        price_out_usd=2634.0,
        cost_basis_source="spot",
        initiator="external",
    )
    assert deposit_id is not None
    ledger.add_lot(
        8453,
        WALLET,
        NATIVE_ADDRESS,
        amount_raw=deposit,
        cost_usd_per_raw=0.0465 / deposit,
        acquired_at=deposit_ts,
        entry_id=deposit_id,
    )
    return swap_id, deposit_id


def _book_sell(ledger: Ledger, order_id: str, ts: float, *, withdraw: int) -> tuple[int, int]:
    """One ETH→USDC swap and the phantom native withdrawal that followed it."""
    tx_hash = "0x" + order_id[-1] * 64
    ledger.insert_order(
        _order(
            order_id,
            ts,
            token_in=NATIVE_ADDRESS,
            token_out=USDC,
            amount_raw=str(SELL_SPENT),
            expected_out_raw="99990",
            min_out_raw="97490",
            tx_hash=tx_hash,
            received_out_raw="99987",
            spent_in_raw=str(SELL_SPENT),
            gas_wei=str(SELL_GAS),
            delivered_token=USDC,
            initiator="agent",
        )
    )
    swap_id = ledger.insert_entry(
        ts=ts,
        chain_id=8453,
        wallet=WALLET,
        kind="swap",
        tx_hash=tx_hash,
        log_index=0,
        token_in=NATIVE_ADDRESS,
        amount_in_raw=SELL_SPENT,
        token_out=USDC,
        amount_out_raw=99987,
        value_usd=0.1,
        cost_basis_source="spot",
        initiator="agent",
        order_id=order_id,
    )
    assert swap_id is not None
    withdraw_ts = ts + 32.7
    withdraw_id = ledger.insert_entry(
        ts=withdraw_ts,
        chain_id=8453,
        wallet=WALLET,
        kind="withdraw",
        tx_hash=None,
        log_index=int(withdraw_ts),
        token_in=NATIVE_ADDRESS,
        amount_in_raw=withdraw,
        value_usd=0.102,
        cost_basis_source="spot",
        initiator="external",
    )
    assert withdraw_id is not None
    ledger.add_realized(
        entry_id=withdraw_id,
        chain_id=8453,
        wallet=WALLET,
        token=NATIVE_ADDRESS,
        amount_raw=withdraw,
        proceeds_usd=0.102,
        cost_usd=0.1,
        ts=withdraw_ts,
    )
    return swap_id, withdraw_id


def _downgrade_to_v5(ledger: Ledger) -> None:
    ledger._conn.execute("ALTER TABLE sync_state DROP COLUMN full_synced_at")
    ledger._conn.execute("DROP TABLE ledger_repairs")
    ledger._conn.execute("UPDATE schema_version SET version = 5")


def _count(ledger: Ledger, sql: str) -> int:
    return int(ledger._conn.execute(sql).fetchone()[0])


@pytest.fixture
def v5_path(tmp_path: Path) -> Path:
    """A version-5 ledger holding the broken rows plus look-alikes that must survive."""
    path = tmp_path / "trading.sqlite"
    ledger = Ledger(path)
    ledger.set_sync_state(8453, WALLET, last_block=100, oldest_block=1)
    _book_buy(ledger, SWAP_A, SWAP_A_TS, DEPOSIT_A)
    _book_buy(ledger, SWAP_B, SWAP_B_TS, DEPOSIT_B)
    _book_sell(ledger, "ord_c", SELL_TS, withdraw=PHANTOM_WITHDRAW)

    # Look-alikes. A sane native-out order (received ≥ min) with an
    # unrelated native deposit right after it.
    _book_buy(
        ledger,
        {
            **SWAP_A,
            "order_id": "ord_sane",
            "tx_hash": "0x" + "d" * 64,
            "received_out_raw": "18900000000000",
        },
        SWAP_B_TS + 1_000,
        5 * 10**15,
    )
    # A broken-looking order whose neighbouring deposit is far too big to be
    # its receipt: left alone, and logged.
    _book_buy(
        ledger,
        {**SWAP_A, "order_id": "ord_toobig", "tx_hash": "0x" + "e" * 64},
        SWAP_B_TS + 2_000,
        10**17,
    )
    # A real native withdrawal near a sell, but not what the sell spent.
    _book_sell(ledger, "ord_f", SELL_TS + 1_000, withdraw=SELL_SPENT * 2)
    # A native withdrawal with a note (booked by hand) that happens to match.
    ledger.insert_entry(
        ts=SELL_TS + 10,
        chain_id=8453,
        wallet=WALLET,
        kind="withdraw",
        tx_hash=None,
        log_index=int(SELL_TS + 10),
        token_in=NATIVE_ADDRESS,
        amount_in_raw=PHANTOM_WITHDRAW,
        note="sent to a friend",
        initiator="external",
    )
    _downgrade_to_v5(ledger)
    ledger.close()
    return path


class TestNativeReceiptRepair:
    def test_receipts_fold_back_into_their_swaps(self, v5_path: Path) -> None:
        ledger = Ledger(v5_path)
        try:
            version = ledger._conn.execute("SELECT version FROM schema_version").fetchone()[0]
            assert version == SCHEMA_VERSION
            for spec, deposit in ((SWAP_A, DEPOSIT_A), (SWAP_B, DEPOSIT_B)):
                fixed = deposit + int(spec["gas_wei"])
                assert int(spec["min_out_raw"]) <= fixed <= int(spec["expected_out_raw"])
                order = ledger.get_order(spec["order_id"])
                assert order and order["received_out_raw"] == str(fixed)
                swap = ledger.entries_for_tx(WALLET, spec["tx_hash"])[0]
                assert swap["amount_out_raw"] == str(fixed)
            # The phantom deposits and their lots are gone.
            deposits = ledger.list_entries(wallet=WALLET, kind="deposit")
            assert {int(d["amount_out_raw"]) for d in deposits} == {5 * 10**15, 10**17}
            lots = ledger._conn.execute(
                "SELECT amount_raw_remaining FROM lots WHERE token = ?", (NATIVE_ADDRESS,)
            ).fetchall()
            assert DEPOSIT_A not in {int(r[0]) for r in lots}
            assert DEPOSIT_B not in {int(r[0]) for r in lots}
            # The swaps' own lots are not patched: the rebuild re-derives them.
            assert int(SWAP_A["received_out_raw"]) in {int(r[0]) for r in lots}
            assert ledger.repair_pending() == REPAIR_PENDING_MESSAGE
        finally:
            ledger.close()

    def test_phantom_withdrawals_go(self, v5_path: Path) -> None:
        ledger = Ledger(v5_path)
        try:
            withdraws = ledger.list_entries(wallet=WALLET, kind="withdraw")
            amounts = sorted((int(w["amount_in_raw"]), w["note"]) for w in withdraws)
            assert amounts == [
                (PHANTOM_WITHDRAW, "sent to a friend"),  # noted rows are the user's
                (SELL_SPENT * 2, None),  # not what the sell spent
            ]
            # The realized row the phantom produced went with it; the one
            # the real withdrawal produced is still attached to its entry.
            realized = ledger._conn.execute("SELECT entry_id FROM realized").fetchall()
            survivor = next(w for w in withdraws if int(w["amount_in_raw"]) == SELL_SPENT * 2)
            assert [int(r["entry_id"]) for r in realized] == [int(survivor["id"])]
        finally:
            ledger.close()

    def test_look_alikes_are_untouched(self, v5_path: Path) -> None:
        ledger = Ledger(v5_path)
        try:
            sane = ledger.get_order("ord_sane")
            assert sane and sane["received_out_raw"] == "18900000000000"
            toobig = ledger.get_order("ord_toobig")
            assert toobig and toobig["received_out_raw"] == SWAP_A["received_out_raw"]
            assert (
                ledger.entries_for_tx(WALLET, "0x" + "e" * 64)[0]["amount_out_raw"]
                == (SWAP_A["received_out_raw"])
            )
            repairs = ledger._conn.execute(
                "SELECT version, wallet, chain_id FROM ledger_repairs"
            ).fetchall()
            assert [tuple(r) for r in repairs] == [(6, WALLET, 8453)]
        finally:
            ledger.close()

    def test_repair_is_idempotent(self, v5_path: Path) -> None:
        first = Ledger(v5_path)
        snapshot = (
            [dict(r) for r in first._conn.execute("SELECT * FROM entries ORDER BY id")],
            [dict(r) for r in first._conn.execute("SELECT * FROM orders ORDER BY order_id")],
            [dict(r) for r in first._conn.execute("SELECT * FROM lots ORDER BY id")],
            _count(first, "SELECT COUNT(*) FROM ledger_repairs"),
        )
        first.close()
        second = Ledger(v5_path)
        try:
            # Reopening is a no-op (version 6 now), and so is running the
            # repair itself again: nothing left matches.
            second._repair_native_receipts()
            again = (
                [dict(r) for r in second._conn.execute("SELECT * FROM entries ORDER BY id")],
                [dict(r) for r in second._conn.execute("SELECT * FROM orders ORDER BY order_id")],
                [dict(r) for r in second._conn.execute("SELECT * FROM lots ORDER BY id")],
                _count(second, "SELECT COUNT(*) FROM ledger_repairs"),
            )
            assert again == snapshot
            assert again[3] == 1
        finally:
            second.close()

    def test_repair_pending_clears_after_a_full_sync(self, v5_path: Path) -> None:
        ledger = Ledger(v5_path)
        try:
            assert ledger.repair_pending() == REPAIR_PENDING_MESSAGE
            # An incremental pass does not count.
            ledger.set_sync_state(8453, WALLET, last_block=200, oldest_block=1)
            assert ledger.repair_pending() == REPAIR_PENDING_MESSAGE
            assert ledger.sync_state(8453, WALLET)["full_synced_at"] is None
            # A full rebuild's swap-in stamps the row; the nag goes.
            ledger.set_sync_state(8453, WALLET, last_block=300, oldest_block=1, full=True)
            stamped = ledger.sync_state(8453, WALLET)["full_synced_at"]
            assert stamped is not None
            assert ledger.repair_pending() is None
            # And the stamp survives the next incremental pass.
            ledger.set_sync_state(8453, WALLET, last_block=400, oldest_block=1)
            assert ledger.sync_state(8453, WALLET)["full_synced_at"] == stamped
            assert ledger.repair_pending() is None
        finally:
            ledger.close()

    def test_fresh_ledger_has_nothing_pending(self, ledger: Ledger) -> None:
        assert ledger.repair_pending() is None
        ledger._repair_native_receipts()
        assert ledger.repair_pending() is None
        assert _count(ledger, "SELECT COUNT(*) FROM ledger_repairs") == 0

    def test_removing_the_wallet_forgets_its_repair(self, v5_path: Path) -> None:
        ledger = Ledger(v5_path)
        try:
            ledger.remove_wallet(WALLET)
            assert ledger.repair_pending() is None
        finally:
            ledger.close()


class TestRebuildStampsFullSync:
    async def test_rebuild_stamps_and_incremental_keeps(
        self, syncer: WalletSyncer, chain: FakeChain, ledger: Ledger
    ) -> None:
        wallet = _wallet(created_block=9_000)
        chain.set_native(WALLET, 10**16)
        await syncer.sync(wallet, BASE)
        assert ledger.sync_state(8453, WALLET)["full_synced_at"] is None
        await syncer.sync(wallet, BASE, full=True)
        stamped = ledger.sync_state(8453, WALLET)["full_synced_at"]
        assert stamped is not None
        await syncer.sync(wallet, BASE)
        assert ledger.sync_state(8453, WALLET)["full_synced_at"] == stamped
