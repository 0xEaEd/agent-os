"""gmgn-wallet-analysis: a trailing valueless flag became the chain (#3431).

``analyze.py``'s argument loop recognised a flag by *name and a value after
it*::

    if args[k] == "--latency" and k + 1 < len(args):

so a value-taking flag in last position failed its own branch, fell through
to the positional branch, and was appended to ``rest``. With
``analyze.py <wallet> --latency`` that makes ``rest == [wallet, "--latency"]``
and the unpacking ``wallet, chain = rest[0], rest[1]`` reads the flag name as
the chain -- the run then analyses ``--latency``, with no error anywhere.

Same shape as #2863 in ``poolsdotfun``, fixed in #2911: a flag must be
recognised by its name, and a missing value reported.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
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
    / "gmgn-wallet-analysis"
    / "scripts"
    / "analyze.py"
)


def _analyze():
    src_root = str(ROOT / "src")
    if src_root not in sys.path:
        sys.path.insert(0, src_root)
    spec = importlib.util.spec_from_file_location("gmgn_analyze", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gmgn_analyze"] = module
    spec.loader.exec_module(module)
    return module


def _run(argv: list[str]) -> tuple[int, str]:
    """Run ``main`` with no network: every case here must exit before the
    first API call."""
    module = _analyze()
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.main(["analyze.py", *argv])
    return code, out.getvalue() + err.getvalue()


@pytest.mark.parametrize("flag", ["--latency", "--size", "--fixture"])
def test_a_trailing_flag_without_a_value_is_an_error(flag: str) -> None:
    """The issue's repro. Previously this ran with ``chain=<flag name>``."""
    code, output = _run(["0xWALLET", flag])

    assert code == 2
    assert flag in output


@pytest.mark.parametrize("flag", ["--latency", "--size", "--fixture"])
def test_the_flag_name_never_becomes_a_positional(flag: str) -> None:
    """Stated as the property: whatever happens, the run must not proceed
    with the flag name standing in for the chain."""
    code, output = _run(["0xWALLET", flag])

    assert code != 0
    assert "Data pull failed" not in output  # i.e. it never reached collect()


def test_a_flag_followed_by_a_value_still_parses() -> None:
    """The value-taking branch is unchanged; this exits on the missing
    positional, not on the flag."""
    code, output = _run(["--latency", "5"])

    assert code == 2
    # The usage text (which itself mentions --latency), not the flag error.
    assert "needs a value" not in output


def test_a_valueless_flag_is_unaffected() -> None:
    """``--brief`` takes no value and must still be accepted anywhere."""
    code, _ = _run(["--brief"])

    assert code == 2  # usage: no wallet/chain given


def test_too_few_positionals_still_prints_usage() -> None:
    code, output = _run(["0xWALLET"])

    assert code == 2
    assert "needs a value" not in output
