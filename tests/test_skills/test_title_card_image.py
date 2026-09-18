import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "src" / "agentos" / "skills" / "bundled" / "title-card-image" / "scripts" / "render.py"
)


def test_render_rejects_non_positive_dimensions(tmp_path: Path):
    out = tmp_path / "out.png"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--text", "Test", "--output", str(out), "--width", "0"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Error: --width and --height must be positive integers." in result.stderr

    result_h = subprocess.run(
        [sys.executable, str(SCRIPT), "--text", "Test", "--output", str(out), "--height", "-10"],
        capture_output=True,
        text=True,
    )
    assert result_h.returncode == 1
    assert "Error: --width and --height must be positive integers." in result_h.stderr


def test_render_rejects_non_positive_font_sizes(tmp_path: Path):
    out = tmp_path / "out.png"
    result_font = subprocess.run(
        [sys.executable, str(SCRIPT), "--text", "Test", "--output", str(out), "--font-size", "0"],
        capture_output=True,
        text=True,
    )
    assert result_font.returncode == 1
    assert "Error: --font-size must be a positive integer." in result_font.stderr

    result_sub = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--text",
            "Test",
            "--output",
            str(out),
            "--subtitle-size",
            "-5",
        ],
        capture_output=True,
        text=True,
    )
    assert result_sub.returncode == 1
    assert "Error: --subtitle-size must be a positive integer." in result_sub.stderr


def test_render_rejects_non_positive_max_chars_per_line(tmp_path: Path):
    out = tmp_path / "out.png"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--text",
            "Test",
            "--output",
            str(out),
            "--max-chars-per-line",
            "0",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Error: --max-chars-per-line must be a positive integer." in result.stderr
