"""Regression tests for issue #1023: CSV/TSV multiline quoted field parsing."""

from __future__ import annotations

import csv
import threading
from pathlib import Path

import pytest

from agentos.tools.builtin.filesystem import _read_delimited_rows
from agentos.tools.types import ToolError

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

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert rows[1] == ["name", "description"]
    assert rows[2] == ["Alice", "Line one\nLine two\nLine three"]
    assert rows[3] == ["Bob", "Simple"]
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

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert rows[1] == ["key", "value"]
    assert rows[2] == ["item", "one, two, three"]
    assert rows[3] == ["other", "plain"]
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

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert rows[1] == ["id", "data"]
    assert rows[2] == ["1", "first, value\nsecond, value"]
    assert rows[3] == ["2", "ok"]
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

    [(_, rows, _)] = _read_delimited_rows(tsv_file, "\t")

    assert rows[1] == ["name", "notes"]
    assert rows[2] == ["Alice", "Line1\nLine2"]
    assert rows[3] == ["Bob", "ok"]
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

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert len(rows) == 2, f"Expected 2 rows for {label!r}, got {len(rows)}"
    assert rows[1] == ["a", "b"]
    assert rows[2] == [f"x{sep}y", "z"]


# ── Issue #1580: fields exceeding Python's default 128KB field limit ────


def test_field_exceeding_default_limit_is_parsed_not_crashed(tmp_path: Path) -> None:
    """The issue's exact reproduction: a single cell over 131,072 characters
    used to raise an unhandled _csv.Error instead of being parsed."""
    csv_file = tmp_path / "large_field.csv"
    csv_file.write_text("id,payload\n1," + "A" * 131_073, encoding="utf-8")

    [(_, rows, count)] = _read_delimited_rows(csv_file, ",")

    assert count == 2
    assert rows[1] == ["id", "payload"]
    assert rows[2] == ["1", "A" * 131_073]


def test_field_size_limit_is_restored_after_reading(tmp_path: Path) -> None:
    """csv.field_size_limit() is process-global; the excursion to
    accommodate a large field must not leak into later, unrelated csv use
    elsewhere in the process."""
    original_limit = csv.field_size_limit()
    csv_file = tmp_path / "large_field.csv"
    csv_file.write_text("id,payload\n1," + "A" * (original_limit * 2), encoding="utf-8")

    _read_delimited_rows(csv_file, ",")

    assert csv.field_size_limit() == original_limit


def test_field_size_limit_restored_even_when_parsing_raises(tmp_path: Path, monkeypatch) -> None:
    """The limit must be restored on the error path too, not only on success."""
    original_limit = csv.field_size_limit()
    csv_file = tmp_path / "large_field.csv"
    csv_file.write_text("id,payload\n1," + "A" * (original_limit * 2), encoding="utf-8")

    class _BoomReader:
        def __iter__(self):
            raise csv.Error("boom")

    monkeypatch.setattr(csv, "reader", lambda *a, **k: _BoomReader())

    with pytest.raises(ToolError, match="Cannot parse"):
        _read_delimited_rows(csv_file, ",")

    assert csv.field_size_limit() == original_limit


def test_concurrent_reads_do_not_race_on_the_global_field_size_limit(
    tmp_path: Path,
) -> None:
    """Two large-field reads running on separate threads must not observe
    each other's temporarily-raised limit or its restoration mid-parse."""
    original_limit = csv.field_size_limit()
    files = []
    for index in range(4):
        size = original_limit + 1000 * (index + 1)
        path = tmp_path / f"large_{index}.csv"
        path.write_text(f"id,payload\n{index}," + "A" * size, encoding="utf-8")
        files.append((path, size))

    results: list[BaseException | int] = [0] * len(files)

    def _worker(slot: int, path: Path, size: int) -> None:
        try:
            [(_, rows, _)] = _read_delimited_rows(path, ",")
            results[slot] = len(rows[2][1])
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            results[slot] = exc  # type: ignore[assignment]

    threads = [
        threading.Thread(target=_worker, args=(i, path, size))
        for i, (path, size) in enumerate(files)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for (path, size), result in zip(files, results, strict=True):
        assert result == size, f"{path.name}: expected field length {size}, got {result!r}"
    assert csv.field_size_limit() == original_limit
