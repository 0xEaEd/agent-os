"""Issues #3585 and #3588: two line counters outside ``tools`` had the old rule.

#3176 settled it for this toolchain -- a line ends at a newline, because that
is what ``git diff``, every editor and ``read_file`` count -- and added
``split_lines``/``split_lines_keepends``. The three call sites it fixed are
all inside ``agentos.tools``. Two outside it still used ``str.splitlines()``,
which breaks on eleven characters:

* ``memory.embedding.chunk_text`` numbers the chunks it indexes. Those numbers
  are persisted by ``store`` and shown to the user -- ``rpc_memory`` returns
  them as ``startLine``/``endLine`` and ``agentos memory search`` prints them
  -- so a form feed or a lone CR anywhere in a file offset every chunk after
  it against a grid nothing else uses (#3585).
* ``skills.outline.parse_sections`` finds a body's headings. A ``#`` after one
  of those characters was read as a heading and invented a section, which is
  the very thing the fence guard beside it exists to prevent (#3588).

Neither module could import the helper where it lived: ``agentos.tools``'s
``__init__`` registers every builtin tool and so imports memory and skills
straight back. The module moved to ``agentos.lines``, with the old path
re-exporting it.
"""

from __future__ import annotations

import pytest

from agentos.lines import split_lines, split_lines_keepends
from agentos.memory.embedding import chunk_text
from agentos.skills.outline import parse_sections

FORM_FEED = "\x0c"
VERTICAL_TAB = "\x0b"
CARRIAGE_RETURN = "\r"
NEL = "\u0085"
LINE_SEPARATOR = " "
PARAGRAPH_SEPARATOR = " "

BOUNDARY_CHARS = [
    pytest.param(FORM_FEED, id="form-feed"),
    pytest.param(VERTICAL_TAB, id="vertical-tab"),
    pytest.param(CARRIAGE_RETURN, id="lone-cr"),
    pytest.param(NEL, id="nel"),
    pytest.param(LINE_SEPARATOR, id="u2028"),
    pytest.param(PARAGRAPH_SEPARATOR, id="u2029"),
]


# ── #3585: the line numbers a search result shows ──────────────────────────


@pytest.mark.parametrize("char", BOUNDARY_CHARS)
def test_a_chunks_span_is_the_newline_line_count(char: str) -> None:
    body = f"line 1\nline 2{char}still line 2\nline 3\nline 4\n"

    chunks = chunk_text(body, chunk_tokens=10_000, chunk_overlap=0)

    assert len(chunks) == 1
    assert chunks[0][1] == len(split_lines(body)) == 4


def test_a_plain_body_is_numbered_exactly_as_before() -> None:
    body = "".join(f"line {n}\n" for n in range(1, 9))

    assert chunk_text(body, chunk_tokens=10_000, chunk_overlap=0)[0][:2] == (1, 8)


@pytest.mark.parametrize("char", BOUNDARY_CHARS)
def test_the_character_stays_inside_the_chunk_text(char: str) -> None:
    """It is content, so it has to survive into what gets embedded."""
    body = f"alpha\nbeta{char}gamma\n"

    assert char in chunk_text(body, chunk_tokens=10_000, chunk_overlap=0)[0][2]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "".join(f"line {n} with a little text on it\n" for n in range(1, 400)),
            id="many-short-lines",
        ),
        pytest.param(
            "short\n" + "x" * 40_000 + "\nshort\n" + "tail\n" * 50,
            id="one-dominant-line",
        ),
        pytest.param(
            "".join(f"line {n}\n" for n in range(1, 200)) + "last",
            id="no-trailing-newline",
        ),
        pytest.param(f"alpha\nbeta{FORM_FEED}gamma\n" * 200, id="many-form-feeds"),
    ],
)
def test_every_line_still_lands_in_some_chunk(body: str) -> None:
    """The invariant the overlap logic (#3348) exists to hold: no line is
    dropped, and the span numbers stay on the newline grid."""
    chunks = chunk_text(body)
    covered: set[int] = set()
    for start, end, _text in chunks:
        covered.update(range(start, end + 1))

    assert covered == set(range(1, len(split_lines(body)) + 1))


# ── #3588: headings in a skill body ────────────────────────────────────────


@pytest.mark.parametrize("char", BOUNDARY_CHARS)
def test_a_hash_after_a_boundary_character_is_not_a_heading(char: str) -> None:
    body = f"# Real heading\n\nprose with a break{char}# Not a heading, same paragraph\n\nmore\n"

    assert [(s.level, s.title) for s in parse_sections(body)] == [(1, "Real heading")]


def test_an_ordinary_body_parses_exactly_as_before() -> None:
    body = "# One\n\na\n\n## Two\n\nb\n\n# Three\n\nc\n"

    assert [(s.level, s.title) for s in parse_sections(body)] == [
        (1, "One"),
        (2, "Two"),
        (1, "Three"),
    ]


def test_section_offsets_still_point_at_their_heading() -> None:
    """``keepends`` is load-bearing: ``offset`` is a running character
    position, so the terminators have to be counted."""
    body = f"# One\n\nprose{FORM_FEED}more prose\n\n# Two\n\nb\n"

    for section in parse_sections(body):
        assert body[section.start :].startswith(f"# {section.title}")


def test_a_fenced_hash_is_still_not_a_heading() -> None:
    body = "# Real\n\n```\n# fake\n```\n"

    assert [s.title for s in parse_sections(body)] == ["Real"]


def test_a_crlf_body_parses_the_same_as_an_lf_one() -> None:
    lf = "# One\n\na\n\n## Two\n\nb\n"

    assert [(s.level, s.title) for s in parse_sections(lf.replace("\n", "\r\n"))] == [
        (s.level, s.title) for s in parse_sections(lf)
    ]


# ── the move itself ────────────────────────────────────────────────────────


def test_the_old_import_path_still_works() -> None:
    """``tools.builtin._lines`` re-exports, so nothing that imported it
    before has to change."""
    from agentos.tools.builtin import _lines

    assert _lines.split_lines is split_lines
    assert _lines.split_lines_keepends is split_lines_keepends


def test_the_new_home_imports_without_pulling_in_tools() -> None:
    """The reason for the move: importing the helper must not drag in
    ``agentos.tools``, whose ``__init__`` registers every builtin tool."""
    import ast
    from pathlib import Path

    source = Path("src/agentos/lines.py").read_text(encoding="utf-8")
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(name.startswith("agentos") for name in imported), imported
