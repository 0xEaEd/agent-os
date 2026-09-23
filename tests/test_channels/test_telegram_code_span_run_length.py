"""Issue #3173: a code span closed on a *substring* of a longer backtick run.

``_replace_code_spans`` located the closing delimiter with
``text.find(marker, marker_end)``. ``str.find`` matches the opener's run
inside a longer one, so a one-backtick span that quotes a two-backtick
sequence -- the ordinary Markdown spelling for showing a literal backtick --
paired with the *first* backtick of that sequence. The second one opened a
fresh span and the quoted backticks were consumed as delimiters: content the
agent wrote was deleted from the message the user received, with nothing to
show anything had gone missing.

CommonMark closes a code span only on a backtick run of exactly the opener's
length; a longer run inside the span is content.
"""

from __future__ import annotations

from agentos.channels._telegram_formatting import render_telegram_html


def test_a_span_quoting_a_longer_backtick_run_keeps_its_content() -> None:
    """The issue's own repro: ``` ` `` ` ``` must render the two backticks."""
    rendered = render_telegram_html("Use ` `` ` to nest.")

    assert rendered == "Use <code>``</code> to nest."


def test_a_longer_run_inside_a_span_is_content_not_two_spans() -> None:
    """The run is too long to close the span, so it belongs to the content."""
    rendered = render_telegram_html("`a``b`")

    assert rendered == "<code>a``b</code>"
    # The pre-fix output split this into two spans and dropped the backticks.
    assert rendered.count("<code>") == 1


def test_a_double_backtick_span_still_closes_on_its_own_run() -> None:
    """The case the substring search happened to get right stays right: a
    single backtick inside a ``-delimited span is content, not a closer."""
    assert render_telegram_html("``a ` b``") == "<code>a ` b</code>"


def test_a_double_backtick_span_quoting_one_backtick() -> None:
    """CommonMark's own example: the single space on each side is stripped."""
    assert render_telegram_html("`` ` ``") == "<code>`</code>"


def test_separate_spans_on_one_line_are_unaffected() -> None:
    rendered = render_telegram_html("`x` and `y`")

    assert rendered == "<code>x</code> and <code>y</code>"


def test_a_run_that_never_closes_is_left_as_literal_text() -> None:
    """An opener with no matching run is not a span; skipping wrong-length
    runs must not turn it into one, or swallow the rest of the message."""
    assert render_telegram_html("unclosed ` span") == "unclosed ` span"
    assert render_telegram_html("unclosed `` span with ` inside") == (
        "unclosed `` span with ` inside"
    )


def test_a_wrong_length_run_is_skipped_whole() -> None:
    """Regression guard on the scan itself. Rejecting a run one character at a
    time would restart the search *inside* the run just rejected and match its
    tail, reintroducing the bug for any opener shorter than the run it meets.
    """
    rendered = render_telegram_html("`a```b`")

    assert rendered == "<code>a```b</code>"


def test_inline_emphasis_outside_spans_still_renders() -> None:
    """The span pass runs before the emphasis passes; the change must not
    disturb what reaches them."""
    rendered = render_telegram_html("a *b* and **c** with `code` too")

    assert rendered == "a <i>b</i> and <b>c</b> with <code>code</code> too"
