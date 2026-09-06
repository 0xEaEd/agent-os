"""Offline regression tests for the gmgn-holder-analysis script (issue #957).

``analyze.py`` indexed ``sys.argv[1]`` and ``sys.argv[2]`` at import time with
no length check, so running it with no arguments -- or with ``--help`` -- died
with an unhandled ``IndexError`` traceback instead of printing usage. Mirrors
the guard the sibling gmgn-wallet-score script already carries.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "gmgn-holder-analysis"
    / "scripts"
    / "analyze.py"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "args",
    [pytest.param((), id="no-args"), pytest.param(("0xabc",), id="one-arg")],
)
def test_missing_args_print_usage_and_exit_2(args: tuple[str, ...]) -> None:
    result = _run(*args)
    assert result.returncode == 2
    assert "Usage:" in result.stderr
    assert "<token_address>" in result.stderr
    assert "<chain>" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flag_prints_usage_and_exits_0(flag: str) -> None:
    result = _run(flag)
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "<token_address>" in result.stdout
    assert "Traceback" not in result.stderr
