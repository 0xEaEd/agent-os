from __future__ import annotations

import httpx
import pytest

from agentos.trading.chains import BASE, NATIVE_ADDRESS
from agentos.trading.ledger import Ledger
from agentos.trading.prices import PriceService
from agentos.trading.spam import TokenCurator
from tests.test_trading.fakes import USDC, WALLET, FakePrices

JUNK = "0x9999000000000000000000000000000000000009"
NEW = "0x9999000000000000000000000000000000000010"


@pytest.fixture
def prices_fake() -> FakePrices:
    prices = FakePrices()
    prices.spot[("base", USDC)] = 1.0  # a real pool ($1M liquidity in the fake)
    return prices


@pytest.fixture
async def stack(ledger: Ledger, prices_fake: FakePrices):
    state = {"now": 1_000_000.0, "outage": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["outage"] and request.url.host == "api.dexscreener.com":
            return httpx.Response(503, text="down")
        return prices_fake.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        prices = PriceService(http=http, ttl_s=0, now=lambda: state["now"])
        curator = TokenCurator(ledger, prices, now=lambda: state["now"])
        state["prices"] = prices_fake
        yield curator, state


def _add(ledger: Ledger, address: str, **kw) -> None:
    ledger.upsert_token(8453, address, symbol="X", name="X", decimals=18, **kw)


class TestTokenCurator:
    async def test_unlisted_unpriced_untouched_is_hidden(self, ledger: Ledger, stack) -> None:
        curator, _ = stack
        _add(ledger, JUNK)
        assert await curator.review(BASE, JUNK) is True
        row = ledger.get_token(8453, JUNK)
        assert row and row["hidden"] == 1 and row["hidden_by"] == "auto"
        assert ledger.hidden_tokens(8453) == {JUNK}

    async def test_a_pool_or_a_listing_keeps_it_shown(self, ledger: Ledger, stack) -> None:
        curator, _ = stack
        _add(ledger, USDC)  # unverified in the ledger, but DexScreener has a deep pool
        assert await curator.review(BASE, USDC) is False
        _add(ledger, NEW, verified=True)  # listed: never even priced
        assert await curator.review(BASE, NEW) is False
        assert ledger.hidden_tokens(8453) == set()

    async def test_native_and_unknown_are_never_hidden(self, ledger: Ledger, stack) -> None:
        curator, _ = stack
        assert await curator.review(BASE, NATIVE_ADDRESS) is False
        assert await curator.review(BASE, NEW) is False  # not in the ledger at all

    async def test_price_outage_is_not_a_verdict(self, ledger: Ledger, stack) -> None:
        curator, state = stack
        _add(ledger, JUNK)
        state["outage"] = True
        assert await curator.review(BASE, JUNK) is False
        row = ledger.get_token(8453, JUNK)
        assert row and row["hidden"] == 0 and row["classified_at"] is None
        state["outage"] = False
        assert await curator.review(BASE, JUNK) is True

    async def test_spent_or_touched_is_shown(self, ledger: Ledger, stack) -> None:
        curator, _ = stack
        _add(ledger, JUNK)
        ledger.insert_entry(
            ts=1.0,
            chain_id=8453,
            wallet=WALLET,
            kind="withdraw",
            tx_hash="0x" + "1" * 64,
            log_index=0,
            token_in=JUNK,
            amount_in_raw=1,
        )
        assert await curator.review(BASE, JUNK) is False
        _add(ledger, NEW)
        assert await curator.review(BASE, NEW) is True
        ledger.touch_token(8453, NEW)  # a quote or swap named it on purpose
        assert await curator.review(BASE, NEW) is False
        assert ledger.hidden_tokens(8453) == set()

    async def test_daily_recheck_resurfaces_a_launch(self, ledger: Ledger, stack) -> None:
        curator, state = stack
        _add(ledger, JUNK)
        assert await curator.review(BASE, JUNK) is True
        # It gained a pool an hour later: not looked at yet.
        state["now"] += 3600
        state["prices"].spot[("base", JUNK)] = 0.5
        assert await curator.review(BASE, JUNK) is True
        # A day later it is.
        state["now"] += 24 * 3600
        assert await curator.review(BASE, JUNK) is False

    async def test_daily_recheck_hides_a_shown_token_whose_pool_drained(
        self, ledger: Ledger, stack
    ) -> None:
        """Seen live on Base: a SEED lookalike airdrop kept a pool for its first
        look, then drained it to $0.05 — and stayed on the screen at a made-up
        price, because only hidden tokens were ever looked at again."""
        curator, state = stack
        _add(ledger, JUNK)
        state["prices"].spot[("base", JUNK)] = 0.678  # a real pool, for now
        assert await curator.review(BASE, JUNK) is False
        # The pool is pulled an hour later: not due yet, still shown.
        del state["prices"].spot[("base", JUNK)]
        state["now"] += 3600
        assert await curator.review(BASE, JUNK) is False
        # A day after the first verdict it is looked at again, and hidden.
        state["now"] += 24 * 3600
        assert await curator.review(BASE, JUNK) is True
        row = ledger.get_token(8453, JUNK)
        assert row and row["hidden"] == 1 and row["hidden_by"] == "auto"
        assert row["classified_at"] == state["now"]

    async def test_user_choice_is_final(self, ledger: Ledger, stack) -> None:
        curator, state = stack
        _add(ledger, USDC)
        ledger.set_token_hidden(8453, USDC, True, by="user", classified_at=state["now"])
        assert await curator.review(BASE, USDC, force=True) is True
        ledger.set_token_hidden(8453, JUNK, False, by="user", classified_at=state["now"])
        _add(ledger, JUNK)
        ledger.set_token_hidden(8453, JUNK, False, by="user", classified_at=state["now"])
        assert await curator.review(BASE, JUNK, force=True) is False
        # The classifier's own verdicts never touch a user row.
        ledger.set_token_hidden(8453, JUNK, True, by="auto", classified_at=state["now"])
        row = ledger.get_token(8453, JUNK)
        assert row and row["hidden"] == 0 and row["hidden_by"] == "user"
