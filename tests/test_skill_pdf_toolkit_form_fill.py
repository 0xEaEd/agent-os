"""pdf-toolkit ``form_fill.py`` — an unusable data file is refused, not coerced to ``{}``.

A data file that was not a JSON object used to be silently treated as an
empty mapping: the script wrote a complete, entirely blank form and exited 0
(#1903).
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


def _make_form(path: Path) -> None:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=LETTER)
    c.acroForm.textfield(name="full_name", x=72, y=700, width=200, height=20)
    c.acroForm.textfield(name="city", x=72, y=660, width=200, height=20)
    c.showPage()
    c.save()


def _field_values(path: Path) -> dict[str, str]:
    from pypdf import PdfReader

    fields = PdfReader(str(path)).get_fields() or {}
    return {name: str(field.get("/V") or "") for name, field in fields.items()}


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data_text: str | bytes
) -> tuple[int, Path]:
    form_fill = _form_fill_module()
    form = tmp_path / "form.pdf"
    _make_form(form)
    data = tmp_path / "data.json"
    data.write_bytes(data_text if isinstance(data_text, bytes) else data_text.encode("utf-8"))
    out = tmp_path / "filled.pdf"
    monkeypatch.setattr(sys, "argv", ["form_fill.py", str(form), str(data), "--out", str(out)])
    return form_fill.main(), out


def test_object_data_file_fills_the_form(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, monkeypatch, json.dumps({"full_name": "Ada", "city": "Jakarta"}))

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"pages_processed": 1, "fields": 2}
    assert _field_values(out) == {"full_name": "Ada", "city": "Jakarta"}


@pytest.mark.parametrize(
    "data_text",
    [
        '["full_name", "Ada"]',
        '[{"full_name": "Ada"}]',
        '"Ada"',
        "42",
        "null",
    ],
)
def test_non_object_data_file_is_refused_and_nothing_is_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    data_text: str,
) -> None:
    code, out = _run(tmp_path, monkeypatch, data_text)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: data ")
    assert "JSON object" in captured.err
    assert not out.exists(), "a refused data file must not produce a blank form"


@pytest.mark.parametrize(
    "data_bytes",
    [
        b'{"full_name": "Ada",',
        # UTF-16LE with BOM — what PowerShell 5.1's Out-File writes by default.
        '{"full_name": "Ada"}'.encode("utf-16"),
    ],
)
def test_invalid_json_data_file_is_refused_with_a_clean_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    data_bytes: bytes,
) -> None:
    code, out = _run(tmp_path, monkeypatch, data_bytes)

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: data ")
    assert "not valid JSON" in captured.err
    assert not out.exists()
