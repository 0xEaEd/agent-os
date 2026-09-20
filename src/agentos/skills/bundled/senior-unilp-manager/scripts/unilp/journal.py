"""Durable mandate state for the ratchet runner.

Three files per mandate, and the split between them is load-bearing:

``<id>.json``       the mandate — a materialized view, replaced atomically
``<id>.log.jsonl``  the write-ahead log — append-only, never replaced
``<id>.lock``       flock target — never replaced, never read

The ordering rule for every side effect is: append the *intent* record and fsync, do the
thing, append the *outcome* record and fsync, then replace the mandate. A crash before the
replace leaves ``log.lastSeq > mandate.lastSeq``, which :meth:`MandateStore.load` detects
and repairs by replaying the tail. A crash anywhere else is resolved against the chain, not
against these files — they record what we *tried*, and only the chain knows what happened.

**This is state, not cache.** It deliberately does not live under ``~/.cache`` like
``prices.py``: a cache cleaner deleting a half-fired mandate is a fund-loss bug, whereas
deleting a stale price is free.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .chains import load_env, state_root  # noqa: F401 — re-exported for existing callers
from .hexutil import to_hex
from .keccak import keccak256

SCHEMA_VERSION = 1

# How long a `claim` is considered fresh when reporting status. Purely cosmetic — flock is
# what actually prevents two runners colliding, and the OS releases it when a process dies.
CLAIM_TTL_SECS = 900

# A mandate id is exactly what `mandate_id` produces and nothing else.
MANDATE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# ``state_root`` moved to ``chains.py`` so the read path (poolcache) can share it
# without importing this module, which needs ``fcntl`` and so does not load on Windows.


def canonical_json(payload) -> str:
    """Stable serialisation for hashing. Sorted keys, no whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def mandate_id(immutable: dict) -> str:
    """16 bytes of keccak over the fields that define *which* mandate this is.

    Not ``plan_hash``: that truncates to 4 bytes, which is the right size for a human to
    read back off a terminal but far too small to serve as an identity — and this value
    names a file that authorizes unattended broadcasts.
    """
    digest = keccak256(to_hex(canonical_json(immutable).encode("utf-8")))
    return digest[2:34]


