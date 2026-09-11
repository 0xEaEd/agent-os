from __future__ import annotations

import pytest

from agentos.tools.builtin.media import _parse_page_range
from agentos.tools.types import SafeToolError


def test_parse_page_range_accepts_spaces_around_hyphen() -> None:
    # Page range with spaces around hyphen
    indices = _parse_page_range("1 - 5", 10)
    assert indices == [0, 1, 2, 3, 4]


def test_parse_page_range_multiple_segments_with_spaces() -> None:
    # Multiple segments with arbitrary whitespace
    indices = _parse_page_range(" 1 - 3 , 5 - 6 , 8 ", 10)
    assert indices == [0, 1, 2, 4, 5, 7]


def test_parse_page_range_invalid_syntax_raises_safetoolerror() -> None:
    with pytest.raises(SafeToolError, match="Invalid page range"):
        _parse_page_range("1 - abc", 10)

    with pytest.raises(SafeToolError, match="Invalid page range"):
        _parse_page_range("5 - 2", 10)


def test_parse_page_range_exceeds_total_raises_safetoolerror() -> None:
    with pytest.raises(SafeToolError, match="exceeds document length"):
        _parse_page_range("1 - 15", 10)
