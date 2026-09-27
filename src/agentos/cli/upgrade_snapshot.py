"""Backward-compatible alias for :mod:`agentos.compat.upgrade_snapshot`.

The implementation moved to ``agentos.compat`` so the gateway's
``updates.verifyData`` RPC can share it without a ``gateway -> cli`` import
edge. Import the real module for anything that needs to monkeypatch its
internals; this shim only re-exports the public names.
"""

from __future__ import annotations

from agentos.compat.upgrade_snapshot import (
    DEFAULT_KEEP,
    MANIFEST_NAME,
    MAX_FILE_BYTES,
    SNAPSHOT_PREFIX,
    DataCheck,
    SnapshotEntry,
    SnapshotResult,
    create_snapshot,
    latest_snapshot,
    list_snapshots,
    prune_snapshots,
    read_manifest,
    restore_snapshot,
    snapshots_root,
    state_databases,
    verify_state,
)

__all__ = [
    "DEFAULT_KEEP",
    "MANIFEST_NAME",
    "MAX_FILE_BYTES",
    "SNAPSHOT_PREFIX",
    "DataCheck",
    "SnapshotEntry",
    "SnapshotResult",
    "create_snapshot",
    "latest_snapshot",
    "list_snapshots",
    "prune_snapshots",
    "read_manifest",
    "restore_snapshot",
    "snapshots_root",
    "state_databases",
    "verify_state",
]
