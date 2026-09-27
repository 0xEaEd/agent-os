"""A fence rebalanced across a seam must still cut on a word/line boundary.

``split_text_for_limit`` documents its own contract as "the cut point is found
by binary search and then nudged back to the nearest line/word boundary so a
chunk doesn't end mid-word". ``_rebalance_open_fence`` -- the branch taken when
a fence opens the segment, i.e. every chunk after the first of a long code
block -- ran a fresh binary search and used it raw, so exactly the path where a
mid-word cut is most visible was the one that skipped the nudge: code is
rendered verbatim and the seam inserts a synthetic ``\\n``` between the halves,
so ``line_017 = compute(17)`` arrived as ``line_017 `` and ``= compute(17)`` on
separate lines of two separate messages.

The nudge must not reintroduce the non-advancing split #2127 guarded against,
so the loop-termination property is pinned here too.
"""

from __future__ import annotations

import pytest

from agentos.channels._util import split_text_for_limit

LIMIT = 400


def _code_block(lines: int = 60, lang: str = "py") -> str:
    body = "\n".join(f"line_{index:03d} = compute({index})" for index in range(lines))
    return f"```{lang}\n{body}\n```"


def _chunks(segment: str, limit: int = LIMIT) -> list[str]:
    out: list[str] = []
    remaining = segment
    for _ in range(500):
        head, tail = split_text_for_limit(remaining, limit)
        out.append(head)
        if not tail:
            return out
        assert len(tail) < len(remaining), "split must advance"
        remaining = tail
    raise AssertionError("splitter did not terminate")


def test_the_seam_lands_on_a_line_boundary() -> None:
    head, tail = split_text_for_limit(_code_block(), LIMIT)

    # The head's last code line is whole, and the tail's first one starts at
    # the beginning of a statement rather than in the middle of one.
    assert head.endswith("```")
    body_end = head[: -len("```")].rstrip("\n")
    assert body_end.splitlines()[-1] == "line_016 = compute(16)"
    assert tail.startswith("```py\nline_017 = compute(17)\n")


def test_no_code_line_is_broken_in_two_across_any_seam() -> None:
    """The visible symptom, checked over the whole block rather than one seam."""
    for chunk in _chunks(_code_block()):
        for line in chunk.splitlines():
            if line.startswith("```") or not line:
                continue
            assert line.startswith("line_"), f"cut mid-statement: {line!r}"
            assert line.endswith(")"), f"cut mid-statement: {line!r}"


def test_the_closing_fence_does_not_gain_a_blank_line() -> None:
    """A head already ending on a newline must not get ``\\n``` glued on.

    The blank line renders *inside* the delivered code block.
    """
    head, _tail = split_text_for_limit(_code_block(), LIMIT)

    assert not head.endswith("\n\n```")


def test_every_chunk_still_fits_the_limit_and_balances_its_fences() -> None:
    for chunk in _chunks(_code_block()):
        assert len(chunk) <= LIMIT
        assert chunk.count("```") % 2 == 0


def test_the_language_tag_still_rides_along() -> None:
    _head, tail = split_text_for_limit(_code_block(lang="python"), LIMIT)

    assert tail.startswith("```python\n")


@pytest.mark.parametrize("limit", [8, 12, 25, 60, 400])
def test_a_bare_fence_with_no_boundary_to_nudge_to_still_advances(limit: int) -> None:
    """#2127's guarantee: no boundary in reach must fall back, never loop.

    A run with no newline and no space offers the nudge nothing, and a tiny
    limit leaves no room for the closing marker at all. Both must terminate.
    """
    segment = "```" + ("x" * 500)

    assert _chunks(segment, limit)


def test_text_without_a_fence_is_untouched() -> None:
    plain = " ".join(f"word{index:03d}" for index in range(200))

    head, tail = split_text_for_limit(plain, LIMIT)

    assert head.endswith(" ")
    assert not head.rstrip().endswith("wor")
    assert tail.startswith("word")
