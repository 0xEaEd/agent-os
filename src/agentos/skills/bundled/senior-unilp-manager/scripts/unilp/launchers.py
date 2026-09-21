"""Launchpad awareness — port of ``launchers.mjs``.

Which protocol deployed a token, where its pool is, and who holds the LP, all from
on-chain registries with no PoolManager log scan.

This exists because log-scan discovery is unusable on Base: find_pools_for_token walks
~2,700 sequential eth_getLogs chunks (head ~49M vs logScan.fromBlock 25.35M at 9k per
chunk) and the RPC rejects anything wider. Every launcher below instead publishes a
registry that maps token -> hook, so the poolId can be derived with one keccak.

See assets/v4-reference.md for the architecture and the verified probe transcripts.
"""

from __future__ import annotations

import re

from .abi_codec import encode_function_data
from .abi_defs import (
    CLANKER_FACTORY_ABI,
    CLANKER_LOCKER_ABI,
    DOPPLER_AIRLOCK_ABI,
    POSITION_MANAGER_ABI,
)
from .hexutil import checksum_address, strip0x
from .v4_pool import (
    DYNAMIC_FEE_FLAG,
    NATIVE,
    compute_pool_id,
    decode_position_info,
    normalize_pool_key,
    sort_currencies,
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# `kind` drives the lookup strategy:
#   'clanker' — factory.tokenDeploymentInfo(token) -> {token, hook, locker, extensions[]}
#   'doppler' — airlock.getAssetData(token) -> {numeraire, …, poolInitializer(= the hook)}
#
# `hooks` is only used for reverse-labeling and as a derivation fallback. It goes stale
# as launchers ship new versions, which is exactly why the registry lookup comes first.
LAUNCHERS: dict[str, list[dict]] = {
    "base": [
        {
            "id": "clanker-v4.1",
            "name": "Clanker v4.1",
            "kind": "clanker",
            "docs": "https://clanker.world/docs/references/deployed-contracts#base-8453",
            # v4.1 shipped new hooks against the v4.0 factory.
            "factory": "0xE85A59c628F7d27878ACeB4bf3b35733630083a9",
            "hooks": {
                "0xd60d6b218116cfd801e28f78d011a203d2b068cc": "ClankerHookDynamicFeeV2",
                "0xb429d62f8f3bffb98cdb9569533ea23bf0ba28cc": "ClankerHookStaticFeeV2",
            },
            "contracts": {
                "0xebb25bb797d82cb78e1bc70406b13233c0854413": "ClankerSniperAuctionV2",
                "0xc5aa2945d52a4096b946891ef8e01668f82eb74e": "ClankerSniperUtilV2",
                "0xf652b3610d75d81871bf96db50825d9af28391e0": "ClankerAirdropV2",
                "0xaa12bb11e9876fcafc7c46dbeb985d3fa23832c9": "ClankerPoolExtensionAllowlist",
            },
        },
        {
            "id": "clanker-v4.0",
            "name": "Clanker v4",
            "kind": "clanker",
            "docs": "https://clanker.world/docs/references/deployed-contracts#base-8453",
            "factory": "0xE85A59c628F7d27878ACeB4bf3b35733630083a9",
            "hooks": {
                "0x34a45c6b61876d739400bd71228cbcbd4f53e8cc": "ClankerHookDynamicFee",
                "0xdd5eeaff7bd481ad55db083062b13a3cdf0a68cc": "ClankerHookStaticFee",
            },
            "contracts": {
                "0xf3622742b1e446d92e45e22923ef11c2fcd55d68": "ClankerFeeLocker",
                "0x63d2dfea64b3433f4071a98665bcd7ca14d93496": "ClankerLpLockerFeeConversion",
                "0x29d17c1a8d851d7d4ca97fae97acadb398d9cce0": "ClankerLpLocker",
                "0x8e845ead15737bf71904a30bddd3aee76d6adf6c": "ClankerVault",
                "0x56fa0da89ed94822e46734e736d34cab72df344f": "ClankerAirdrop",
                "0xfdc013ce003980889cffd66b0c8329545ae1d1e8": "ClankerSniperAuctionV0",
                "0x8806169969ae96bfaadb3efd4b10785beeb321b3": "ClankerSniperUtilV0",
                "0xe143f9872a33c955f23cf442bb4b1efb3a7402a2": "ClankerMevBlockDelay",
            },
        },
        {
            "id": "liquid",
            "name": "Liquid Protocol",
            "kind": "clanker",  # fork — same factory/locker interface
            "docs": "https://app.liquidprotocol.org/docs#contracts",
            "factory": "0x04F1a284168743759BE6554f607a10CEBdB77760",
            "hooks": {
                "0x80e2f7dc8c2c880bbc4bdf80a5fb0eb8b1db68cc": "LiquidHookDynamicFeeV2",
                "0x9811f10cd549c754fa9e5785989c422a762c28cc": "LiquidHookStaticFeeV2",
            },
            "contracts": {
                "0xf7d3be3fc0de76fa5550c29a8f6fa53667b876ff": "LiquidFeeLocker",
                "0xb614167d79adbaa9ba35d05fe1d5542d7316ccaa": "LiquidPoolExtensionAllowlist",
                "0x77247fcd1d5e34a3703aca898a591dc7422435f3": "LiquidLpLockerFeeConversion",
            },
        },
        {
            "id": "doppler",
            "name": "Doppler (Bankr)",
            "kind": "doppler",
            "docs": "https://docs.doppler.lol/reference/contract-addresses",
            "airlock": "0x660eAaEdEBc968f8f3694354FA8EC0b4c5Ba8D12",
            # Best-effort only: every Doppler token also gets its own mined hook, so
            # this list can never be complete. getAssetData is the authoritative lookup.
            "hooks": {
                "0x53b4c21a6cb61d64f636abbfa6e8e90e6558e8ad": "UniswapV4Initializer",
                "0x65de470da664a5be139a5d812be5fda0d76cc951": "UniswapV4MulticurveInitializer",
                "0xa36715da46ddf4a769f3290f49af58bf8132ed8e":
                    "UniswapV4ScheduledMulticurveInitializer",
                "0xbdf938149ac6a781f94faa0ed45e6a0e984c6544":
                    "UniswapV4ScheduledMulticurveInitializer (live)",
                "0xd6fecff347c6203a41874e8d77de669b54e7a500": "UniswapV4MigratorHook",
            },
            "contracts": {
                "0x660eaaedebc968f8f3694354fa8ec0b4c5ba8d12": "Doppler Airlock",
                "0xb35469ee64a87afd19b31615094fe3962d73e421": "DopplerDeployer",
                "0x136191b46478cab023cbc01a36160c4aad81677a": "Doppler Bundler",
                "0x4225c632b62622bd7b0a3ec9745c0a866ff94f6f": "Doppler TokenFactory",
                "0xf0b5141dd9096254b2ca624dff26024f46087229": "Doppler TokenFactory80",
                "0x43d0d97ec9241a8f05a264f94b82a1d2e600f2b3": "DopplerLensQuoter",
                "0xd3b4cf7fd24381e90a4f012fc6c5976b87b9b3ce": "UniswapV4Migrator",
                "0x0a00775d71a42cd33d62780003035e7f5b47bd3a": "StreamableFeesLocker",
                "0xce3212e6536f33cd6fbfee265224131353ca3d47": "StreamableFeesLockerV2",
                "0x5f3ba43d44375286296cb85f1ea2ebfa25dde731": "UniswapV2Migrator",
                "0xe0dc4012ac9c868f09c6e4b20d66ed46d6f258d0":
                    "LockableUniswapV3Initializer (v3)",
                "0xaa47d2977d622dbdfd33eef6a8276727c52eb4e5": "UniswapV3Initializer (v3)",
            },
        },
    ],

    # No Airlock has been verified on this chain, so resolve_launcher has nothing to
    # query and skips the entry. The hook label still does work: every Doppler pool
    # is (token, quote, 0x800000, 200, hook), so the labelled hook alone lets
    # labelled_hooks() derive the poolId and one getSlot0 confirm it — which is the
    # only discovery route left now that the RPC caps eth_getLogs at 100k blocks.
    "robinhood": [
        {
            "id": "doppler",
            "name": "Doppler (Bankr)",
            "kind": "doppler",
            "docs": "https://docs.doppler.lol/reference/contract-addresses",
            "hooks": {
                "0x4e3468951d49f2eea976ed0d6e75ffcb44a9a544":
                    "DopplerHookInitializer (AGENTOS)",
            },
        },
    ],
}


def launchers_for(chain: dict) -> list[dict]:
    """Every launcher entry configured for a chain (empty when the chain has none)."""
    return LAUNCHERS.get(chain["key"], [])


def labelled_hooks(chain: dict) -> list[tuple[dict, str]]:
    """Every ``(entry, hook)`` a chain's launcher table labels, checksummed.

    A hook address is a registry of one: a launcher's pools are fully determined by
    it, so a token can be tested against it with a few keccaks and one getSlot0.
    This is how a chain without a queryable factory or airlock still gets
    registry-style discovery.
    """
    out = []
    for entry in launchers_for(chain):
        for hook in (entry.get("hooks") or {}):
            out.append((entry, checksum_address(hook)))
    return out


def queryable_launchers(chain: dict) -> list[dict]:
    """Entries that expose an on-chain registry we can actually query."""
    return [
        entry for entry in launchers_for(chain)
        if (entry.get("airlock") if entry["kind"] == "doppler" else entry.get("factory"))
    ]


# ---------------------------------------------------------------------------
# Address labeling
# ---------------------------------------------------------------------------

_label_cache: dict[str, dict[str, str]] = {}


def _label_map(chain: dict) -> dict[str, str]:
    cached = _label_cache.get(chain["key"])
    if cached is not None:
        return cached

    mapping: dict[str, str] = {}

    def put(addr: str | None, label: str) -> None:
        if addr:
            mapping[addr.lower()] = label

    put(chain.get("positionManager"), "Uniswap v4 PositionManager")
    put(chain.get("poolManager"), "Uniswap v4 PoolManager")
    put(chain.get("permit2"), "Permit2")
    put(chain.get("universalRouter"), "Uniswap UniversalRouter")

    for entry in launchers_for(chain):
        put(entry.get("factory"), f"{entry['name']} factory")
        put(entry.get("airlock"), f"{entry['name']} Airlock")
        for addr, name in (entry.get("hooks") or {}).items():
            put(addr, f"{entry['name']}: {name}")
        for addr, name in (entry.get("contracts") or {}).items():
            put(addr, name)

    _label_cache[chain["key"]] = mapping
    return mapping


def label_address(chain: dict, address: str | None) -> str | None:
    """Human name for a known protocol address, or None."""
    if not address:
        return None
    return _label_map(chain).get(address.lower())


_LOCKER_RE = re.compile(r"LpLocker|FeesLocker", re.IGNORECASE)


def is_locker_address(chain: dict, address: str | None) -> bool:
    """True when this address is a launcher's LP locker — the position is not
    withdrawable."""
    label = label_address(chain, address)
    return label is not None and _LOCKER_RE.search(label) is not None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _is_zero(addr: str | None) -> bool:
    return not addr or addr.lower() == NATIVE


def resolve_launcher(client, chain: dict, token: str) -> dict | None:
    """Which launcher deployed ``token``, if any.

    Every registry is queried in one multicall; a token that was not launched by a
    given protocol comes back as a zero-address struct (Clanker) or a zero numeraire
    (Doppler) rather than reverting, so both shapes are treated as "not mine".
    """
    entries = queryable_launchers(chain)
    if not entries:
        return None

    asset = checksum_address(token)
    calls = []
    for entry in entries:
        if entry["kind"] == "doppler":
            calls.append({
                "address": checksum_address(entry["airlock"]),
                "abi": DOPPLER_AIRLOCK_ABI,
                "functionName": "getAssetData",
                "args": [asset],
            })
        else:
            calls.append({
                "address": checksum_address(entry["factory"]),
                "abi": CLANKER_FACTORY_ABI,
                "functionName": "tokenDeploymentInfo",
                "args": [asset],
            })
    results = client.multicall(calls)

    for entry, result in zip(entries, results):
        if result["status"] != "success":
            continue

        if entry["kind"] == "doppler":
            numeraire, timelock, governance, migrator, initializer = result["result"][:5]
            if _is_zero(initializer):
                continue
            return {
                "launcher": entry["id"],
                "name": entry["name"],
                "kind": "doppler",
                "docs": entry.get("docs"),
                "token": asset,
                "hook": checksum_address(initializer),
                "locker": None,
                "numeraire": None if _is_zero(numeraire) else checksum_address(numeraire),
                "extras": {
                    "timelock": timelock,
                    "governance": governance,
                    "liquidityMigrator": migrator,
                },
            }

        info = result["result"]
        # The v4.0 and v4.1 entries share one factory, so a Clanker hit is attributed
        # by hook rather than by which row answered.
        if _is_zero(info["token"]) or _is_zero(info["hook"]):
            continue
        by_hook = next(
            (e for e in entries if (e.get("hooks") or {}).get(info["hook"].lower())),
            None,
        )
        owner = by_hook or entry
        return {
            "launcher": owner["id"],
            "name": owner["name"],
            "kind": "clanker",
            "docs": owner.get("docs"),
            "token": asset,
            "hook": checksum_address(info["hook"]),
            "locker": None if _is_zero(info["locker"]) else checksum_address(info["locker"]),
            "numeraire": None,  # discovered from the locker record / pool candidates
            "extras": {
                "extensions": [checksum_address(a) for a in (info.get("extensions") or [])]
            },
        }

    return None


# ---------------------------------------------------------------------------
# Pool derivation
# ---------------------------------------------------------------------------


def derive_pool_candidates(chain: dict, token: str, hook: str | None = None,
                           numeraire: str | None = None) -> list[dict]:
    """Candidate poolIds for a launcher-deployed token.

    All three launchers open dynamic-fee pools at tickSpacing 200 (verified on Clanker
    RED and Doppler BLEND), so the search space is just the pairing currency. When the
    registry told us the numeraire there is exactly one candidate; otherwise every
    known quote is tried and the caller filters by which ones actually have a slot0.
    """
    if not hook:
        return []
    if numeraire:
        quotes = [numeraire]
    else:
        quotes = [q for q in (chain.get("knownQuotes") or {})
                  if q.lower() != token.lower()]

    fees = [DYNAMIC_FEE_FLAG]
    spacings = [200]
    out = []
    seen = set()

    for quote in quotes:
        if quote.lower() == token.lower():
            continue
        currency0, currency1 = sort_currencies(token, quote)
        for fee in fees:
            for tick_spacing in spacings:
                pool_key = normalize_pool_key({
                    "currency0": currency0, "currency1": currency1, "fee": fee,
                    "tickSpacing": tick_spacing, "hooks": hook,
                })
                pool_id = compute_pool_id(pool_key)
                if pool_id in seen:
                    continue
                seen.add(pool_id)
                out.append({"poolId": pool_id, "poolKey": pool_key})
    return out


# ---------------------------------------------------------------------------
# Locked positions
# ---------------------------------------------------------------------------

_WORD = 64  # hex characters in one 32-byte word


def probe_position_ids(client, chain: dict, locker: str | None, token: str,
                       max_ids: int = 64) -> list[dict]:
    """Position NFT ids held by a Clanker/Liquid locker for ``token``.

    ``tokenRewards`` returns a struct whose field order differs across Clanker versions
    and is not guaranteed to match in the Liquid fork, so nothing is read by offset.
    Instead every 32-byte word that could plausibly be a tokenId is treated as a
    candidate, and — because both lockers store a launch as
    ``(startPositionId, positionCount)`` with the NFTs minted consecutively — a small
    integer in the following word expands the candidate into a run.

    Nothing is trusted: every id is confirmed against the PositionManager, and only ids
    actually held by this locker AND sitting in a pool containing ``token`` survive. A
    layout change therefore degrades to "found fewer positions", never to wrong output.

    Verified: Clanker RED -> (2886509, 5), Liquid VLAD -> (2886409, 3); the id one past
    each run fails both checks.
    """
    if not locker:
        return []

    try:
        raw = client.call(
            checksum_address(locker),
            encode_function_data(CLANKER_LOCKER_ABI, "tokenRewards",
                                 [checksum_address(token)]),
        )
    except Exception:  # noqa: BLE001 — a locker that does not answer means no positions
        return []
    hex_body = strip0x(raw or "")
    if not hex_body:
        return []

    words = [int(hex_body[i:i + _WORD], 16)
             for i in range(0, len(hex_body) - _WORD + 1, _WORD)]

    try:
        next_token_id = int(client.read(
            chain["positionManager"], POSITION_MANAGER_ABI, "nextTokenId"
        ))
    except Exception:  # noqa: BLE001
        next_token_id = None

    candidates: list[int] = []

    def add(token_id: int) -> None:
        if token_id < 1:
            return
        if next_token_id is not None and token_id >= next_token_id:
            return
        if token_id in candidates:
            return
        if len(candidates) < max_ids:
            candidates.append(token_id)

    for i, value in enumerate(words):
        # Skip zeros, ABI offsets/lengths, fee flags, bps values.
        if value < 1000:
            continue
        if next_token_id is not None and value >= next_token_id:
            continue
        add(value)
        # (startPositionId, positionCount) — expand the run when the next word looks
        # like a count.
        count = words[i + 1] if i + 1 < len(words) else None
        if count is not None and 1 < count <= 64:
            for k in range(1, count):
                add(value + k)
    if not candidates:
        return []

    calls = []
    for token_id in candidates:
        calls.append({"address": chain["positionManager"], "abi": POSITION_MANAGER_ABI,
                      "functionName": "getPoolAndPositionInfo", "args": [token_id]})
        calls.append({"address": chain["positionManager"], "abi": POSITION_MANAGER_ABI,
                      "functionName": "ownerOf", "args": [token_id]})
        calls.append({"address": chain["positionManager"], "abi": POSITION_MANAGER_ABI,
                      "functionName": "getPositionLiquidity", "args": [token_id]})
    results = client.multicall(calls)

    wanted = token.lower()
    locker_lc = locker.lower()
    out = []
    for i, token_id in enumerate(candidates):
        info, owner, liq = results[i * 3:i * 3 + 3]
        if info["status"] != "success" or owner["status"] != "success":
            continue
        if owner["result"].lower() != locker_lc:
            continue
        pool_key = normalize_pool_key(info["result"][0])
        if wanted not in (pool_key["currency0"].lower(), pool_key["currency1"].lower()):
            continue
        decoded = decode_position_info(info["result"][1])
        out.append({
            "tokenId": token_id,
            "poolKey": pool_key,
            "poolId": compute_pool_id(pool_key),
            "tickLower": decoded["tickLower"],
            "tickUpper": decoded["tickUpper"],
            "liquidity": int(liq["result"]) if liq["status"] == "success" else None,
        })
    out.sort(key=lambda p: p["tokenId"])
    return out
