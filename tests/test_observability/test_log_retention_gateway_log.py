"""The retention sweeper has to cover the one log nothing else bounds.

``DEFAULT_LOG_PATTERNS`` named ``agentos.log*``, a filename nothing in AgentOS
writes, while the gateway daemon's own stdout/stderr sink is
``~/.agentos/logs/gateway.log`` -- opened ``"ab"`` by ``cli/gateway_lifecycle.py``,
inherited by the child for its whole life, and rotated by nothing. It was
therefore never aged out and never counted against
``observability.log_retention_max_total_mb``.

It is also the one family that must be reclaimed by truncation rather than
``unlink``: the running daemon still holds the descriptor, so removing the path
would leave it appending into an orphaned inode whose bytes the filesystem keeps
charging and no path reaches.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from agentos.observability.retention import (
    DEFAULT_LOG_PATTERNS,
    prune_expired_log_files,
)

# Kilobyte-scale fixtures on purpose: `truncate` is sparse on ext4 but
# allocates for real on NTFS, and CI also runs windows-latest.
_KB = 1024


def _write(path: Path, size: int, age_days: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        fh.truncate(size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def test_no_pattern_names_a_file_agentos_never_writes() -> None:
    # The stale pattern is the whole bug: it looked like the gateway log was
    # covered while the real filename went unswept.
    assert "agentos.log*" not in DEFAULT_LOG_PATTERNS
    assert "gateway.log*" in DEFAULT_LOG_PATTERNS


def test_an_expired_gateway_log_is_examined_and_reclaimed(tmp_path: Path) -> None:
    gateway_log = _write(tmp_path / "gateway.log", 40 * _KB, age_days=100)

    result = prune_expired_log_files(log_dir=tmp_path, retention_days=14, max_total_bytes=500 * _KB)

    assert result.files_examined == 1
    assert result.files_pruned == 1
    assert result.bytes_freed == 40 * _KB
    assert gateway_log.stat().st_size == 0


def test_the_gateway_log_is_truncated_in_place_not_unlinked(tmp_path: Path) -> None:
    # A descriptor the daemon already holds must keep resolving to the same
    # inode, or its writes vanish into an orphan until the gateway restarts.
    gateway_log = _write(tmp_path / "gateway.log", 8 * _KB, age_days=100)
    inode_before = gateway_log.stat().st_ino

    with gateway_log.open("ab") as held:
        prune_expired_log_files(log_dir=tmp_path, retention_days=14, max_total_bytes=500 * _KB)
        held.write(b"after the sweep\n")
        held.flush()

    assert gateway_log.exists()
    assert gateway_log.stat().st_ino == inode_before
    assert gateway_log.read_bytes() == b"after the sweep\n"


def test_gateway_log_bytes_count_against_the_total_budget(tmp_path: Path) -> None:
    _write(tmp_path / "gateway.log", 400 * _KB, age_days=3)
    _write(tmp_path / "decisions-20260101.jsonl", 120 * _KB, age_days=2)

    result = prune_expired_log_files(
        log_dir=tmp_path,
        retention_days=0,  # TTL off: this is purely the size budget
        max_total_bytes=500 * _KB,
    )

    assert result.files_examined == 2
    assert result.files_pruned == 1
    remaining = sum(p.stat().st_size for p in tmp_path.iterdir())
    assert remaining <= 500 * _KB


def test_a_recently_written_gateway_log_survives_the_debounce(tmp_path: Path) -> None:
    gateway_log = _write(tmp_path / "gateway.log", 900 * _KB, age_days=0)

    result = prune_expired_log_files(
        log_dir=tmp_path,
        retention_days=14,
        max_total_bytes=100 * _KB,
        debounce_seconds=60.0,
    )

    assert result.files_pruned == 0
    assert gateway_log.stat().st_size == 900 * _KB


def test_an_expired_decisions_file_is_still_removed_outright(tmp_path: Path) -> None:
    # Positive control: the append-only carve-out must not turn the ordinary
    # families into truncate-in-place, which would leave empty files forever.
    decisions = _write(tmp_path / "decisions-20260101.jsonl", 10 * _KB, age_days=100)
    traces = _write(tmp_path / "traces-20260101.jsonl", 10 * _KB, age_days=100)

    result = prune_expired_log_files(log_dir=tmp_path, retention_days=14, max_total_bytes=500 * _KB)

    assert result.files_pruned == 2
    assert result.bytes_freed == 20 * _KB
    assert not decisions.exists()
    assert not traces.exists()


def test_debug_log_is_left_to_its_rotating_handler(tmp_path: Path) -> None:
    # Bounded by log_file_max_bytes x log_file_backup_count already; a second
    # owner deleting it out from under the handler would only lose lines.
    debug_log = _write(tmp_path / "debug.log", 50 * _KB, age_days=100)

    result = prune_expired_log_files(log_dir=tmp_path, retention_days=14, max_total_bytes=1 * _KB)

    assert result.files_examined == 0
    assert debug_log.stat().st_size == 50 * _KB


def test_an_already_empty_expired_gateway_log_is_not_rewritten(tmp_path: Path) -> None:
    gateway_log = _write(tmp_path / "gateway.log", 0, age_days=100)
    mtime_before = gateway_log.stat().st_mtime

    result = prune_expired_log_files(log_dir=tmp_path, retention_days=14, max_total_bytes=500 * _KB)

    assert result.bytes_freed == 0
    assert gateway_log.stat().st_mtime == mtime_before
