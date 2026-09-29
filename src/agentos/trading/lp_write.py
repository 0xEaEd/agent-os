"""Uniswap V4 LP writes: collect fees, remove liquidity, add liquidity.

``docs/lp-write.md`` is the contract. This module plans, it never signs: a
planner reads the position or pool through a :class:`~agentos.trading.lp.ChainEnv`
(the engine's RPC and prices, the same one the LP cards use), sizes the change
with the bundled skill's exact V4 math, encodes the ``modifyLiquidities`` call
with the skill's action encoders, simulates it from the wallet and returns an
``LpPlan`` -- a JSON-safe dict that the order row keeps in ``quote_json``.

The service (``TradingService.lp_*``) parks every plan for approval. On
approval :func:`revalidate` re-reads the pool against the plan's bounds, the
engine sends the approvals the plan needs (exact amounts, never unlimited)
and then the call :func:`modify_liquidities_call` rebuilds from the plan --
the rebuilt call must hash to the ``planHash`` the user approved.

Everything here does blocking HTTP through the skill's client and runs in a
worker thread.
"""

from __future__ import annotations

import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from agentos.trading import lp
from agentos.trading.chains import NATIVE_ADDRESS, is_native
from agentos.trading.service import TradingError

#: Slippage when the caller names none, in percent.
DEFAULT_SLIPPAGE_PCT = 1.0
#: The widest slippage a plan accepts (the agent's own ceiling is lower).
MAX_SLIPPAGE_PCT = 50.0
#: The range an ``add`` uses when none is given: ±20 % around the current price.
DEFAULT_RANGE = "pct:20"
#: ``above`` / ``below`` with no percentage.
ONE_SIDED_DEFAULT_PCT = 20.0
#: ``modifyLiquidities`` deadline: latest block timestamp + this, set at execution.
DEADLINE_S = 600
#: A Permit2 allowance to the PositionManager is granted for this long.
PERMIT2_EXPIRY_S = 30 * 60
#: A Permit2 allowance that lapses within this is treated as already lapsed.
PERMIT2_EXPIRY_MARGIN_S = 120
UINT160_MAX = (1 << 160) - 1
UINT128_MAX = (1 << 128) - 1
#: Gas kept back from a native deposit (units, priced at the node's gas price
#: with 2x headroom): the order still has to pay for itself and its approvals.
NATIVE_GAS_RESERVE_UNITS = 600_000
#: Liquidity used to read a range's token ratio before a USD deposit is sized.
_REF_LIQUIDITY = 10**30
#: ``eth_simulateV1`` is asked for; a node without it answers with ``eth_call``.
SIMULATE_V1 = "eth_simulateV1"

OPS = ("collect", "remove", "add")
ORDER_KIND = {"collect": "lp_collect", "remove": "lp_remove", "add": "lp_add"}

_SUFFIX = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}
_MCAP = r"\$?\s*(\d+(?:\.\d+)?(?:e\+?\d+)?)\s*([kmbt]?)"
_MCAP_RANGE = re.compile(rf"^{_MCAP}\s*(?:-|\.\.|:|to)\s*{_MCAP}$", re.IGNORECASE)
_MCAP_ONE = re.compile(rf"^{_MCAP}$", re.IGNORECASE)


def _range_error(message: str, **details: Any) -> TradingError:
    return TradingError("trading.lp.range_invalid", message, details=details or None)


# ── ranges ──────────────────────────────────────────────────────────────────


def parse_mcap(text: str) -> float:
    """``2M``, ``2.5m``, ``750k``, ``1e6``, ``$3B`` → USD."""
    match = _MCAP_ONE.match(str(text).strip().replace(",", ""))
    if match is None:
        raise _range_error(f"not a market cap: {text!r} (use 2M, 2.5m, 750k or 1e6)")
    value = float(match.group(1)) * _SUFFIX[match.group(2).lower()]
    if not value > 0 or not math.isfinite(value):
        raise _range_error(f"market cap must be above zero: {text!r}")
    return value


def parse_range(text: str | None) -> dict[str, Any]:
    """A ``--range`` value → ``{"kind": "mcap"|"pct"|"full"|"ticks", ...}``.

    ``mcap:2M-10M`` (either order), ``pct:20`` (±20 % around the current
    price), ``full``, ``ticks:-201000:-180000``, and the one-sided
    ``above:20`` (all base token: from just above the price up 20 %) and
    ``below:20`` (all quote token: from just below the price down 20 %);
    a bare ``above`` / ``below`` is 20 %. ``None`` is the default range.
    """
    raw = (text or DEFAULT_RANGE).strip()
    kind, _, rest = raw.partition(":")
    kind = kind.strip().lower()
    rest = rest.strip()
    if kind == "full" and not rest:
        return {"kind": "full", "text": "full"}
    if kind == "pct":
        try:
            pct = float(rest.rstrip("%"))
        except ValueError:
            raise _range_error(f"pct:N needs a number, got {rest!r}") from None
        if not 0 < pct < 100:
            raise _range_error("pct:N must be above 0 and below 100")
        return {"kind": "pct", "pct": pct, "text": f"pct:{pct:g}"}
    if kind in ("above", "below"):
        if not rest:
            pct = ONE_SIDED_DEFAULT_PCT
        else:
            try:
                pct = float(rest.rstrip("%"))
            except ValueError:
                raise _range_error(f"{kind}:N needs a number, got {rest!r}") from None
        if not math.isfinite(pct) or pct <= 0:
            raise _range_error(f"{kind}:N must be above 0")
        if kind == "below" and pct >= 100:
            raise _range_error("below:N must be below 100 (the price cannot fall 100 %)")
        return {"kind": kind, "pct": pct, "text": f"{kind}:{pct:g}"}
    if kind == "mcap":
        match = _MCAP_RANGE.match(rest.replace(",", ""))
        if match is None:
            raise _range_error(f"mcap:LO-HI needs two market caps, got {rest!r} (e.g. mcap:2M-10M)")
        lo = parse_mcap(match.group(1) + match.group(2))
        hi = parse_mcap(match.group(3) + match.group(4))
        lo, hi = min(lo, hi), max(lo, hi)
        if lo == hi:
            raise _range_error("mcap:LO-HI describes a zero-width band")
        return {"kind": "mcap", "lo": lo, "hi": hi, "text": f"mcap:{lo:g}-{hi:g}"}
    if kind == "ticks":
        parts = [p.strip() for p in rest.split(":")]
        if len(parts) != 2:
            raise _range_error(f"ticks:LO:HI needs two ticks, got {rest!r}")
        try:
            lo, hi = int(parts[0]), int(parts[1])
        except ValueError:
            raise _range_error(f"ticks must be integers, got {rest!r}") from None
        return {"kind": "ticks", "lo": lo, "hi": hi, "text": f"ticks:{lo}:{hi}"}
    raise _range_error(
        f"unknown range {raw!r}: use mcap:LO-HI, pct:N, above[:N], below[:N], full or ticks:LO:HI",
    )


