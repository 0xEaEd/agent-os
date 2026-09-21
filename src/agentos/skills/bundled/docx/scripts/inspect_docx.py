"""Dump `.docx` structure as JSON for LLM consumption.

Stdlib + python-docx only. Cross-platform, stateless, exits 0 on success.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.table import Table, _Cell
from lxml import etree


def _cell_text(cell: _Cell) -> str:
    """Joined paragraph texts of one `<w:tc>`, recursing into nested tables."""
    parts = [para.text for para in cell.paragraphs]
    for nested in cell.tables:
        for row in _iter_table_rows(nested):
            parts.extend(_cell_text(_Cell(tc, nested)) for tc in row)
    return "\n".join(part for part in parts if part != "")


def _iter_table_rows(table: Table) -> list[list[Any]]:
    """One `<w:tc>` list per `<w:tr>`, without resolving merged cells.

    ``row.cells`` resolves vertically merged cells against the row above and
    raises ``ValueError`` on the irregular grids other generators produce; it
    also repeats a horizontally merged cell once per grid column it spans.
    Walking the ``<w:tc>`` elements directly visits each cell exactly once.
    """
    return [list(tr.tc_lst) for tr in table._tbl.tr_lst]


def _write_stdout(text: str) -> None:
    """Write *text* to stdout as UTF-8, surviving a non-UTF-8 stdout encoding.

    ``print`` encodes through ``sys.stdout.encoding``, which on Windows is the
    console code page (cp1252, cp936, cp932) and not UTF-8, so a character
    outside that page raises ``UnicodeEncodeError`` before a byte is written —
    the document decides whether the skill runs. The binary buffer is therefore
    the primary path, matching the ``--out`` branch, which already passes
    ``encoding="utf-8"``. A stream without a usable ``buffer`` — a wrapper, or a
    captured stdout — still gets the text, escaped rather than lost.
    """
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(text.encode("utf-8"))
            buffer.flush()
            return
        except (AttributeError, OSError, ValueError):
            # Buffer closed or not writable — fall through to the text layer.
            pass

    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    # Lossless: unencodable chars become \\uXXXX escapes, not "?".
    sys.stdout.write(text.encode(encoding, errors="backslashreplace").decode(encoding))
    sys.stdout.flush()


def inspect(path: Path) -> dict[str, Any]:
    try:
        doc = Document(str(path))
    except (PackageNotFoundError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
        raise ValueError(f"not a readable .docx file: {path} ({exc})") from exc

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

    tables: list[list[list[str]]] = []
    for tbl in doc.tables:
        tables.append([[_cell_text(_Cell(tc, tbl)) for tc in row] for row in _iter_table_rows(tbl)])

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
    try:
        payload = inspect(args.path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        _write_stdout(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
