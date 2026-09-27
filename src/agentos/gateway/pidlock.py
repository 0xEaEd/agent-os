"""PID file + exclusive lock for the gateway process.

Prevents two gateway instances from sharing the same STATE_DIR.

Design:
- The pid file (``gateway.pid``) is always readable: written atomically then fsynced.
- The lock file (``gateway.pid.lock``) carries the OS exclusive byte-range lock so the
  pid file itself stays open for readers even while the lock is held.
- The lock file is never unlinked. Both platform locks are keyed to the open
  handle/inode, not the path: a starter that opened the old inode and a starter
  that created a fresh one would each win their own lock and both proceed (#2119).
  A zero-byte anchor left on disk costs nothing; removing it costs exclusivity.
- The lock is taken *before* the pid file is reconciled. Whoever holds the lock
  is the live gateway, so a pid file found under a freshly won lock is stale by
  construction and no liveness probe is needed to decide whether to replace it.

Platform locking:
- Windows: msvcrt.locking(lock_fd, LK_NBLCK, 1) on gateway.pid.lock
- POSIX:   fcntl.flock(lock_fd, LOCK_EX | LOCK_NB) on gateway.pid.lock

Usage::

    lock = GatewayPidLock(state_dir)
    lock.acquire()          # raises SystemExit(1) if another live instance holds it
    # lock released automatically via atexit registered in acquire(); the OS drops
    # the handle lock itself if the process dies before atexit runs
"""

from __future__ import annotations

import atexit
import datetime
import json
import logging
import os
import sys
from pathlib import Path
from typing import IO

from agentos.application.gateway_pidlock import (
    LOCK_FILENAME,
    PID_FILENAME,
    live_gateway_pid,
    read_pid_from_path,
    try_lock,
    unlock,
)

log = logging.getLogger(__name__)

__all__ = ["GatewayPidLock", "live_gateway_pid"]

# The lock primitives and the liveness probe live in
# ``agentos.application.gateway_pidlock`` so the approval queue (application
# layer) can ask "does a live gateway own this state dir?" without importing
# the gateway package. They keep their historical names here for callers and
# tests.
_PID_FILENAME = PID_FILENAME
_LOCK_FILENAME = LOCK_FILENAME
_try_lock = try_lock
_unlock = unlock
_read_pid_from_path = read_pid_from_path


class GatewayPidLock:
    """Exclusive PID-file lock for one gateway instance per STATE_DIR."""

    def __init__(self, state_dir: str | Path) -> None:
        self._state_dir = Path(state_dir)
        self._pid_path = self._state_dir / _PID_FILENAME
        self._lock_path = self._state_dir / _LOCK_FILENAME
        self._lock_fh: IO[bytes] | None = None
        # Cached payload written by this instance (readable without reopening the file).
        self._written: dict | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> None:
        """Acquire the PID file lock.

        Algorithm:
        1. Acquire the exclusive OS lock on gateway.pid.lock (separate file so
           gateway.pid stays freely readable while the lock is held).
           - Lock fails → another gateway is live; SystemExit(1) naming its pid
             (read from gateway.pid) and the STATE_DIR.
        2. If gateway.pid still exists it belongs to a gateway that died without
           cleaning up (the holder would have kept the lock): log a warning and
           overwrite it.
        3. Write pid + start_ts (ISO 8601) to gateway.pid, fsync.
        4. Register atexit cleanup.
        """
        self._state_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 1: exclusive OS lock on the lock file ────────────────
        # Append mode: the anchor now outlives every holder, and truncating a
        # file whose first byte another process has locked fails on Windows
        # before _try_lock gets to report the contention properly.
        lock_fh = open(str(self._lock_path), "a+b")  # noqa: WPS515

        if not _try_lock(lock_fh):
            existing_pid = _read_pid_from_path(self._pid_path)
            lock_fh.close()
            log.error(
                "gateway.pidlock.already_running",
                extra={"pid": existing_pid, "state_dir": str(self._state_dir)},
            )
            pid_str = str(existing_pid) if existing_pid is not None else "unknown"
            print(
                f"ERROR: Another gateway is already running "
                f"(pid={pid_str}, state_dir={self._state_dir}). "
                f"Stop it first or remove {self._pid_path}.",
                file=sys.stderr,
            )
            sys.exit(1)

        self._lock_fh = lock_fh

        # ── Step 2: reconcile a leftover pid file ─────────────────────
        # We hold the lock, so nothing that wrote this file is still the live
        # gateway. Probing the pid would only add a way to get it wrong: a
        # false negative used to unlink a live gateway's pid file, and a reused
        # pid would refuse to start over a process that is not a gateway.
        if self._pid_path.exists():
            stale_pid = _read_pid_from_path(self._pid_path)
            log.warning(
                "gateway.pidlock.stale_overwritten",
                extra={"stale_pid": stale_pid, "state_dir": str(self._state_dir)},
            )

        # ── Step 3: write pid + start_ts to the pid file ─────────────
        self._write_pid()

        # ── Step 4: register cleanup ──────────────────────────────────
        atexit.register(self.release)

    def release(self) -> None:
        """Release the lock and remove the PID file. Safe to call multiple times.

        ``gateway.pid.lock`` deliberately stays on disk: it is the rendezvous
        point every starter locks against, and unlinking it hands the next
        opener a fresh inode with an independent lock.
        """
        if self._lock_fh is None:
            return
        fh = self._lock_fh
        self._lock_fh = None
        try:
            _unlock(fh)
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass
        try:
            self._pid_path.unlink(missing_ok=True)
        except OSError:
            pass

    @property
    def pid(self) -> int | None:
        """The PID written to the pid file by this instance, or None before acquire()."""
        return self._written.get("pid") if self._written else None

    @property
    def start_ts(self) -> str | None:
        """The start_ts written to the pid file by this instance, or None before acquire()."""
        return self._written.get("start_ts") if self._written else None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_pid(self) -> None:
        self._written = {
            "pid": os.getpid(),
            "start_ts": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        payload = json.dumps(self._written).encode()
        # Write to the pid file (not the lock file) so readers can open it freely.
        with open(str(self._pid_path), "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
