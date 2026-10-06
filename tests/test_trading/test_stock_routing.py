"""Stock Tokens route through Uniswap (docs/markets.md, "Prerequisites").

The aggregator refuses the Robinhood Stock Tokens (``token_not_tradeable``);
Uniswap's Trading API quotes them. The engine picks the provider per pair,
retries a refusal the flag missed once through Uniswap, and without a key
says how to add one.
"""

from __future__ import annotations

import pytest

from agentos.trading.chains import BASE, ROBINHOOD
from agentos.trading.prices import PriceService, is_stock_token_name
from agentos.trading.service import STOCK_TOKEN_UNISWAP_HINT, TradingError, TradingService
from tests.test_trading.fakes import (
    AAPL,
    USDC,
    WETH,
    FakeAggregator,
    FakeChain,
    FakeUniswap,
)
from tests.test_trading.test_service import _wire_swap_effects

WETH_RH = "0xaaaa000000000000000000000000000000000003"
NOT_TRADEABLE = (400, {"error": {"code": "TOKEN_NOT_TRADEABLE", "message": "Not tradeable"}})


class TestStockTokenName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("NVIDIA • Robinhood Token", True),
            ("Apple • Robinhood Token", True),
            ("International Business Machines • Robinhood Toke", True),
            ("SPDR Portfolio S&P 500 High Dividend ETF • Robinhood T", True),
            ("Tesla •Robinhood Token ", True),
            (" NVIDIA Robinhood Token ", False),
            ("memestock TSLA", False),
            ("NVIDIA - Robinhood Token", False),
            ("Robinhood Token", False),
            ("", False),
        ],
    )
    def test_cases(self, name: str, expected: bool) -> None:
        assert is_stock_token_name(name) is expected

    def test_a_truncated_list_entry_is_flagged(self) -> None:
        meta = PriceService._meta_from_list(
            ROBINHOOD,
            {
                "chainId": 4663,
                "address": "0xbbbb000000000000000000000000000000000001",
                "symbol": "IBM",
                "name": "International Business Machines • Robinhood Toke",
                "decimals": 18,
            },
        )
        assert meta is not None and meta.stock_token
        # The suffix only means something on Robinhood Chain.
        base = PriceService._meta_from_list(
            BASE,
            {
                "chainId": 8453,
                "address": "0xbbbb000000000000000000000000000000000001",
                "symbol": "IBM",
                "name": "International Business Machines • Robinhood Toke",
                "decimals": 18,
            },
        )
        assert base is not None and not base.stock_token


async def _quote(service: TradingService, **overrides: object) -> dict:
    params: dict = {
        "chain": ROBINHOOD,
        "wallet": None,
        "token_in": "AAPL",
        "token_out": WETH_RH,
        "amount_in": "0.5",
    }
    params.update(overrides)
    return await service.quote(**params)


