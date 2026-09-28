"""An offline Uniswap V4 world for the LP-card builders (``agentos.trading.lp``).

The fake stands in for the skill library's ``RpcClient`` at the level its call
sites use -- ``multicall`` answered by ``functionName``, plus ``call``/``read``/
``get_logs``/``batch`` -- the same approach as the skill's own ``selftest.py``.
Pool state is derived from a list of positions exactly the way the PoolManager
keeps it (tick bitmap, liquidityNet, active liquidity), so the tick walk in the
library runs for real against it.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from agentos.trading import lp
from agentos.trading.chains import BASE, ROBINHOOD, ChainSpec

LIB = lp.unilp()
Q128 = 1 << 128
NATIVE = lp.NATIVE_ADDRESS
PERMIT2 = str(LIB.chains.PERMIT2).lower()
ERC20_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SEL_MODIFY = "0xdd46508f"
SEL_ERC20_APPROVE = "0x095ea7b3"
SEL_PERMIT2_APPROVE = "0x87517c45"
#: Custom-error selectors the fake PositionManager reverts with (v4-periphery / Permit2).
MAX_EXCEEDED = "0x7983c051"
MIN_INSUFFICIENT = "0x8f5d532e"
DEADLINE_PASSED = "0x8b063d73"
NOT_APPROVED = "0x5354b3d5"
ALLOWANCE_EXPIRED = "0xd81b2f2e"
INSUFFICIENT_ALLOWANCE = "0xf96fb071"
NOT_SETTLED = "0x5212cba1"
DELTA_NOT_NEGATIVE = "0x3351b260"
#: Gas the fake chain charges every transaction it mines.
GAS_USED = 200_000
GAS_PRICE = 10**8


class Revert(Exception):  # noqa: N818 - it is what the chain says
    """A call the fake chain refused; ``data`` is the revert blob (a selector here)."""

    def __init__(self, data: str, message: str = "execution reverted") -> None:
        super().__init__(f"{message} ({data})")
        self.data = data
        self.message = message


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
    # -- the PositionManager's world: balances, allowances, time -------------
    #: Native balances by lower-cased address (share ``FakeChain.native`` to link).
    native: dict[str, int] = field(default_factory=dict)
    #: ERC-20 balances, token -> holder -> raw (share ``FakeChain.erc20``).
    erc20: dict[str, dict[str, int]] = field(default_factory=dict)
    #: ERC-20 allowances, ``"token:owner:spender"`` (share ``FakeChain.allowances``).
    erc20_allowances: dict[str, int] = field(default_factory=dict)
    #: Permit2 allowances, (token, owner, spender) -> (amount, expiration).
    permit2: dict[tuple[str, str, str], tuple[int, int]] = field(default_factory=dict)
    timestamp: int = 1_760_000_000
    gas_price: int = GAS_PRICE
    #: Whether the node serves ``eth_simulateV1`` (else it is "method not found").
    simulate_v1: bool = True
    #: Every simulation request, as the calls it carried.
    simulations: list[list[dict[str, Any]]] = field(default_factory=list)
    #: ``modifyLiquidities`` calls that were mined, as (sender, actions by name).
    mined: list[tuple[str, list[str]]] = field(default_factory=list)
    _held: int = 0

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
        if target == PERMIT2 and name == "allowance":
            owner, token, spender = (str(a).lower() for a in args)
            amount, expiration = self.permit2.get((token, owner, spender), (0, 0))
            return (amount, expiration, 0)
        if target in self.tokens and name == "balanceOf":
            return self.erc20.get(target, {}).get(str(args[0]).lower(), 0)
        if target in self.tokens and name == "allowance":
            owner, spender = (str(a).lower() for a in args)
            return self.erc20_allowances.get(f"{target}:{owner}:{spender}", 0)
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

    # -- raw JSON-RPC (the planner's native balance, gas price, simulation) --
    def get_block(self, block: str = "latest") -> dict[str, Any]:
        return {"number": hex(self.block), "timestamp": hex(self.timestamp)}

    def request(self, method: str, params: list[Any] | None = None) -> Any:
        params = params or []
        if method == "eth_getBalance":
            return hex(self.native.get(str(params[0]).lower(), 0))
        if method == "eth_gasPrice":
            return hex(self.gas_price)
        if method == "eth_blockNumber":
            return hex(self.block)
        if method == "eth_getBlockByNumber":
            return self.get_block()
        if method == "eth_simulateV1":
            if not self.simulate_v1:
                raise LIB.rpc.RpcError(
                    method, {"code": -32601, "message": "the method eth_simulateV1 does not exist"}
                )
            calls = params[0]["blockStateCalls"][0]["calls"]
            self.simulations.append(list(calls))
            saved = self._snapshot()
            out = []
            try:
                for call in calls:
                    value = int(call.get("value") or "0x0", 16)
                    try:
                        logs = self.apply(call["from"], call["to"], call["data"], value)
                    except Revert as exc:
                        out.append(
                            {
                                "status": "0x0",
                                "gasUsed": hex(40_000),
                                "logs": [],
                                "error": {"message": exc.message, "data": exc.data},
                            }
                        )
                        continue
                    out.append({"status": "0x1", "gasUsed": hex(GAS_USED), "logs": logs})
            finally:
                self._restore(saved)
            return [{"number": hex(self.block + 1), "calls": out}]
        if method == "eth_call":
            call = params[0]
            saved = self._snapshot()
            try:
                self.apply(
                    call["from"], call["to"], call["data"], int(call.get("value") or "0x0", 16)
                )
            except Revert as exc:
                raise LIB.rpc.RpcError(
                    method, {"code": 3, "message": exc.message, "data": exc.data}
                ) from exc
            finally:
                self._restore(saved)
            return "0x"
        raise LookupError(f"unhandled {method}")

    # -- the PositionManager, Permit2 and ERC-20 approvals ------------------
    def _snapshot(self) -> tuple[Any, ...]:
        return (
            copy.deepcopy(self.positions),
            copy.deepcopy(self.native),
            copy.deepcopy(self.erc20),
            copy.deepcopy(self.erc20_allowances),
            copy.deepcopy(self.permit2),
            self.next_token_id,
        )

    def _restore(self, saved: tuple[Any, ...]) -> None:
        positions, native, erc20, allowances, permit2, next_id = saved
        # In place: the dicts may be shared with a FakeChain.
        for live, old in (
            (self.positions, positions),
            (self.native, native),
            (self.erc20, erc20),
            (self.erc20_allowances, allowances),
            (self.permit2, permit2),
        ):
            live.clear()
            live.update(old)
        self.next_token_id = next_id

    def fund(self, owner: str, token: str, raw: int) -> None:
        if token.lower() == NATIVE:
            self.native[owner.lower()] = raw
        else:
            self.erc20.setdefault(token.lower(), {})[owner.lower()] = raw

    def balance(self, owner: str, token: str) -> int:
        if token.lower() == NATIVE:
            return self.native.get(owner.lower(), 0)
        return self.erc20.get(token.lower(), {}).get(owner.lower(), 0)

    def execute(self, sender: str, to: str, data: str, value: int = 0) -> list[dict[str, Any]]:
        """Mine one call: all of it, or (on a revert) none of it."""
        saved = self._snapshot()
        try:
            return self.apply(sender, to, data, value)
        except Revert:
            self._restore(saved)
            raise

    def apply(self, sender: str, to: str, data: str, value: int = 0) -> list[dict[str, Any]]:
        sender, target = sender.lower(), to.lower()
        selector = data[:10].lower()
        if target in self.tokens and selector == SEL_ERC20_APPROVE:
            spender, amount = LIB.abi_codec.decode(
                [{"type": "address"}, {"type": "uint256"}], "0x" + data[10:]
            )
            self.erc20_allowances[f"{target}:{sender}:{spender.lower()}"] = int(amount)
            return []
        if target == PERMIT2 and selector == SEL_PERMIT2_APPROVE:
            token, spender, amount, expiration = LIB.abi_codec.decode(
                [{"type": "address"}, {"type": "address"}, {"type": "uint160"}, {"type": "uint48"}],
                "0x" + data[10:],
            )
            self.permit2[(token.lower(), sender, spender.lower())] = (int(amount), int(expiration))
            return []
        if target == self.chain["positionManager"].lower() and selector == SEL_MODIFY:
            return self._modify(sender, data, value)
        raise Revert("0x", f"nothing to call at {to}")

    def _amounts(self, pos_pool: Pool, tl: int, tu: int, liq: int, up: bool) -> tuple[int, int]:
        sqrt = LIB.v4_math.get_sqrt_ratio_at_tick(pos_pool.tick)
        got = LIB.v4_math.get_amounts_for_liquidity_at_ticks(sqrt, tl, tu, liq, up)
        return int(got["amount0"]), int(got["amount1"])

    def _owned(self, token_id: int, sender: str) -> Position:
        pos = self.positions.get(int(token_id))
        if pos is None or pos.owner != sender:
            raise Revert(NOT_APPROVED)
        return pos

    def _pay(self, sender: str, currency: str, amount: int, logs: list[dict[str, Any]]) -> None:
        """Settle a debt of the call: native from msg.value, an ERC-20 through Permit2."""
        if currency == NATIVE:
            if self._held < amount:
                raise Revert(NOT_SETTLED, "not enough ETH sent")
            self._held -= amount
            return
        pm = self.chain["positionManager"].lower()
        p_amount, p_expiration = self.permit2.get((currency, sender, pm), (0, 0))
        if p_expiration < self.timestamp:
            raise Revert(ALLOWANCE_EXPIRED)
        if p_amount < amount:
            raise Revert(INSUFFICIENT_ALLOWANCE)
        key = f"{currency}:{sender}:{PERMIT2}"
        if self.erc20_allowances.get(key, 0) < amount:
            raise Revert("0x", "ERC20: transfer amount exceeds allowance")
        held = self.balance(sender, currency)
        if held < amount:
            raise Revert("0x", "ERC20: transfer amount exceeds balance")
        self.permit2[(currency, sender, pm)] = (p_amount - amount, p_expiration)
        self.erc20_allowances[key] -= amount
        self.fund(sender, currency, held - amount)
        manager = self.chain["poolManager"].lower()
        self.fund(manager, currency, self.balance(manager, currency) + amount)
        logs.append(erc20_transfer_log(currency, sender, manager, amount))

    def _take(self, currency: str, to: str, amount: int, logs: list[dict[str, Any]]) -> None:
        self.fund(to, currency, self.balance(to, currency) + amount)
        if currency != NATIVE:
            logs.append(erc20_transfer_log(currency, self.chain["poolManager"].lower(), to, amount))

    def _modify(self, sender: str, data: str, value: int) -> list[dict[str, Any]]:
        """``modifyLiquidities(unlockData, deadline)``: the actions, one by one, then settle."""
        codec, names = LIB.abi_codec, LIB.v4_actions.ACTION_NAMES
        unlock, deadline = codec.decode([{"type": "bytes"}, {"type": "uint256"}], "0x" + data[10:])
        if int(deadline) < self.timestamp:
            raise Revert(DEADLINE_PASSED)
        actions_hex, params = codec.decode([{"type": "bytes"}, {"type": "bytes[]"}], unlock)
        actions = [names[a] for a in bytes.fromhex(actions_hex[2:])]
        if value:
            if self.native.get(sender, 0) < value:
                raise Revert("0x", "insufficient funds for value")
            self.native[sender] -= value
        self._held = value
        pm = self.chain["positionManager"].lower()
        deltas: dict[str, int] = defaultdict(int)
        logs: list[dict[str, Any]] = []
        uint, addr, blob = {"type": "uint256"}, {"type": "address"}, {"type": "bytes"}
        u128, i24 = {"type": "uint128"}, {"type": "int24"}
        for action, param in zip(actions, params, strict=True):
            if action == "MINT_POSITION":
                key, tl, tu, liq, max0, max1, owner, _hook = codec.decode(
                    [LIB.abi.POOL_KEY_TUPLE_PARAM, i24, i24, uint, u128, u128, addr, blob], param
                )
                pool = self.pools[str(LIB.v4_pool.compute_pool_id(key)).lower()]
                owed0, owed1 = self._amounts(pool, tl, tu, liq, True)
                if owed0 > max0 or owed1 > max1:
                    raise Revert(MAX_EXCEEDED)
                token_id = self.next_token_id
                self.next_token_id += 1
                self.positions[token_id] = Position(
                    token_id, pool.pool_id, tl, tu, liq, owner.lower()
                )
                c0, c1 = key["currency0"].lower(), key["currency1"].lower()
                deltas[c0] -= owed0
                deltas[c1] -= owed1
                logs.append(nft_transfer_log(pm, NATIVE, owner, token_id))
                continue
            if action in ("INCREASE_LIQUIDITY", "DECREASE_LIQUIDITY"):
                token_id, liq, bound0, bound1, _hook = codec.decode(
                    [uint, uint, u128, u128, blob], param
                )
                pos = self._owned(token_id, sender)
                pool = self.pools[pos.pool_id]
                c0, c1 = (k.lower() for k in (pool.key["currency0"], pool.key["currency1"]))
                if action == "INCREASE_LIQUIDITY":
                    owed0, owed1 = self._amounts(pool, pos.tick_lower, pos.tick_upper, liq, True)
                    if owed0 > bound0 or owed1 > bound1:
                        raise Revert(MAX_EXCEEDED)
                    deltas[c0] += pos.fees0 - owed0
                    deltas[c1] += pos.fees1 - owed1
                    pos.liquidity += liq
                else:
                    if liq > pos.liquidity:
                        raise Revert("0x", "not enough liquidity")
                    got0, got1 = self._amounts(pool, pos.tick_lower, pos.tick_upper, liq, False)
                    if got0 < bound0 or got1 < bound1:
                        raise Revert(MIN_INSUFFICIENT)
                    deltas[c0] += got0 + pos.fees0
                    deltas[c1] += got1 + pos.fees1
                    pos.liquidity -= liq
                pos.fees0 = pos.fees1 = 0
                continue
            if action == "BURN_POSITION":
                token_id, _min0, _min1, _hook = codec.decode([uint, u128, u128, blob], param)
                pos = self._owned(token_id, sender)
                if pos.liquidity:
                    raise Revert("0x", "position not empty")
                del self.positions[int(token_id)]
                logs.append(nft_transfer_log(pm, sender, NATIVE, int(token_id)))
                continue
            if action == "SETTLE_PAIR":
                for currency in codec.decode([addr, addr], param):
                    c = currency.lower()
                    if deltas[c] > 0:
                        raise Revert(DELTA_NOT_NEGATIVE)
                    if deltas[c] < 0:
                        self._pay(sender, c, -deltas[c], logs)
                    deltas[c] = 0
                continue
            if action == "CLOSE_CURRENCY":
                (currency,) = codec.decode([addr], param)
                c = currency.lower()
                if deltas[c] < 0:
                    self._pay(sender, c, -deltas[c], logs)
                elif deltas[c] > 0:
                    self._take(c, sender, deltas[c], logs)
                deltas[c] = 0
                continue
            if action == "TAKE_PAIR":
                c0, c1, recipient = codec.decode([addr, addr, addr], param)
                for c in (c0.lower(), c1.lower()):
                    if deltas[c] < 0:
                        raise Revert(NOT_SETTLED)
                    if deltas[c] > 0:
                        self._take(c, recipient.lower(), deltas[c], logs)
                    deltas[c] = 0
                continue
            if action == "SWEEP":
                currency, recipient = codec.decode([addr, addr], param)
                if currency.lower() == NATIVE and self._held:
                    self._take(NATIVE, recipient.lower(), self._held, logs)
                    self._held = 0
                continue
            raise Revert("0x", f"unsupported action {action}")
        if any(deltas.values()):
            raise Revert(NOT_SETTLED)
        self.mined.append((sender, actions))
        return logs

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


def erc20_transfer_log(token: str, sender: str, to: str, amount: int) -> dict[str, Any]:
    return {
        "address": token,
        "topics": [ERC20_TRANSFER, _topic(sender), _topic(to)],
        "data": "0x" + format(int(amount), "064x"),
    }


def nft_transfer_log(manager: str, sender: str, to: str, token_id: int) -> dict[str, Any]:
    return {
        "address": manager,
        "topics": [LIB.abi.TOPIC_ERC721_TRANSFER, _topic(sender), _topic(to), hex(token_id)],
        "data": "0x",
    }


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


# ── LP writes: a Base world for the order pipeline (docs/lp-write.md) ──────

USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
#: ~2,000 USDC per WETH (and per ETH): WETH/ETH is currency0, USDC currency1.
TICK_2000 = -200_311
#: The wallet's positions in ``write_world``.
POS_WETH = 7001  # WETH/USDC 0.05 %, in range, with fees
POS_ETH = 7002  # ETH/USDC 0.3 % (native currency0), in range, with fees
POS_OUTSIDER = 7003  # somebody else's
POS_CLOSED = 7004  # the wallet's, emptied
POS_BARE = 7005  # WETH/USDC, in range, no fees


def weth_usdc_key() -> dict[str, Any]:
    return dict(
        LIB.v4_pool.normalize_pool_key(
            {"currency0": WETH, "currency1": USDC, "fee": 500, "tickSpacing": 10, "hooks": NATIVE}
        )
    )


def eth_usdc_key() -> dict[str, Any]:
    return dict(
        LIB.v4_pool.normalize_pool_key(
            {
                "currency0": NATIVE,
                "currency1": USDC,
                "fee": 3000,
                "tickSpacing": 60,
                "hooks": NATIVE,
            }
        )
    )


def write_world(owner: str, *, block: int = 1_000) -> World:
    """WETH/USDC and ETH/USDC pools at ~$2,000, with the wallet's positions in them."""
    world = World(spec=BASE, block=block, next_token_id=9_000)
    world.tokens = {WETH.lower(): ("WETH", 18, None), USDC.lower(): ("USDC", 6, None)}
    weth = world.add_pool(weth_usdc_key(), tick=TICK_2000)
    eth = world.add_pool(eth_usdc_key(), tick=TICK_2000)
    lo, hi = TICK_2000 - 1_000, TICK_2000 + 1_000
    world.add_position(
        weth, POS_WETH, lo // 10 * 10, hi // 10 * 10, 10**14, owner, fees0=10**15, fees1=2 * 10**6
    )
    world.add_position(
        eth, POS_ETH, lo // 60 * 60, hi // 60 * 60, 10**14, owner, fees0=2 * 10**15, fees1=10**6
    )
    world.add_position(weth, POS_OUTSIDER, lo // 10 * 10, hi // 10 * 10, 10**14, OUTSIDER)
    world.add_position(weth, POS_CLOSED, lo // 10 * 10, hi // 10 * 10, 0, owner)
    world.add_position(weth, POS_BARE, TICK_2000 - 500, TICK_2000 + 500, 10**13, owner)
    manager = world.chain["poolManager"].lower()
    world.fund(manager, WETH, 10**24)
    world.fund(manager, USDC, 10**15)
    return world


def write_prices() -> lp.PriceFn:
    return world_prices(World(), {WETH: 2000.0, USDC: 1.0, NATIVE: 2000.0})


def write_env(world: World, owner: str) -> lp.ChainEnv:
    return env_for(world, prices=write_prices(), vault={owner.lower(): "Main"})


def link(world: World, chain: Any) -> None:
    """Make ``world`` and a ``FakeChain`` share balances and ERC-20 allowances."""
    chain.native.update(world.native)
    for token, holders in world.erc20.items():
        chain.erc20.setdefault(token, {}).update(holders)
    world.native = chain.native
    world.erc20 = chain.erc20
    world.erc20_allowances = chain.allowances


def wire(world: World, chain: Any, wallet: str) -> list[dict[str, Any]]:
    """Mine every signed transaction on ``world``; returns the transactions, in order."""
    from tests.test_trading.fakes import decode_fake_raw

    mined: list[dict[str, Any]] = []

    def on_send(raw: str) -> str:
        tx = decode_fake_raw(raw)
        chain._seq += 1
        tx_hash = "0x" + format(0xC0FFEE00 + chain._seq, "x").rjust(64, "0")
        sender = wallet.lower()
        chain.nonces[sender] = int(tx.get("nonce", 0)) + 1
        world.native[sender] = world.native.get(sender, 0) - GAS_USED * GAS_PRICE
        chain.record_transaction(tx_hash, {**tx, "from": wallet})
        mined.append(tx)
        try:
            logs = world.execute(sender, str(tx["to"]), str(tx["data"]), int(tx.get("value") or 0))
            status = 1
        except Revert:
            logs, status = [], 0
        for index, entry in enumerate(logs):
            entry.update(transactionHash=tx_hash, blockNumber=hex(chain.block), logIndex=hex(index))
        chain.receipt(tx_hash, status=status, gas_used=GAS_USED, gas_price=GAS_PRICE, logs=logs)
        return tx_hash

    chain.on_send = on_send
    return mined
