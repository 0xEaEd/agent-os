"""Issue #3174: a GFM delimiter row of one or two dashes was not a table.

Both Markdown renderers spelled the delimiter cell ``^:?-{3,}:?$``. GFM asks
for *one or more* hyphens with an optional leading and/or trailing colon, so
``-``, ``--``, ``:-``, ``-:`` and ``:-:`` are all valid. A table written with
the compact spelling was not recognised as a table at all, and the raw pipes
and dashes were delivered to the reader as prose.

Both sites are covered here because they are one root cause with one fix:

* ``agentos.channels._telegram_formatting._TABLE_DELIMITER_RE``
* ``agentos.cli.tui.terminal.markdown_stream._TABLE_SEPARATOR_CELL_RE``
"""

from __future__ import annotations

import pytest

from agentos.channels._telegram_formatting import render_telegram_html
from agentos.cli.tui.terminal.markdown_stream import (
    MarkdownStreamRenderer,
    _is_table_separator_row,
)

_HEADER = "| Name | Qty |"
_ROW = "| Bolt | 12 |"


def _telegram(delimiter: str) -> str:
    return render_telegram_html(f"{_HEADER}\n{delimiter}\n{_ROW}")


@pytest.mark.parametrize(
    "delimiter",
    [
        "| - | - |",
        "| -- | -- |",
        "|:-:|:-:|",
        "| :- | -: |",
        "|---|---|",
        "| :--- | ---: |",
    ],
)
def test_telegram_renders_every_valid_gfm_delimiter_row(delimiter: str) -> None:
    rendered = _telegram(delimiter)

    assert rendered == "<b>Name — Qty</b>\n<b>Bolt:</b> 12"
    # The failure mode is silent passthrough, so pin it directly: no raw
    # delimiter may survive into what the user is sent.
    assert "|" not in rendered


def test_telegram_leaves_a_non_table_block_alone() -> None:
    """The looser cell pattern must not start claiming ordinary prose."""
    source = "not | a | table\nnor | this | one"

    assert render_telegram_html(source) == source


def test_telegram_a_dashless_delimiter_is_not_a_table() -> None:
    """``:`` on its own is not a delimiter cell; the pattern still needs a dash."""
    source = f"{_HEADER}\n| : | : |\n{_ROW}"

    assert render_telegram_html(source) == source


@pytest.mark.parametrize(
    "line",
    ["| - | - |", "| -- | -- |", "|:-:|:-:|", "| :- | -: |", "|---|---|"],
)
def test_terminal_accepts_every_valid_gfm_delimiter_row(line: str) -> None:
    assert _is_table_separator_row(line) is True


@pytest.mark.parametrize(
    "line",
    ["| ---not a separator | --- |", "| a | b |", "| -x- | --- |"],
)
def test_terminal_still_rejects_a_cell_that_only_starts_like_a_delimiter(
    line: str,
) -> None:
    """Loosening the dash count must not loosen the rest of the cell: a cell
    carrying anything beyond dashes and colons is not a delimiter."""
    assert _is_table_separator_row(line) is False


def test_terminal_renders_a_short_dash_table_as_a_table() -> None:
    renderer = MarkdownStreamRenderer(enabled=True)
    out = renderer.feed(f"{_HEADER}\n| - | - |\n{_ROW}\n") + renderer.flush()

    # A rendered table draws its separator with a box-drawing rule; an
    # unrecognised one would still carry the literal dashes.
    assert "─" in out
    assert "| - | - |" not in out
