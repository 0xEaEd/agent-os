"""Gateway pid-file primitives shared by the gateway and the application layer.

The gateway's :class:`agentos.gateway.pidlock.GatewayPidLock` owns the
lifecycle (acquire, write, release). This module holds the parts other layers
need without importing the gateway package: the file names, the OS lock
primitives, and :func:`live_gateway_pid`, which the approval queue uses to
refuse writes from a process that shares a state directory with a running
gateway but has no approval surface of its own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import IO, Any, cast

PID_FILENAME = "gateway.pid"
LOCK_FILENAME = "gateway.pid.lock"

__all__ = [
    "LOCK_FILENAME",
    "PID_FILENAME",
    "live_gateway_pid",
    "read_pid_from_path",
    "try_lock",
    "unlock",
]


def live_gateway_pid(state_dir: str | Path) -> int | None:
    """Return the pid of a live gateway that owns ``state_dir`` and is not us.

    ``None`` when no gateway wrote a pid file, when the pid file is this
    process's own, or when it is a leftover from a gateway that died without
    cleaning up. Liveness is judged the way :meth:`GatewayPidLock.acquire`
    judges it — by whether the OS lock on ``gateway.pid.lock`` is still held —
    never by probing the pid, which a reused pid would get wrong.

    Used by the approval queue to refuse writes from a process that shares a
    state directory with a running gateway but has no approval surface of its
    own: such a row would surface as a prompt in the gateway's UI, asked of a
    user who never issued the command.
    """
    root = Path(state_dir)
    pid = read_pid_from_path(root / PID_FILENAME)
    if pid is None or pid == os.getpid():
        return None
    lock_path = root / LOCK_FILENAME
    if not lock_path.exists():
        return None
    try:
        fh = open(str(lock_path), "a+b")  # noqa: WPS515
    except OSError:
        # Cannot tell; a pid file with an unreadable lock anchor is treated
        # as live so the caller fails closed.
        return pid
    try:
        if try_lock(fh):
            unlock(fh)
            return None
        return pid
    finally:
        fh.close()


def try_lock(fh: IO[bytes]) -> bool:
    if os.name == "nt":
        import msvcrt

        msvcrt_mod = cast(Any, msvcrt)
        try:
            fh.seek(0)
            msvcrt_mod.locking(fh.fileno(), msvcrt_mod.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    else:
        import fcntl

        fcntl_mod = cast(Any, fcntl)
        try:
            fcntl_mod.flock(fh.fileno(), fcntl_mod.LOCK_EX | fcntl_mod.LOCK_NB)
            return True
        except OSError:
            return False


def unlock(fh: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt_mod = cast(Any, msvcrt)
        try:
            fh.seek(0)
            msvcrt_mod.locking(fh.fileno(), msvcrt_mod.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl_mod = cast(Any, fcntl)
        try:
            fcntl_mod.flock(fh.fileno(), fcntl_mod.LOCK_UN)
        except OSError:
            pass


def read_pid_from_path(path: Path) -> int | None:
    try:
        info = json.loads(path.read_bytes())
        return int(info["pid"])
    except Exception:  # noqa: BLE001
        return None
