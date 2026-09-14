"""subtitle-burner builds a force_style libass can actually parse.

``--font`` takes a comma-separated fallback chain and documents "First
wins", but the whole chain was interpolated into ``FontName=``. Since
``force_style`` is itself a comma-separated list of ``key=value`` pairs,
every fallback after the first arrived as a nameless entry and the styling
quietly reverted to libass defaults -- with the shipped default font, on
every single run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "subtitle-burner"
    / "scripts"
    / "burn.py"
)


@pytest.fixture(scope="module")
def burn():
    spec = importlib.util.spec_from_file_location("subtitle_burn_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_shipped_default_chain_reduces_to_one_font(burn) -> None:
    assert burn.primary_font("Microsoft YaHei,SimHei,Arial Unicode MS,Arial") == "Microsoft YaHei"


def test_a_single_font_is_untouched(burn) -> None:
    assert burn.primary_font("Arial") == "Arial"


def test_surrounding_whitespace_is_trimmed(burn) -> None:
    assert burn.primary_font("  Noto Sans CJK , Arial ") == "Noto Sans CJK"


def test_a_leading_empty_entry_is_skipped(burn) -> None:
    assert burn.primary_font(",Arial") == "Arial"


def test_the_font_name_never_carries_a_comma_into_force_style(burn) -> None:
    """A comma here would be read as the start of the next style key."""
    assert "," not in burn.primary_font("Microsoft YaHei,SimHei,Arial")