def resolve_ticks(side: lp.Side, state: dict[str, Any], spec: dict[str, Any]) -> tuple[int, int]:
    """The (tickLower, tickUpper) a parsed range means in this pool, on its spacing."""
    m = lp.unilp().v4_math
    spacing = int(state["poolKey"]["tickSpacing"])
    lo_usable, hi_usable = m.min_usable_tick(spacing), m.max_usable_tick(spacing)
    kind = spec["kind"]
    if kind == "full":
        return lo_usable, hi_usable
    if kind == "ticks":
        lo, hi = int(spec["lo"]), int(spec["hi"])
        misaligned = [t for t in (lo, hi) if t % spacing]
        if misaligned:
            raise _range_error(
                f"ticks must be multiples of the pool's tick spacing {spacing}: try "
                f"ticks:{m.snap_tick(lo, spacing, 'down')}:{m.snap_tick(hi, spacing, 'up')}",
                tickSpacing=spacing,
            )
    elif kind == "pct":
        step = math.log(1.0001)
        up = math.log(1 + spec["pct"] / 100) / step
        down = math.log(1 - spec["pct"] / 100) / step
        tick = int(state["tick"])
        # A base token's price rises with the tick when it is currency0 and
        # falls with it when it is currency1.
        if side.base_is_currency1:
            raw_lo, raw_hi = tick - up, tick - down
        else:
            raw_lo, raw_hi = tick + down, tick + up
        lo = m.snap_tick(math.floor(raw_lo), spacing, "down")
        hi = m.snap_tick(math.ceil(raw_hi), spacing, "up")
    elif kind in ("above", "below"):
        lo, hi = _one_sided_ticks(side, state, kind, float(spec["pct"]))
    elif kind == "mcap":
        if not side.has_supply:
            raise _range_error(
                f"{side.base['symbol']} has no readable total supply, so a market-cap range "
                "cannot be placed; use pct:N or ticks:LO:HI"
            )
        if side.quote_usd is None:
            raise _range_error(
                f"no USD price for {side.quote['symbol']}, so a market-cap range cannot be "
                "placed; use pct:N or ticks:LO:HI"
            )
        kwargs = side.math_kwargs()
        a = m.tick_at_mcap(spec["lo"], **kwargs)
        b = m.tick_at_mcap(spec["hi"], **kwargs)
        lo = m.snap_tick(min(a, b), spacing, "down")
        hi = m.snap_tick(max(a, b), spacing, "up")
    else:  # pragma: no cover - parse_range only makes the six above
        raise _range_error(f"unknown range kind {kind!r}")
    if lo >= hi:
        raise _range_error(f"the range is empty after snapping to tick spacing {spacing}")
    if lo < lo_usable or hi > hi_usable:
        raise _range_error(
            f"ticks must lie within {lo_usable}..{hi_usable} for tick spacing {spacing}"
        )
    return lo, hi


def _one_sided_ticks(
    side: lp.Side, state: dict[str, Any], kind: str, pct: float
) -> tuple[int, int]:
    """``above:N`` / ``below:N`` in this pool: a band on one side of the current tick only.

    ``above`` is the base token's price from now up ``pct`` %, which holds only
    the base token; ``below`` is its price from now down ``pct`` %, only the
    quote. Which way that runs in ticks depends on whether the base is
    currency0 (price rises with the tick) or currency1 (falls with it). The
    band is snapped outward and then pulled off the current tick
    (``pull_off_current_tick``), so it never straddles it; a ``pct`` narrower
    than one tick spacing still gets one spacing.
    """
    m = lp.unilp().v4_math
    spacing = int(state["poolKey"]["tickSpacing"])
    tick = int(state["tick"])
    step = math.log(1.0001)
    frac = pct / 100
    width = math.log(1 + frac) / step if kind == "above" else -math.log(1 - frac) / step
    # The currency the range holds: the base for "above", the quote for "below".
    principal = "currency0" if (kind == "above") != side.base_is_currency1 else "currency1"
    floor_tick = math.floor(tick / spacing) * spacing
    if principal == "currency0":  # the range sits above the current tick
        lo = floor_tick
        hi = max(math.ceil(math.ceil(tick + width) / spacing) * spacing, floor_tick + 2 * spacing)
    else:  # below it
        hi = floor_tick + spacing
        lo = min(math.floor(math.floor(tick - width) / spacing) * spacing, floor_tick - spacing)
    try:
        lo, hi = m.pull_off_current_tick(tick, lo, hi, spacing, principal)
    except RuntimeError as exc:  # pragma: no cover - the band is always wide enough
        raise _range_error(str(exc)) from None
    return int(lo), int(hi)


# ── chain reads ─────────────────────────────────────────────────────────────


def _hex_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    text = str(value or "0x0")
    return int(text, 16) if text.startswith("0x") else int(text)


def block_timestamp(env: lp.ChainEnv) -> int:
    block = env.client.get_block("latest") or {}
    return _hex_int(block.get("timestamp"))


def balances(env: lp.ChainEnv, wallet: str, currencies: list[str]) -> dict[str, int]:
    """Raw balances of ``wallet``, lower-cased currency → amount (native included)."""
    lib = env.lib
    out: dict[str, int] = {}
    tokens = [c for c in dict.fromkeys(c.lower() for c in currencies) if not is_native(c)]
    if any(is_native(c) for c in currencies):
        out[NATIVE_ADDRESS] = _hex_int(env.client.request("eth_getBalance", [wallet, "latest"]))
    if tokens:
        results = env.client.multicall(
            [
                {
                    "address": lib.hexutil.checksum_address(t),
                    "abi": lib.abi.ERC20_ABI,
                    "functionName": "balanceOf",
                    "args": [lib.hexutil.checksum_address(wallet)],
                }
                for t in tokens
            ],
            allow_failure=False,
        )
        for token, result in zip(tokens, results, strict=True):
            out[token] = int(result["result"])
    return out


def allowances(env: lp.ChainEnv, wallet: str, token: str) -> tuple[int, int, int]:
    """(ERC-20 allowance to Permit2, Permit2 amount to the PositionManager, its expiration)."""
    lib = env.lib
    chain = env.chain
    hx = lib.hexutil
    owner = hx.checksum_address(wallet)
    address = hx.checksum_address(token)
    erc20, permit2 = env.client.multicall(
        [
            {
                "address": address,
                "abi": lib.abi.ERC20_ABI,
                "functionName": "allowance",
                "args": [owner, hx.checksum_address(chain["permit2"])],
            },
            {
                "address": hx.checksum_address(chain["permit2"]),
                "abi": lib.abi.PERMIT2_ABI,
                "functionName": "allowance",
                "args": [owner, address, hx.checksum_address(chain["positionManager"])],
            },
        ],
        allow_failure=False,
    )
    amount, expiration, _nonce = permit2["result"]
    return int(erc20["result"]), int(amount), int(expiration)


