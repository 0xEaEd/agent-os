"""Issue #3487: an offset-aware ISO timestamp destroyed the output workbook.

``create_xlsx`` and ``edit_xlsx`` parse ISO-looking strings with
``datetime.fromisoformat``. A ``Z`` / ``+HH:MM`` suffix yields an offset-aware
datetime, which openpyxl refuses partway through ``wb.save()`` (``TypeError:
Excel does not support timezones``). ``wb.save`` had already truncated its
destination by then, so the run left a broken zip behind -- and with ``--out``
pointing at the input, the caller's only copy was gone.

Two guards: the offset is dropped before assignment (the wall-clock time is
stored), and both scripts save through a temp file beside the destination
that only replaces it once the save has succeeded.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "xlsx" / "scripts"

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits / symlinks")


def _scripts() -> tuple[Any, Any]:
    sys.path.insert(0, str(SCRIPTS))
    try:
        import create_xlsx  # type: ignore[import-not-found]
        import edit_xlsx  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return create_xlsx, edit_xlsx


def _script(name: str) -> Any:
    create_xlsx, edit_xlsx = _scripts()
    return {"create_xlsx": create_xlsx, "edit_xlsx": edit_xlsx}[name]


def _make_book(path: Path, value: Any = "keep me") -> Path:
    create_xlsx, _ = _scripts()
    create_xlsx.build({"sheets": [{"name": "S", "rows": [[value]]}]}).save(str(path))
    return path


def _a1(path: Path) -> Any:
    return load_workbook(str(path))["S"]["A1"].value


def _is_workbook(path: Path) -> bool:
    with zipfile.ZipFile(str(path)) as zf:
        return "[Content_Types].xml" in zf.namelist()


def _edit_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    src: Path,
    out: Path,
    ops: list[dict[str, Any]],
) -> int:
    _, edit_xlsx = _scripts()
    ops_path = tmp_path / "ops.json"
    ops_path.write_text(json.dumps(ops), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["edit_xlsx.py", str(src), str(ops_path), "--out", str(out)])
    return int(edit_xlsx.main())


def _set_a1(value: Any) -> list[dict[str, Any]]:
    return [{"op": "set_cell", "sheet": "S", "row": 1, "col": 1, "value": value}]


TZ_STAMPS = [
    pytest.param("2026-01-02T03:04:05Z", id="zulu"),
    pytest.param("2026-01-02T03:04:05+00:00", id="utc_offset"),
    pytest.param("2026-01-02T03:04:05+07:00", id="positive_offset"),
    pytest.param("2026-01-02T03:04:05-05:30", id="negative_offset"),
]


@pytest.mark.parametrize("stamp", TZ_STAMPS)
def test_set_cell_stores_an_offset_aware_stamp_as_wall_clock(
    stamp: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _make_book(tmp_path / "in.xlsx")
    out = tmp_path / "out.xlsx"

    rc = _edit_cli(monkeypatch, tmp_path, src, out, _set_a1(stamp))

    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"applied": 1}
    assert _is_workbook(out)
    # The time as written, not converted to UTC or local time.
    assert _a1(out) == datetime(2026, 1, 2, 3, 4, 5)


@pytest.mark.parametrize("stamp", TZ_STAMPS)
def test_create_xlsx_stores_an_offset_aware_stamp_as_wall_clock(
    stamp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_xlsx, _ = _scripts()
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"sheets": [{"name": "S", "rows": [[stamp]]}]}), encoding="utf-8")
    out = tmp_path / "out.xlsx"
    monkeypatch.setattr(sys, "argv", ["create_xlsx.py", str(spec), "--out", str(out)])

    assert create_xlsx.main() == 0
    assert _is_workbook(out)
    assert _a1(out) == datetime(2026, 1, 2, 3, 4, 5)


def test_as_text_still_keeps_an_offset_aware_stamp_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _make_book(tmp_path / "in.xlsx")
    out = tmp_path / "out.xlsx"
    ops = [{**_set_a1("2026-01-02T03:04:05Z")[0], "as_text": True}]

    assert _edit_cli(monkeypatch, tmp_path, src, out, ops) == 0
    capsys.readouterr()
    assert _a1(out) == "2026-01-02T03:04:05Z"


def test_an_in_place_edit_survives_a_save_openpyxl_refuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The issue's data loss, driven through the real openpyxl writer.

    An offset-aware datetime that reaches the workbook some other way still
    makes ``wb.save`` raise mid-write; the input must come out byte-identical.
    """
    _, edit_xlsx = _scripts()
    src = _make_book(tmp_path / "book.xlsx")
    before = src.read_bytes()

    def _poison(wb: Any, ops: list[dict[str, Any]]) -> int:
        wb["S"]["A1"].value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        return 1

    monkeypatch.setattr(edit_xlsx, "apply_ops", _poison)

    with pytest.raises(TypeError, match="timezones"):
        _edit_cli(monkeypatch, tmp_path, src, src, _set_a1("x"))
    capsys.readouterr()

    assert src.read_bytes() == before
    assert _a1(src) == "keep me"


