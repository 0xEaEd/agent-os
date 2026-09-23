"""subtitle-burner ``burn.py`` — a quote in the path broke the filtergraph (#3162).

``_escape_subtitle_path`` wrote a single quote as ``\\'``. That works in a
shell; it does not work in an ffmpeg filtergraph. Inside a single-quoted
ffmpeg token there is no escaping at all -- a quote ends the token whatever
precedes it -- so the rest of the path fell outside the quotes and was read
as filter syntax::

    [AVFilterGraph @ ...] No option name near ''s_cues.srt...'
    Error initializing filter 'subtitles' with args 'test's_cues.srt...'

Any subtitle file, or any directory above it, containing an apostrophe
failed the burn outright. ffmpeg's own spelling is ``'\\''`` -- close the
quote, escape the quote, reopen -- which is what the concat writer in
video-merger already emits.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "subtitle-burner" / "scripts"


def _burn_module():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import burn  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return burn


def _av_get_token(token: str) -> str:
    """One ffmpeg unescaping pass, the way ``av_get_token`` reads a token.

    Inside ``'...'`` every character is literal and a quote closes the
    section; outside, a backslash escapes the next character.
    """
    out: list[str] = []
    quoted = False
    index = 0
    while index < len(token):
        char = token[index]
        if quoted:
            if char == "'":
                quoted = False
            else:
                out.append(char)
            index += 1
            continue
        if char == "'":
            quoted = True
            index += 1
            continue
        if char == "\\" and index + 1 < len(token):
            out.append(token[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _filter_argument_value(escaped: str) -> str:
    """What the ``subtitles=`` filter finally opens, after **both** passes.

    A ``-vf`` argument is tokenised twice: the filtergraph parser reads it
    first and strips the outer quotes, then the option parser reads the
    result. The existing ``\\:`` drive-colon escape is aimed at that second
    pass, which is the clue that one level of quote escaping is not enough:
    after the first pass a singly-escaped quote is bare again, and the second
    pass opens a section with it that never closes.
    """
    return _av_get_token(_av_get_token(f"'{escaped}'"))


@pytest.mark.parametrize(
    "name",
    [
        "test's_cues.srt",
        "user's subtitles/cues.srt",
        "two''quotes.srt",
        "'leading.srt",
        "trailing'.srt",
    ],
)
def test_a_quoted_path_survives_both_parser_passes(name: str) -> None:
    """The property that matters: what ffmpeg finally opens is the path given."""
    burn = _burn_module()

    escaped = burn._escape_subtitle_path(name)

    assert _filter_argument_value(escaped) == name


@pytest.mark.parametrize(
    "name",
    ["test's_cues.srt", "two''quotes.srt", "trailing'.srt"],
)
def test_a_single_level_escape_would_not_have_survived(name: str) -> None:
    """The regression this PR exists for, modelled rather than asserted by
    absence: the shell/concat spelling loses the quote on the second pass,
    which is exactly what ffmpeg reported (`Unable to open tests_cues.srt`)."""
    single_level = name.replace("'", "'\\''")

    assert _filter_argument_value(single_level) != name


def test_the_quote_is_written_at_two_levels() -> None:
    burn = _burn_module()

    escaped = burn._escape_subtitle_path("test's_cues.srt")

    assert escaped == "test'\\\\\\''s_cues.srt"
    # ...and specifically not the one-level spelling that fails pass two.
    assert escaped != "test'\\''s_cues.srt"
    # Nor the bare backslash-quote, which the graph pass strips outright.
    assert escaped != "test\\'s_cues.srt"


def test_the_quoted_token_is_balanced() -> None:
    """An odd number of quotes is exactly the state that made ffmpeg read the
    remainder of the path as filter options."""
    burn = _burn_module()

    escaped = burn._escape_subtitle_path("a'b'c.srt")
    token = "'" + escaped + "'"

    assert token.count("'") % 2 == 0


def test_a_path_without_quotes_is_untouched() -> None:
    burn = _burn_module()

    assert burn._escape_subtitle_path("plain/cues.srt") == "plain/cues.srt"


def test_windows_path_handling_is_unchanged() -> None:
    """Backslashes still become forward slashes and the drive colon is still
    escaped -- the quote fix must not disturb either."""
    burn = _burn_module()

    escaped = burn._escape_subtitle_path("C:\\Videos\\Clips\\cues.srt")

    assert escaped == "C\\:/Videos/Clips/cues.srt"


def test_a_windows_path_containing_a_quote_gets_both_treatments() -> None:
    burn = _burn_module()

    escaped = burn._escape_subtitle_path("C:\\Videos\\Clips\\a'b\\cues.srt")

    assert escaped.startswith("C\\:/Videos/Clips/")
    assert "'\\\\\\''" in escaped
    # Both escapes survive together, and the path still arrives intact.
    assert _filter_argument_value(escaped) == "C:/Videos/Clips/a'b/cues.srt"
