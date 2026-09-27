"""``run_sandboxed``'s wall-clock timeout must bound the whole process tree.

``run_sandboxed`` documents the wall-clock timeout as the one limit that
stays reliable even on platforms without :mod:`resource` (Windows): "Wall
time is enforced with ``subprocess.Popen.communicate``'s ``timeout``
argument; on timeout we kill the process group". Before this fix, only the
direct child was ever killed -- a command that backgrounds a job (the
ordinary ``cmd &`` shell idiom, not an adversarial construct) left a
grandchild holding the same stdout/stderr pipes, so the plain
``communicate()`` call blocked until *that* process exited on its own,
regardless of ``wall_seconds``. Confirmed directly: ``sh -c "(sleep 10 &);
exit 0"`` under a 1-second wall limit took just over 10 seconds to return.

``run_sandboxed`` is the real backend behind
:class:`agentos.sandbox.backend.noop.NoopBackend`, the fallback used
whenever namespace isolation (bubblewrap/seatbelt) is unavailable -- this
is not a theoretical-only code path.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from agentos.safety.sandbox import REASON_WALL_LIMIT, SandboxLimits, run_sandboxed

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix", reason="process-group backgrounding is a POSIX shell idiom"
)


@POSIX_ONLY
def test_a_backgrounded_grandchild_no_longer_blocks_past_wall_seconds() -> None:
    start = time.monotonic()
    result = run_sandboxed(
        ["sh", "-c", "(sleep 10 &) ; exit 0"],
        SandboxLimits(wall_seconds=1),
    )
    elapsed = time.monotonic() - start

    assert result.reason == REASON_WALL_LIMIT
    # Generous margin over the 1s limit for CI scheduling noise, but nowhere
    # near the unfixed behavior's ~10s (the backgrounded sleep's own duration).
    assert elapsed < 4.0, f"still blocked on the grandchild: {elapsed:.2f}s"


@POSIX_ONLY
def test_the_backgrounded_grandchild_is_actually_killed_not_just_orphaned(
    tmp_path: Path,
) -> None:
    """Returning promptly isn't enough on its own -- the grandchild itself
    must be killed too, or the sandbox's resource bound is still defeated
    even though the caller stops waiting for it."""
    marker = tmp_path / "grandchild-finished"
    result = run_sandboxed(
        ["sh", "-c", f"(sleep 0.4 && touch {marker!s} &) ; exit 0"],
        SandboxLimits(wall_seconds=0.1),
    )

    assert result.reason == REASON_WALL_LIMIT
    # Comfortably past the grandchild's own 0.4s sleep: if it survived the
    # kill, the marker would exist by now.
    time.sleep(0.8)
    assert not marker.exists(), "the backgrounded grandchild kept running after the kill"


def test_a_direct_non_backgrounding_timeout_is_unaffected() -> None:
    """The already-working case: a child with no grandchild of its own."""
    start = time.monotonic()
    result = run_sandboxed(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SandboxLimits(wall_seconds=1),
    )
    elapsed = time.monotonic() - start

    assert result.reason == REASON_WALL_LIMIT
    assert result.returncode != 0
    assert elapsed < 4.0


def test_normal_completion_is_unaffected_by_the_new_process_group() -> None:
    result = run_sandboxed([sys.executable, "-c", "print('ok')"])

    assert result.returncode == 0
    assert "ok" in result.stdout