def test_create_xlsx_leaves_an_existing_out_intact_when_openpyxl_refuses(
    tmp_path: Path,
) -> None:
    create_xlsx, _ = _scripts()
    out = _make_book(tmp_path / "book.xlsx")
    before = out.read_bytes()
    wb = create_xlsx.build({"sheets": [{"name": "S", "rows": [["new"]]}]})
    wb["S"]["A1"].value = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    with pytest.raises(TypeError, match="timezones"):
        create_xlsx._save_atomic(wb, out)

    assert out.read_bytes() == before


class _PartialWriteBook:
    """Writes some bytes to the path it is handed, then fails -- the shape of
    an openpyxl save that dies partway through."""

    def __init__(self) -> None:
        self.saved_to: list[Path] = []

    def save(self, filename: str) -> None:
        self.saved_to.append(Path(filename))
        Path(filename).write_bytes(b"PK\x03\x04 truncated")
        raise TypeError("Excel does not support timezones in datetimes")


@pytest.mark.parametrize("module", ["create_xlsx", "edit_xlsx"])
def test_a_failed_save_uses_a_unique_sibling_temp_and_cleans_it_up(
    module: str, tmp_path: Path
) -> None:
    script = _script(module)
    out = _make_book(tmp_path / "book.xlsx")
    before = out.read_bytes()
    # A file of the caller's that a fixed ``<out>.tmp`` name would clobber.
    bystander = tmp_path / "book.xlsx.tmp"
    bystander.write_bytes(b"not ours")
    listing = sorted(p.name for p in tmp_path.iterdir())

    book = _PartialWriteBook()
    with pytest.raises(TypeError):
        script._save_atomic(book, out)

    (tmp_name,) = book.saved_to
    assert tmp_name.parent == out.resolve().parent
    assert tmp_name.name not in {out.name, bystander.name}
    assert out.read_bytes() == before
    assert bystander.read_bytes() == b"not ours"
    assert sorted(p.name for p in tmp_path.iterdir()) == listing


@pytest.mark.parametrize("module", ["create_xlsx", "edit_xlsx"])
def test_a_clean_save_leaves_only_the_destination(module: str, tmp_path: Path) -> None:
    create_xlsx, _ = _scripts()
    script = _script(module)
    out = tmp_path / "book.xlsx"
    wb = create_xlsx.build({"sheets": [{"name": "S", "rows": [["ok"]]}]})

    script._save_atomic(wb, out)

    assert [p.name for p in tmp_path.iterdir()] == ["book.xlsx"]
    assert _a1(out) == "ok"


@POSIX_ONLY
def test_an_in_place_edit_through_a_symlink_updates_the_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real = _make_book(tmp_path / "real.xlsx")
    link = tmp_path / "link.xlsx"
    link.symlink_to(real.name)

    assert _edit_cli(monkeypatch, tmp_path, link, link, _set_a1("edited")) == 0
    capsys.readouterr()

    assert link.is_symlink()
    assert os.readlink(link) == real.name
    assert _a1(real) == "edited"


@POSIX_ONLY
def test_an_in_place_edit_keeps_the_workbook_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _make_book(tmp_path / "book.xlsx")
    src.chmod(0o600)

    assert _edit_cli(monkeypatch, tmp_path, src, src, _set_a1("edited")) == 0
    capsys.readouterr()

    assert stat.S_IMODE(src.stat().st_mode) == 0o600
    assert _a1(src) == "edited"


@POSIX_ONLY
@pytest.mark.parametrize("module", ["create_xlsx", "edit_xlsx"])
def test_a_new_output_file_gets_the_umask_mode_not_the_temp_files(
    module: str, tmp_path: Path
) -> None:
    """``mkstemp`` makes its file ``0600``; a fresh ``--out`` must instead get
    what a plain ``wb.save`` would have produced under the current umask."""
    create_xlsx, _ = _scripts()
    script = _script(module)
    out = tmp_path / "fresh.xlsx"
    wb = create_xlsx.build({"sheets": [{"name": "S", "rows": [["ok"]]}]})

    previous = os.umask(0o027)
    try:
        script._save_atomic(wb, out)
    finally:
        os.umask(previous)

    assert stat.S_IMODE(out.stat().st_mode) == 0o640
