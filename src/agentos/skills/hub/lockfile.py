"""Lockfile management for installed skills."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypeVar

from agentos.paths import default_agentos_home

_T = TypeVar("_T")

#: Serializes threads *within* this process before they reach the OS file
#: lock below. See _locked() for why the file lock alone is not enough.
_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Serialize this process's threads, then take the cross-process file lock.

    The OS lock in :func:`_file_locked` is per-handle and only guards other
    processes. Within one process it is not merely redundant but actively
    unsafe on Windows: ``msvcrt.locking`` refuses to block on a region the
    calling process already holds, raising ``OSError(EDEADLOCK)`` instead of
    waiting, so a second thread's update fails outright and its entry is lost.
    POSIX ``flock`` blocks instead, which is why this only ever surfaced on
    Windows. Taking a process-wide mutex first means exactly one thread is
    ever inside the file lock, so the deadlock guard cannot trigger.
    """
    with _THREAD_LOCK, _file_locked(path):
        yield


@contextlib.contextmanager
def _file_locked(path: Path) -> Iterator[None]:
    """Hold an exclusive, cross-process OS lock for a lockfile read-modify-write.

    Two concurrent installs each doing `load()` -> mutate -> `save()` on the
    same lockfile race on that cycle: whichever writes last wins, silently
    discarding the other's entry. The lock lives on a sibling `*.lock` file
    (never `path` itself) so acquiring it never depends on `path` existing or
    being valid JSON, and releasing it never touches the data file. Mirrors
    the fcntl/msvcrt pattern already used for `_rate_limits.json` in
    `agentos.channel_pairing`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+b") as lock_file:

        def unlock() -> None:
            return None

        if os.name == "posix":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]

            def unlock() -> None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]

        elif os.name == "nt":  # pragma: no cover - exercised on Windows CI.
            import msvcrt

            if lock_file.seek(0, os.SEEK_END) == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]

            def unlock() -> None:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

        try:
            yield
        finally:
            unlock()


def default_lockfile_path() -> Path:
    """Return the shared skills lockfile path.

    Installer, CLI, and the Installed inventory must read the same file; each
    of them deriving it separately is how they drift apart.
    """
    return default_agentos_home() / "skills-lock.json"


@dataclass
class LockEntry:
    """A single installed skill entry in the lockfile."""

    source: str
    identifier: str
    version: str = ""
    installed_at: str = ""
    path: str = ""
    sha256: str = ""
    license: str = ""
    upstream_url: str = ""
    #: Publisher slug the installing catalog row claimed. A *selector*, not a
    #: description: it is looked up in the server-side allowlist
    #: (:func:`agentos.skills.publishers.resolve_publisher`) before anything is
    #: displayed, so a hub cannot mint brand identity by naming itself here any
    #: more than a ``SKILL.md`` can.
    publisher_id: str = ""
    #: Raw ``provider`` string from the catalog row, kept for diagnostics —
    #: it tells an operator which brand an *unrecognized* publisher claimed to
    #: be. Never rendered as branding; only :attr:`publisher_id` is resolved.
    publisher_name: str = ""
    source_trust: str = ""
    scan_verdict: str = ""
    scan_strategy: str = ""
    scan_findings: list[dict[str, str | int]] = field(default_factory=list)


@dataclass
class Lockfile:
    """Manages .agentos/skills-lock.json."""

    version: int = 1
    installed: dict[str, LockEntry] = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> Lockfile:
        if not path.exists():
            return Lockfile()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            lf = Lockfile(version=data.get("version", 1))
            # Every LockEntry field must be listed here or it is silently
            # dropped on load, however faithfully install-time wrote it.
            known_fields = {
                "source",
                "identifier",
                "version",
                "installed_at",
                "path",
                "sha256",
                "license",
                "upstream_url",
                "publisher_id",
                "publisher_name",
                "source_trust",
                "scan_verdict",
                "scan_strategy",
                "scan_findings",
            }
            for name, entry_data in data.get("installed", {}).items():
                filtered = {k: v for k, v in entry_data.items() if k in known_fields}
                lf.installed[name] = LockEntry(**filtered)
            return lf
        except (json.JSONDecodeError, TypeError, OSError):
            return Lockfile()

    def save(self, path: Path) -> None:
        """Write the lockfile atomically via a same-directory temp file + `os.replace`.

        A direct `path.write_text()` can leave a truncated file behind if the
        process is killed or the disk fills mid-write — and `load()`'s broad
        `except (..., OSError): return Lockfile()` then silently reports that
        truncated file as an *empty* lockfile, masking the corruption instead
        of surfacing it. Writing to a `.tmp` sibling and swapping it in with
        `os.replace` (atomic on both POSIX and Windows) means a failure
        leaves the previous valid file in place, never a half-written one.
        The temp file lives next to `path` so the replace stays on one
        filesystem.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.version,
            "installed": {name: asdict(entry) for name, entry in self.installed.items()},
        }
        payload = json.dumps(data, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    def add(self, name: str, entry: LockEntry) -> None:
        self.installed[name] = entry

    def remove(self, name: str) -> bool:
        if name in self.installed:
            del self.installed[name]
            return True
        return False

    def get(self, name: str) -> LockEntry | None:
        return self.installed.get(name)

    @classmethod
    def update(cls, path: Path, mutate: Callable[[Lockfile], _T]) -> _T:
        """Atomically read-modify-write the lockfile at `path`.

        Holds an exclusive lock across the whole `load()` -> `mutate()` ->
        `save()` cycle, so a second concurrent install/uninstall/update
        merges with the first's write instead of clobbering it. Every
        installer call site that used to do its own load/mutate/save should
        go through this instead. Returns whatever `mutate` returns, so
        call sites needing a result (e.g. `remove()`'s bool) still get it.
        """
        with _locked(path):
            lockfile = cls.load(path)
            result = mutate(lockfile)
            lockfile.save(path)
            return result


def compute_sha256(directory: Path) -> str:
    """Compute SHA-256 digest of all non-dotfiles in a directory."""
    hasher = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not any(p.startswith(".") for p in path.relative_to(directory).parts):
            hasher.update(str(path.relative_to(directory)).encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()
