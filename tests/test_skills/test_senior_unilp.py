import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "src" / "agentos" / "skills" / "bundled" / "senior-unilp-manager" / "scripts"


def _load(name: str):
    """Import a module from the skill's scripts dir without leaking it into sys.path.

    A permanent ``sys.path.insert`` at module scope survives pytest collection and
    shadows same-named bare modules (``selftest``) from sibling skills.
    """
    entry = str(SCRIPTS_DIR)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        return importlib.import_module(name)
    finally:
        if added:
            sys.path.remove(entry)


@pytest.fixture(scope="module")
def parse_args():
    return _load("unilp.fmt").parse_args


def test_parse_args_boolean_flag_does_not_consume_subcommand(parse_args):
    args = parse_args(["--json", "positions", "--chain", "ethereum"])
    assert args.get("json") is True
    assert args.get("_") == ["positions"]
    assert args.get("chain") == "ethereum"


def test_parse_args_include_v3_flag_before_subcommand(parse_args):
    args = parse_args(["--include-v3", "pools", "--min-tvl", "1000"])
    assert args.get("include-v3") is True
    assert args.get("_") == ["pools"]
    assert args.get("min-tvl") == "1000"


def test_parse_args_multiple_boolean_flags_before_subcommand(parse_args):
    args = parse_args(["--all-pools", "--no-hook", "--json", "pools"])
    assert args.get("all-pools") is True
    assert args.get("no-hook") is True
    assert args.get("json") is True
    assert args.get("_") == ["pools"]