def approval_steps(
    env: lp.ChainEnv, wallet: str, needs: list[tuple[dict[str, Any], int]], now: int
) -> list[dict[str, Any]]:
    """What an ``add`` must approve before it can settle, for each ERC-20 it deposits.

    Two legs per token, as the PositionManager pulls through Permit2:
    ``ERC20.approve(Permit2)`` and ``Permit2.approve(token, PositionManager)``.
    ``amountRaw`` is the slippage maximum -- the exact most the call can take.
    """
    steps: list[dict[str, Any]] = []
    for meta, amount in needs:
        if amount <= 0 or is_native(meta["address"]):
            continue
        erc20, p2_amount, p2_expiration = allowances(env, wallet, meta["address"])
        common = {"token": meta["address"], "symbol": meta["symbol"], "amountRaw": str(amount)}
        steps.append({**common, "step": "erc20->permit2", "needed": erc20 < amount})
        lapsed = p2_expiration < now + PERMIT2_EXPIRY_MARGIN_S
        steps.append({**common, "step": "permit2->posm", "needed": p2_amount < amount or lapsed})
    return steps


def approval_call(env: lp.ChainEnv, step: dict[str, Any], expiration: int) -> tuple[str, str]:
    """(to, calldata) of one approval step, for exactly ``step.amountRaw``."""
    lib = env.lib
    hx = lib.hexutil
    chain = env.chain
    amount = int(step["amountRaw"])
    token = hx.checksum_address(step["token"])
    if step["step"] == "erc20->permit2":
        data = lib.abi_codec.encode_function_data(
            lib.abi.ERC20_ABI, "approve", [hx.checksum_address(chain["permit2"]), amount]
        )
        return token, data
    if amount > UINT160_MAX:
        raise TradingError("trading.invalid", "approval amount does not fit Permit2's uint160")
    data = lib.abi_codec.encode_function_data(
        lib.abi.PERMIT2_ABI,
        "approve",
        [token, hx.checksum_address(chain["positionManager"]), amount, int(expiration)],
    )
    return hx.checksum_address(chain["permit2"]), data


