"""Three defects in the terminal renderer's inline pass.

* #3426 -- a backslash escape was printed *and* ignored: ``\\*not italic\\*``
  came out with its backslashes on screen and the italic applied anyway.
* #3427 -- a code span opened with two backticks was read by a
  single-backtick-only pattern, so ``` ``code with ` tick`` ``` lost the inner
  backtick and left the outer pair on screen.
* #3428 -- the pattern claim order, not the nesting, decided which of two
  overlapping spans survived, so the enclosing one was discarded and its
  ``**`` / ``~~`` delimiters printed.

They are fixed together because they are one function: ``_render_inline``
tokenises, and all three are that tokeniser mis-reading a span. Landing them
separately would mean three branches editing the same dozen lines.

Assertions are made on what the reader sees -- the markup with Rich tags
stripped -- rather than on the tag soup, plus a few that check the styling is
actually applied and nested.
"""

from __future__ import annotations

import re

import pytest

from agentos.cli.tui.terminal.markdown_stream import MarkdownStreamRenderer


def _render(source: str) -> str:
    renderer = MarkdownStreamRenderer(enabled=True)
    return (renderer.feed(source + "\n") + renderer.flush()).rstrip()


def _user_sees(source: str) -> str:
    """The display text: markup removed, which is what reaches the terminal."""
    return re.sub(r"\[[^\]]*\]", "", _render(source)).rstrip()


# ---------------------------------------------------------------------------
# #3426 -- backslash escapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"literal \*not italic\* here", "literal *not italic* here"),
        (r"a \_b\_ c", "a _b_ c"),
        (r"\`not code\`", "`not code`"),
        (r"\*\*not bold\*\*", "**not bold**"),
        (r"\~\~not struck\~\~", "~~not struck~~"),
    ],
)
def test_an_escaped_marker_is_consumed_and_not_applied(source: str, expected: str) -> None:
    rendered = _render(source)

    assert _user_sees(source) == expected
    assert "\\" not in _user_sees(source)
    assert "[italic]" not in rendered and "[bold]" not in rendered


def test_an_escaped_backtick_does_not_open_a_code_span() -> None:
    assert "#DDFF66" not in _render(r"\`not code\`")


@pytest.mark.parametrize("source", [r"regex \d+\s*", "C:\\Users\\name", r"a \n b"])
def test_a_backslash_before_a_non_punctuation_character_is_kept(source: str) -> None:
    """Only punctuation is escapable; a regex class or a Windows path must
    round-trip unchanged, which is the exclusion named in #3305's triage."""
    assert _user_sees(source) == source


# ---------------------------------------------------------------------------
# #3427 -- code span delimiter runs
# ---------------------------------------------------------------------------


def test_a_double_backtick_span_keeps_the_backtick_it_quotes() -> None:
    """The issue's repro. The inner backtick was being deleted outright."""
    assert _user_sees("``code with ` tick``") == "code with ` tick"


def test_a_double_backtick_span_is_one_styled_span() -> None:
    rendered = _render("``code with ` tick``")

    assert rendered.count("#DDFF66") == 1
    assert "``" not in rendered


def test_a_single_backtick_span_still_works() -> None:
    assert _user_sees("`plain code`") == "plain code"


def test_two_separate_spans_on_one_line_stay_separate() -> None:
    rendered = _render("`one` and `two`")

    assert _user_sees("`one` and `two`") == "one and two"
    assert rendered.count("#DDFF66") == 2


# ---------------------------------------------------------------------------
# #3428 -- nested emphasis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**bold with *italic* inside**", "bold with italic inside"),
        ("~~struck with *italic* inside~~", "struck with italic inside"),
        ("*italic with ~~struck~~ inside*", "italic with struck inside"),
        (
            "**Warning: the `--force` flag is *not* reversible**",
            "Warning: the --force flag is not reversible",
        ),
    ],
)
def test_nested_emphasis_does_not_print_its_delimiters(source: str, expected: str) -> None:
    assert _user_sees(source) == expected


