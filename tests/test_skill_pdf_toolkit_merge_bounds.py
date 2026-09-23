"""pdf-toolkit ``merge.py`` — a page span is never expanded before it is clamped.

``requested_pages`` materialised the whole requested interval and ``merge``
called it once per entry to compute ``skipped``, so a manifest asking for
``"1-20000000"`` allocated twenty million ints (and listed every one of them as
skipped) to merge a two-page file: 79 s and 763 MiB in measurement, ``MemoryError``
for a larger bound (#3179). The sibling ``split.py`` had the same defect (#2996).
"""

from __future__ import annotations

import json
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "pdf-toolkit" / "scripts"

HUGE = "1-20000000"


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


@pytest.fixture
def two_pages(tmp_path: Path) -> Path:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, 2, "ALPHA")
    return pdf


def test_a_span_past_the_end_costs_nothing_to_parse() -> None:
    merge = _merge_module()

    tracemalloc.start()
    started = time.perf_counter()
    pages = merge.parse_ranges(HUGE, 2)
    elapsed = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    assert pages == [1, 2]
    assert elapsed < 1.0, f"clamping must not expand the span ({elapsed:.1f}s)"
    assert peak < 8 * 1024 * 1024, f"peak {peak / 1024 / 1024:.1f} MiB"


def test_page_spans_does_not_expand_a_span() -> None:
    merge = _merge_module()

    assert merge.page_spans("1-3,5,9-7", 2) == [(1, 3), (5, 5), (7, 9)]


def test_an_absent_spec_is_the_whole_document() -> None:
    merge = _merge_module()

    assert merge.page_spans(None, 4) == [(1, 4)]
    assert merge.parse_ranges(None, 4) == [1, 2, 3, 4]


def test_skipped_pages_are_capped_and_the_rest_counted() -> None:
    merge = _merge_module()

    listed, omitted = merge.skipped_pages(HUGE, 2)

    assert listed == list(range(3, 3 + merge.MAX_REPORTED_SKIPPED))
    assert len(listed) + omitted == 20_000_000 - 2


def test_a_short_overrun_is_still_listed_in_full() -> None:
    merge = _merge_module()

    assert merge.skipped_pages("1-5", 2) == ([3, 4, 5], 0)


def test_pages_below_one_count_as_skipped_too() -> None:
    merge = _merge_module()

    listed, omitted = merge.skipped_pages("0-0,1-2", 2)

    assert listed == [0]
    assert omitted == 0


def test_merge_with_a_huge_span_writes_the_pages_that_exist(
    two_pages: Path, tmp_path: Path
) -> None:
    merge = _merge_module()
    out = tmp_path / "merged.pdf"

    started = time.perf_counter()
    result = merge.merge([{"file": str(two_pages), "pages": HUGE}], out)
    elapsed = time.perf_counter() - started

    assert result.pages_written == 2
    assert out.is_file()
    assert elapsed < 5.0, f"merge must not expand the span ({elapsed:.1f}s)"
    assert result.skipped[0][0] == str(two_pages)
    assert len(result.skipped[0][1]) == merge.MAX_REPORTED_SKIPPED
    assert dict(result.skipped_omitted)[str(two_pages)] == 20_000_000 - 2 - 1000


def test_main_summary_reports_the_omitted_count(
    two_pages: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    merge = _merge_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"file": str(two_pages), "pages": HUGE}]), encoding="utf-8")
    out = tmp_path / "merged.pdf"
    monkeypatch.setattr(sys, "argv", ["merge.py", str(manifest), "--out", str(out)])

    assert merge.main() == 0

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    entry = summary["skipped_pages"][0]
    assert entry["file"] == str(two_pages)
    assert len(entry["pages"]) == merge.MAX_REPORTED_SKIPPED
    assert entry["omitted"] == 20_000_000 - 2 - 1000
    assert "more" in captured.err


def test_an_ordinary_summary_keeps_its_shape(
    two_pages: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No ``omitted`` key when nothing was capped."""
    merge = _merge_module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"file": str(two_pages), "pages": "1-4"}]), encoding="utf-8")
    out = tmp_path / "merged.pdf"
    monkeypatch.setattr(sys, "argv", ["merge.py", str(manifest), "--out", str(out)])

    assert merge.main() == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["skipped_pages"] == [{"file": str(two_pages), "pages": [3, 4]}]


def test_a_malformed_spec_is_still_reported_not_raised() -> None:
    merge = _merge_module()

    with pytest.raises(merge.PageSpecError):
        merge.page_spans("a-b", 2)
