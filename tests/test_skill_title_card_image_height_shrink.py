"""Issue #3375: title-card-image render.py auto-shrinks for canvas width but not height.

``_fit_font``'s shrink loop only checked ``_max_text_width(...) > args.width * 0.88``.
Nothing fed the stacked lines' total height back into it, and the vertical start
position ``y = (args.height - total_h) // 2`` was used unclamped -- so a headline (or
subtitle) that wrapped into enough lines rendered its top lines above row 0 and its
bottom lines below the last row, invisibly, while the script still printed the output
path and exited 0.

``fit_stack_to_height`` is the extracted, directly-testable fix: pure arithmetic over
line counts and font sizes, no PIL/font rendering involved, so these tests are exact
and do not depend on font metrics or which fonts happen to be installed.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "title-card-image"
    / "scripts"
    / "render.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("title_card_render_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TITLE_LINES = [
    "The Coffee",
    "Shop Where",
    "Two",
    "Strangers",
    "Meet Again",
    "After Ten",
    "Long Years",
    "Apart",
]
SUB_LINES = ["A Short Drama", "About Fate,", "Memory, and Second", "Chances in the", "City"]


def test_the_3375_repro_no_longer_overflows_the_canvas() -> None:
    """The exact numbers from #3375: 8 title lines + 5 subtitle lines at the
    documented default sizes (80/32) stack to 1020px, which used to overflow
    an 800px canvas by 220px with no warning.
    """
    mod = _load_module()

    assert mod._stack_height(80, 32, TITLE_LINES, SUB_LINES) == 1020  # the bug, unpatched

    title_size, sub_size, fits = mod.fit_stack_to_height(80, 32, TITLE_LINES, SUB_LINES, 800)

    assert fits
    assert mod._stack_height(title_size, sub_size, TITLE_LINES, SUB_LINES) <= 800
    assert title_size < 80  # it actually had to shrink to get there
    assert sub_size < 32


def test_a_stack_that_already_fits_is_left_untouched() -> None:
    mod = _load_module()

    title_size, sub_size, fits = mod.fit_stack_to_height(80, 32, TITLE_LINES, SUB_LINES, 1280)

    assert fits
    assert (title_size, sub_size) == (80, 32)


def test_a_title_only_stack_shrinks_the_same_way() -> None:
    """No subtitle: the ``sub_lines and ...`` guards must not blow up on an
    empty list, and sub_size must stay untouched since there is nothing to size.
    """
    mod = _load_module()

    title_size, sub_size, fits = mod.fit_stack_to_height(80, 32, TITLE_LINES, [], 400)

    assert fits
    assert title_size < 80
    assert sub_size == 32  # untouched: there is no subtitle to shrink


def test_a_canvas_too_small_even_at_the_floor_is_reported_as_not_fitting() -> None:
    mod = _load_module()

    title_size, sub_size, fits = mod.fit_stack_to_height(80, 32, TITLE_LINES, SUB_LINES, 50)

    assert not fits
    assert title_size == 12
    assert sub_size == 12


def test_shrinking_never_increases_a_size_and_never_goes_below_the_floor() -> None:
    mod = _load_module()

    for height in (2000, 1000, 500, 200, 100, 1):
        title_size, sub_size, _fits = mod.fit_stack_to_height(
            80, 32, TITLE_LINES, SUB_LINES, height
        )
        assert 12 <= title_size <= 80
        assert 12 <= sub_size <= 32


def test_fitting_is_idempotent() -> None:
    """Guard: feeding an already-fitted result back in must not shrink it further."""
    mod = _load_module()

    t1, s1, fits1 = mod.fit_stack_to_height(80, 32, TITLE_LINES, SUB_LINES, 800)
    t2, s2, fits2 = mod.fit_stack_to_height(t1, s1, TITLE_LINES, SUB_LINES, 800)

    assert (t1, s1, fits1) == (t2, s2, fits2)


# ── end-to-end smoke tests: the CLI actually wires this in ───────────────


def _run(tmp_path: Path, *extra_args: str) -> tuple[int, str, Path]:
    out = tmp_path / "card.png"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(out), *extra_args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stderr, out


def test_cli_writes_a_file_and_does_not_warn_when_the_shrink_makes_it_fit(
    tmp_path: Path,
) -> None:
    code, stderr, out = _run(
        tmp_path,
        "--text",
        "The Coffee Shop Where Two Strangers Meet Again After Ten Long Years Apart",
        "--subtitle",
        "A Short Drama About Fate, Memory, and Second Chances in the City",
        "--width",
        "720",
        "--height",
        "800",
    )

    assert code == 0
    assert "Warning" not in stderr, stderr
    assert out.is_file()


def test_cli_fails_loudly_and_writes_no_file_when_nothing_can_make_it_fit(
    tmp_path: Path,
) -> None:
    code, stderr, out = _run(
        tmp_path,
        "--text",
        "This is a fairly long headline that will wrap across many lines",
        "--max-chars-per-line",
        "8",
        "--width",
        "720",
        "--height",
        "50",
    )

    assert code != 0
    assert "Error" in stderr
    assert "exceeds the 50px canvas height" in stderr
    assert "minimum font size (12px)" in stderr
    assert not out.exists()


def test_cli_auto_shrink_no_disables_the_height_fit_too(tmp_path: Path) -> None:
    """Guard: ``--auto-shrink no`` must keep disabling both axes, not just width."""
    code, stderr, out = _run(
        tmp_path,
        "--text",
        "The Coffee Shop Where Two Strangers Meet Again After Ten Long Years Apart",
        "--subtitle",
        "A Short Drama About Fate, Memory, and Second Chances in the City",
        "--width",
        "720",
        "--height",
        "800",
        "--auto-shrink",
        "no",
    )

    assert code == 0
    assert stderr == ""  # no height-fit warning: the caller opted out entirely
    assert out.is_file()


@pytest.mark.parametrize("auto_shrink", ["yes", "no"])
def test_cli_handles_a_missing_subtitle(tmp_path: Path, auto_shrink: str) -> None:
    code, stderr, out = _run(
        tmp_path,
        "--text",
        "The Coffee Shop Where Two Strangers Meet Again After Ten Long Years Apart",
        "--width",
        "720",
        "--height",
        "800",
        "--auto-shrink",
        auto_shrink,
    )

    assert code == 0
    assert out.is_file()


# ── real end-to-end: actual PIL glyph bboxes, not just the arithmetic ─────


def test_the_3375_repro_keeps_every_drawn_lines_glyph_bbox_on_canvas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs the real ``main()`` pipeline in-process (not the pure-arithmetic
    helper) and records every ``ImageDraw.text(...)`` call render.py issues,
    then re-derives each line's actual rendered bbox with PIL's own
    ``textbbox`` -- using whatever font this machine picked -- and asserts it
    lies within ``[0, height]``.

    This is the shape the review asked for: a test that fails on `main`
    because the drawn text genuinely escapes the canvas (the title's first
    line starts at a negative y), not because ``fit_stack_to_height`` is
    missing (an ``AttributeError`` proves nothing about the rendering bug
    itself). The title's last wrapped line is "...Years Apart", which ends in
    "Apart" -- its descender ("p") is exactly what a fit to 100% of the
    canvas height (rather than the safe area) would let slip past row
    ``height``, since ``_stack_height``'s arithmetic only knows the nominal
    font size, not how far a real glyph draws below it.
    """
    mod = _load_module()
    width, height = 720, 800

    calls: list[tuple[tuple[int, int], str, object]] = []
    original_text = ImageDraw.ImageDraw.text

    def _recording_text(self, xy, text, fill=None, font=None, *args, **kwargs):
        calls.append((xy, text, font))
        return original_text(self, xy, text, fill=fill, font=font, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", _recording_text)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render.py",
            "--output",
            str(tmp_path / "card.png"),
            "--text",
            "The Coffee Shop Where Two Strangers Meet Again After Ten Long Years Apart",
            "--subtitle",
            "A Short Drama About Fate, Memory, and Second Chances in the City",
            "--width",
            str(width),
            "--height",
            str(height),
        ],
    )

    rc = mod.main()

    assert rc == 0
    assert calls, "no lines were drawn"
    assert any(any(c in text for c in "gjpqy") for _xy, text, _font in calls), (
        "repro text must include a descender line, e.g. 'Apart'"
    )

    probe = ImageDraw.Draw(Image.new("RGB", (width, height)))
    for xy, text, font in calls:
        bbox = probe.textbbox(xy, text, font=font)
        top, bottom = bbox[1], bbox[3]
        assert 0 <= top, f"{text!r} drawn bbox {bbox} starts above row 0"
        assert bottom <= height, f"{text!r} drawn bbox {bbox} extends past row {height}"
