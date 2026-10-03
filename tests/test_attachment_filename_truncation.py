"""Issue #3569: truncating a filename from the left ate its extension.

Both sanitisers cut with ``name[:160]``, so the extension -- the last thing
in the string -- was the first thing lost. ``_safe_download_name`` is handed
straight to ``FileResponse(filename=...)`` and becomes the
``Content-Disposition`` filename, so the browser saved ``...aaaa.pd`` and the
file would not open by double-click: the bytes are fine, the name is not.

160 characters is not an unusual length for a generated artifact. A model
naming a report after the question that produced it gets there, and so does
any ``<topic>-<date>-<session>`` scheme.
"""

from __future__ import annotations

import pytest

from agentos.artifacts import _safe_filename
from agentos.attachment_refs import MAX_FILENAME_SUFFIX, truncate_filename
from agentos.gateway.attachments import _safe_download_name

LIMIT = 160

#: Both surfaces that shape a filename for a human or an OS to act on.
SANITISERS = [
    pytest.param(_safe_filename, id="artifact-name"),
    pytest.param(_safe_download_name, id="content-disposition"),
]


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("sanitise", SANITISERS)
@pytest.mark.parametrize("stem_length", [157, 158, 159, 160, 161, 200, 400])
@pytest.mark.parametrize("suffix", [".pdf", ".xlsx", ".png", ".md", ".tar.gz"])
def test_a_long_name_keeps_its_extension(sanitise, stem_length: int, suffix: str) -> None:
    out = sanitise("a" * stem_length + suffix)

    assert out.endswith("." + suffix.rsplit(".", 1)[-1])
    assert len(out) <= LIMIT


@pytest.mark.parametrize("sanitise", SANITISERS)
def test_the_reported_case(sanitise) -> None:
    name = "quarterly-earnings-call-transcript-" + "x" * 130 + ".pdf"

    out = sanitise(name)

    assert len(name) > LIMIT, "the input has to be over the bound for this to mean anything"
    assert out.endswith(".pdf")
    assert len(out) == LIMIT


@pytest.mark.parametrize("sanitise", SANITISERS)
@pytest.mark.parametrize("stem_length", [156, 157, 158, 159, 160, 161, 162, 163])
def test_no_stem_length_loses_the_dot(sanitise, stem_length: int) -> None:
    """The three off-by-one results the issue tabulated: `.pd`, `.p`, `.`."""
    out = sanitise("a" * stem_length + ".pdf")

    assert out.endswith(".pdf"), "a whole extension or none of it, never a sliced one"


# ── the helper's own contract ──────────────────────────────────────────────


def test_a_name_within_the_limit_is_returned_as_is() -> None:
    assert truncate_filename("short.pdf", LIMIT) == "short.pdf"


def test_a_name_with_no_extension_is_cut_plainly() -> None:
    out = truncate_filename("a" * 200, LIMIT)

    assert out == "a" * LIMIT


def test_an_overlong_tail_is_not_treated_as_an_extension() -> None:
    """A 40-character run after a dot is a name with a dot in it. Protecting
    it would spend the whole budget on it."""
    name = "a" * 200 + "." + "x" * (MAX_FILENAME_SUFFIX + 1)

    out = truncate_filename(name, LIMIT)

    assert out == name[:LIMIT]
    assert len(out) == LIMIT


def test_a_tail_containing_a_space_is_not_an_extension() -> None:
    name = "a" * 200 + ". pdf"

    assert truncate_filename(name, LIMIT) == name[:LIMIT]


def test_a_multipart_extension_keeps_its_last_part() -> None:
    out = truncate_filename("a" * 200 + ".tar.gz", LIMIT)

    assert out.endswith(".gz")
    assert len(out) == LIMIT


def test_a_dotfile_with_no_stem_is_cut_plainly() -> None:
    """``.hidden`` is all suffix and no stem; there is nothing to trim."""
    assert truncate_filename(".hidden", 4) == ".hid"


def test_a_limit_smaller_than_the_extension_falls_back_to_a_plain_cut() -> None:
    assert truncate_filename("abc.pdf", 3) == "abc"
    assert truncate_filename("abc.pdf", 4) == "abc."


def test_a_non_positive_limit_is_empty() -> None:
    assert truncate_filename("abc.pdf", 0) == ""
    assert truncate_filename("abc.pdf", -1) == ""


# ── what must not change ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("  spaced  ", "spaced"),
        ("a/b/c.txt", "c.txt"),
        ("..", "artifact"),
        ("", "artifact"),
        (".hidden", ".hidden"),
    ],
)
def test_safe_filename_keeps_its_existing_behaviour(raw: str, expected: str) -> None:
    assert _safe_filename(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("report.pdf", "report.pdf"),
        ("a/b/c.txt", "a b c.txt"),
        ("", "attachment"),
        (None, "attachment"),
    ],
)
def test_safe_download_name_keeps_its_existing_behaviour(raw: object, expected: str) -> None:
    assert _safe_download_name(raw) == expected


@pytest.mark.parametrize("sanitise", SANITISERS)
def test_the_bound_still_holds(sanitise) -> None:
    for name in ["a" * 500, "a" * 500 + ".pdf", "." * 500 + "x.pdf"]:
        assert len(sanitise(name)) <= LIMIT