def test_the_outer_span_wins_and_the_inner_one_survives_inside_it() -> None:
    """Not just "no stray asterisks": both styles have to be applied, with the
    inner one nested in the outer."""
    assert _render("**bold with *italic* inside**") == (
        "[bold]bold with [italic]italic[/] inside[/]"
    )
    assert _render("~~struck with *italic* inside~~") == (
        "[strike]struck with [italic]italic[/] inside[/]"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**just bold**", "just bold"),
        ("*just italic*", "just italic"),
        ("~~just struck~~", "just struck"),
    ],
)
def test_unnested_emphasis_is_unchanged(source: str, expected: str) -> None:
    assert _user_sees(source) == expected


# ---------------------------------------------------------------------------
# Things the tokeniser must still not touch
# ---------------------------------------------------------------------------


def test_a_snake_case_identifier_is_not_emphasised() -> None:
    assert _user_sees("call snake_case_name now") == "call snake_case_name now"


def test_a_link_still_renders_with_its_destination() -> None:
    assert _user_sees("[docs](https://x.test)") == "docs (https://x.test)"


def test_a_bare_asterisk_is_left_alone() -> None:
    assert _user_sees("2 * 3 = 6") == "2 * 3 = 6"


def test_markup_in_model_text_cannot_inject_rich_tags() -> None:
    """The escaping contract the span assembly exists for."""
    assert "[/]" not in _user_sees("literal [bold]not a tag[/] here")


# ---------------------------------------------------------------------------
# Escapes are NOT processed inside a code span (review of #3436)
# ---------------------------------------------------------------------------
#
# CommonMark does not unescape inside a code span, and the first revision of
# this change did: `_park_escaped_punctuation` ran before code spans were
# recognised, so a regex or a Windows path shown in backticks came out
# corrupted. Code spans are now parked first and restored last.


def _printed(source: str) -> str:
    """What Rich actually prints for the rendered markup.

    Stripping ``[...]`` with a regex is not good enough here: the payloads
    below legitimately contain square brackets, and the regex would eat them
    along with the markup tags.
    """
    import io as _io

    from rich.console import Console

    buffer = _io.StringIO()
    Console(file=buffer, no_color=True, width=200, markup=True, highlight=False).print(
        _render(source)
    )
    return buffer.getvalue().rstrip()


@pytest.mark.parametrize(
    ("source", "printed"),
    [
        (r"`re.sub(r'\.', '', s)`", r"re.sub(r'\.', '', s)"),
        (r"`\*`", r"\*"),
        (r"`C:\Program Files\(x86)\`", r"C:\Program Files\(x86)" + "\\"),
        (r"`\d+\s*`", r"\d+\s*"),
    ],
)
def test_a_backslash_inside_a_code_span_is_left_alone(source: str, printed: str) -> None:
    """A regex or a Windows path in backticks is code the model is showing the
    user; unescaping it corrupts what they read."""
    assert _printed(source) == printed


def test_a_bracketed_regex_in_a_code_span_keeps_its_backslashes_in_the_markup() -> None:
    r"""``^\[\d+\]$`` asserted at the markup level on purpose.

    The span's content is carried through intact -- which is what this change
    is responsible for -- but Rich consumes the backslash of a literal ``\[``
    when it prints. That happens identically on ``main`` (both produce
    ``[bold #DDFF66]^\[\d+\]$[/]``), so it is a separate, pre-existing gap
    in ``_rich_escape`` rather than anything this PR introduces.
    """
    assert r"^\[\d+\]$" in _render(r"`^\[\d+\]$`")


def test_an_escaped_backtick_outside_a_span_still_cannot_open_one() -> None:
    """The invariant that makes "code spans first" safe: parking them first
    must not let an escaped delimiter start a span."""
    rendered = _render(r"\`not code\`")

    assert "#DDFF66" not in rendered
    assert _user_sees(r"\`not code\`") == "`not code`"


def test_a_code_span_inside_bold_survives_both_passes() -> None:
    assert _user_sees("**bold with `code` inside**") == "bold with code inside"
    assert "#DDFF66" in _render("**bold with `code` inside**")