def simulate(env: lp.ChainEnv, wallet: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate ``calls`` in order from ``wallet``; the verdict is the last one's.

    ``eth_simulateV1`` runs them in one block with state carried over, so an
    ``add`` whose approvals are not on chain yet is simulated *after* them. A
    node without it gets a plain ``eth_call`` of a single call (value
    included -- the skill's own fallback drops it, and a native deposit then
    fails to settle); several calls without ``eth_simulateV1`` cannot be
    simulated at all and say so (``ok: None``) -- the engine simulates the
    real transaction again right before it signs.
    """
    lib = env.lib
    hx = lib.hexutil
    payload = []
    for call in calls:
        item = {
            "from": hx.checksum_address(wallet),
            "to": hx.checksum_address(call["to"]),
            "data": call["data"],
        }
        if int(call.get("value") or 0) > 0:
            item["value"] = hex(int(call["value"]))
        payload.append(item)
    try:
        result = env.client.request(
            SIMULATE_V1,
            [
                {
                    "blockStateCalls": [{"calls": payload}],
                    "traceTransfers": True,
                    "validation": False,
                },
                "latest",
            ],
        )
        block = result[0] if isinstance(result, list) else result
        entries = (block or {}).get("calls") or []
        if len(entries) == len(payload):
            total = 0
            for index, entry in enumerate(entries):
                used = _hex_int(entry.get("gasUsed")) if entry.get("gasUsed") else 0
                total += used
                if entry.get("status") != "0x1":
                    error = entry.get("error") or {}
                    revert = {"data": error.get("data"), "message": error.get("message")}
                    return {
                        "ok": False,
                        "gasUsed": used or None,
                        "method": SIMULATE_V1,
                        "revert": lib.simulate.describe_revert(revert) or "reverted",
                        "failedCall": "approval" if index < len(entries) - 1 else "main",
                    }
            last = entries[-1]
            return {
                "ok": True,
                "gasUsed": _hex_int(last["gasUsed"]) if last.get("gasUsed") else None,
                "gasTotal": total or None,
                "method": SIMULATE_V1,
                "revert": None,
            }
    except Exception as exc:  # noqa: BLE001 - a node without eth_simulateV1 falls through
        lp.log.debug("trading.lp_simulate_v1_unavailable", chain=env.spec.key, error=str(exc))
    if len(payload) > 1:
        return {
            "ok": None,
            "gasUsed": None,
            "method": "skipped",
            "revert": None,
            "note": "the node has no eth_simulateV1 and approvals are still pending; "
            "the transaction is simulated again before it is signed",
        }
    try:
        env.client.request("eth_call", [payload[0], "latest"])
    except Exception as exc:  # noqa: BLE001 - classified below
        if isinstance(exc, lib.rpc.RpcError) and exc.answered:
            revert = {"data": exc.data, "message": str(exc)}
            return {
                "ok": False,
                "gasUsed": None,
                "method": "eth_call",
                "revert": lib.simulate.describe_revert(revert) or "reverted",
                "failedCall": "main",
            }
        raise TradingError(
            "trading.rpc",
            f"{env.spec.name}: the node refused to simulate the call ({exc}); try again",
        ) from exc
    return {"ok": True, "gasUsed": None, "method": "eth_call", "revert": None}


def _gas_usd(env: lp.ChainEnv, gas: int | None) -> float | None:
    if not gas:
        return None
    try:
        price = _hex_int(env.client.request("eth_gasPrice", []))
    except Exception:  # noqa: BLE001 - an estimate, never fatal
        return None
    native = lp.usd_prices(env, [NATIVE_ADDRESS]).get(NATIVE_ADDRESS)
    if native is None:
        return None
    return lp.finite(gas * price / 10**18 * native)


# ── encoding ────────────────────────────────────────────────────────────────


def with_slippage_up(amount: int, bps: int) -> int:
    """A maximum: ``amount`` plus ``bps``, rounded up (the skill's rule)."""
    if amount <= 0:
        return 0
    return int(amount) * (10_000 + int(bps)) // 10_000 + 1


def with_slippage_down(amount: int, bps: int) -> int:
    """A minimum: ``amount`` less ``bps``, rounded down (the skill's rule)."""
    return int(amount) * (10_000 - int(bps)) // 10_000


def _bps(slippage_pct: float | None) -> int:
    return int(round(float(slippage_pct or 0.0) * 100))


def _orient(plan: dict[str, Any]) -> bool:
    """True when the plan's base token is the pool's currency1."""
    key = plan["pool"]["poolKey"]
    return str(plan["token"]["address"]).lower() == str(key["currency1"]).lower()


def _to_currency(plan: dict[str, Any], base: int, quote: int) -> tuple[int, int]:
    return (quote, base) if _orient(plan) else (base, quote)


def encode_actions(plan: dict[str, Any]) -> dict[str, Any]:
    """``{actions, params, value}`` for a plan, through the skill's encoders."""
    lib = lp.unilp()
    va = lib.v4_actions
    key = plan["pool"]["poolKey"]
    recipient = plan["wallet"]
    token_id = int(plan["tokenId"]) if plan.get("tokenId") is not None else None
    liquidity = int(plan["liquidity"])
    b0, b1 = _to_currency(plan, int(plan["bounds"]["base"]), int(plan["bounds"]["quote"]))
    op = plan["op"]
    if op == "collect":
        assert token_id is not None
        built: dict[str, Any] = va.build_collect_plan(key, token_id, recipient)
    elif op == "remove":
        assert token_id is not None
        if plan.get("burn"):
            built = va.build_burn_plan(key, token_id, liquidity, b0, b1, recipient)
        else:
            built = va.build_decrease_plan(key, token_id, liquidity, b0, b1, recipient)
    elif op == "add" and not plan.get("increase"):
        rng = plan["range"]
        built = va.build_mint_plan(
            key, rng["tickLower"], rng["tickUpper"], liquidity, b0, b1, recipient
        )
    elif op == "add" and "CLOSE_CURRENCY" in (plan.get("actions") or []):
        assert token_id is not None
        built = _increase_closing(key, token_id, liquidity, b0, b1, recipient)
    else:
        assert token_id is not None
        built = va.build_increase_plan(key, token_id, liquidity, b0, b1, recipient)
    return built


def _increase_closing(
    key: dict[str, Any], token_id: int, liquidity: int, max0: int, max1: int, recipient: str
) -> dict[str, Any]:
    """INCREASE → CLOSE_CURRENCY × 2 (→ SWEEP): for a position with fees to collect.

    An increase credits the position's uncollected fees against what it owes;
    when a currency's fees exceed what the new liquidity needs of it, its delta
    is positive and SETTLE_PAIR reverts (DeltaNotNegative). CLOSE_CURRENCY
    settles a negative delta and takes a positive one, so the fees come home.
    """
    lib = lp.unilp()
    va = lib.v4_actions
    actions = [
        va.ACTIONS["INCREASE_LIQUIDITY"],
        va.ACTIONS["CLOSE_CURRENCY"],
        va.ACTIONS["CLOSE_CURRENCY"],
    ]
    params = [
        va.encode_increase_liquidity(token_id, liquidity, max0, max1),
        lib.abi_codec.encode([{"type": "address"}], [va.checksum_address(key["currency0"])]),
        lib.abi_codec.encode([{"type": "address"}], [va.checksum_address(key["currency1"])]),
    ]
    native = va.is_native_currency(key["currency0"])
    if native:
        actions.append(va.ACTIONS["SWEEP"])
        params.append(va.encode_sweep(key["currency0"], recipient))
    return {"actions": actions, "params": params, "value": int(max0) if native else 0}


def plan_hash(chain_id: int, to: str, built: dict[str, Any]) -> str:
    """First 4 bytes of keccak over the canonical call (everything but the deadline)."""
    from eth_utils import keccak

    canonical = json.dumps(
        {
            "actions": [int(a) for a in built["actions"]],
            "chainId": int(chain_id),
            "params": [str(p).lower() for p in built["params"]],
            "to": to.lower(),
            "value": str(int(built["value"])),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "0x" + keccak(canonical.encode("utf-8"))[:4].hex()


def modify_liquidities_call(plan: dict[str, Any], deadline: int) -> tuple[str, str, int]:
    """``(to, data, value)`` of ``PositionManager.modifyLiquidities`` for ``plan``.

    Rebuilt from the plan's fields, then checked against the ``planHash`` the
    user approved: a plan edited between approval and signing is refused.
    """
    lib = lp.unilp()
    built = encode_actions(plan)
    to = str(plan["positionManager"])
    if plan_hash(int(plan["chain"]["id"]), to, built) != plan.get("planHash"):
        raise TradingError(
            "trading.invalid", "the LP call no longer matches the approved plan; plan it again"
        )
    unlock = lib.v4_actions.encode_unlock_data(built["actions"], built["params"])
    data = lib.abi_codec.encode_function_data(
        lib.abi.POSITION_MANAGER_ABI, "modifyLiquidities", [unlock, int(deadline)]
    )
    return lib.hexutil.checksum_address(to), data, int(built["value"])


def _action_names(built: dict[str, Any]) -> list[str]:
    names = lp.unilp().v4_actions.ACTION_NAMES
    return [str(names.get(int(a), hex(int(a)))) for a in built["actions"]]


# ── shared plan pieces ──────────────────────────────────────────────────────


def _amounts(side: lp.Side, amount0: int, amount1: int) -> dict[str, Any]:
    base_raw, quote_raw = side.split(int(amount0), int(amount1))
    out: dict[str, Any] = {
        "base": lp.amount_json(base_raw, side.base["decimals"], side.base_usd),
        "quote": lp.amount_json(quote_raw, side.quote["decimals"], side.quote_usd),
    }
    out["usd"] = lp._sum_usd(out["base"]["usd"], out["quote"]["usd"])
    return out


def _pool_json(state: dict[str, Any]) -> dict[str, Any]:
    key = state["poolKey"]
    hooks = str(key["hooks"])
    return {
        "poolId": str(state["poolId"]).lower(),
        "poolKey": dict(key),
        "tick": int(state["tick"]),
        "sqrtPriceX96": str(int(state["sqrtPriceX96"])),
        "feePct": lp.fee_pct(key),
        "hook": None if int(hooks, 16) == 0 else hooks,
        "tickSpacing": int(key["tickSpacing"]),
        "liquidity": str(state.get("activeLiquidity") or 0),
    }


def _pool_state(env: lp.ChainEnv, pool_id: str, key: dict[str, Any]) -> dict[str, Any]:
    states = lp.pool_states(env, [{"poolId": pool_id, "poolKey": key}])
    if not states:
        raise TradingError(
            "trading.lp.not_found", f"pool {pool_id} is not initialised on {env.spec.name}"
        )
    return states[0]


def _is_empty_key(key: dict[str, Any]) -> bool:
    return (
        int(str(key["currency0"]), 16) == 0
        and int(str(key["currency1"]), 16) == 0
        and int(key["fee"]) == 0
        and int(key["tickSpacing"]) == 0
    )


def _wallet_label(env: lp.ChainEnv, wallet: str) -> str:
    return env.vault.get(wallet.lower()) or env.lib.fmt.short(wallet)


def _position(
    env: lp.ChainEnv, token_id: int
) -> tuple[dict[str, Any], dict[str, Any], lp.Side, tuple[int, int] | None]:
    """(position, pool state, orientation, uncollected fees) of one NFT."""
    if token_id <= 0:
        raise TradingError("trading.invalid", "tokenId must be a positive integer")
    pos = lp._load_position(env, token_id)
    if _is_empty_key(pos["poolKey"]):
        raise TradingError(
            "trading.lp.not_found",
            f"position #{token_id} does not exist on {env.spec.name} (burned, or never minted)",
            details={"tokenId": str(token_id), "chainId": env.spec.chain_id},
        )
    pool_id = env.lib.v4_pool.compute_pool_id(pos["poolKey"])
    state = _pool_state(env, pool_id, pos["poolKey"])
    side = lp.with_implied_base_price(lp.make_side(env, pos["poolKey"]), state, liquid_only=False)
    fees = lp.fees_owed(env, [(pos, state)]).get(int(token_id))
    if fees is None:
        env.warn(f"uncollected fees of #{token_id} could not be read")
    return pos, state, side, fees


def _check_owner(
    env: lp.ChainEnv, pos: dict[str, Any], wallet: str | None, *, check_owner: bool
) -> str:
    """The owner, checksummed; refused unless it is a vault wallet (and ``wallet``)."""
    hx = env.lib.hexutil
    owner = hx.checksum_address(str(pos["owner"]))
    if not check_owner:
        return str(owner)
    if owner.lower() not in env.vault or (wallet and wallet.lower() != owner.lower()):
        raise TradingError(
            "trading.lp.not_owner",
            f"position #{pos['tokenId']} on {env.spec.name} is held by {owner}, which is not "
            + (f"the wallet {hx.checksum_address(wallet)}" if wallet else "a wallet in this vault"),
            details={
                "tokenId": str(pos["tokenId"]),
                "owner": owner,
                "chainId": env.spec.chain_id,
            },
        )
    return str(owner)


def _require_open(env: lp.ChainEnv, pos: dict[str, Any]) -> None:
    if int(pos["liquidity"]) <= 0:
        raise TradingError(
            "trading.lp.position_closed",
            f"position #{pos['tokenId']} on {env.spec.name} holds no liquidity (closed); "
            "there is nothing to collect or remove",
            details={"tokenId": str(pos["tokenId"]), "chainId": env.spec.chain_id},
        )


def _finish(
    env: lp.ChainEnv,
    plan: dict[str, Any],
    *,
    wallet: str,
    approvals_first: list[dict[str, Any]] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Encode, hash and simulate a plan; a simulated revert refuses it."""
    lib = env.lib
    built = encode_actions(plan)
    plan["actions"] = _action_names(built)
    plan["value"] = str(int(built["value"]))
    pm = lib.hexutil.checksum_address(env.chain["positionManager"])
    plan["planHash"] = plan_hash(env.spec.chain_id, pm, built)
    stamp = now if now is not None else block_timestamp(env)
    unlock = lib.v4_actions.encode_unlock_data(built["actions"], built["params"])
    data = lib.abi_codec.encode_function_data(
        lib.abi.POSITION_MANAGER_ABI, "modifyLiquidities", [unlock, stamp + DEADLINE_S]
    )
    calls: list[dict[str, Any]] = []
    for step in approvals_first or []:
        to, call_data = approval_call(env, step, stamp + PERMIT2_EXPIRY_S)
        calls.append({"to": to, "data": call_data, "value": 0})
    calls.append({"to": pm, "data": data, "value": int(built["value"])})
    sim = simulate(env, wallet, calls)
    plan["simulation"] = {
        "ok": sim["ok"],
        "gasUsed": sim.get("gasUsed"),
        "method": sim["method"],
        "revert": sim.get("revert"),
    }
    if sim.get("note"):
        plan["simulation"]["note"] = sim["note"]
    if sim["ok"] is False:
        where = "an approval" if sim.get("failedCall") == "approval" else "the LP call"
        raise TradingError(
            "trading.simulation_failed",
            f"{where} reverted in simulation on {env.spec.name}: {sim.get('revert')}",
            details={"simulation": plan["simulation"], "planHash": plan["planHash"]},
        )
    plan["gasUsd"] = _gas_usd(env, sim.get("gasTotal") or sim.get("gasUsed"))
    plan["warnings"] = list(env.warnings)
    return plan


def _base_plan(
    env: lp.ChainEnv,
    op: str,
    *,
    wallet: str,
    token_id: int | None,
    state: dict[str, Any],
    side: lp.Side,
    tick_lower: int,
    tick_upper: int,
) -> dict[str, Any]:
    return {
        "op": op,
        "chain": lp.chain_json(env.spec),
        "tokenId": str(token_id) if token_id is not None else None,
        "increase": False,
        "wallet": env.lib.hexutil.checksum_address(wallet),
        "positionManager": env.lib.hexutil.checksum_address(env.chain["positionManager"]),
        "pool": _pool_json(state),
        "token": side.token_json("base"),
        "quote": side.token_json("quote"),
        "range": side.range_json(tick_lower, tick_upper),
        "status": None,
        "liquidity": "0",
        "pct": None,
        "burn": False,
        "expected": None,
        "bounds": {"base": "0", "quote": "0"},
        "fees": None,
        "positionValueUsd": None,
        "approvals": [],
        "oneSided": None,
        "simulation": None,
        "gasUsd": None,
        "slippagePct": None,
        "planHash": None,
        "createdAtBlock": int(env.block),
    }


def _position_value(
    side: lp.Side, state: dict[str, Any], pos: dict[str, Any], fees: tuple[int, int] | None
) -> tuple[dict[str, Any] | None, float | None]:
    """(fees JSON, the position's value in USD before the change)."""
    m = lp.unilp().v4_math
    principal = m.get_amounts_for_liquidity_at_ticks(
        int(state["sqrtPriceX96"]),
        int(pos["tickLower"]),
        int(pos["tickUpper"]),
        int(pos["liquidity"]),
    )
    whole = _amounts(side, principal["amount0"], principal["amount1"])
    fees_json = _amounts(side, *fees) if fees is not None else None
    value = lp._sum_usd(whole["usd"], fees_json["usd"] if fees_json else None)
    return fees_json, value


# ── planners ────────────────────────────────────────────────────────────────


def plan_collect(
    env: lp.ChainEnv,
    token_id: int,
    wallet: str | None = None,
    *,
    check_owner: bool = True,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Collect a position's fees: a zero-liquidity decrease and a take, no slippage.

    ``wallet`` (optional) is the wallet the caller expects to own it; the owner
    must be a vault wallet either way. ``check_owner=False`` is for dry runs of
    somebody else's position (simulated from its real owner). A position whose
    uncollected fees read as zero on both sides is refused with
    ``trading.lp.nothing_to_collect`` -- a transaction that pays gas to move
    nothing -- unless ``allow_empty``; fees that could not be read are not zero.
    """
    pos, state, side, fees = _position(env, token_id)
    owner = _check_owner(env, pos, wallet, check_owner=check_owner)
    _require_open(env, pos)
    if fees is not None and not any(fees) and not allow_empty:
        raise TradingError(
            "trading.lp.nothing_to_collect",
            f"position #{token_id} on {env.spec.name} has no uncollected fees; collecting "
            "would only pay gas (pass allowEmpty / --allow-empty to do it anyway)",
            details={"tokenId": str(token_id), "chainId": env.spec.chain_id},
        )
    plan = _base_plan(
        env,
        "collect",
        wallet=owner,
        token_id=token_id,
        state=state,
        side=side,
        tick_lower=int(pos["tickLower"]),
        tick_upper=int(pos["tickUpper"]),
    )
    fees_json, value = _position_value(side, state, pos, fees)
    zero = (0, 0)
    plan["status"] = side.status(
        int(state["tick"]), int(pos["tickLower"]), int(pos["tickUpper"]), int(pos["liquidity"])
    )
    plan["expected"] = _amounts(side, *(fees or zero))
    if fees is None:
        plan["expected"]["usd"] = None
    plan["fees"] = fees_json
    plan["positionValueUsd"] = value
    return _finish(env, plan, wallet=owner)


def plan_remove(
    env: lp.ChainEnv,
    token_id: int,
    pct: float = 100.0,
    slippage_pct: float | None = None,
    wallet: str | None = None,
    *,
    check_owner: bool = True,
) -> dict[str, Any]:
    """Take ``pct`` % of a position's liquidity out (100 burns the NFT), fees included.

    Minimums are the principal the removed liquidity holds at today's price
    less the slippage; V4 pays every uncollected fee on any decrease, on top.
    """
    m = env.lib.v4_math
    pct = float(pct)
    if not 0 < pct <= 100:
        raise TradingError("trading.invalid", "pct must be above 0 and at most 100")
    slippage = _slippage(slippage_pct)
    pos, state, side, fees = _position(env, token_id)
    owner = _check_owner(env, pos, wallet, check_owner=check_owner)
    _require_open(env, pos)
    whole = int(pos["liquidity"])
    burn = pct >= 100
    liquidity = whole if burn else whole * int(round(pct * 100)) // 10_000
    if liquidity <= 0:
        raise TradingError("trading.invalid", f"{pct:g}% of #{token_id} is no liquidity at all")
    tl, tu = int(pos["tickLower"]), int(pos["tickUpper"])
    principal = m.get_amounts_for_liquidity_at_ticks(int(state["sqrtPriceX96"]), tl, tu, liquidity)
    bps = _bps(slippage)
    min0 = with_slippage_down(principal["amount0"], bps)
    min1 = with_slippage_down(principal["amount1"], bps)
    plan = _base_plan(
        env, "remove", wallet=owner, token_id=token_id, state=state, side=side,
        tick_lower=tl, tick_upper=tu,
    )  # fmt: skip
    fees_json, value = _position_value(side, state, pos, fees)
    got0 = principal["amount0"] + (fees[0] if fees else 0)
    got1 = principal["amount1"] + (fees[1] if fees else 0)
    plan["status"] = side.status(int(state["tick"]), tl, tu, whole)
    plan["liquidity"] = str(liquidity)
    plan["pct"] = pct
    plan["burn"] = burn
    plan["expected"] = _amounts(side, got0, got1)
    if fees is None:
        plan["expected"]["usd"] = None
    base_min, quote_min = side.split(min0, min1)
    plan["bounds"] = {"base": str(base_min), "quote": str(quote_min)}
    plan["fees"] = fees_json
    plan["positionValueUsd"] = value
    plan["slippagePct"] = slippage
    return _finish(env, plan, wallet=owner)


def _slippage(slippage_pct: float | None) -> float:
    value = DEFAULT_SLIPPAGE_PCT if slippage_pct is None else float(slippage_pct)
    if not 0 <= value <= MAX_SLIPPAGE_PCT or not math.isfinite(value):
        raise TradingError(
            "trading.invalid", f"slippage must be between 0 and {MAX_SLIPPAGE_PCT:g} %"
        )
    return value


def _to_raw(text: str | None, decimals: int, name: str) -> int | None:
    if text is None or str(text).strip() == "":
        return None
    try:
        value = Decimal(str(text).strip())
    except InvalidOperation:
        raise TradingError("trading.invalid", f"{name} must be a number, got {text!r}") from None
    if not value.is_finite() or value <= 0:
        raise TradingError("trading.invalid", f"{name} must be above zero")
    raw = int(lp.unilp().hexutil.parse_units(format(value, "f"), int(decimals)))
    if raw <= 0:
        raise TradingError("trading.invalid", f"{name} is below one base unit of the token")
    return raw


def _pick_pool(
    env: lp.ChainEnv, target: str, quote: str | None = None, fee: int | None = None
) -> tuple[dict[str, Any], lp.Side]:
    """The pool an ``add`` goes into: a poolId, or the best-ranked live pool of a token.

    ``quote`` keeps the pools pairing the token with it; ``fee`` those on that
    fee tier (the deepest wins when several share it, e.g. other hooks).
    """
    lib = env.lib
    if lp._is_pool_id(target):
        pool_id = target.strip().lower()
        key = lp.pool_key_for_id(env, pool_id)
        if lib.v4_pool.compute_pool_id(key).lower() != pool_id:
            raise TradingError("trading.lp.not_found", f"PoolKey does not hash to {pool_id}")
        state = _pool_state(env, pool_id, key)
        lp.check_pool_fee(state, fee)
        return state, lp.with_implied_base_price(lp.make_side(env, state["poolKey"]), state)
    token = NATIVE_ADDRESS if is_native(target) else lib.hexutil.checksum_address(target)
    if quote is not None:
        quote = NATIVE_ADDRESS if is_native(quote) else lib.hexutil.checksum_address(quote)
    live, _ = lp.discover_pools(env, token, quote, fee)
    if live:
        live = lp.select_fee(env, live, fee, token=token, quote=quote)
    if not live:
        what = f"paired with {quote} " if quote else ""
        raise TradingError(
            "trading.lp.not_found",
            f"no Uniswap V4 pool {what}holds {token} on {env.spec.name}; pass a poolId",
            details={"token": token, "quote": quote, "chainId": env.spec.chain_id},
        )
    currencies = [s["poolKey"][k] for s in live for k in ("currency0", "currency1")]
    lp.token_metas(env, currencies)
    lp.usd_prices(env, currencies)
    ranked = []
    for state in live:
        side = lp.with_implied_base_price(lp.make_side(env, state["poolKey"], token), state)
        ranked.append((lp._rank(env, state, side, lp.active_depth_usd(side, state)), state, side))
    ranked.sort(key=lambda item: item[0])
    _, state, side = ranked[0]
    # Ranked from slot0 alone: no candidate is tick-walked, so none can leave a
    # scan warning or a partial flag behind -- the one line below is all they add.
    if len(ranked) > 1:
        liquid = sum(1 for _, pool, _ in ranked if lp.is_liquid(pool))
        env.warn(
            f"{len(ranked)} V4 pools hold {side.base['symbol']} ({liquid} with active "
            "liquidity); using the deepest "
            f"({lp.fee_pct(state['poolKey'])}, {state['poolId']}) -- pass --quote/--fee "
            "or a poolId to choose"
        )
    return state, side


def _native_reserve(env: lp.ChainEnv) -> int:
    try:
        price = _hex_int(env.client.request("eth_gasPrice", []))
    except Exception:  # noqa: BLE001 - fall back to a fixed guess
        price = 10**9
    return NATIVE_GAS_RESERVE_UNITS * price * 2


def plan_add(
    env: lp.ChainEnv,
    target: str | None,
    *,
    quote: str | None = None,
    fee: int | None = None,
    usd: float | None = None,
    amount_base: str | None = None,
    amount_quote: str | None = None,
    range_spec: str | None = None,
    to_position: int | None = None,
    wallet: str | None = None,
    slippage_pct: float | None = None,
    check_balances: bool = True,
) -> dict[str, Any]:
    """Mint a position (or add to one with ``to_position``) from a deposit.

    Sized in USD (split between the two sides as the range needs at today's
    price) or in token units (``amount_base`` and/or ``amount_quote``; the
    binding side wins, the other is computed). ``quote`` / ``fee`` pick the pool
    among those holding ``target`` (``_pick_pool``); with ``to_position`` they
    must describe that position's pool. Required amounts round up and
    the maxima add the slippage; a wallet short of a side is refused with
    ``trading.insufficient_balance`` -- nothing is swapped to make up for it.
    """
    lib = env.lib
    m = lib.v4_math
    slippage = _slippage(slippage_pct)
    if usd is not None and (amount_base is not None or amount_quote is not None):
        raise TradingError(
            "trading.invalid", "size the deposit in USD or in token amounts, not both"
        )
    if usd is None and amount_base is None and amount_quote is None:
        raise TradingError("trading.invalid", "an add needs usd, amountBase or amountQuote")
    if usd is not None and (not math.isfinite(float(usd)) or float(usd) <= 0):
        raise TradingError("trading.invalid", "usd must be above zero")

    pos: dict[str, Any] | None = None
    fees: tuple[int, int] | None = None
    if to_position is not None:
        if range_spec:
            raise TradingError(
                "trading.invalid", "--range cannot change an existing position's range"
            )
        pos, state, side, fees = _position(env, int(to_position))
        owner = _check_owner(env, pos, wallet, check_owner=True)
        wallet = owner
        for named in (target, quote):
            if named and not _names_pool(named, state):
                raise TradingError(
                    "trading.invalid",
                    f"#{to_position} is in pool {state['poolId']}, which {named} is not part of",
                )
        lp.check_pool_fee(state, fee)
        tl, tu = int(pos["tickLower"]), int(pos["tickUpper"])
        range_text = f"position:{to_position}"
    else:
        if not target:
            raise TradingError("trading.invalid", "a token or poolId is required")
        if not wallet:
            raise TradingError("trading.invalid", "a wallet is required")
        state, side = _pick_pool(env, target, quote, fee)
        parsed = parse_range(range_spec)
        range_text = str(parsed["text"])
        tl, tu = resolve_ticks(side, state, parsed)
    assert wallet is not None

    sqrt_p = int(state["sqrtPriceX96"])
    sa, sb = m.get_sqrt_ratio_at_tick(tl), m.get_sqrt_ratio_at_tick(tu)
    needs0, needs1 = sqrt_p < sb, sqrt_p > sa
    one_sided: str | None = None
    if not (needs0 and needs1):
        only1 = not needs0
        one_sided = "base" if only1 == side.base_is_currency1 else "quote"

    meta0, meta1 = side.meta0, side.meta1
    if usd is not None:
        ref = m.get_amounts_for_liquidity(sqrt_p, sa, sb, _REF_LIQUIDITY)
        prices = lp.usd_prices(env, [meta0["address"], meta1["address"]])
        px0 = prices.get(meta0["address"].lower())
        px1 = prices.get(meta1["address"].lower())
        # A base price the lookup has not got is the pool's own (quote × pool price).
        if side.base_is_currency1:
            px1 = px1 if px1 is not None else side.base_usd
        else:
            px0 = px0 if px0 is not None else side.base_usd
        value_ref = 0.0
        for amount, meta, price in ((ref["amount0"], meta0, px0), (ref["amount1"], meta1, px1)):
            if amount <= 0:
                continue
            if price is None:
                raise TradingError(
                    "trading.unpriced",
                    f"no USD price for {meta['symbol']} on {env.spec.name}; size the deposit "
                    "in token units (amountBase / amountQuote) instead",
                    details={"token": meta["address"], "chainId": env.spec.chain_id},
                )
            value_ref += amount / 10 ** int(meta["decimals"]) * float(price)
        if not value_ref > 0:
            raise TradingError("trading.invalid", "the range holds nothing at this price")
        scale = float(usd) / value_ref
        desired0 = int(ref["amount0"] * scale)
        desired1 = int(ref["amount1"] * scale)
        liquidity = int(m.get_liquidity_for_amounts(sqrt_p, sa, sb, desired0, desired1))
    else:
        base_raw = _to_raw(amount_base, int(side.base["decimals"]), "amountBase")
        quote_raw = _to_raw(amount_quote, int(side.quote["decimals"]), "amountQuote")
        given0, given1 = (quote_raw, base_raw) if side.base_is_currency1 else (base_raw, quote_raw)
        candidates: list[int] = []
        if given0 is not None and needs0:
            candidates.append(int(m.get_liquidity_for_amount0(max(sqrt_p, sa), sb, given0)))
        if given1 is not None and needs1:
            candidates.append(int(m.get_liquidity_for_amount1(sa, min(sqrt_p, sb), given1)))
        if not candidates:
            wanted = side.base if one_sided == "base" else side.quote
            raise TradingError(
                "trading.invalid",
                f"this range holds only {wanted['symbol']} at today's price; size the deposit "
                f"in {wanted['symbol']} (amount{'Base' if one_sided == 'base' else 'Quote'}) "
                "or in USD",
                details={"oneSided": one_sided},
            )
        unused = [
            name
            for name, given, needed in (("0", given0, needs0), ("1", given1, needs1))
            if given is not None and not needed
        ]
        if unused:
            env.warn("one of the amounts given is not needed by this range at today's price")
        liquidity = min(candidates)
    if liquidity <= 0:
        raise TradingError(
            "trading.invalid", "the deposit is too small to mint any liquidity in this range"
        )
    if liquidity > UINT128_MAX:
        raise TradingError("trading.invalid", "the deposit is too large for one position")
    required = m.get_amounts_for_liquidity(sqrt_p, sa, sb, liquidity, True)
    req0, req1 = int(required["amount0"]), int(required["amount1"])
    bps = _bps(slippage)
    max0, max1 = with_slippage_up(req0, bps), with_slippage_up(req1, bps)

    if check_balances:
        max0, max1 = _fit_balances(env, wallet, side, (req0, req1), (max0, max1))

    token_id = int(to_position) if to_position is not None else None
    plan = _base_plan(
        env, "add", wallet=wallet, token_id=token_id, state=state, side=side,
        tick_lower=tl, tick_upper=tu,
    )  # fmt: skip
    plan["increase"] = to_position is not None
    plan["rangeSpec"] = range_text
    plan["rangeDefaulted"] = to_position is None and not range_spec
    plan["liquidity"] = str(liquidity)
    plan["expected"] = _amounts(side, req0, req1)
    base_max, quote_max = side.split(max0, max1)
    plan["bounds"] = {"base": str(base_max), "quote": str(quote_max)}
    plan["oneSided"] = one_sided
    plan["slippagePct"] = slippage
    plan["status"] = side.status(int(state["tick"]), tl, tu, liquidity)
    if pos is not None:
        fees_json, value = _position_value(side, state, pos, fees)
        plan["fees"] = fees_json
        plan["positionValueUsd"] = value
        if fees and (fees[0] > 0 or fees[1] > 0):
            plan["actions"] = ["INCREASE_LIQUIDITY", "CLOSE_CURRENCY", "CLOSE_CURRENCY"]
    now = block_timestamp(env)
    steps = approval_steps(env, wallet, [(meta0, max0), (meta1, max1)], now)
    plan["approvals"] = steps
    return _finish(
        env, plan, wallet=wallet, approvals_first=[s for s in steps if s["needed"]], now=now
    )


def _names_pool(target: str, state: dict[str, Any]) -> bool:
    text = target.strip().lower()
    if lp._is_pool_id(text):
        return text == str(state["poolId"]).lower()
    key = state["poolKey"]
    if is_native(text) or text == "eth":
        text = NATIVE_ADDRESS
    return text in (str(key["currency0"]).lower(), str(key["currency1"]).lower())


def _fit_balances(
    env: lp.ChainEnv,
    wallet: str,
    side: lp.Side,
    required: tuple[int, int],
    maxima: tuple[int, int],
) -> tuple[int, int]:
    """Refuse a deposit the wallet cannot cover; cap a maximum at the balance.

    A wallet that covers what the position needs but not the slippage buffer
    on top gets its maximum trimmed to the balance (the call can never take
    more than the wallet holds anyway). Native currency keeps a gas reserve.
    """
    metas = (side.meta0, side.meta1)
    held = balances(env, wallet, [metas[0]["address"], metas[1]["address"]])
    out = list(maxima)
    for i, meta in enumerate(metas):
        need = required[i]
        if need <= 0:
            continue
        address = str(meta["address"]).lower()
        have = held.get(NATIVE_ADDRESS if is_native(address) else address, 0)
        reserve = _native_reserve(env) if is_native(address) else 0
        available = max(0, have - reserve)
        if available < need:
            which = "base" if (i == 1) == side.base_is_currency1 else "quote"
            fmt = env.lib.hexutil.format_units
            decimals = int(meta["decimals"])
            gas_note = " after keeping gas" if reserve else ""
            raise TradingError(
                "trading.insufficient_balance",
                f"{_wallet_label(env, wallet)} holds {fmt(have, decimals)} {meta['symbol']}"
                f"{gas_note}; this deposit needs {fmt(need, decimals)} {meta['symbol']} "
                f"({which} side). Nothing is swapped for you: fund the wallet, or choose a "
                "range that sits entirely on the side you hold",
                details={
                    "side": which,
                    "token": meta["address"],
                    "symbol": meta["symbol"],
                    "needRaw": str(need),
                    "haveRaw": str(have),
                    "reserveRaw": str(reserve),
                },
            )
        out[i] = min(out[i], available)
    return out[0], out[1]


# ── re-validation at execution ──────────────────────────────────────────────


def revalidate(env: lp.ChainEnv, plan: dict[str, Any]) -> dict[str, Any]:
    """Check an approved plan against the chain right before it is signed.

    ``add``: the amounts the plan's liquidity needs *now* must stay within
    the approved maxima, and the wallet must still hold them. ``remove``:
    what the liquidity is worth now must still meet the approved minimums.
    Either failing is ``trading.price_moved``. The position must still be the
    wallet's (``trading.lp.not_owner``) and still open. Returns the deadline
    to sign with and the approval steps still needed, read live.
    """
    lib = env.lib
    m = lib.v4_math
    op = plan["op"]
    wallet = str(plan["wallet"])
    key = plan["pool"]["poolKey"]
    state = _pool_state(env, str(plan["pool"]["poolId"]), key)
    now = block_timestamp(env)
    sqrt_p = int(state["sqrtPriceX96"])
    tl, tu = int(plan["range"]["tickLower"]), int(plan["range"]["tickUpper"])
    liquidity = int(plan["liquidity"])
    token_id = plan.get("tokenId")
    if op in ("collect", "remove") or plan.get("increase"):
        pos = lp._load_position(env, int(token_id or 0))
        if str(pos["owner"]).lower() != wallet.lower():
            raise TradingError(
                "trading.lp.not_owner",
                f"position #{token_id} is no longer held by {wallet}",
                details={"tokenId": str(token_id), "owner": str(pos["owner"])},
            )
        _require_open(env, pos)
        if op == "remove" and int(pos["liquidity"]) < liquidity:
            raise TradingError(
                "trading.invalid",
                f"position #{token_id} holds less liquidity than when the order was made; "
                "plan the removal again",
            )
    b0, b1 = _to_currency(plan, int(plan["bounds"]["base"]), int(plan["bounds"]["quote"]))
    sa, sb = m.get_sqrt_ratio_at_tick(tl), m.get_sqrt_ratio_at_tick(tu)
    steps: list[dict[str, Any]] = []
    if op == "remove":
        now_amounts = m.get_amounts_for_liquidity(sqrt_p, sa, sb, liquidity, False)
        if int(now_amounts["amount0"]) < b0 or int(now_amounts["amount1"]) < b1:
            raise TradingError(
                "trading.price_moved",
                "the pool's price moved since approval: the removal would now return less "
                "than the approved minimum; plan it again",
                details={"tick": int(state["tick"]), "planTick": int(plan["pool"]["tick"])},
            )
    elif op == "add":
        need = m.get_amounts_for_liquidity(sqrt_p, sa, sb, liquidity, True)
        need0, need1 = int(need["amount0"]), int(need["amount1"])
        if need0 > b0 or need1 > b1:
            raise TradingError(
                "trading.price_moved",
                "the pool's price moved since approval: the deposit now needs more than the "
                "approved maximum; plan it again",
                details={"tick": int(state["tick"]), "planTick": int(plan["pool"]["tick"])},
            )
        side = lp.make_side(env, key, str(plan["token"]["address"]))
        _fit_balances(env, wallet, side, (need0, need1), (b0, b1))
        metas = [(side.meta0, b0), (side.meta1, b1)]
        steps = approval_steps(env, wallet, metas, now)
    return {
        "deadline": now + DEADLINE_S,
        "timestamp": now,
        "tick": int(state["tick"]),
        "approvals": steps,
    }


def permit2_visible(env: lp.ChainEnv, wallet: str, token: str, amount: int, now: int) -> bool:
    """Whether the node answering now sees a Permit2 allowance of ``amount`` (unexpired)."""
    _erc20, p2_amount, p2_expiration = allowances(env, wallet, token)
    return p2_amount >= amount and p2_expiration >= now + PERMIT2_EXPIRY_MARGIN_S


def minted_token_id(receipt: dict[str, Any], position_manager: str, wallet: str) -> int | None:
    """The tokenId a mint created: the PositionManager's ``Transfer(0x0 → wallet)`` log."""
    topic = lp.unilp().abi.TOPIC_ERC721_TRANSFER.lower()
    for entry in receipt.get("logs") or []:
        if not isinstance(entry, dict):
            continue
        topics = [str(t).lower() for t in entry.get("topics") or []]
        if len(topics) != 4 or topics[0] != topic:
            continue
        if str(entry.get("address") or "").lower() != position_manager.lower():
            continue
        if int(topics[1], 16) != 0 or "0x" + topics[2][-40:] != wallet.lower():
            continue
        return int(topics[3], 16)
    return None
