"""Dump `.docx` structure as JSON for LLM consumption.

Stdlib + python-docx only. Cross-platform, stateless, exits 0 on success.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table, _Cell


def _extract_tables(doc: Document) -> list[list[list[str]]]:
    """Extract tables walking <w:tc> directly.

    Avoids ValueError on irregular grids and repeats on merged cells,
    and recurses into nested tables.
    """
    result: list[list[list[str]]] = []

    def _walk_table(tbl: Table) -> None:
        rows: list[list[str]] = []
        nested_to_walk: list[Table] = []
        for tr in tbl._tbl.tr_lst:
            row_cells: list[str] = []
            for tc in tr.tc_lst:
                cell = _Cell(tc, tbl)
                row_cells.append(cell.text)
                if cell.tables:
                    nested_to_walk.extend(cell.tables)
            if row_cells:
                rows.append(row_cells)
        if rows:
            result.append(rows)
        for nested in nested_to_walk:
            _walk_table(nested)

    for tbl in doc.tables:
        _walk_table(tbl)
    return result


def inspect(path: Path) -> dict[str, Any]:
    doc = Document(str(path))

    paragraphs: list[dict[str, Any]] = []
    for idx, para in enumerate(doc.paragraphs):
        paragraphs.append(
            {
                "index": idx,
                "text": para.text,
                "style": para.style.name if para.style is not None else "",
                "runs": [
                    {"text": run.text, "bold": bool(run.bold), "italic": bool(run.italic)}
                    for run in para.runs
                ],
            }
        )

    tables = _extract_tables(doc)

    body_xml = doc.element.body.xml if doc.element is not None else ""
    has_tracked_changes = "<w:ins" in body_xml or "<w:del" in body_xml

    return {
        "paragraphs": paragraphs,
        "tables": tables,
        "sections": len(doc.sections),
        "has_tracked_changes": has_tracked_changes,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dump .docx structure as JSON.")
    parser.add_argument("path", type=Path, help="Path to a .docx file")
    parser.add_argument(
        "--out", type=Path, default=None, help="Optional output JSON path; default stdout"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.path.is_file():
        print(f"error: {args.path} not found", file=sys.stderr)
        return 2
    if args.path.suffix.lower() != ".docx":
        print(f"error: expected .docx, got {args.path.suffix!r}", file=sys.stderr)
        return 2
    try:
        payload = inspect(args.path)
    except (PackageNotFoundError, BadZipFile, Exception) as exc:  # noqa: BLE001
        print(f"error: failed to parse {args.path}: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
