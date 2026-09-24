"""Shared launch planning: read state, mine, simulate, hash.

Sits between the two entrypoints so ``pools_read.py simulate`` and
``pools_write.py launch`` compute the same numbers from the same code. It reads
the chain and does arithmetic; it never signs and never imports anything that
can. That is what lets the read script call into it safely.

The PLAN_HASH built here is the skill's confirmation token: a launch is planned,
printed, and hashed, and the broadcast only proceeds when the user echoes that
exact hash back. Anything that would change what lands on-chain is inside the
hash; anything that legitimately drifts between planning and sending (the wall
clock, gas prices) is outside it.
"""

from __future__ import annotations

import json
from typing import Any

from .abi_codec import decode
from .chains import ASSET_DECIMALS, CHAIN_ID, PARTY_FACTORY, USDG, WETH, asset_label
from .factory import (
    PARTY_FACTORY_ABI,
    TOTAL_SUPPLY,
    encode_launch,
    explain_revert,
    fdv_usd,
    mine_salt,
    price_from_tick,
    sorts_below,
    validate_start_tick,
)
from .keccak import keccak256
from .rpc import RpcError

CHAINLINK_ABI: list[dict] = [
    {"type": "function", "name": "decimals", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"type": "function", "name": "description", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
    {"type": "function", "name": "latestRoundData", "stateMutability": "view",
     "inputs": [], "outputs": [
         {"name": "roundId", "type": "uint80"},
         {"name": "answer", "type": "int256"},
         {"name": "startedAt", "type": "uint256"},
         {"name": "updatedAt", "type": "uint256"},
         {"name": "answeredInRound", "type": "uint80"},
     ]},
]


def read_factory_state(client: Any, factory: str = PARTY_FACTORY) -> dict:
    """The factory's global switches, in one round trip.

    A field whose call errored comes back as ``None``, which callers must treat
    as *unknown* rather than as a value. ``paused`` in particular: ``None`` is
    falsy, so testing it directly would read an unreadable factory as "not
    paused" and launch into a disabled contract. Use :func:`require_launchable`.
    """
    names = ["paused", "locker", "initialFdvUsd", "owner", "weth", "usdg",
             "sequencerUptimeFeed"]
    types = {"paused": "bool", "locker": "address", "initialFdvUsd": "uint256",
             "owner": "address", "weth": "address", "usdg": "address",
             "sequencerUptimeFeed": "address"}
    from .abi_codec import encode_function_data

    calls = [
        {"method": "eth_call",
         "params": [{"to": factory, "data": encode_function_data(PARTY_FACTORY_ABI, n)},
                    "latest"]}
        for n in names
    ]
    results = client.batch(calls, chunk_size=len(names))
    out: dict[str, Any] = {}
    for name, result in zip(names, results):
        if isinstance(result, dict) and "error" in result:
            out[name] = None
            continue
        out[name] = decode([{"type": types[name]}], result)[0]
    return out


def require_launchable(state: dict) -> None:
    """Refuse a launch unless the factory is *known* to be open for business.

    Fails closed on an unreadable switch. An RPC hiccup that turns `paused` into
    ``None`` must not be indistinguishable from `paused == False`: launching into
    a paused factory wastes the whole ~6.1M gas, and "we could not tell" is not
    the same answer as "no".
    """
    if state.get("paused") is None:
        raise RuntimeError(
            "could not read the factory's `paused` switch — refusing to launch on an "
            "unknown state. Re-run; if it persists the RPC endpoint is degraded."
        )
    if state["paused"]:
        raise RuntimeError("the pools.fun factory is paused; launches are disabled.")
    locker = state.get("locker")
    if not locker or int(locker, 16) == 0:
        raise RuntimeError("the factory has no locker configured; launches would revert.")


def _node_fault_message(what: str, exc: RpcError) -> str:
    """The message for a node fault a caller would otherwise read as a contract answer."""
    return (
        f"the RPC endpoint refused {what} ({exc}) — this is a node fault, not a "
        "contract revert. Retry in a moment."
    )


def _simulation_failure(exc: RpcError) -> str:
    """A failed launch simulation: only a revert is evidence about the contract."""
    hint = explain_revert(exc.data)
    if hint:
        return hint
    if exc.answered:
        return f"simulation reverted: {exc}"
    return _node_fault_message("the launch simulation", exc)


def read_start_tick(client: Any, paired_asset: str,
                    factory: str = PARTY_FACTORY) -> tuple[int, bool]:
    """The tick a launch would use right now, and whether the live feed produced it.

    ``live == False`` means the factory fell back to a pinned tick because the
    price feed was stale or the sequencer was down — the one condition under which
    the opening FDV drifts off its target.
    """
    try:
        tick, live = client.read(factory, PARTY_FACTORY_ABI, "startTickFor", [paired_asset])
    except RpcError as exc:
        hint = explain_revert(exc.data)
        if hint:
            raise RuntimeError(hint) from exc
        if not exc.answered:
            raise RuntimeError(
                _node_fault_message(f"startTickFor for {paired_asset}", exc)
            ) from exc
        raise RuntimeError(f"startTickFor reverted for {paired_asset}") from exc
    return int(tick), bool(live)


def read_paired_curve(client: Any, paired_asset: str,
                      factory: str = PARTY_FACTORY) -> dict:
    # A named tuple output decodes to a dict (see abi_codec._decode_param), so
    # read the fields by name rather than unpacking positionally.
    curve = client.read(factory, PARTY_FACTORY_ABI, "getPairedAssetCurve", [paired_asset])
    return {"feed": curve["feed"], "maxPriceAge": int(curve["maxPriceAge"]),
            "fallbackTick": int(curve["fallbackTick"]), "set": bool(curve["set"])}


def read_paired_usd(client: Any, paired_asset: str,
                    factory: str = PARTY_FACTORY) -> dict | None:
    """USD price of the paired asset, straight from the feed the factory prices off.

    Deliberately not an external price API: this is the exact number that produced
    the launch tick, so the FDV shown to the user reconciles with the protocol's
    own arithmetic instead of approximating it.
    """
    curve = read_paired_curve(client, paired_asset, factory)
    feed = curve.get("feed")
    if not feed or int(feed, 16) == 0:
        return None
    try:
        decimals = client.read(feed, CHAINLINK_ABI, "decimals")
        _, answer, _, updated_at, _ = client.read(feed, CHAINLINK_ABI, "latestRoundData")
    except Exception:
        return None
    if int(answer) <= 0:
        return None
    return {"usd": int(answer) / float(10 ** int(decimals)), "feed": feed,
            "updatedAt": int(updated_at), "maxPriceAge": curve["maxPriceAge"]}


def simulate_launch(client: Any, *, factory: str, name: str, symbol: str,
                    metadata_uri: str, salt: str, paired_asset: str,
                    start_tick: int, deadline: int, creator: str,
                    fee_recipient: str, dev_buy_amount_in: int,
                    value_wei: int, from_address: str) -> dict:
    """``eth_call`` the real launch and read back what it would produce.

    ``devBuyMinOut`` is pinned to 0 so the call reports the true fill rather than
    reverting against a guess. The number is exact, not indicative: the dev buy is
    the pool's first swap and happens inside the same transaction, so there is no
    window for anyone to move the price first.

    A balance state override is attached so this works from a wallet that has not
    been funded yet — otherwise planning a dev buy would require already holding
    the ETH, which defeats the point of planning.
    """
    data = encode_launch(name, symbol, metadata_uri, salt, paired_asset, start_tick,
                         deadline, creator, fee_recipient, dev_buy_amount_in, 0)
    call: dict[str, Any] = {"from": from_address, "to": factory, "data": data}
    if value_wei:
        call["value"] = hex(value_wei)
    # Cover the dev buy plus generous gas headroom.
    override_balance = value_wei + 10**18
    params: list[Any] = [call, "latest",
                         {from_address: {"balance": hex(override_balance)}}]
    try:
        raw = client.request("eth_call", params)
    except RpcError as exc:
        hint = explain_revert(exc.data)
        if hint:
            raise RuntimeError(hint) from exc
        # Some nodes reject the third parameter outright; retry without it so the
        # command still works, just requiring a funded wallet for dev-buy plans.
        if "override" in str(exc).lower() or exc.code == -32602:
            try:
                raw = client.request("eth_call", [call, "latest"])
            except RpcError as inner:
                raise RuntimeError(_simulation_failure(inner)) from inner
        else:
            raise RuntimeError(_simulation_failure(exc)) from exc
    token, pool, dev_buy_out = decode(
        [{"type": "address"}, {"type": "address"}, {"type": "uint256"}], raw)
    return {"token": token, "pool": pool, "devBuyOut": int(dev_buy_out)}


def plan_launch(client: Any, *, factory: str, name: str, symbol: str,
                metadata_uri: str, paired_asset: str, creator: str,
                fee_recipient: str, deadline: int, dev_buy_wei: int = 0,
                dev_buy_asset: int = 0, slippage_bps: int = 100,
                salt: str | None = None, max_salt_attempts: int = 5000,
                allow_fallback_tick: bool = False,
                on_progress: Any = None) -> dict:
    """Assemble a complete, checked launch plan. Reads only."""
    if dev_buy_wei and dev_buy_asset:
        raise RuntimeError(
            "use either --dev-buy (native ETH) or --dev-buy-asset (paired ERC20), "
            "never both — the factory reverts AmbiguousDevBuy."
        )
    if dev_buy_wei and paired_asset.lower() != WETH.lower():
        raise RuntimeError(
            f"a native-ETH dev buy only works on WETH pairs; this pair is "
            f"{asset_label(paired_asset)}. Use --dev-buy-asset instead (run `approve` first)."
        )

    state = read_factory_state(client, factory)
    require_launchable(state)

    allowed = client.read(factory, PARTY_FACTORY_ABI, "allowedPairedAsset", [paired_asset])
    if not allowed:
        raise RuntimeError(
            f"{asset_label(paired_asset)} ({paired_asset}) is not on the factory's "
            f"paired-asset allowlist. Run `pools_read.py assets` to see what is launchable."
        )

    start_tick, live = read_start_tick(client, paired_asset, factory)
    validate_start_tick(start_tick)
    if not live and not allow_fallback_tick:
        raise RuntimeError(
            f"the launch tick ({start_tick}) came from the factory's fallback, not the "
            f"live price feed — the opening FDV will be off target. Wait for the feed "
            f"to recover, or pass --allow-fallback-tick to launch anyway."
        )

    if salt:
        salt_value = salt if salt.startswith("0x") else "0x" + salt
        if len(salt_value) != 66:
            raise RuntimeError("--salt must be 32 bytes of hex")
        from .factory import encode_compute_token_address
        token_preview = decode([{"type": "address"}], client.call(
            factory, encode_compute_token_address(
                creator, salt_value, name, symbol, metadata_uri)))[0]
        if not sorts_below(token_preview, paired_asset):
            raise RuntimeError(
                f"--salt produces {token_preview}, which does not sort below "
                f"{asset_label(paired_asset)} ({paired_asset}). Drop --salt to mine one."
            )
        attempts = 1
    else:
        salt_value, token_preview, attempts = mine_salt(
            client, factory, creator, name, symbol, metadata_uri, paired_asset,
            max_attempts=max_salt_attempts, on_progress=on_progress)

    sim = simulate_launch(
        client, factory=factory, name=name, symbol=symbol, metadata_uri=metadata_uri,
        salt=salt_value, paired_asset=paired_asset, start_tick=start_tick,
        deadline=deadline, creator=creator, fee_recipient=fee_recipient,
        dev_buy_amount_in=dev_buy_asset, value_wei=dev_buy_wei, from_address=creator)

    if sim["token"].lower() != token_preview.lower():
        raise RuntimeError(
            f"internal check failed: simulation deployed {sim['token']} but the mined "
            f"salt predicted {token_preview}. Re-run."
        )

    dev_buy_min_out = sim["devBuyOut"] * (10_000 - slippage_bps) // 10_000

    price_data = read_paired_usd(client, paired_asset, factory)
    paired_usd = price_data["usd"] if price_data else None

    plan = {
        "name": name,
        "symbol": symbol,
        "metadataUri": metadata_uri,
        "salt": salt_value,
        "pairedAsset": paired_asset,
        "expectedStartTick": start_tick,
        "creator": creator,
        "feeRecipient": fee_recipient,
        "devBuyAmountIn": dev_buy_asset,
        "devBuyValueWei": dev_buy_wei,
        "devBuyMinOut": dev_buy_min_out,
        "slippageBps": slippage_bps,
        "chainId": CHAIN_ID,
        "factory": factory,
    }
    plan["planHash"] = plan_hash(plan)
    # Everything below is context for the human, not part of the commitment.
    plan.update({
        "token": sim["token"],
        "pool": sim["pool"],
        "devBuyOut": sim["devBuyOut"],
        "tickLive": live,
        "saltAttempts": attempts,
        "pairedUsd": paired_usd,
        "priceFeed": price_data,
        "tokenPriceUsd": (price_from_tick(start_tick) * paired_usd) if paired_usd else None,
        "fdvUsd": fdv_usd(start_tick, paired_usd) if paired_usd else None,
        "targetFdvUsd": int(state["initialFdvUsd"]) if state.get("initialFdvUsd") else None,
        "locker": state.get("locker"),
        "supply": TOTAL_SUPPLY,
        "deadline": deadline,
        "supplyPct": (sim["devBuyOut"] / TOTAL_SUPPLY * 100) if sim["devBuyOut"] else 0.0,
    })
    return plan


# Fields that define what will land on-chain. The absolute deadline and gas
# prices are excluded on purpose: both legitimately differ between the moment a
# plan is printed and the moment it is confirmed, and including them would make
# every hash expire instantly without making the commitment any stronger.
_HASHED_FIELDS = (
    "name", "symbol", "metadataUri", "salt", "pairedAsset", "expectedStartTick",
    "creator", "feeRecipient", "devBuyAmountIn", "devBuyValueWei", "devBuyMinOut",
    "chainId", "factory",
)


def plan_hash(plan: dict) -> str:
    """A short, stable digest of everything that matters about a launch."""
    payload = {}
    for key in _HASHED_FIELDS:
        value = plan.get(key)
        payload[key] = value.lower() if isinstance(value, str) and value.startswith("0x") \
            else value
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return keccak256(canonical.encode())[:10]


def action_hash(action: str, fields: dict) -> str:
    """The same commitment scheme for the simpler, non-launch writes.

    Fee collection and approvals have nothing in common with a launch's field set,
    so they get their own canonical payload rather than being forced into the
    launch shape. ``action`` is part of the digest, which is what stops a `collect`
    confirmation from authorising a `claim` on the same token.
    """
    payload: dict[str, Any] = {"action": action}
    for key, value in fields.items():
        payload[key] = value.lower() if isinstance(value, str) and value.startswith("0x") \
            else value
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return keccak256(canonical.encode())[:10]


def verify_plan_unchanged(plan: dict, expected: str) -> None:
    actual = plan_hash(plan)
    if actual.lower() != expected.lower().strip():
        raise RuntimeError(
            f"plan changed since it was printed (now {actual}, you confirmed {expected}). "
            "Re-run the dry run and confirm the new hash."
        )


def paired_decimals(asset: str) -> int:
    return ASSET_DECIMALS.get(asset.lower(), 18)


__all__ = [
    "CHAINLINK_ABI", "read_factory_state", "read_start_tick", "read_paired_curve",
    "read_paired_usd", "simulate_launch", "plan_launch", "plan_hash", "action_hash",
    "verify_plan_unchanged", "paired_decimals", "USDG", "WETH",
]
