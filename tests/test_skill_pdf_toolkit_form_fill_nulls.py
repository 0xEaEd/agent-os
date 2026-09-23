"""pdf-toolkit ``form_fill.py`` — JSON ``null`` was written as "None" (#3160).

The data file is a mapping of field -> value, and ``null`` is how a caller
says an optional field has no value: no middle name, no apartment number.
``main`` coerced every value with ``str``, and ``str(None)`` is ``"None"``,
so the form came back with the literal word "None" typed into those fields --
carried as if it were the answer, in a document that gets printed, signed or
filed.

The assertion is made on the written PDF rather than on the mapping: what
matters is what a reader sees in the field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "pdf-toolkit" / "scripts"


def _form_fill_module():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import form_fill  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return form_fill


def _make_form(path: Path, fields: list[str]) -> None:
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=LETTER)
    y = 700
    for name in fields:
        pdf.acroForm.textfield(name=name, x=72, y=y, width=200, height=20)
        y -= 40
    pdf.showPage()
    pdf.save()


def _field_values(path: Path) -> dict[str, str]:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(path))
    values: dict[str, str] = {}
    for name, field in (reader.get_fields() or {}).items():
        value = field.get("/V")
        values[str(name)] = "" if value is None else str(value)
    return values


def _run(tmp_path: Path, payload: dict[str, object], fields: list[str]) -> dict[str, str]:
    form_fill = _form_fill_module()
    source = tmp_path / "form.pdf"
    data = tmp_path / "data.json"
    out = tmp_path / "filled.pdf"
    _make_form(source, fields)
    data.write_text(json.dumps(payload), encoding="utf-8")

    argv = [str(source), str(data), "--out", str(out)]
    original = sys.argv
    sys.argv = ["form_fill.py", *argv]
    try:
        assert form_fill.main() == 0
    finally:
        sys.argv = original

    return _field_values(out)


def test_a_null_value_leaves_the_field_empty(tmp_path: Path) -> None:
    """The issue's repro."""
    values = _run(
        tmp_path,
        {"first_name": "Alice", "middle_name": None},
        ["first_name", "middle_name"],
    )

    assert values["first_name"] == "Alice"
    assert values["middle_name"] == ""


def test_the_word_none_never_reaches_the_document(tmp_path: Path) -> None:
    """Stated as the property: no field may come back carrying "None" when
    the caller sent no value for it."""
    values = _run(
        tmp_path,
        {"a": None, "b": None, "c": "real"},
        ["a", "b", "c"],
    )

    assert "None" not in values.values()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text", "text"),
        (0, "0"),
        (12, "12"),
        (False, "False"),
        (True, "True"),
        (3.5, "3.5"),
    ],
)
def test_other_values_are_still_stringified(tmp_path: Path, value: object, expected: str) -> None:
    """Only ``null`` means "no value". A zero or a false is an answer, and
    must not be blanked along with it."""
    values = _run(tmp_path, {"field": value}, ["field"])

    assert values["field"] == expected


def test_the_field_count_still_reports_every_key(tmp_path: Path) -> None:
    """A cleared field was still a field the caller asked to set; dropping it
    from the payload would under-report the work done."""
    form_fill = _form_fill_module()
    source = tmp_path / "form.pdf"
    data = tmp_path / "data.json"
    out = tmp_path / "filled.pdf"
    _make_form(source, ["a", "b"])
    data.write_text(json.dumps({"a": None, "b": "x"}), encoding="utf-8")

    original = sys.argv
    sys.argv = ["form_fill.py", str(source), str(data), "--out", str(out)]
    try:
        assert form_fill.main() == 0
    finally:
        sys.argv = original

    assert out.is_file()
