"""An offline Uniswap V4 world for the LP-card builders (``agentos.trading.lp``).

The fake stands in for the skill library's ``RpcClient`` at the level its call
sites use -- ``multicall`` answered by ``functionName``, plus ``call``/``read``/
``get_logs``/``batch`` -- the same approach as the skill's own ``selftest.py``.
Pool state is derived from a list of positions exactly the way the PoolManager
keeps it (tick bitmap, liquidityNet, active liquidity), so the tick walk in the
library runs for real against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from agentos.trading import lp
from agentos.trading.chains import BASE, ROBINHOOD, ChainSpec

LIB = lp.unilp()
Q128 = 1 << 128

WETH = "0x4200000000000000000000000000000000000006"
PEPE = "0x52b492a33e447cdb854c7fc19f1e57e8bfa1777d"
FOO = "0xf00df00df00df00df00df00df00df00df00df00d"
BAR = "0xba7ba7ba7ba7ba7ba7ba7ba7ba7ba7ba7ba7ba7b"
WALLET = "0x1111111111111111111111111111111111111111"
OUTSIDER = "0x3333333333333333333333333333333333333333"
#: Clanker v4.1 static-fee hook and the (labelled) Clanker LP locker.
CLANKER_HOOK = "0xb429d62f8f3bFFb98CdB9569533eA23bF0Ba28CC"
CLANKER_LOCKER = "0x29d17C1A8D851d7d4cA97FAe97AcAdb398D9cCE0"
CLANKER_FACTORY = "0xE85A59c628F7d27878ACeB4bf3b35733630083a9"


def _cs(address: str) -> str:
    return str(LIB.hexutil.checksum_address(address))


def pepe_pool_key() -> dict[str, Any]:
    return dict(
        LIB.v4_pool.normalize_pool_key(
            {
                "currency0": WETH,
                "currency1": PEPE,
                "fee": 0x800000,
                "tickSpacing": 200,
                "hooks": CLANKER_HOOK,
            }
        )
    )


def foo_pool_key() -> dict[str, Any]:
    c0, c1 = sorted([FOO, BAR], key=str.lower)
    return dict(
        LIB.v4_pool.normalize_pool_key(
            {
                "currency0": c0,
                "currency1": c1,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": lp.NATIVE_ADDRESS,
            }
        )
    )


@dataclass
class Position:
    token_id: int
    pool_id: str
    tick_lower: int
    tick_upper: int
    liquidity: int
    owner: str
    fees0: int = 0
    fees1: int = 0


@dataclass
class Pool:
    key: dict[str, Any]
    tick: int
    lp_fee: int = 10_000

    @cached_property
    def pool_id(self) -> str:
        return str(LIB.v4_pool.compute_pool_id(self.key)).lower()


@dataclass
class World:
    """A chain's worth of pools, tokens and position NFTs."""

    spec: ChainSpec = BASE
    block: int = 21_044_901
    tokens: dict[str, tuple[str, int, int | None]] = field(default_factory=dict)
    pools: dict[str, Pool] = field(default_factory=dict)
    positions: dict[int, Position] = field(default_factory=dict)
    #: Clanker launches: token (lower) -> (hook, locker, first locked tokenId).
    clanker: dict[str, tuple[str, str, int]] = field(default_factory=dict)
    next_token_id: int = 60_000
    logs_refused: bool = True
    calls: list[str] = field(default_factory=list)
    #: Deployed bytecode by lower-cased address (``eth_getCode``); EOAs are absent.
    codes: dict[str, str] = field(default_factory=dict)

    @property
    def chain(self) -> dict[str, Any]:
        return dict(LIB.chains.CHAINS[self.spec.key])

    def indexer(self, owner: str, **_: Any) -> list[int]:
        """A perfect NFT indexer: every tokenId ``owner`` holds (all on one page)."""
        return sorted(t for t, p in self.positions.items() if p.owner == owner.lower())

    # -- building ----------------------------------------------------------
    def add_pool(self, key: dict[str, Any], tick: int) -> Pool:
        pool = Pool(key=key, tick=tick)
        self.pools[pool.pool_id] = pool
        return pool

    def add_position(
        self,
        pool: Pool,
        token_id: int,
        tl: int,
        tu: int,
        liquidity: int,
        owner: str,
        fees0: int = 0,
        fees1: int = 0,
    ) -> Position:
        pos = Position(token_id, pool.pool_id, tl, tu, liquidity, owner.lower(), fees0, fees1)
        self.positions[token_id] = pos
        return pos

    # -- derived pool state ------------------------------------------------
    def _live(self, pool_id: str) -> list[Position]:
        return [p for p in self.positions.values() if p.pool_id == pool_id and p.liquidity > 0]

    def _nets(self, pool_id: str) -> dict[int, int]:
        nets: dict[int, int] = {}
        for p in self._live(pool_id):
            nets[p.tick_lower] = nets.get(p.tick_lower, 0) + p.liquidity
            nets[p.tick_upper] = nets.get(p.tick_upper, 0) - p.liquidity
        return nets

    def _bitmap(self, pool_id: str, word: int) -> int:
        spacing = int(self.pools[pool_id].key["tickSpacing"])
        bits = 0
        for tick in self._nets(pool_id):
            compressed = tick // spacing
            if compressed // 256 == word:
                bits |= 1 << (compressed % 256)
        return bits

    def active_liquidity(self, pool_id: str) -> int:
        tick = self.pools[pool_id].tick
        return sum(p.liquidity for p in self._live(pool_id) if p.tick_lower <= tick < p.tick_upper)

    # -- the RpcClient surface ----------------------------------------------
    def block_number(self) -> int:
        return self.block

    def _answer(self, call: dict[str, Any]) -> Any:
        name = call["functionName"]
        args = call.get("args") or []
        target = str(call["address"]).lower()
        chain = self.chain
        self.calls.append(name)
        if target == chain["stateView"].lower():
            pool = self.pools.get(str(args[0]).lower())
            if name == "getSlot0":
                if pool is None:
                    return (0, 0, 0, 0)
                sqrt = LIB.v4_math.get_sqrt_ratio_at_tick(pool.tick)
                return (sqrt, pool.tick, 0, pool.lp_fee)
            if pool is None:
                raise LookupError("no pool")
            if name == "getLiquidity":
                return self.active_liquidity(pool.pool_id)
            if name == "getTickBitmap":
                return self._bitmap(pool.pool_id, int(args[1]))
            if name == "getTickLiquidity":
                net = self._nets(pool.pool_id).get(int(args[1]), 0)
                return (abs(net), net)
            if name == "getPositionInfo":
                pos = self.positions[int(args[4], 16)]
                return (pos.liquidity, 0, 0)
            if name == "getFeeGrowthInside":
                pos = next(
                    p
                    for p in self.positions.values()
                    if p.pool_id == pool.pool_id
                    and (p.tick_lower, p.tick_upper) == (int(args[1]), int(args[2]))
                )
                return (
                    -(-pos.fees0 * Q128 // pos.liquidity),
                    -(-pos.fees1 * Q128 // pos.liquidity),
                )
        if target == chain["positionManager"].lower():
            if name == "balanceOf":
                return sum(1 for p in self.positions.values() if p.owner == str(args[0]).lower())
            token_id = int(args[0])
            pos = self.positions.get(token_id)
            if pos is None:
                raise LookupError("no such token")
            if name == "ownerOf":
                return _cs(pos.owner)
            if name == "getPositionLiquidity":
                return pos.liquidity
            if name == "getPoolAndPositionInfo":
                return (self.pools[pos.pool_id].key, pack_info(pos))
        if name == "tokenDeploymentInfo":
            launch = self.clanker.get(str(args[0]).lower())
            if target != CLANKER_FACTORY.lower():
                raise LookupError("not a factory")
            zero = lp.NATIVE_ADDRESS
            if launch is None:
                return {"token": zero, "hook": zero, "locker": zero, "extensions": []}
            return {"token": args[0], "hook": launch[0], "locker": launch[1], "extensions": []}
        if name == "getAssetData":
            raise LookupError("no airlock")
        meta = self.tokens.get(target)
        if meta is not None:
            symbol, decimals, supply = meta
            if name == "symbol":
                return symbol
            if name == "decimals":
                return decimals
            if name == "totalSupply":
                if supply is None:
                    raise LookupError("no supply")
                return supply
        raise LookupError(f"unhandled {name} on {target}")

    def multicall(
        self, calls: list[dict[str, Any]], allow_failure: bool = True, block: str = "latest"
    ) -> list[dict[str, Any]]:
        out = []
        for call in calls:
            try:
                out.append({"status": "success", "result": self._answer(call)})
            except LookupError as exc:
                if not allow_failure:
                    raise RuntimeError(str(exc)) from exc
                out.append({"status": "failure", "result": None, "error": str(exc)})
        return out

    def call(self, to: str, data: str, block: str = "latest") -> str:
        """Only the locker's ``tokenRewards`` goes through a raw eth_call."""
        for token, (_hook, locker, first_id) in self.clanker.items():
            if to.lower() == locker.lower() and token[2:] in data.lower():
                words = [0x20, first_id, 1, 0]
                return "0x" + "".join(format(w, "064x") for w in words)
        return "0x"

    def read(
        self, address: str, abi: list[Any], function_name: str, args: list[Any] | None = None
    ) -> Any:
        if function_name == "nextTokenId":
            return self.next_token_id
        raise LookupError(function_name)

    def get_logs(self, params: dict[str, Any]) -> list[Any]:
        """Full-history logs when the node serves them: the PoolManager's Initialize only."""
        if self.logs_refused:
            raise RuntimeError("eth_getLogs is limited to a 2,000 range")
        topics = params.get("topics") or []
        if topics and topics[0] == LIB.abi.TOPIC_INITIALIZE:
            return [e for e in self.initialize_logs() if _matches(e["topics"], topics)]
        return []

    def initialize_logs(self) -> list[dict[str, Any]]:
        out = []
        for i, pool in enumerate(self.pools.values()):
            key = pool.key
            words = [
                int(key["fee"]),
                int(key["tickSpacing"]) % (1 << 256),
                int(key["hooks"], 16),
                LIB.v4_math.get_sqrt_ratio_at_tick(pool.tick),
                pool.tick % (1 << 256),
            ]
            out.append(
                {
                    "address": self.chain["poolManager"],
                    "topics": [
                        LIB.abi.TOPIC_INITIALIZE,
                        pool.pool_id,
                        _topic(key["currency0"]),
                        _topic(key["currency1"]),
                    ],
                    "data": "0x" + "".join(format(w, "064x") for w in words),
                    "blockNumber": hex(10_000 + i),
                    "logIndex": "0x0",
                    "transactionHash": "0x" + "00" * 32,
                }
            )
        return out

    def batch(self, calls: list[dict[str, Any]], chunk_size: int = 20) -> list[Any]:
        out: list[Any] = []
        for call in calls:
            if call["method"] == "eth_getCode":
                out.append(self.codes.get(str(call["params"][0]).lower(), "0x"))
            else:
                out.append([])
        return out


def _topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def _matches(topics: list[str], wanted: list[Any]) -> bool:
    """``eth_getLogs`` topic filtering: ``None`` is a wildcard, a list is any-of."""
    for i, want in enumerate(wanted):
        if want is None:
            continue
        if i >= len(topics):
            return False
        options = want if isinstance(want, list) else [want]
        if topics[i].lower() not in {o.lower() for o in options}:
            return False
    return True


def transfer_log(token_id: int, sender: str, to: str, block: int, index: int = 0) -> dict[str, Any]:
    """An ERC-721 Transfer of a position NFT, as ``eth_getLogs`` returns it."""
    return {
        "topics": [LIB.abi.TOPIC_ERC721_TRANSFER, _topic(sender), _topic(to), hex(token_id)],
        "blockNumber": hex(block),
        "logIndex": hex(index),
    }


def serve_transfer_logs(world: World, logs: list[dict[str, Any]]) -> list[list[Any]]:
    """Make ``world`` serve ``logs`` to topic-filtered full-history requests; returns the asks."""
    asked: list[list[Any]] = []

    def get_logs(params: dict[str, Any]) -> list[Any]:
        asked.append(params["topics"])
        return [e for e in logs if _matches(e["topics"], params["topics"])]

    world.logs_refused = False
    world.get_logs = get_logs  # type: ignore[method-assign]
    return asked


def pack_info(pos: Position) -> int:
    """PositionInfo as the PositionManager packs it."""
    lower = pos.tick_lower & 0xFFFFFF
    upper = pos.tick_upper & 0xFFFFFF
    truncated = int(pos.pool_id[2:52], 16)
    return (truncated << 56) | (upper << 32) | (lower << 8)


def pepe_world() -> World:
    """Base: a Clanker-launched PEPE/WETH pool, LP locked, plus an unpriced FOO/BAR pool.

    WETH is currency0, so PEPE (the base token) is currency1 and every price
    orientation in the builders is exercised the "inverted" way.
    """
    world = World()
    world.tokens = {
        WETH.lower(): ("WETH", 18, 1_900_000 * 10**18),
        PEPE.lower(): ("PEPE", 18, 100_000_000_000 * 10**18),
        FOO.lower(): ("FOO", 18, 1_000_000 * 10**18),
        BAR.lower(): ("BAR", 6, 50_000_000 * 10**6),
    }
    world.clanker[PEPE.lower()] = (CLANKER_HOOK, CLANKER_LOCKER, 48_000)
    pepe = world.add_pool(pepe_pool_key(), tick=184_206)
    # The launch position, held by the locker.
    world.add_position(pepe, 48_000, 180_000, 200_000, 6 * 10**24, CLANKER_LOCKER)
    # Somebody else's range below the price.
    world.add_position(pepe, 47_100, 170_000, 184_000, 2 * 10**24, OUTSIDER)
    # The wallet: one position the price has run past, one in range, one closed.
    world.add_position(pepe, 48_213, 186_000, 190_000, 8 * 10**23, WALLET, 0, 0)
    world.add_position(
        pepe,
        48_214,
        183_000,
        186_000,
        15 * 10**23,
        WALLET,
        fees0=42 * 10**15,
        fees1=1_850_000 * 10**18,
    )
    world.add_position(pepe, 48_216, 176_000, 178_000, 0, WALLET)
    # Neither side has a price anywhere: the "no price" row. BAR (6 decimals)
    # is currency0 and, with no known quote in the pair, the base.
    foo = world.add_pool(foo_pool_key(), tick=303_390)
    world.add_position(
        foo, 48_215, 300_000, 306_000, 10**18, WALLET, fees0=5 * 10**6, fees1=200 * 10**18
    )
    return world


def world_prices(world: World, table: dict[str, float] | None = None) -> lp.PriceFn:
    known = {WETH.lower(): 2512.37} if table is None else {k.lower(): v for k, v in table.items()}

    def prices(addresses: list[str]) -> dict[str, float | None]:
        return {a.lower(): known.get(a.lower()) for a in addresses}

    return prices


def env_for(
    world: World,
    *,
    prices: lp.PriceFn | None = None,
    nft_ids: lp.NftIdsFn | None = None,
    vault: dict[str, str] | None = None,
) -> lp.ChainEnv:
    env = lp.ChainEnv(
        spec=world.spec,
        client=world,
        prices=prices or world_prices(world),
        lib=LIB,
        nft_ids=nft_ids,
        vault=vault if vault is not None else {WALLET.lower(): "Main"},
    )
    lp.start(env)
    env.fetched_at = "2026-09-27T09:30:00Z"
    return env


#: Robinhood Chain: USDG is one of the chain's known quote assets; BONER is not.
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
BONER = "0x98096d17e191b3da1d5f99a6d7b3584351b11e18"


def boner_key(fee: int, tick_spacing: int) -> dict[str, Any]:
    c0, c1 = sorted([USDG, BONER])
    return dict(
        LIB.v4_pool.normalize_pool_key(
            {
                "currency0": c0,
                "currency1": c1,
                "fee": fee,
                "tickSpacing": tick_spacing,
                "hooks": lp.NATIVE_ADDRESS,
            }
        )
    )


def boner_world(live_fee: tuple[int, int] = (9000, 90)) -> World:
    """Robinhood Chain: BONER/USDG in two pools, as the live chain had it on 2026-09-27.

    An abandoned 0.3 % pool (initialised, never funded, its price stale at
    ~$0.018) sits in the conventional fee tiers; the real market is a 0.9 %
    pool (tick spacing 90) only an Initialize log can reveal, at ~$0.0466.
    USDG sorts first, so BONER is currency1.
    """
    world = World(spec=ROBINHOOD, block=73_900_000)
    world.tokens = {
        USDG.lower(): ("USDG", 6, None),
        BONER.lower(): ("BONER", 18, 1_000_000_000 * 10**18),
    }
    world.add_pool(boner_key(3000, 60), tick=316_386)
    live = world.add_pool(boner_key(*live_fee), tick=306_994)
    spacing = live_fee[1]
    world.add_position(
        live,
        3_295_139,
        (305_910 // spacing) * spacing,
        (308_070 // spacing) * spacing,
        4 * 10**18,
        WALLET,
    )
    return world


def empty_world(spec: ChainSpec = ROBINHOOD) -> World:
    return World(spec=spec, block=73_859_199)
