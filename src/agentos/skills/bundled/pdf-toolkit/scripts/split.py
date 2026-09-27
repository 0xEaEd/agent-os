"""Split a PDF into multiple files by page-range spec.

Each disjoint range becomes one output file: <stem>_001.pdf, _002.pdf, ...

Usage:
    split.py input.pdf --pages "1-3,5,7-9" --out out_dir/

A range may leave one end open: ``7-`` runs to the last page, ``-3`` starts at
the first.

Pages past the end of the document are never written silently: the summary
lists them under ``skipped_pages``, and a spec with no page in range is an
error rather than an empty success.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

# Bundled scripts run under AgentOS's own interpreter; the path insert only
# matters in a source checkout where the package is not installed (#2804).
_SRC_ROOT = str(Path(__file__).resolve().parents[5])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from agentos.skill_stdio import write_stdout as _write_stdout  # noqa: E402


class PageSpecError(ValueError):
    """A ``--pages`` value that cannot be parsed. Reported as ``error:`` / exit
    2, never as a traceback: the caller passed bad input, the script did not
    break."""


def _page_number(token: str, spec: str) -> int:
    """Parse one page number, or raise :class:`PageSpecError` naming the flag."""
    try:
        return int(token)
    except ValueError:
        hint = ""
        if any(dash in token for dash in "–—−"):
            # A model writes an en dash more often than one would like, and it
            # is invisible in a diff: the token never splits, so the whole
            # thing lands in int().
            hint = " (that looks like an en/em dash; ranges use a plain '-')"
        raise PageSpecError(
            f"invalid --pages value {spec!r}: {token.strip()!r} is not a page "
            f"number{hint}; expected 1-based numbers and ranges, e.g. '1-3,5'"
        ) from None


def _range_bounds(token: str, spec: str) -> tuple[int | None, int | None]:
    """Both ends of ``lo-hi``, or raise :class:`PageSpecError`.

    Either end may be left open -- ``3-`` runs to the last page, ``-5`` starts
    at the first -- and comes back as ``None``: closing it needs the document's
    page count, which :func:`page_spans` has and this does not. A bare ``-``
    names no page at all and is rejected as such rather than reported as
    ``'' is not a page number``.
    """
    lo_s, hi_s = token.split("-", 1)
    lo_s, hi_s = lo_s.strip(), hi_s.strip()
    if not lo_s and not hi_s:
        raise PageSpecError(
            f"invalid --pages value {spec!r}: open-ended range {token!r} gives "
            f"neither end; e.g. '3-7', '3-' (to the last page) or '-7' (from the first)"
        )
    return (
        _page_number(lo_s, spec) if lo_s else None,
        _page_number(hi_s, spec) if hi_s else None,
    )


def _parse_spec(spec: str) -> list[tuple[int | None, int | None]]:
    """Parse ``'1-3,5,7-'`` into inclusive spans, with an open end as ``None``.

    Needs no document, so :func:`split` runs it before opening one and every
    malformed spec is named up front. A reversed range is normalised (``5-3``
    is ``3-5``); an open-ended one is never turned around.
    """
    spans: list[tuple[int | None, int | None]] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        lo: int | None
        hi: int | None
        if "-" in token:
            lo, hi = _range_bounds(token, spec)
            if lo is not None and hi is not None and lo > hi:
                lo, hi = hi, lo
        else:
            lo = hi = _page_number(token, spec)
        spans.append((lo, hi))
    return spans


def _close_spans(spans: list[tuple[int | None, int | None]], total: int) -> list[tuple[int, int]]:
    """Close every open end against a document of *total* pages.

    ``N-`` becomes ``N..total`` and ``-M`` becomes ``1..M``. When the end that
    was given already lies outside the document (``9-`` on five pages, ``-0``),
    the span is just that page, so it is reported as skipped -- ``9-`` is never
    turned around into ``5-9``.
    """
    closed: list[tuple[int, int]] = []
    for lo, hi in spans:
        if lo is not None and hi is not None:
            closed.append((lo, hi))
        elif lo is not None:
            closed.append((lo, max(lo, total)))
        elif hi is not None:
            closed.append((min(1, hi), hi))
    return closed


def page_spans(spec: str, total: int) -> list[tuple[int, int]]:
    """Parse ``'1-3,5,7-'`` into inclusive ``(lo, hi)`` spans, without expanding them.

    Open ends are closed against *total*. A span is two numbers; the pages it
    covers are only ever materialised after clamping to the document, so a spec
    naming more pages than exist costs nothing to parse. A *spec* that cannot
    be parsed raises :class:`PageSpecError`.
    """
    return _close_spans(_parse_spec(spec), total)


def split_ranges(spec: str) -> list[list[int]]:
    """Every page each range of *spec* names, in order, without clamping.

    Unbounded, and no longer used by :func:`split`: expanding a span before the
    document's length was known let ``1-100000000`` allocate a hundred million
    ints to split a five-page file (#2996). Kept for callers outside this
    script; new code wants :func:`page_spans`. An open-ended range cannot be
    expanded without a page count, so it raises :class:`PageSpecError` here.
    """
    groups: list[list[int]] = []
    for lo, hi in _parse_spec(spec):
        if lo is None or hi is None:
            raise PageSpecError(
                f"invalid --pages value {spec!r}: an open-ended range needs the "
                f"document's page count; use page_spans(spec, total)"
            )
        groups.append(list(range(lo, hi + 1)))
    return groups


#: How many out-of-range pages ``skipped_pages`` lists one by one. Beyond this
#: the rest are counted in ``skipped_pages_omitted`` instead of enumerated, so a
#: runaway span cannot turn the summary into a hundred-million-entry list.
MAX_REPORTED_SKIPPED = 1000


@dataclass
class SplitResult:
    """What a split actually produced, including what it could not."""

    total_pages: int
    parts: list[tuple[Path, list[int]]] = field(default_factory=list)
    skipped_pages: list[int] = field(default_factory=list)
    #: Out-of-range pages counted but not listed, past MAX_REPORTED_SKIPPED.
    skipped_pages_omitted: int = 0

    def skip(self, lo: int, hi: int) -> None:
        """Record the inclusive span ``lo..hi`` as skipped, listing at most the cap."""
        if lo > hi:
            return
        room = max(0, MAX_REPORTED_SKIPPED - len(self.skipped_pages))
        listed = min(room, hi - lo + 1)
        self.skipped_pages.extend(range(lo, lo + listed))
        self.skipped_pages_omitted += (hi - lo + 1) - listed

    @property
    def files(self) -> list[Path]:
        return [path for path, _ in self.parts]


def split(input_path: Path, pages_spec: str, out_dir: Path) -> SplitResult:
    # Parse before opening the document: an unparseable spec is the caller's
    # mistake, and it should be named before any work is done on their behalf.
    spans = _parse_spec(pages_spec)
    reader = PdfReader(str(input_path))
    result = SplitResult(total_pages=len(reader.pages))
    total = result.total_pages
    # Only the part of a span the document has is ever expanded; what falls
    # outside it is recorded as a span, so ``1-100000000`` costs five pages.
    for lo, hi in _close_spans(spans, total):
        result.skip(lo, min(hi, 0))  # before page 1
        valid_pages = list(range(max(lo, 1), min(hi, total) + 1))
        result.skip(max(lo, total + 1), hi)  # past the last page
        if not valid_pages:
            continue
        writer = PdfWriter()
        for page_num in valid_pages:
            writer.add_page(reader.pages[page_num - 1])
        # Number the files that exist, not the groups in the spec: a caller
        # globbing the output directory expects _001 to be the first part.
        out_path = out_dir / f"{input_path.stem}_{len(result.parts) + 1:03d}.pdf"
        # Created here, not up front, so a spec with no page in range leaves
        # nothing behind — not even an empty directory.
        out_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as fh:
            writer.write(fh)
        result.parts.append((out_path, valid_pages))
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a PDF by page ranges.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--pages", required=True, help="e.g. '1-3,5,7-9' or '4-'")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.input.is_file():
        print(f"error: input {args.input} not found", file=sys.stderr)
        return 2
    try:
        result = split(args.input, args.pages, args.out)
    except PageSpecError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not result.parts:
        print(
            f"error: no page in {args.pages!r} exists in {args.input} ({result.total_pages} pages)",
            file=sys.stderr,
        )
        return 2
    if result.skipped_pages:
        skipped = ", ".join(str(p) for p in result.skipped_pages)
        if result.skipped_pages_omitted:
            skipped += f" (and {result.skipped_pages_omitted:,} more)"
        print(
            f"warn: skipped pages outside 1-{result.total_pages} of {args.input}: {skipped}",
            file=sys.stderr,
        )
    _write_stdout(
        json.dumps(
            {
                "files": [str(p) for p in result.files],
                "count": len(result.parts),
                "parts": [{"file": str(p), "pages": pages} for p, pages in result.parts],
                "skipped_pages": result.skipped_pages,
                "total_pages": result.total_pages,
                # Only present when the list was capped, so an ordinary summary
                # keeps exactly the shape it had before.
                **(
                    {"skipped_pages_omitted": result.skipped_pages_omitted}
                    if result.skipped_pages_omitted
                    else {}
                ),
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