class TestQuoteRouting:
    async def test_a_stock_token_quotes_through_uniswap_when_a_key_exists(
        self,
        funded_service: TradingService,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        assert funded_service.provider_id() == "aggregator"
        # Either side: selling the Stock Token and buying it.
        sell = await _quote(funded_service)
        buy = await _quote(funded_service, token_in=WETH_RH, token_out="AAPL", amount_in="0.01")
        assert sell["provider"] == "uniswap" and buy["provider"] == "uniswap"
        assert sell["tokenIn"]["stockToken"] is True
        assert fake_uniswap.quotes == 2
        assert fake_aggregator.quotes == 0
        # The configured provider is untouched.
        assert funded_service.provider_id() == "aggregator"

    async def test_without_a_key_the_refusal_carries_the_uniswap_hint(
        self,
        funded_service: TradingService,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        funded_service.config.uniswap_api_key = ""
        fake_aggregator.quote_error = NOT_TRADEABLE
        with pytest.raises(TradingError) as caught:
            await _quote(funded_service)
        assert caught.value.code == "trading.token_not_tradeable"
        assert str(caught.value).startswith("Not tradeable.")
        assert str(caught.value).endswith(STOCK_TOKEN_UNISWAP_HINT)
        assert STOCK_TOKEN_UNISWAP_HINT == (
            "Stock Tokens route through Uniswap: add a Uniswap API key "
            "(`agentos config set trading.uniswap_api_key <key>`, "
            "or Settings › Trading in the desktop app)"
        )
        assert fake_aggregator.quotes == 1
        assert fake_uniswap.quotes == 0

    async def test_a_pair_without_a_stock_token_stays_on_the_aggregator(
        self,
        funded_service: TradingService,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        quote = await _quote(
            funded_service, chain=BASE, token_in="USDC", token_out="WETH", amount_in="10"
        )
        assert quote["provider"] == "aggregator"
        assert fake_aggregator.quotes == 1
        assert fake_uniswap.quotes == 0
        # Other refusals are not retried through Uniswap.
        fake_aggregator.quote_error = (400, {"error": {"code": "UNKNOWN_TOKEN"}})
        with pytest.raises(TradingError) as caught:
            await _quote(
                funded_service, chain=BASE, token_in="USDC", token_out="WETH", amount_in="10"
            )
        assert caught.value.code == "trading.unknown_token"
        assert STOCK_TOKEN_UNISWAP_HINT not in str(caught.value)
        assert fake_uniswap.quotes == 0

    async def test_a_refusal_the_flag_missed_is_retried_once_through_uniswap(
        self,
        funded_service: TradingService,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        fake_aggregator.quote_error = NOT_TRADEABLE
        quote = await _quote(
            funded_service, chain=BASE, token_in="USDC", token_out="WETH", amount_in="10"
        )
        assert quote["provider"] == "uniswap"
        assert fake_aggregator.quotes == 1
        assert fake_uniswap.quotes == 1

    async def test_uniswap_configured_is_left_alone(
        self,
        funded_service: TradingService,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        funded_service.config.provider = "uniswap"
        quote = await _quote(
            funded_service, chain=BASE, token_in="USDC", token_out="WETH", amount_in="10"
        )
        assert quote["provider"] == "uniswap"
        assert fake_aggregator.quotes == 0


class TestOrderRouting:
    async def test_a_stock_token_swap_records_and_executes_through_uniswap(
        self,
        funded_service: TradingService,
        robinhood_chain: FakeChain,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        service = funded_service
        wallet = service.test_wallet  # type: ignore[attr-defined]
        robinhood_chain.tokens[WETH_RH] = ("WETH", "Wrapped Ether", 18)
        robinhood_chain.set_native(wallet, 10**18)
        robinhood_chain.set_erc20(AAPL, wallet, 100 * 10**18)
        _wire_swap_effects(robinhood_chain, wallet, out_token=WETH_RH)
        orders = await service.swap(
            chain=ROBINHOOD,
            wallets=None,
            token_in="AAPL",
            token_out=WETH_RH,
            amount_in="0.5",
            amount_pct=None,
            slippage_pct=None,
            initiator="manual",
            session_key=None,
            note=None,
            wait=True,
        )
        order = orders[0]
        assert order["provider"] == "uniswap", order
        assert order["status"] == "confirmed", order
        # The row is what DCA runs, trigger fires and approvals read back.
        row = service.ledger.get_order(order["orderId"])
        assert row is not None and row["provider"] == "uniswap"
        assert fake_aggregator.quotes == 0
        assert fake_uniswap.quotes >= 1
        assert len(robinhood_chain.sent) == 1

    async def test_an_order_refused_by_the_aggregator_moves_to_uniswap(
        self,
        funded_service: TradingService,
        base_chain: FakeChain,
        fake_aggregator: FakeAggregator,
        fake_uniswap: FakeUniswap,
    ) -> None:
        service = funded_service
        wallet = service.test_wallet  # type: ignore[attr-defined]
        _wire_swap_effects(base_chain, wallet, out_token=WETH)
        fake_aggregator.quote_error = NOT_TRADEABLE
        orders = await service.swap(
            chain=BASE,
            wallets=None,
            token_in=USDC,
            token_out="WETH",
            amount_in="10",
            amount_pct=None,
            slippage_pct=None,
            initiator="manual",
            session_key=None,
            note=None,
            wait=True,
        )
        order = orders[0]
        assert order["provider"] == "uniswap", order
        assert order["status"] == "confirmed", order
        assert fake_aggregator.quotes == 1

    async def test_without_a_key_the_failed_order_says_how_to_fix_it(
        self,
        funded_service: TradingService,
        robinhood_chain: FakeChain,
        fake_aggregator: FakeAggregator,
    ) -> None:
        service = funded_service
        service.config.uniswap_api_key = ""
        wallet = service.test_wallet  # type: ignore[attr-defined]
        robinhood_chain.tokens[WETH_RH] = ("WETH", "Wrapped Ether", 18)
        robinhood_chain.set_native(wallet, 10**18)
        robinhood_chain.set_erc20(AAPL, wallet, 100 * 10**18)
        fake_aggregator.quote_error = NOT_TRADEABLE
        orders = await service.swap(
            chain=ROBINHOOD,
            wallets=None,
            token_in="AAPL",
            token_out=WETH_RH,
            amount_in="0.5",
            amount_pct=None,
            slippage_pct=None,
            initiator="manual",
            session_key=None,
            note=None,
            wait=True,
        )
        order = orders[0]
        assert order["provider"] == "aggregator", order
        assert order["status"] == "failed", order
        assert "trading.token_not_tradeable" in order["reason"]
        assert STOCK_TOKEN_UNISWAP_HINT in order["reason"]
        assert robinhood_chain.sent == []
