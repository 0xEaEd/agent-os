"""pdf-toolkit ``merge.py`` — pages an input does not have are reported, never dropped.

A manifest range running past an input's last page used to contribute fewer
pages than asked for — or none at all — with exit 0, a clean stderr and a
summary that named only the count written. The sibling ``split.py`` already
reports exactly this under ``skipped_pages`` and refuses to produce nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "pdf-toolkit" / "scripts"


def _merge_module():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import merge  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return merge


def _make_pdf(path: Path, pages: int, tag: str) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=LETTER)
    for number in range(1, pages + 1):
        c.setFont("Helvetica", 14)
        c.drawString(72, 720, f"{tag} PAGE {number}")
        c.showPage()
    c.save()


def _page_count(path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(path)).pages)


@pytest.fixture
def three_and_two(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    _make_pdf(a, 3, "ALPHA")
    _make_pdf(b, 2, "BRAVO")
    return a, b


def test_an_in_range_merge_reports_nothing_skipped(
    three_and_two: tuple[Path, Path], tmp_path: Path
) -> None:
    merge = _merge_module()
    a, b = three_and_two

    result = merge.merge([{"file": str(a), "pages": "1-3"}, {"file": str(b)}], tmp_path / "c.pdf")

    assert result.pages_written == 5
    assert result.skipped == []
    assert result.missing_files == []
    assert _page_count(tmp_path / "c.pdf") == 5


def test_a_range_past_an_input_reports_the_pages_it_could_not_take(
    three_and_two: tuple[Path, Path], tmp_path: Path
) -> None:
    merge = _merge_module()
    a, b = three_and_two

    result = merge.merge(
        [{"file": str(a), "pages": "1-3"}, {"file": str(b), "pages": "5,7,9-11"}],
        tmp_path / "c.pdf",
    )

    assert result.pages_written == 3
    assert result.skipped == [(str(b), [5, 7, 9, 10, 11])]


def test_a_merge_with_no_page_in_range_writes_no_file(
    three_and_two: tuple[Path, Path], tmp_path: Path
) -> None:
    merge = _merge_module()
    _, b = three_and_two
    out = tmp_path / "nested" / "c.pdf"

    result = merge.merge([{"file": str(b), "pages": "9-11"}], out)

    assert result.pages_written == 0
    assert not out.exists(), "a zero-page PDF must not be left behind as if it were a merge"
    assert not out.parent.exists(), "nothing may be written, not even the output directory"


def test_main_summary_names_the_skipped_pages(
    three_and_two: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    merge = _merge_module()
    a, b = three_and_two
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps([{"file": str(a)}, {"file": str(b), "pages": "1,4"}]), encoding="utf-8"
    )
    out = tmp_path / "c.pdf"
    monkeypatch.setattr(sys, "argv", ["merge.py", str(manifest), "--out", str(out)])

    assert merge.main() == 0

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["pages_written"] == 4
    assert summary["skipped_pages"] == [{"file": str(b), "pages": [4]}]
    assert summary["missing_files"] == []
    assert "4" in captured.err, "dropped pages must be warned on stderr"


def test_main_fails_when_no_requested_page_exists(
    three_and_two: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    merge = _merge_module()
    _, b = three_and_two
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps([{"file": str(b), "pages": "9-11"}]), encoding="utf-8")
    out = tmp_path / "c.pdf"
    monkeypatch.setattr(sys, "argv", ["merge.py", str(manifest), "--out", str(out)])

    assert merge.main() == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error:")
    assert not out.exists()


def test_main_stays_quiet_on_stderr_when_nothing_was_dropped(
    three_and_two: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    merge = _merge_module()
    a, b = three_and_two
    out = tmp_path / "c.pdf"
    monkeypatch.setattr(sys, "argv", ["merge.py", str(a), str(b), "--out", str(out)])

    assert merge.main() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    summary = json.loads(captured.out)
    assert summary["pages_written"] == 5
    assert summary["skipped_pages"] == []


def test_requested_pages_keeps_what_parse_ranges_drops() -> None:
    merge = _merge_module()

    # Guard: ``parse_ranges`` keeps its published behaviour.
    assert merge.parse_ranges(None, 4) == [1, 2, 3, 4]
    assert merge.parse_ranges("1,99", 4) == [1]
    assert merge.requested_pages("1,99", 4) == [1, 99]
    assert merge.requested_pages(None, 3) == [1, 2, 3]
    assert merge.requested_pages("5-3", 10) == [3, 4, 5]
