"""Three defects in the terminal renderer's inline pass (#3426).

* Backslash escapes: ``\\*not italic\\*`` came out with its backslashes on
  screen and the italic applied anyway.
* Code span runs: a span opened with two backticks was read by a
  single-backtick-only pattern, so ``` ``code with ` tick`` ``` lost the
  inner backtick and left the outer pair on screen.
* Nested emphasis: the pattern claim order, not the nesting, decided which of
  two overlapping spans survived, so the enclosing one was discarded and its
  ``**`` / ``~~`` delimiters printed.

Code spans are protected before escapes are parked, so nothing inside one is
unescaped: CommonMark does not process backslash escapes in code, and regexes
and Windows paths in inline code are common in agent replies.

Assertions are made on what the reader sees -- the markup parsed by Rich, the
way the terminal gets it -- rather than on the tag soup.
"""

from __future__ import annotations

import pytest
from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

from agentos.cli.tui.terminal.markdown_stream import (
    _INLINE_CODE_STYLE,
    MarkdownStreamRenderer,
)

#: The inline code style as Rich reports it on a parsed span.
_CODE = Style.normalize(_INLINE_CODE_STYLE)


def _render(source: str) -> str:
    renderer = MarkdownStreamRenderer(enabled=True)
    return (renderer.feed(source + "\n") + renderer.flush()).rstrip("\n")


def _display(source: str) -> Text:
    return Text.from_markup(_render(source), emoji=False)


def _user_sees(source: str) -> str:
    """The display text, which is what reaches the terminal."""
    return _display(source).plain


def _styled_runs(source: str) -> list[tuple[str, str]]:
    text = _display(source)
    return [(text.plain[s.start : s.end], str(s.style)) for s in text.spans]


# ---------------------------------------------------------------------------
# Backslash escapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"literal \*not italic\* here", "literal *not italic* here"),
        (r"a \_b\_ c", "a _b_ c"),
        (r"\*\*not bold\*\*", "**not bold**"),
        (r"\~\~not struck\~\~", "~~not struck~~"),
        (r"\[bold]not a tag", "[bold]not a tag"),
    ],
)
def test_an_escaped_marker_is_consumed_and_not_applied(source: str, expected: str) -> None:
    assert _user_sees(source) == expected
    assert _styled_runs(source) == []


def test_an_escaped_backtick_does_not_open_a_code_span() -> None:
    assert _user_sees(r"\`not code\`") == "`not code`"
    assert _styled_runs(r"\`not code\`") == []


def test_an_escaped_backslash_leaves_the_backtick_a_delimiter() -> None:
    assert _user_sees(r"a \\`code` b") == "a \\code b"
    assert _styled_runs(r"a \\`code` b") == [("code", _CODE)]


@pytest.mark.parametrize("source", [r"regex \d+\s*", "C:\\Users\\name", r"a \n b"])
def test_a_backslash_before_a_non_punctuation_character_is_kept(source: str) -> None:
    """Only punctuation is escapable; a regex class or a Windows path must
    round-trip unchanged."""
    assert _user_sees(source) == source


@pytest.mark.parametrize("bracketed", ["[x]", "[ x"])
def test_an_escaped_backslash_before_a_bracket_prints_one_backslash(bracketed: str) -> None:
    """Rich reads any backslash-bracket as an escaped bracket, so the
    backslash this escape leaves behind has to be protected from that too."""
    assert _user_sees(f"a \\\\{bracketed} b") == f"a \\{bracketed} b"


# ---------------------------------------------------------------------------
# Code spans: closed by a run of the opener's length, contents literal
# ---------------------------------------------------------------------------


def test_a_double_backtick_span_keeps_the_backtick_it_quotes() -> None:
    assert _user_sees("``code with ` tick``") == "code with ` tick"
    assert _styled_runs("``code with ` tick``") == [("code with ` tick", _CODE)]


def test_a_single_backtick_span_still_works() -> None:
    assert _styled_runs("`plain code`") == [("plain code", _CODE)]


def test_two_separate_spans_on_one_line_stay_separate() -> None:
    assert _user_sees("`one` and `two`") == "one and two"
    assert _styled_runs("`one` and `two`") == [
        ("one", _CODE),
        ("two", _CODE),
    ]


@pytest.mark.parametrize(
    "code",
    [
        r"re.sub(r'\.', '', s)",
        r"^\[\d+\]$",
        r"\*",
        "C:\\Program Files\\(x86)\\",
    ],
)
def test_a_backslash_inside_a_code_span_is_not_an_escape(code: str) -> None:
    """The review rows on #3436: escapes parked before code spans were
    protected stripped these backslashes, and the trailing one of the Windows
    path escaped the closing backtick so no span was found at all."""
    assert _user_sees(f"`{code}`") == code
    assert _styled_runs(f"`{code}`") == [(code, _CODE)]


def test_escapes_outside_a_code_span_are_consumed_while_its_contents_are_not() -> None:
    source = r"\*literal\* and `\*kept\*`"

    assert _user_sees(source) == r"*literal* and \*kept\*"
    assert _styled_runs(source) == [(r"\*kept\*", _CODE)]


def test_markup_inside_a_code_span_stays_literal() -> None:
    assert _user_sees("`[bold]x[/]`") == "[bold]x[/]"