def jsonable(value):
    """Recursively stringify ints that would lose precision in a JSON number.

    ``lp_write.Big`` is an ``int`` subclass, so ``json.dumps`` emits it as a bare number and
    a 128-bit liquidity value silently rounds for anything reading the file with a JS-style
    parser. Everything above 2**53 becomes a string, matching ``lp_read``'s convention.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > 2**53 else int(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


class MandateStore:
    """One mandate's files, with the locking and durability rules applied."""

    def __init__(self, root: Path, chain_key: str, ident: str) -> None:
        # Validate BEFORE any path is built, not after. `--id` reaches here straight from
        # argv, and `Path / "../../x"` is a perfectly ordinary path that `unlink` will
        # happily follow out of the state directory. Pinning the shape to what `mandate_id`
        # emits removes the whole class rather than filtering for `..`.
        if not MANDATE_ID_RE.match(str(ident)):
            raise RuntimeError(
                f"{ident!r} is not a mandate id — expected 32 lowercase hex characters. "
                "Run `list` to see the ids that exist."
            )
        self.dir = Path(root) / "ratchet" / str(chain_key)
        self.id = str(ident)
        self.path = self.dir / f"{self.id}.json"
        self.log_path = self.dir / f"{self.id}.log.jsonl"
        # The lock lives on its own file because `save` calls os.replace, which swaps the
        # inode. A flock held on the mandate file itself would guard an inode that no longer
        # exists, and a second runner would acquire the "same" lock immediately.
        self.lock_path = self.dir / f"{self.id}.lock"

    # -- discovery ---------------------------------------------------------

    @classmethod
    def list_ids(cls, root: Path, chain_key: str) -> list[str]:
        directory = Path(root) / "ratchet" / str(chain_key)
        if not directory.is_dir():
            return []
        # Anything else in here is not ours — a stray file must not turn `tick --all` into
        # a crash that stops the mandates behind it from being serviced.
        return sorted(p.stem for p in directory.glob("*.json") if MANDATE_ID_RE.match(p.stem))

    # -- locking -----------------------------------------------------------

    @contextmanager
    def lock(self, blocking: bool = False):
        """Exclusive advisory lock for the whole tick, receipt wait included.

        Overlapping cron ticks are expected and are not an error: the loser exits quietly.
        Yields True when acquired, False when another runner holds it.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        handle = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        acquired = False
        try:
            flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
            try:
                fcntl.flock(handle, flags)
                acquired = True
            except OSError:
                acquired = False
            yield acquired
        finally:
            if acquired:
                fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)

    # -- mandate -----------------------------------------------------------

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict | None:
        try:
            with self.path.open(encoding="utf-8") as handle:
                mandate = json.load(handle)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"mandate {self.path} is unreadable ({exc}). Refusing to guess its state — "
                "inspect the file and the .log.jsonl beside it."
            ) from exc
        if not isinstance(mandate, dict):
            raise RuntimeError(f"mandate {self.path} is not an object")

        # The identity must survive a round trip through the file. This catches truncation
        # and hand-editing; it is not a defence against someone who can write this
        # directory, since that person can also read the dotenv holding the signing key.
        expected = mandate_id(mandate.get("immutable") or {})
        if expected != self.id:
            raise RuntimeError(
                f"mandate {self.path} does not hash to its own filename (recomputed "
                f"{expected}). The file was edited or corrupted."
            )
        return mandate

    def save(self, mandate: dict) -> None:
        """Atomic replace, with the fsyncs ``prices.py`` can afford to skip and this cannot."""
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(jsonable(mandate), indent=2, sort_keys=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.dir, delete=False
        )
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        # Fsync the directory too, or the rename itself can be lost on a power cut.
        directory = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        except OSError:
            pass
        finally:
            os.close(directory)

    def delete(self) -> None:
        for path in (self.path, self.log_path, self.lock_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # -- audit log ---------------------------------------------------------

    def append(self, record: dict) -> int:
        """Append one record and fsync. Returns the sequence number assigned."""
        self.dir.mkdir(parents=True, exist_ok=True)
        seq = self.last_seq() + 1
        entry = dict(record)
        entry["seq"] = seq
        entry.setdefault("ts", int(time.time()))
        entry.setdefault("mandateId", self.id)
        line = json.dumps(jsonable(entry), sort_keys=True) + "\n"
        # O_APPEND makes a single write atomic against other appenders, so a concurrent
        # runner that slipped past the lock still cannot interleave half a record.
        handle = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(handle, line.encode("utf-8"))
            os.fsync(handle)
        finally:
            os.close(handle)
        return seq

    def records(self) -> list[dict]:
        """Every journalled record, in order.

        Only the **final** line may be unreadable. A torn last line is what a power cut
        during the closing ``os.write`` looks like, and the record it lost is by definition
        the one whose side effect had not been confirmed yet — the recovery path handles
        that against the chain. A bad line anywhere *else* is different in kind: appends are
        sequential, so a later line exists only because that one was already complete. Its
        damage came from something other than a crash, and since the tail of this file is
        what restores in-flight state after a crash, silently dropping it could erase the
        only record that a transaction was signed. That one stops the runner.
        """
        try:
            text = self.log_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        lines = text.splitlines()
        out = []
        for index, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError as exc:
                if index == len(lines) - 1:
                    break  # torn final line from a power cut; everything before it stands
                raise RuntimeError(
                    f"{self.log_path} line {index + 1} is corrupt ({exc}). This is not a "
                    "torn tail — records after it parsed. Refusing to run on a journal "
                    "that may be missing a send record; inspect it by hand."
                ) from exc
            if isinstance(parsed, dict):
                out.append(parsed)
        return out

    def last_seq(self) -> int:
        records = self.records()
        return max((int(r.get("seq") or 0) for r in records), default=0)

    def tail(self, since_seq: int) -> list[dict]:
        return [r for r in self.records() if int(r.get("seq") or 0) > int(since_seq)]
