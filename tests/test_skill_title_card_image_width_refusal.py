"""Issue #3546: auto-shrink refused an off-canvas image for height, not width.

#3375 gave `render.py` a height fit and settled what happens when no font
size makes the text fit: say so on stderr, exit non-zero, write nothing. The
width fit, which predates it, stopped at the floor whether or not it got
there and the caller drew with the result -- so the same failure produced a
PNG with the headline's first and last characters sliced off at the canvas
edge, exit 0, and nothing on stderr. The card then goes straight into a
video-still-animator clip.

Reaching the floor needs a token the wrapper will not break, which is by
design (`test_overlong_word_stays_whole`, on the grounds that "auto-shrink
fits it"). A URL or a hashtag is one such token, and the widths where it
stops fitting are ordinary card sizes -- a 104-character URL clips at any
`--width` of 570 or below.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "title-card-image"
    / "scripts"
    / "render.py"
)

#: One unbreakable token, long enough to overrun a 540px card at the floor.
LONG_URL = (
    "https://videos.example.com/2026/s01/"
    "the-coffee-shop-encounter-official-trailer-4k-hdr-remastered-edition"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("title_card_width_under_test", _SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(monkeypatch: pytest.MonkeyPatch, out: Path, *extra: str) -> int:
    mod = _load_module()
    monkeypatch.setattr(sys, "argv", ["render.py", "--output", str(out), *extra])
    return mod.main()


def _ink_columns(path: Path) -> tuple[bool, bool]:
    """Whether any drawn pixel touches the left / right edge of the canvas."""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    background = image.getpixel((0, 0))
    width, height = image.size
    pixels = image.load()
    left = any(pixels[0, y] != background for y in range(height))
    right = any(pixels[width - 1, y] != background for y in range(height))
    return left, right


# ── the issue's reproduction ───────────────────────────────────────────────


def test_a_headline_too_wide_at_the_floor_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "card.png"

    code = _run(monkeypatch, out, "--text", LONG_URL, "--width", "540", "--height", "960")

    assert code == 1
    assert not out.exists(), "no clipped image is left behind"
    assert "exceeds the 540px canvas width" in capsys.readouterr().err


def test_a_subtitle_too_wide_at_the_floor_is_refused_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "card.png"

    code = _run(monkeypatch, out, "--text", "Trailer", "--subtitle", LONG_URL, "--width", "300")

    assert code == 1
    assert not out.exists()
    assert "canvas width" in capsys.readouterr().err


def test_width_and_height_now_fail_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The asymmetry this is about, asserted side by side."""
    wide = tmp_path / "wide.png"
    tall = tmp_path / "tall.png"

    wide_code = _run(monkeypatch, wide, "--text", LONG_URL, "--width", "540", "--height", "960")
    tall_code = _run(
        monkeypatch, tall, "--text", "line " * 200, "--width", "540", "--height", "120"
    )

    assert (wide_code, wide.exists()) == (tall_code, tall.exists()) == (1, False)
    err = capsys.readouterr().err
    assert "canvas width" in err and "canvas height" in err


# ── the floor is one number ────────────────────────────────────────────────


def test_the_width_floor_is_the_shared_constant() -> None:
    """``while size > 12`` used to leave ``size`` at 11 -- under the floor its
    own error message quotes and under what ``fit_stack_to_height`` assumes."""
    mod = _load_module()

    assert mod.SHRINK_FLOOR == 12
    assert mod.fit_stack_to_height.__defaults__ or mod.fit_stack_to_height.__kwdefaults__, (
        "shrink_floor is still a keyword default"
    )
    assert mod.fit_stack_to_height.__kwdefaults__["shrink_floor"] == mod.SHRINK_FLOOR


def test_an_unloadable_font_warns_once_not_once_per_shrink_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--text",
            "A headline long enough to shrink several steps",
            "--font",
            str(tmp_path / "missing.ttf"),
            "--width",
            "200",
            "--output",
            str(tmp_path / "card.png"),
        ],
    )
    mod.main()

    assert capsys.readouterr().err.count("not loadable") == 1


# ── what must not change ───────────────────────────────────────────────────


def test_a_headline_that_fits_after_shrinking_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "card.png"

    code = _run(monkeypatch, out, "--text", "Announcements for Cryptocurrency", "--width", "320")

    assert code == 0
    assert out.is_file() and out.stat().st_size > 0
    assert _ink_columns(out) == (False, False), "and it fits inside the canvas"


def test_an_ordinary_card_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "card.png"

    code = _run(monkeypatch, out, "--text", "咖啡店偶遇", "--subtitle", "短剧")

    assert code == 0
    assert out.is_file()
    assert _ink_columns(out) == (False, False)


def test_auto_shrink_no_still_writes_whatever_it_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--auto-shrink no`` opts out of the fit, so it opts out of the refusal
    -- the same way #3375 left the height path alone under that flag."""
    out = tmp_path / "card.png"

    code = _run(monkeypatch, out, "--text", LONG_URL, "--width", "540", "--auto-shrink", "no")

    assert code == 0
    assert out.is_file()


def test_an_empty_subtitle_does_not_trip_the_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "card.png"

    assert _run(monkeypatch, out, "--text", "Short", "--subtitle", "") == 0
    assert out.is_file()
