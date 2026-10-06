"""``trading.markets``: every pool a token trades in, from a recorded-style GeckoTerminal set.

The contract is ``docs/markets.md``. The pages below follow the shape
GeckoTerminal answered for NVDA on Robinhood Chain on 2026-10-06 (attributes,
relationships, ``included[]`` tokens and dexes); the numbers are taken from
that answer where a test pins them.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from agentos.trading import markets as mk
from agentos.trading.chains import ROBINHOOD
from agentos.trading.service import TradingError, TradingService
from tests.test_trading.fakes import (
    FakeAggregator,
    FakeChain,
    FakeIndexer,
    FakePrices,
    FakeUniswap,
    make_transport,
)

NVDA = "0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec"
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
AI = "0x2e8c00000000000000000000000000000000e1e1"
#: Borrows NVDA's symbol and name (without the bullet); not the Stock Token.
FAKE_NVDA = "0xbad0000000000000000000000000000000000001"
TINY = "0x7171000000000000000000000000000000000001"
FEED = "0xfeed000000000000000000000000000000000001"

NVDA_POOL = "0x6444a8e0b267406a15db74ca00c4a24bdfa81ed3180f5b6d0851f8ed6f4f29c5"
AI_POOL = "0xcbdfea9000000000000000000000000000000000000000000000000000ce27"
FAKE_POOL = "0x00000000000000000000000000000000000fa4e1"
TINY_POOL = "0x0000000000000000000000000000000000071171"


def _token(address: str, symbol: str, name: str, decimals: int = 18) -> dict[str, Any]:
    return {
        "id": f"robinhood_{address}",
        "type": "token",
        "attributes": {
            "address": address,
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "image_url": f"https://img/{symbol}.png",
        },
    }


def _dex(dex_id: str, name: str) -> dict[str, Any]:
    return {"id": dex_id, "type": "dex", "attributes": {"name": name}}


def _pool(
    address: str,
    name: str,
    base: str,
    quote: str,
    dex: str,
    *,
    tvl: str,
    base_usd: str,
    quote_usd: str,
    base_in_quote: str,
    quote_in_base: str,
    volume: str = "1000.5",
) -> dict[str, Any]:
    return {
        "id": f"robinhood_{address}",
        "type": "pool",
        "attributes": {
            "base_token_price_usd": base_usd,
            "quote_token_price_usd": quote_usd,
            "base_token_price_quote_token": base_in_quote,
            "quote_token_price_base_token": quote_in_base,
            "address": address,
            "name": name,
            "pool_created_at": "2026-07-25T00:52:36Z",
            "price_change_percentage": {"h1": "0.14", "h24": "2.01"},
            "transactions": {"h24": {"buys": 812, "sells": 790, "buyers": 1, "sellers": 2}},
            "volume_usd": {"h24": volume},
            "reserve_in_usd": tvl,
        },
        "relationships": {
            "base_token": {"data": {"id": f"robinhood_{base}", "type": "token"}},
            "quote_token": {"data": {"id": f"robinhood_{quote}", "type": "token"}},
            "dex": {"data": {"id": dex, "type": "dex"}},
        },
    }


def _filler(index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """A launchpad token priced in NVDA, comfortably above the default TVL floor."""
    address = "0x" + format(0xF1000 + index, "x").rjust(40, "0")
    pool_address = "0x" + format(0xB1000 + index, "x").rjust(40, "0")
    symbol = f"FILL{index}"
    pool = _pool(
        pool_address,
        f"{symbol} / NVDA",
        address,
        NVDA,
        "pons-v2-dex",
        tvl=str(20_000 + index),
        base_usd="0.01",
        quote_usd="240.2",
        base_in_quote="0.0000416",
        quote_in_base="24020",
    )
    return pool, _token(address, symbol, f"Filler {index}")


def _page_one() -> dict[str, Any]:
    pools = [
        _pool(
            NVDA_POOL,
            "NVDA / USDG 0.01%",
            NVDA,
            USDG,
            "uniswap-v4-robinhood",
            tvl="762742.8504",
            base_usd="240.3017762048",
            quote_usd="0.999601273523243",
            base_in_quote="234.1476294422",
            quote_in_base="0.004270809841",
            volume="13645970.9616489",
        ),
        _pool(
            AI_POOL,
            "AI / NVDA",
            AI,
            NVDA,
            "bankr-robinhood",
            tvl="4730665.8503",
            base_usd="0.1103804417",
            quote_usd="239.99985596935",
            base_in_quote="0.000710917196",
            quote_in_base="1406.6335793955",
            volume="806473.0",
        ),
        _pool(
            FAKE_POOL,
            "NVDA / NVDA",
            FAKE_NVDA,
            NVDA,
            "uniswap-v2-robinhood",
            tvl="649000",
            base_usd="0.5",
            quote_usd="240.1",
            base_in_quote="0.00208",
            quote_in_base="480.2",
        ),
        _pool(
            TINY_POOL,
            "TINY / NVDA",
            TINY,
            NVDA,
            "pons-v2-dex",
            tvl="512.25",
            base_usd="0.0001",
            quote_usd="240.1",
            base_in_quote="0.0000004",
            quote_in_base="2401000",
        ),
    ]
    included = [
        _token(NVDA, "NVDA", "NVIDIA • Robinhood Token"),
        _token(USDG, "USDG", "Global Dollar", 6),
        _token(AI, "AI", "Artificial Inu"),
        _token(FAKE_NVDA, "NVDA", " NVIDIA Robinhood Token "),
        _token(TINY, "TINY", "Tiny"),
        _dex("uniswap-v4-robinhood", "Uniswap V4 (Robinhood)"),
        _dex("bankr-robinhood", "Bankr (Robinhood)"),
        _dex("uniswap-v2-robinhood", "Uniswap V2 (Robinhood)"),
        _dex("pons-v2-dex", "Pons V2 Dex"),
    ]
    for index in range(16):
        pool, token = _filler(index)
        pools.append(pool)
        included.append(token)
    return {"data": pools, "included": included}


def _filler_page(start: int, count: int) -> dict[str, Any]:
    pools, included = [], [_token(NVDA, "NVDA", "NVIDIA • Robinhood Token")]
    for index in range(start, start + count):
        pool, token = _filler(index)
        pools.append(pool)
        included.append(token)
    included.append(_dex("pons-v2-dex", "Pons V2 Dex"))
    return {"data": pools, "included": included}


@dataclass
class FakeMarkets:
    """GeckoTerminal pools pages, DexScreener token-pairs, the feed directory and the feed."""

    pages: dict[int, dict[str, Any]] = field(default_factory=dict)
    #: page -> HTTP status to answer instead of the page.
    status: dict[int, int] = field(default_factory=dict)
    dexscreener: list[dict[str, Any]] | None = field(default_factory=list)
    oracle_answer: int | None = None
    oracle_updated_at: int = 0
    oracle_paused: bool = False
    gecko: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response | None:
        host, path = request.url.host, request.url.path
        if host == "api.geckoterminal.com" and path.endswith(f"/tokens/{NVDA}/pools"):
            self.gecko.append(request)
            page = int(request.url.params.get("page", "1"))
            if page in self.status:
                return httpx.Response(self.status[page], json={"status": self.status[page]})
            return httpx.Response(200, json=self.pages.get(page, {"data": [], "included": []}))
        if host == "api.dexscreener.com" and path.startswith("/token-pairs/v1/"):
            if self.dexscreener is None:
                return httpx.Response(503)
            return httpx.Response(200, json=self.dexscreener)
        if host == "reference-data-directory.vercel.app":
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "Robinhood NVDA / USD",
                        "proxyAddress": FEED,
                        "heartbeat": 86_400,
                        "docs": {"baseAsset": "NVDA"},
                    }
                ],
            )
        if host == "rpc.mainnet.chain.robinhood.com":
            body = json.loads(request.content or b"null")
            if isinstance(body, dict) and body.get("method") == "eth_call":
                call = body["params"][0]
                to, data = str(call.get("to")).lower(), str(call.get("data"))
                if to == FEED and data == mk.SEL_LATEST_ROUND_DATA:
                    if self.oracle_answer is None:
                        return _rpc(body, "0x")
                    words = [1, self.oracle_answer, 0, self.oracle_updated_at, 1]
                    return _rpc(body, "0x" + "".join(format(w, "x").rjust(64, "0") for w in words))
                if to == NVDA and data == mk.SEL_ORACLE_PAUSED:
                    return _rpc(body, "0x" + format(int(self.oracle_paused), "x").rjust(64, "0"))
        return None


def _rpc(body: dict[str, Any], result: str) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": result})


@pytest.fixture
def fake_markets() -> FakeMarkets:
    world = FakeMarkets()
    world.pages[1] = _page_one()
    world.pages[2] = _filler_page(16, 5)  # short: the listing ends here
    return world


@pytest.fixture
def transport(
    base_chain: FakeChain,
    robinhood_chain: FakeChain,
    fake_uniswap: FakeUniswap,
    fake_aggregator: FakeAggregator,
    fake_prices: FakePrices,
    fake_indexer: FakeIndexer,
    fake_markets: FakeMarkets,
) -> httpx.MockTransport:
    fake_prices.lists["robinhood"] += [
        {"chainId": 4663, "address": NVDA, "symbol": "NVDA", "name": "NVIDIA • Robinhood Token"},
        {
            "chainId": 4663,
            "address": USDG,
            "symbol": "USDG",
            "name": "Global Dollar",
            "decimals": 6,
        },
        {"chainId": 4663, "address": AI, "symbol": "AI", "name": "Artificial Inu"},
        {"chainId": 4663, "address": "0x" + "d1" * 20, "symbol": "DUP", "name": "Dup One"},
        {"chainId": 4663, "address": "0x" + "d2" * 20, "symbol": "DUP", "name": "Dup Two"},
    ]
    fake_prices.spot[("robinhood", NVDA)] = 240.30
    inner = make_transport(
        chains={
            "mainnet.base.org": base_chain,
            "rpc.mainnet.chain.robinhood.com": robinhood_chain,
        },
        uniswap=fake_uniswap,
        aggregator=fake_aggregator,
        prices=fake_prices,
        indexer=fake_indexer,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        answer = fake_markets.handle(request)
        return answer if answer is not None else inner.handle_request(request)

    return httpx.MockTransport(handler)


async def _read(service: TradingService, **kwargs: Any) -> dict[str, Any]:
    return await mk.markets(service, chain=ROBINHOOD, target=kwargs.pop("target", "NVDA"), **kwargs)


def _row(result: dict[str, Any], side: str, pair: str) -> dict[str, Any]:
    rows = [r for r in result["sections"][side] if r["pair"] == pair]
    assert len(rows) == 1, [r["pair"] for r in result["sections"][side]]
    return rows[0]


async def test_sections_split_by_side(service: TradingService, fake_markets: FakeMarkets) -> None:
    result = await _read(service)
    assert result["version"] == 1 and result["kind"] == "markets"
    assert result["chain"] == {
        "id": 4663,
        "key": "robinhood",
        "name": "Robinhood Chain",
        "explorer": "https://robinhoodchain.blockscout.com",
    }
    assert result["partial"] is False and result["warnings"] == []
    token = result["token"]
    assert token["address"] == NVDA and token["symbol"] == "NVDA"
    assert token["stockToken"] is True and token["verified"] is True
    assert token["priceUsd"] == pytest.approx(240.30)
    assert token["oracle"] is None  # the feed answered nothing usable

    ai = _row(result, "quote", "AI/NVDA")
    assert ai["dex"] == {"id": "bankr-robinhood", "label": "Bankr", "version": None}
    assert ai["launcher"] == "Bankr" and ai["viaUniswap"] is False and ai["feePct"] is None
    assert ai["counterparty"]["symbol"] == "AI" and ai["counterparty"]["lookalike"] is False
    assert ai["counterparty"]["verified"] is True and ai["counterparty"]["stockToken"] is False
    assert ai["tvlUsd"] == pytest.approx(4730665.8503)
    assert ai["volume24hUsd"] == pytest.approx(806473.0)
    assert ai["txns24h"] == {"buys": 812, "sells": 790}
    # Quote rows: the counterparty's price, and the counterparty priced in NVDA.
    assert ai["priceUsd"] == pytest.approx(0.1103804417)
    assert ai["priceInToken"] == pytest.approx(0.000710917196)
    assert ai["premiumPct"] is None
    assert ai["swap"] == {"chainId": 4663, "tokenIn": NVDA, "tokenOut": AI}
    assert ai["url"] == f"https://www.geckoterminal.com/robinhood/pools/{AI_POOL}"

    usdg = _row(result, "base", "NVDA/USDG")
    assert usdg["side"] == "base"
    assert usdg["feePct"] == 0.01 and usdg["viaUniswap"] is True
    assert usdg["dex"] == {"id": "uniswap-v4-robinhood", "label": "Uniswap", "version": "v4"}
    assert usdg["launcher"] is None
    # Base rows: NVDA's price in this pool, and NVDA priced in USDG.
    assert usdg["priceUsd"] == pytest.approx(240.3017762048)
    assert usdg["priceInToken"] == pytest.approx(234.1476294422)
    assert usdg["change24hPct"] == pytest.approx(2.01)
    assert usdg["createdAt"] == "2026-07-25T00:52:36Z"
    assert usdg["swap"] == {"chainId": 4663, "tokenIn": NVDA, "tokenOut": USDG}

    assert [r["pair"] for r in result["sections"]["base"]] == ["NVDA/USDG"]
    quote_tvls = [r["tvlUsd"] for r in result["sections"]["quote"]]
    assert quote_tvls == sorted(quote_tvls, reverse=True)
    assert result["sections"]["quote"][0]["pair"] == "AI/NVDA"

    assert result["counts"] == {
        "scanned": 25,
        "shown": 23,
        "belowMinTvl": 1,
        "hiddenLookalikes": 1,
        "pages": 2,
        "pageCap": 5,
    }
    assert result["request"] == {
        "kind": "markets",
        "params": {
            "target": NVDA,
            "chainId": 4663,
            "side": "all",
            "minTvlUsd": 10_000.0,
            "limit": 50,
            "lookalikes": False,
            "deep": False,
        },
    }
    # Pages were read one by one with the documented query and header.
    first = fake_markets.gecko[0]
    assert first.headers["accept"] == "application/json;version=20230302"
    assert first.url.params["include"] == "base_token,quote_token,dex"
    assert first.url.params["sort"] == "h24_volume_usd_desc"
    # One burst asks for every page up to the cap at once; the short page 2
    # marks the read complete and the extra pages are discarded.
    assert [r.url.params["page"] for r in fake_markets.gecko] == ["1", "2", "3", "4", "5"]


async def test_lookalike_hidden_by_default_and_shown_on_request(
    service: TradingService,
) -> None:
    hidden = await _read(service)
    assert all(r["counterparty"]["address"] != FAKE_NVDA for r in hidden["sections"]["quote"])
    assert hidden["counts"]["hiddenLookalikes"] == 1

    shown = await _read(service, lookalikes=True)
    fake = [r for r in shown["sections"]["quote"] if r["counterparty"]["address"] == FAKE_NVDA]
    assert len(fake) == 1
    assert fake[0]["counterparty"]["lookalike"] is True
    assert fake[0]["counterparty"]["stockToken"] is False
    assert fake[0]["pair"] == "NVDA/NVDA"
    assert shown["counts"]["hiddenLookalikes"] == 0
    assert shown["counts"]["shown"] == hidden["counts"]["shown"] + 1


async def test_rate_limit_on_page_three_is_partial(
    service: TradingService, fake_markets: FakeMarkets
) -> None:
    fake_markets.pages[2] = _filler_page(16, 20)
    fake_markets.status[3] = 429
    result = await _read(service)
    assert result["partial"] is True
    assert result["warnings"][0] == "GeckoTerminal rate limit: showing the first 40 pools"
    assert result["counts"]["pages"] == 2 and result["counts"]["scanned"] == 40
    assert [r.url.params["page"] for r in fake_markets.gecko] == ["1", "2", "3", "4", "5"]


async def test_no_page_at_all_is_unavailable(
    service: TradingService, fake_markets: FakeMarkets
) -> None:
    fake_markets.status[1] = 503
    with pytest.raises(TradingError) as err:
        await _read(service)
    assert err.value.code == "trading.markets.unavailable"
    fake_markets.status[1] = 429
    with pytest.raises(TradingError) as err:
        await _read(service)
    assert err.value.code == "trading.markets.unavailable"
    assert "rate limit" in str(err.value)


async def test_pages_are_cached_per_token(
    service: TradingService, fake_markets: FakeMarkets
) -> None:
    await _read(service)
    await _read(service, side="base")
    await _read(service, lookalikes=True)
    assert len(fake_markets.gecko) == 5  # the first read's burst; the second read made no call


async def test_limit_stops_paging(service: TradingService, fake_markets: FakeMarkets) -> None:
    result = await _read(service, limit=5)
    # A burst reads the first five pages together, so a small limit still
    # costs one burst; the fixture's two pages are what came back.
    assert result["counts"]["shown"] == 5 and result["counts"]["pages"] == 2
    assert len(fake_markets.gecko) == 5
    assert result["partial"] is False


async def test_page_cap_marks_the_read_partial(
    service: TradingService, fake_markets: FakeMarkets
) -> None:
    for page in range(2, 12):
        fake_markets.pages[page] = _filler_page(100 + page * 20, 20)
    result = await _read(service, limit=200)
    assert result["counts"]["pages"] == 5 and result["counts"]["pageCap"] == 5
    assert result["partial"] is True
    assert result["warnings"][0].startswith("Read the first 100 pools (page cap)")
    deep = await _read(service, limit=200, deep=True)
    assert deep["counts"]["pages"] == 10 and deep["counts"]["pageCap"] == 10
    # The deep read reused the five cached pages.
    assert len(fake_markets.gecko) == 10


async def test_side_and_min_tvl_filters(service: TradingService) -> None:
    base = await _read(service, side="base")
    assert base["sections"]["quote"] == []
    assert [r["pair"] for r in base["sections"]["base"]] == ["NVDA/USDG"]
    everything = await _read(service, min_tvl_usd=0)
    assert any(r["pair"] == "TINY/NVDA" for r in everything["sections"]["quote"])
    assert everything["counts"]["belowMinTvl"] == 0


async def test_oracle_and_premium_on_base_rows(
    service: TradingService, fake_markets: FakeMarkets
) -> None:
    fake_markets.oracle_answer = 23_974_000_000  # 239.74 with 8 decimals
    fake_markets.oracle_updated_at = int(time.time()) - 100
    result = await _read(service)
    oracle = result["token"]["oracle"]
    assert oracle["usd"] == pytest.approx(239.74)
    assert oracle["stale"] is False and oracle["paused"] is False
    assert 100 <= oracle["ageSeconds"] < 200
    usdg = _row(result, "base", "NVDA/USDG")
    assert usdg["premiumPct"] == pytest.approx((240.3017762048 / 239.74 - 1) * 100)
    assert _row(result, "quote", "AI/NVDA")["premiumPct"] is None


async def test_stale_or_paused_oracle(service: TradingService, fake_markets: FakeMarkets) -> None:
    fake_markets.oracle_answer = 23_974_000_000
    fake_markets.oracle_updated_at = int(time.time()) - 90_000
    fake_markets.oracle_paused = True
    oracle = (await _read(service))["token"]["oracle"]
    assert oracle["stale"] is True and oracle["paused"] is True


async def test_dexscreener_enriches_and_its_failure_is_a_warning(
    service: TradingService, fake_markets: FakeMarkets
) -> None:
    fake_markets.dexscreener = [{"pairAddress": AI_POOL.upper(), "labels": ["v4"]}]
    result = await _read(service)
    assert _row(result, "quote", "AI/NVDA")["dex"]["version"] == "v4"
    mk.clear_cache()
    fake_markets.dexscreener = None
    result = await _read(service)
    assert result["warnings"] and "DexScreener" in result["warnings"][0]
    assert result["partial"] is False


async def test_unknown_symbol_is_not_found(service: TradingService) -> None:
    with pytest.raises(TradingError) as err:
        await _read(service, target="NOPE")
    assert err.value.code == "trading.not_found"


async def test_ambiguous_symbol_keeps_candidates(service: TradingService) -> None:
    with pytest.raises(TradingError) as err:
        await _read(service, target="DUP")
    assert err.value.code == "trading.invalid"
    assert len(err.value.details["candidates"]) == 2


def test_parsers() -> None:
    assert mk.parse_fee_pct("NVDA / USDG 0.01%") == 0.01
    assert mk.parse_fee_pct("SPY / NVDA 0.025%") == 0.025
    assert mk.parse_fee_pct("AI / NVDA") is None
    assert mk.launcher_for("bankr-robinhood") == "Bankr"
    assert mk.launcher_for("pons-v2-dex") == "Pons"
    assert mk.launcher_for("uniswap-v4-robinhood") is None
    assert mk.dex_version("ramses-v3-robinhood") == "v3"
    assert mk.dex_version("giga-v3") == "v3"
    assert mk.dex_version("bankr-robinhood") is None
    assert mk.dex_label("pons-v2-dex", "Pons V2 Dex") == "Pons"
    assert mk.dex_label("uniswap-v3-robinhood", "Uniswap V3 (Robinhood)") == "Uniswap"
    assert mk.dex_label("up-v3", "") == "Up"
    assert mk.is_stock_name("NVIDIA • Robinhood Token")
    assert mk.is_stock_name("International Business Machines • Robinhood Toke")
    assert not mk.is_stock_name(" NVIDIA Robinhood Token ")
    assert not mk.is_stock_name("memestock TSLA")
