from __future__ import annotations

import json

import httpx
import pytest

from agentos.trading import evm as evm_mod
from agentos.trading.chains import BASE, NATIVE_ADDRESS, ROBINHOOD
from agentos.trading.evm import EvmClient, EvmRpcError, EvmTransportError
from agentos.trading.prices import PriceService
from agentos.trading.uniswap import UniswapAuthError, UniswapClient, UniswapError
from tests.test_trading.fakes import (
    AAPL,
    OTHER,
    USDC,
    WALLET,
    WETH,
    FakeChain,
    FakePrices,
    FakeUniswap,
    transfer_log,
)


class TestAbiHelpers:
    def test_encode_and_decode(self) -> None:
        assert evm_mod.pad_address(WALLET) == "0" * 24 + WALLET[2:]
        assert evm_mod.decode_uint("0x") == 0
        assert evm_mod.decode_uint("0x10") == 16
        with pytest.raises(ValueError):
            evm_mod.pad_uint(-1)
        # dynamic string
        text = "USD Coin"
        raw = (
            (32).to_bytes(32, "big")
            + len(text).to_bytes(32, "big")
            + text.encode().ljust(32, b"\0")
        )
        assert evm_mod.decode_string("0x" + raw.hex()) == text
        # bytes32 style
        assert evm_mod.decode_string("0x" + b"MKR".ljust(32, b"\0").hex()) == "MKR"
        assert evm_mod.decode_string("0x1234") == ""

    def test_parse_transfer_log_rejects_erc721(self) -> None:
        log = transfer_log(
            tx_hash="0xaa",
            log_index=1,
            block=5,
            token=USDC,
            sender=WALLET,
            recipient=OTHER,
            amount=7,
        )
        parsed = evm_mod.parse_transfer_log(log)
        assert parsed is not None
        assert (parsed.sender, parsed.recipient, parsed.amount, parsed.block_number) == (
            WALLET,
            OTHER,
            7,
            5,
        )
        nft = dict(log, topics=[*log["topics"], "0x01"])
        assert evm_mod.parse_transfer_log(nft) is None
        assert evm_mod.parse_transfer_log({"topics": []}) is None

    def test_receipt_helpers(self) -> None:
        receipt = {"status": "0x1", "gasUsed": "0x10", "effectiveGasPrice": "0x2", "logs": []}
        assert evm_mod.receipt_succeeded(receipt)
        assert evm_mod.receipt_gas_wei(receipt) == 32
        assert not evm_mod.receipt_succeeded({"status": "0x0"})
        assert not evm_mod.receipt_succeeded(None)


@pytest.fixture
def chain() -> FakeChain:
    c = FakeChain(chain_id=8453, block=3_000)
    c.tokens[USDC] = ("USDC", "USD Coin", 6)
    c.set_native(WALLET, 5 * 10**18)
    c.set_erc20(USDC, WALLET, 42 * 10**6)
    return c


@pytest.fixture
async def client(chain: FakeChain):
    transport = httpx.MockTransport(chain.handle)
    async with httpx.AsyncClient(transport=transport) as http:
        yield EvmClient("https://mainnet.base.org", http=http, max_log_span=1000)


