"""Regression tests for issue #1023: CSV/TSV multiline quoted field parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.tools.builtin.filesystem import _read_delimited_rows

# ── Multiline quoted field ──────────────────────────────────────────────


def test_csv_multiline_quoted_field_stays_single_row(tmp_path: Path) -> None:
    """A quoted field with embedded newlines must be parsed as one cell."""
    csv_content = (
        'name,description\n'
        '"Alice","Line one\nLine two\nLine three"\n'
        '"Bob","Simple"\n'
    )
    csv_file = tmp_path / "multi.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows)] = _read_delimited_rows(csv_file, ",")

    assert rows[0] == ["name", "description"]
    assert rows[1] == ["Alice", "Line one\nLine two\nLine three"]
    assert rows[2] == ["Bob", "Simple"]
    assert len(rows) == 3


# ── Delimiter inside quoted field ───────────────────────────────────────


def test_csv_delimiter_inside_quoted_field(tmp_path: Path) -> None:
    """A comma inside a quoted field must not be treated as a column split."""
    csv_content = (
        'key,value\n'
        '"item","one, two, three"\n'
        '"other","plain"\n'
    )
    csv_file = tmp_path / "delim.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows)] = _read_delimited_rows(csv_file, ",")

    assert rows[0] == ["key", "value"]
    assert rows[1] == ["item", "one, two, three"]
    assert rows[2] == ["other", "plain"]
    assert len(rows) == 3


# ── Both: multiline + delimiter in the same field ───────────────────────


def test_csv_multiline_and_delimiter_in_same_field(tmp_path: Path) -> None:
    """A field containing both embedded newlines and the delimiter character."""
    csv_content = (
        'id,data\n'
        '"1","first, value\nsecond, value"\n'
        '"2","ok"\n'
    )
    csv_file = tmp_path / "combo.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows)] = _read_delimited_rows(csv_file, ",")

    assert rows[0] == ["id", "data"]
    assert rows[1] == ["1", "first, value\nsecond, value"]
    assert rows[2] == ["2", "ok"]
    assert len(rows) == 3


# ── TSV variant ─────────────────────────────────────────────────────────


def test_tsv_multiline_quoted_field(tmp_path: Path) -> None:
    """Same bug applied to TSV files with tab delimiter."""
    tsv_content = (
        "name\tnotes\n"
        '"Alice"\t"Line1\nLine2"\n'
        '"Bob"\t"ok"\n'
    )
    tsv_file = tmp_path / "multi.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    [(_, rows)] = _read_delimited_rows(tsv_file, "\t")

    assert rows[0] == ["name", "notes"]
    assert rows[1] == ["Alice", "Line1\nLine2"]
    assert rows[2] == ["Bob", "ok"]
    assert len(rows) == 3


# ── Unicode line separators ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "sep,label",
    [
        ("\v", "vertical-tab"),
        ("\f", "form-feed"),
        ("\x1c", "file-separator"),
        ("\x1d", "group-separator"),
        ("\x1e", "record-separator"),
        ("\x85", "next-line"),
        ("\u2028", "line-separator"),
        ("\u2029", "paragraph-separator"),
    ],
)
def test_unicode_line_separator_inside_field_not_treated_as_row_break(
    tmp_path: Path, sep: str, label: str
) -> None:
    """splitlines() splits on these; csv.reader(StringIO) must not."""
    csv_content = f'a,b\n"x{sep}y","z"\n'
    csv_file = tmp_path / f"unicode_{label}.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows)] = _read_delimited_rows(csv_file, ",")

    assert len(rows) == 2, f"Expected 2 rows for {label!r}, got {len(rows)}"
    assert rows[0] == ["a", "b"]
    assert rows[1] == [f"x{sep}y", "z"]


# ── Large fields exceeding default 128KB limit (#1580) ──────────────────


def test_csv_field_larger_than_128kb_succeeds(tmp_path: Path) -> None:
    """Fields exceeding standard library 131,072-char limit must parse (#1580)."""
    large_payload = "A" * 150_000
    csv_content = f"id,payload\n1,{large_payload}\n"
    csv_file = tmp_path / "large.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows)] = _read_delimited_rows(csv_file, ",")

    assert len(rows) == 2
    assert rows[0] == ["id", "payload"]
    assert rows[1] == ["1", large_payload]


@pytest.mark.asyncio
async def test_read_spreadsheet_large_field_end_to_end(tmp_path: Path) -> None:
    """read_spreadsheet must return formatted output for CSV with large fields (#1580)."""
    from agentos.tools.builtin.filesystem import read_spreadsheet

    large_payload = "X" * 140_000
    csv_file = tmp_path / "large_e2e.csv"
    csv_file.write_text(f"col1,col2\nval1,{large_payload}\n", encoding="utf-8")

    result = await read_spreadsheet(str(csv_file))
    assert "Sheet: large_e2e.csv (2 rows x 2 columns)" in result
    assert "val1" in result
    assert large_payload in result


def test_csv_error_translated_to_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """csv.Error during delimited row parsing must raise ToolError (#1580)."""
    import csv

    from agentos.tools.types import ToolError

    csv_file = tmp_path / "corrupt.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")

    def _failing_reader(*args: object, **kwargs: object) -> object:
        raise csv.Error("simulated csv parser error")

    monkeypatch.setattr(csv, "reader", _failing_reader)

    with pytest.raises(
        ToolError, match="Cannot parse spreadsheet corrupt.csv: simulated csv parser error"
    ):
        _read_delimited_rows(csv_file, ",")
