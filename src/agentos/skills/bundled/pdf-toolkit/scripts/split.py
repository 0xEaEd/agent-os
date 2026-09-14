"""Split a PDF into multiple files by page-range spec.

Each disjoint range becomes one output file: <stem>_001.pdf, _002.pdf, ...

Usage:
    split.py input.pdf --pages "1-3,5,7-9" --out out_dir/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def _page_number(text: str, token: str) -> int:
    if not text.isdigit():
        raise ValueError(f"invalid page range {token!r}")
    return int(text)


def expand_token(token: str, total: int | None = None) -> range:
    """Expand one page token: ``7``, ``3-5``, ``-5`` (from 1), ``3-`` (to end)."""
    if "-" not in token:
        page = _page_number(token, token)
        return range(page, page + 1)
    lo_s, hi_s = (part.strip() for part in token.split("-", 1))
    # An omitted side is the usual way to write "up to" and "from here on";
    # int("") used to raise straight out of the CLI as a bare ValueError.
    lo = _page_number(lo_s, token) if lo_s else 1
    if hi_s:
        hi = _page_number(hi_s, token)
    elif total is None:
        raise ValueError(f"open-ended range {token!r} needs the page count")
    else:
        hi = total
    if lo > hi:
        lo, hi = hi, lo
    return range(lo, hi + 1)


def split_ranges(spec: str, total: int | None = None) -> list[list[int]]:
    groups: list[list[int]] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            continue
        groups.append(list(expand_token(token, total)))
    return groups


def split(input_path: Path, pages_spec: str, out_dir: Path) -> list[Path]:
    reader = PdfReader(str(input_path))
    total = len(reader.pages)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for idx, group in enumerate(split_ranges(pages_spec, total), start=1):
        valid_pages = [p for p in group if 1 <= p <= total]
        if not valid_pages:
            continue
        writer = PdfWriter()
        for page_num in valid_pages:
            writer.add_page(reader.pages[page_num - 1])
        out_path = out_dir / f"{input_path.stem}_{idx:03d}.pdf"
        with out_path.open("wb") as fh:
            writer.write(fh)
        written.append(out_path)
    return written


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split a PDF by page ranges.")
    parser.add_argument("input", type=Path)
    parser.add_argument("--pages", required=True, help="e.g. '1-3,5,7-9'")
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.input.is_file():
        print(f"error: input {args.input} not found", file=sys.stderr)
        return 2
    try:
        written = split(args.input, args.pages, args.out)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"files": [str(p) for p in written], "count": len(written)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
