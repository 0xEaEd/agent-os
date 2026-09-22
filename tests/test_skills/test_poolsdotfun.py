import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = (
    ROOT / "src" / "agentos" / "skills" / "bundled" / "poolsdotfun-token-launcher" / "scripts"
)


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
    return _load("poolsfun.fmt").parse_args


def test_parse_args_boolean_flag_does_not_consume_subcommand(parse_args):
    args = parse_args(["--json", "launch", "--name", "TestToken"])
    assert args.get("json") is True
    assert args.get("_") == ["launch"]
    assert args.get("name") == "TestToken"


def test_parse_args_multiple_boolean_flags_before_subcommand(parse_args):
    args = parse_args(["--debug", "--broadcast", "preflight", "--chain", "monad"])
    assert args.get("debug") is True
    assert args.get("broadcast") is True
    assert args.get("_") == ["preflight"]
    assert args.get("chain") == "monad"


def test_parse_args_boolean_flag_after_subcommand(parse_args):
    args = parse_args(["assets", "--json"])
    assert args.get("json") is True
    assert args.get("_") == ["assets"]
