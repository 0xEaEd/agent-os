"""On-disk memory of PoolKeys the skill has already confirmed on chain.

A poolId is a keccak hash and cannot be inverted, so every command that takes
``--pool <id>`` has to recover the PoolKey somehow. On a chain whose RPC will not
serve a wide ``eth_getLogs`` range that used to mean ``--token`` on every call, and
one forgotten flag cost a whole model round trip. But ``pools --token`` had already
derived that PoolKey and confirmed it with ``getSlot0`` moments earlier.

So: whenever discovery confirms a pool, remember ``poolId -> PoolKey`` under
``state_root()/pools/<chain>.json``; whenever a poolId needs a PoolKey, look there
first. The mapping is content-addressed — a PoolKey hashes to exactly one poolId —
so a cached entry can never go stale, only missing. A PoolKey spelled out on the
command line still wins, and the caller re-derives the id from it as before.

Writes are atomic (temp file + rename) so a crashed process cannot leave a torn
file, and a corrupt or unreadable file reads as empty rather than aborting a
command: the cache is an accelerator, never a source of truth.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .chains import state_root
from .v4_pool import normalize_pool_key

_DIR = "pools"


def _path(chain: dict) -> Path:
    return state_root() / _DIR / f"{chain['key']}.json"


def _read(chain: dict) -> dict[str, dict]:
    try:
        raw = _path(chain).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def lookup(chain: dict, pool_id: str) -> dict | None:
    """The PoolKey remembered for ``pool_id`` on this chain, or None."""
    entry = _read(chain).get(pool_id.lower())
    if not isinstance(entry, dict):
        return None
    try:
        return normalize_pool_key(entry)
    except Exception:  # noqa: BLE001 — a malformed entry is "not cached"
        return None


def remember(chain: dict, inits: list[dict]) -> None:
    """Record ``poolId -> PoolKey`` for every init. Never raises: caching is optional."""
    fresh = {}
    for init in inits:
        pool_id = (init.get("poolId") or "").lower()
        key = init.get("poolKey")
        if pool_id and isinstance(key, dict):
            fresh[pool_id] = normalize_pool_key(key)
    if not fresh:
        return
    try:
        path = _path(chain)
        current = _read(chain)
        if all(current.get(k) == v for k, v in fresh.items()):
            return
        current.update(fresh)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pools-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        return
