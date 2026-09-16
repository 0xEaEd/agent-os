"""Tests for the bundled srt-from-script skill."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "srt-from-script" / "scripts"


def _import_build_srt() -> object:
    sys.path.insert(0, str(SCRIPTS))
    try:
        import build_srt  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return build_srt


def test_parse_script_extracts_shots() -> None:
    build_srt = _import_build_srt()
    sample = (
        "=== SHOT_1 ===\n"
        "DURATION_S: 5\n"
        "VOICEOVER: Welcome to AgentOS\n"
        "=== SHOT_2 ===\n"
        "DURATION_S: 3\n"
        "VOICEOVER: none\n"
        "=== SHOT_3 ===\n"
        "DURATION_S: 4\n"
        "VOICEOVER: Done.\n"
    )
    shots = build_srt.parse_script(sample)
    assert len(shots) == 3
    assert shots[0] == (1, 5, "Welcome to AgentOS")
    assert shots[1] == (2, 3, "")
    assert shots[2] == (3, 4, "Done.")


def test_build_srt_handles_non_utf8_script_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_srt = _import_build_srt()
    script_path = tmp_path / "script_cp1252.txt"
    # Write bytes with cp1252 smart quote 0x92
    script_path.write_bytes(
        b"=== SHOT_1 ===\n"
        b"DURATION_S: 5\n"
        b"VOICEOVER: It\x92s working\n"
    )
    out_path = tmp_path / "output.srt"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_srt.py", "--script", str(script_path), "--output", str(out_path)],
    )
    assert build_srt.main() == 0
    assert out_path.is_file()
    content = out_path.read_text(encoding="utf-8")
    assert "1" in content
    assert "-->" in content
    assert "working" in content


def test_build_srt_missing_file_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_srt = _import_build_srt()
    missing_path = tmp_path / "nonexistent.txt"
    out_path = tmp_path / "output.srt"
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_srt.py", "--script", str(missing_path), "--output", str(out_path)],
    )
    assert build_srt.main() == 1
    assert not out_path.is_file()