@pytest.mark.parametrize("code", ["\\\\", "\\\\\\"])
def test_a_code_span_ending_in_a_backslash_run_keeps_the_whole_run(code: str) -> None:
    """Rich halves a backslash run in front of a tag, and an odd run escapes
    the tag: three backslashes leaked ``[/]`` and styled the rest of the line
    as code."""
    source = f"use `{code}` here and more"

    assert _user_sees(source) == f"use {code} here and more"
    assert _styled_runs(source) == [(code, _CODE)]


# ---------------------------------------------------------------------------
# Nested emphasis
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
    assert _render("**bold with *italic* inside**") == (
        "[bold]bold with [italic]italic[/] inside[/]"
    )
    assert _render("~~struck with *italic* inside~~") == (
        "[strike]struck with [italic]italic[/] inside[/]"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [("***x***", "x"), ("**bold** and *it* and ***both*** end", "bold and it and both end")],
)
def test_a_triple_asterisk_run_is_bold_and_italic(source: str, expected: str) -> None:
    word = expected.split()[-2] if " " in expected else expected

    assert _user_sees(source) == expected
    assert (word, "bold") in _styled_runs(source)
    assert (word, "italic") in _styled_runs(source)


def test_italic_inside_bold_is_not_read_as_a_triple_run() -> None:
    assert _styled_runs("**a *b* c**") == [("a b c", "bold"), ("b", "italic")]


@pytest.mark.parametrize(
    ("source", "expected", "label"),
    [
        ("use *glob [docs](https://x.test/*.py)", "use *glob docs (https://x.test/*.py)", "docs"),
        ("2 * 3 = [six*](https://x.test)", "2 * 3 = six* (https://x.test)", "six*"),
        ("a * b [x*y](https://x.test)", "a * b x*y (https://x.test)", "x*y"),
        ("~~old [x~~y](u)", "~~old x~~y (u)", "x~~y"),
        ("**note [a**b](u)", "**note a**b (u)", "a**b"),
        ("*see [a](https://x.test/a*b)*", "*see a (https://x.test/a*b)*", "a"),
    ],
)
def test_emphasis_that_only_crosses_a_link_does_not_break_it(
    source: str, expected: str, label: str
) -> None:
    """Only a span that *encloses* another wins over it. One that runs into
    a link partway loses to it, as it always did: the link keeps its text and
    destination and no emphasis is applied."""
    runs = _styled_runs(source)

    assert _user_sees(source) == expected
    # The link's two runs and nothing else: no emphasis was applied.
    assert len(runs) == 2
    assert runs[0][0] == label
    assert runs[1][0].startswith("(https://") or runs[1][0] == "(u)"


def test_emphasis_that_encloses_a_link_styles_it() -> None:
    assert _styled_runs("*a [b](u) c*")[0] == ("a b (u) c", "italic")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**just bold**", "just bold"),
        ("*just italic*", "just italic"),
        ("~~just struck~~", "just struck"),
        ("**a** and **b**", "a and b"),
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
    source = "literal [bold]not a tag[/] here"

    assert _user_sees(source) == source
    assert _styled_runs(source) == []


# ---------------------------------------------------------------------------
# Table cells are measured the way they are drawn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell",
    [
        r"a \* b",
        "**bold with *italic* inside**",
        "~~struck with *italic* inside~~",
        "``code with ` tick``",
        r"`^\[\d+\]$`",
        "\\\\",
        "x \\\\",
        "***x***",
    ],
)
def test_a_table_cell_with_the_new_inline_rules_stays_aligned(cell: str) -> None:
    source = f"| h | x |\n| --- | --- |\n| {cell} | y |\n| longer cell here | z |"
    lines = _display(source).plain.split("\n")

    assert len({cell_len(line) for line in lines}) == 1, lines


@pytest.mark.parametrize(
    ("cell", "shown"),
    [
        # Code keeps both backslashes; in emphasis ``\\`` is an escape for one.
        (r"`x\\`", r"x\\"),
        (r"**b\\**", "b\\"),
        (r"*i\\*", "i\\"),
        (r"~~s\\~~", "s\\"),
    ],
)
@pytest.mark.parametrize("align", ["---", ":-:", "--:"])
def test_a_whole_span_cell_ending_in_a_backslash_stays_aligned(
    cell: str, shown: str, align: str
) -> None:
    """Padding, not the span's closing tag, follows a left or centred cell."""
    source = f"| {cell} | x |\n| {align} | --- |\n| {cell} | y |\n| longer cell here | z |"
    lines = _display(source).plain.split("\n")

    assert len({cell_len(line) for line in lines}) == 1, lines
    assert lines[0].split("|")[1].strip() == shown, lines
    assert lines[2].split("|")[1].strip() == shown, lines


# ---------------------------------------------------------------------------
# A trailing backslash is escaped for what follows the line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("trailing \\", "trailing \\"),
        ("- item \\", "- item \\"),
        ("# heading \\", "heading \\"),
        ("> quoted \\", "▎ quoted \\"),
    ],
)
def test_a_line_ending_in_a_backslash_prints_it_once(source: str, expected: str) -> None:
    """A heading or quote closes its style right after the text, so the
    backslash there must not escape that tag; a paragraph or list item ends
    in plain text, where Rich's padding would print it twice."""
    assert _user_sees(source).rstrip() == expected