class TestEvmClient:
    async def test_reads_and_user_agent(self, client: EvmClient, chain: FakeChain) -> None:
        assert await client.chain_id() == 8453
        assert await client.block_number() == 3_000
        assert await client.get_balance(WALLET) == 5 * 10**18
        assert await client.erc20_balance_of(USDC, WALLET) == 42 * 10**6
        assert await client.erc20_metadata(USDC) == ("USDC", "USD Coin", 6)
        assert await client.erc20_metadata(OTHER) == ("", "", 18)
        assert await client.block_timestamp(10) == 1_700_000_000 + 20

    async def test_missing_user_agent_is_403(self, chain: FakeChain) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            request.headers["user-agent"] = "curl"
            return chain.handle(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(EvmTransportError):
                await EvmClient("https://rpc", http=http).chain_id()

    async def test_batch_balances_and_sequential_fallback(
        self, client: EvmClient, chain: FakeChain
    ) -> None:
        balances = await client.erc20_balances(WALLET, [USDC, WETH])
        assert balances == {USDC: 42 * 10**6, WETH: 0}
        assert len(chain.calls) == 2  # one batch round-trip, two inner calls
        chain.batch_supported = False
        chain.calls.clear()
        assert await client.erc20_balances(WALLET, [USDC, WETH]) == {USDC: 42 * 10**6, WETH: 0}

    async def test_failed_balance_read_is_none_not_zero(
        self, client: EvmClient, chain: FakeChain
    ) -> None:
        """A per-item RPC error is 'unknown'; a zero here would erase a holding."""
        chain.fail_balance_of.add(USDC)
        assert await client.erc20_balances(WALLET, [USDC, WETH]) == {USDC: None, WETH: 0}
        chain.batch_supported = False
        assert await client.erc20_balances(WALLET, [USDC, WETH]) == {USDC: None, WETH: 0}

    async def test_rpc_error_surfaces(self, client: EvmClient) -> None:
        with pytest.raises(EvmRpcError) as info:
            await client.call("eth_nope")
        assert info.value.code == -32601

    async def test_transfer_logs_chunk_and_halve(self, client: EvmClient, chain: FakeChain) -> None:
        chain.max_log_span = 300
        chain.add_transfer(token=USDC, sender=OTHER, recipient=WALLET, amount=1, block=100)
        chain.add_transfer(token=USDC, sender=WALLET, recipient=OTHER, amount=2, block=2_500)
        chain.add_transfer(token=USDC, sender=OTHER, recipient=OTHER, amount=3, block=2_600)
        logs = await client.transfer_logs(WALLET, from_block=0, to_block=3_000)
        assert [(t.block_number, t.amount) for t in logs] == [(100, 1), (2_500, 2)]
        spans = [
            int(c["params"][0]["toBlock"], 16) - int(c["params"][0]["fromBlock"], 16) + 1
            for c in chain.calls
            if c["method"] == "eth_getLogs"
        ]
        # The first attempt at the full span is refused; the client halves until accepted.
        assert spans[0] == 1000 and spans[-1] <= 300 and all(s <= 300 for s in spans[2:])

    async def test_transfer_logs_halve_on_a_gateway_http_500_too(self, chain: FakeChain) -> None:
        """dRPC refuses an oversized range with HTTP 500, not a JSON-RPC error.

        ``approval_logs`` already halved on that; ``transfer_logs`` raised it
        as an outage. Both now halve down to the node's own span and only
        raise below it.
        """
        chain.add_transfer(token=USDC, sender=OTHER, recipient=WALLET, amount=1, block=100)
        chain.add_transfer(token=USDC, sender=WALLET, recipient=OTHER, amount=2, block=2_500)
        gateway = {"max_span": 500}

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            if payload.get("method") == "eth_getLogs":
                flt = payload["params"][0]
                span = int(flt["toBlock"], 16) - int(flt["fromBlock"], 16) + 1
                if span > gateway["max_span"]:
                    return httpx.Response(500, text="query exceeds max block range")
            return chain.handle(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = EvmClient("https://mainnet.base.org", http=http, max_log_span=250)
            logs = await client.transfer_logs(WALLET, from_block=0, to_block=3_000, max_span=4_000)
            assert [(t.block_number, t.amount) for t in logs] == [(100, 1), (2_500, 2)]
            spans = [
                int(c["params"][0]["toBlock"], 16) - int(c["params"][0]["fromBlock"], 16) + 1
                for c in chain.calls
                if c["method"] == "eth_getLogs"
            ]
            assert spans and all(s <= 500 for s in spans)  # only accepted chunks reach the node
            # A 500 at or below the node's own span is a real outage and is raised.
            gateway["max_span"] = 100
            with pytest.raises(EvmTransportError):
                await client.transfer_logs(WALLET, from_block=0, to_block=3_000, max_span=4_000)

    async def test_transfer_logs_dedupes_self_transfer(
        self, client: EvmClient, chain: FakeChain
    ) -> None:
        chain.add_transfer(token=USDC, sender=WALLET, recipient=WALLET, amount=9, block=10)
        logs = await client.transfer_logs(WALLET, from_block=0, to_block=20)
        assert len(logs) == 1

    async def test_fee_data_with_and_without_history(
        self, client: EvmClient, chain: FakeChain
    ) -> None:
        max_fee, tip = await client.fee_data()
        assert max_fee == 2 * 10**8 + 10**6 and tip == 10**6
        chain.fee_history = False
        max_fee, tip = await client.fee_data()
        assert max_fee == 2 * 10**8 + 10**7 and tip == 10**7

    async def test_send_and_receipt(self, client: EvmClient, chain: FakeChain) -> None:
        tx_hash = await client.send_raw_transaction("0xdeadbeef")
        assert chain.sent == ["0xdeadbeef"]
        assert await client.get_transaction_receipt(tx_hash) is None
        assert await client.wait_for_receipt(tx_hash, timeout_s=0.01, interval_s=0.001) is None
        chain.receipt(tx_hash, status=1)
        receipt = await client.wait_for_receipt(tx_hash, timeout_s=1, interval_s=0.001)
        assert receipt is not None and evm_mod.receipt_succeeded(receipt)


class TestUniswapClient:
    @pytest.fixture
    def fake(self) -> FakeUniswap:
        return FakeUniswap()

    @pytest.fixture
    async def http(self, fake: FakeUniswap):
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
            yield client

    async def test_quote_headers_and_shape(
        self, fake: FakeUniswap, http: httpx.AsyncClient
    ) -> None:
        client = UniswapClient("test-key", http=http)
        quote = await client.quote(
            chain_id=8453,
            swapper=WALLET,
            token_in=USDC,
            token_out=WETH,
            amount_raw=10 * 10**6,
        )
        request = fake.requests[-1]
        assert request.headers["x-permit2-disabled"] == "true"
        assert request.headers["x-api-key"] == "test-key"
        assert "x-universal-router-version" not in request.headers
        body = json.loads(request.content)
        assert body["autoSlippage"] == "DEFAULT" and "slippageTolerance" not in body
        assert body["protocols"] == ["V2", "V3", "V4"]
        assert body["type"] == "EXACT_INPUT" and body["amount"] == str(10 * 10**6)
        assert json.loads(request.headers["x-agent-info"])["decision_origin"] == "human_mediated"
        assert quote.routing == "CLASSIC"
        assert quote.amount_out_raw == fake.amount_out
        assert quote.min_out_raw == fake.amount_out * 995 // 1000
        assert quote.price_impact_pct == pytest.approx(0.12)
        assert quote.gas_fee_usd == pytest.approx(0.05)
        assert quote.fresh and quote.permit_data is None

    async def test_explicit_slippage(self, fake: FakeUniswap, http: httpx.AsyncClient) -> None:
        client = UniswapClient("test-key", http=http)
        await client.quote(
            chain_id=8453,
            swapper=WALLET,
            token_in=USDC,
            token_out=WETH,
            amount_raw=1,
            slippage_pct=1.25,
        )
        body = json.loads(fake.requests[-1].content)
        assert body["slippageTolerance"] == 1.25 and "autoSlippage" not in body

    async def test_bad_key(self, fake: FakeUniswap, http: httpx.AsyncClient) -> None:
        client = UniswapClient("wrong", http=http, max_retries=0)
        with pytest.raises(UniswapAuthError):
            await client.quote(
                chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=1
            )
        ok, _, error = await client.probe()
        assert ok is False and error

    async def test_probe_ok_on_404(self, fake: FakeUniswap, http: httpx.AsyncClient) -> None:
        fake.quote_error = (
            404,
            {"errorCode": "NoRouteFoundError", "detail": "No quotes available"},
        )
        ok, latency, error = await UniswapClient("test-key", http=http).probe()
        assert ok is True and error is None and latency is not None

    async def test_error_mapping(self, fake: FakeUniswap, http: httpx.AsyncClient) -> None:
        client = UniswapClient("test-key", http=http, max_retries=0)
        fake.quote_error = (404, {"errorCode": "UnsupportedTokenError"})
        with pytest.raises(UniswapError) as info:
            await client.quote(
                chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=1
            )
        assert info.value.code == "trading.no_route"
        assert "does not route" in str(info.value)
        fake.quote_error = (404, {"errorCode": "UpstreamTimeoutError"})
        with pytest.raises(UniswapError) as info:
            await client.quote(
                chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=1
            )
        assert info.value.retryable is True

    async def test_unsupported_routing_rejected(
        self, fake: FakeUniswap, http: httpx.AsyncClient
    ) -> None:
        fake.routing = "DUTCH_V2"
        with pytest.raises(UniswapError, match="Unsupported routing"):
            await UniswapClient("test-key", http=http).quote(
                chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=1
            )

    async def test_check_approval_and_swap(
        self, fake: FakeUniswap, http: httpx.AsyncClient
    ) -> None:
        client = UniswapClient("test-key", http=http)
        assert (
            await client.check_approval(chain_id=8453, wallet=WALLET, token=USDC, amount_raw=5)
            is None
        )
        fake.approval_needed = True
        approval = await client.check_approval(
            chain_id=8453, wallet=WALLET, token=USDC, amount_raw=5
        )
        assert approval is not None and approval["to"] == USDC
        quote = await client.quote(
            chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=5
        )
        tx = await client.swap(quote, deadline=123, decision_origin="autonomous")
        request = fake.requests[-1]
        body = json.loads(request.content)
        # Whole quote response spread into the body; null permit fields stripped.
        assert body["quote"] == quote.raw_quote and body["deadline"] == 123
        assert body["routing"] == "CLASSIC" and body["requestId"] == quote.request_id
        assert body["simulateTransaction"] is True and body["refreshGasPrice"] is True
        assert "permitData" not in body and "signature" not in body
        info = json.loads(request.headers["x-agent-info"])
        assert info == {
            "integration_name": "agentos",
            "decision_origin": "autonomous",
            "version": info["version"],
        }
        assert (
            request.headers["x-agent-info"].isascii() and " " not in request.headers["x-agent-info"]
        )
        assert tx["to"].lower() == fake.tx_to
        assert await client.swap_status(chain_id=8453, tx_hash="0xabc") == "SUCCESS"

    async def test_permit_requires_signature(
        self, fake: FakeUniswap, http: httpx.AsyncClient
    ) -> None:
        fake.permit_data = {"domain": {}, "types": {}, "values": {}}
        client = UniswapClient("test-key", http=http)
        quote = await client.quote(
            chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=5
        )
        assert quote.permit_data is not None
        with pytest.raises(UniswapError, match="signature"):
            await client.swap(quote)
        await client.swap(quote, signature="0xsig")
        body = json.loads(fake.requests[-1].content)
        assert body["signature"] == "0xsig" and body["permitData"] == fake.permit_data

    async def test_swap_transaction_validation(
        self, fake: FakeUniswap, http: httpx.AsyncClient
    ) -> None:
        from agentos.trading.uniswap import validate_transaction

        good = {"to": "0x" + "11" * 20, "from": "0x" + "22" * 20, "data": "0xabcd", "value": "0"}
        validate_transaction(good)
        for bad in (
            {**good, "data": "0x"},
            {**good, "data": "zz"},
            {**good, "to": "0x1"},
            {**good, "from": None},
            {k: v for k, v in good.items() if k != "value"},
            {**good, "maxFeePerGas": "1", "gasPrice": "1"},
        ):
            with pytest.raises(UniswapError):
                validate_transaction(bad)
        fake.tx_to = "0xnope"
        client = UniswapClient("test-key", http=http)
        quote = await client.quote(
            chain_id=8453, swapper=WALLET, token_in=USDC, token_out=WETH, amount_raw=5
        )
        with pytest.raises(UniswapError, match="valid 'to'"):
            await client.swap(quote)

    async def test_no_key(self, http: httpx.AsyncClient) -> None:
        with pytest.raises(UniswapAuthError):
            await UniswapClient("", http=http).check_approval(
                chain_id=8453, wallet=WALLET, token=USDC, amount_raw=1
            )


class TestPriceService:
    @pytest.fixture
    def fake(self) -> FakePrices:
        prices = FakePrices()
        prices.spot[("base", USDC)] = 1.0
        prices.spot[("base", WETH)] = 2000.0
        prices.spot[("robinhood", AAPL)] = 150.0
        prices.lists["base"] = [
            {"chainId": 8453, "address": USDC, "symbol": "USDC", "name": "USD Coin", "decimals": 6},
            {
                "chainId": 8453,
                "address": WETH,
                "symbol": "WETH",
                "name": "Wrapped Ether",
                "decimals": 18,
            },
        ]
        prices.lists["robinhood"] = [
            {
                "chainId": 4663,
                "address": AAPL,
                "symbol": "AAPL",
                "name": "Apple • Robinhood Token",
                "decimals": 18,
            },
            {
                "chainId": 4663,
                "address": OTHER,
                "symbol": "AAPL",
                "name": "Apple Fan Coin",
                "decimals": 18,
            },
        ]
        prices.history[USDC] = 0.99
        prices.candles = [[1, 1.0, 2.0, 0.5, 1.5, 10.0], [2, 1.5, 2.5, 1.0, 2.0, 12.0]]
        return prices

    @pytest.fixture
    async def svc(self, fake: FakePrices):
        clock = {"now": 1_000_000.0}
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
            service = PriceService(http=http, ttl_s=20, now=lambda: clock["now"])
            service.clock = clock  # type: ignore[attr-defined]
            yield service

    async def test_spot_prices_cache_and_native(self, svc: PriceService, fake: FakePrices) -> None:
        prices = await svc.prices(BASE, [USDC, NATIVE_ADDRESS])
        assert prices[USDC].price_usd == 1.0
        assert prices[NATIVE_ADDRESS].price_usd == 2000.0  # native priced through WETH
        assert prices[USDC].change_24h_pct == 2.5
        calls = len(fake.requests)
        await svc.prices(BASE, [USDC, NATIVE_ADDRESS])
        assert len(fake.requests) == calls  # served from cache
        svc.clock["now"] += 100  # type: ignore[attr-defined]
        await svc.prices(BASE, [USDC])
        assert len(fake.requests) == calls + 1

    async def test_missing_price_is_none(self, svc: PriceService) -> None:
        assert await svc.price(BASE, OTHER) is None

    async def test_native_of_an_eth_chain_borrows_base_eth(
        self, svc: PriceService, fake: FakePrices
    ) -> None:
        """Robinhood has no WETH pool to price its gas coin; ETH is ETH, so Base's stands in."""
        assert ROBINHOOD.weth is None and ROBINHOOD.native_symbol == "ETH"
        prices = await svc.prices(ROBINHOOD, [NATIVE_ADDRESS, AAPL])
        assert prices[NATIVE_ADDRESS].price_usd == 2000.0
        assert prices[NATIVE_ADDRESS].change_24h_pct == 2.5
        assert prices[AAPL].price_usd == 150.0  # the chain's own feed is untouched
        assert await svc.price(ROBINHOOD, NATIVE_ADDRESS) == 2000.0
        # Borrowed per call, never written into Robinhood's cache: a second
        # call within the TTL reads Base's cached ETH and asks nothing new.
        calls = len(fake.requests)
        assert (await svc.prices(ROBINHOOD, [NATIVE_ADDRESS]))[NATIVE_ADDRESS].price_usd == 2000.0
        assert len(fake.requests) == calls
        assert (4663, NATIVE_ADDRESS) in svc._prices and svc._prices[
            (4663, NATIVE_ADDRESS)
        ].price_usd is None
        # Base's own ETH price never goes through the fallback.
        assert (await svc.prices(BASE, [NATIVE_ADDRESS]))[NATIVE_ADDRESS].pair_address

    async def test_token_list_and_stock_flag(self, svc: PriceService) -> None:
        tokens = await svc.token_list(ROBINHOOD)
        assert tokens[AAPL].stock_token is True and tokens[AAPL].verified is True
        assert tokens[OTHER].stock_token is False
        hits = await svc.find_by_symbol(ROBINHOOD, "aapl")
        assert [h.address for h in hits] == [AAPL, OTHER]
        assert (await svc.find_by_symbol(BASE, "eth"))[0].native is True
        assert await svc.known_token(BASE, USDC) is not None
        assert await svc.known_token(BASE, OTHER) is None

    async def test_search(self, svc: PriceService) -> None:
        rows = await svc.search(ROBINHOOD, "AAPL")
        assert rows[0]["address"] == AAPL and rows[0]["stockToken"] is True
        assert rows[0]["priceUsd"] == 150.0
        by_address = await svc.search(BASE, USDC)
        assert by_address[0]["symbol"] == "USDC" and by_address[0]["priceUsd"] == 1.0
        assert (await svc.search(BASE, "ETH"))[0]["native"] is True
        assert await svc.search(BASE, "   ") == []

    async def test_history_and_fallback(self, svc: PriceService, fake: FakePrices) -> None:
        assert await svc.price_at(BASE, USDC, 900_000) == 0.99
        assert await svc.price_at(BASE, WETH, 900_000) is None
        calls = len(fake.requests)
        await svc.price_at(BASE, USDC, 900_100)  # same hour bucket → cached
        assert len(fake.requests) == calls

    async def test_ohlcv_only_on_base(self, svc: PriceService) -> None:
        candles = await svc.ohlcv(BASE, WETH, timeframe="hour", limit=2)
        assert candles == [
            {"t": 1.0, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5},
            {"t": 2.0, "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0},
        ]
        assert await svc.ohlcv(ROBINHOOD, AAPL, timeframe="hour", limit=2) is None
