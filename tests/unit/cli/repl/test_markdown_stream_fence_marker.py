"""Issue #3175: any fence line closed any open fenced block.

``_render_line_inner`` held the block state as a bare toggle over
``_FENCE_RE``, a pattern that neither distinguishes the two fence characters
nor compares run lengths::

    if _FENCE_RE.match(line):
        self._in_fence = not self._in_fence

So a ``~~~`` line inside a backtick block ended it, the block's real closing
fence then opened a *new* block, and everything after came out inverted --
code rendered as prose, prose rendered as code -- for the rest of the turn,
since nothing ever resynchronises the state.

CommonMark closes a fenced block on a fence that uses the same character as
the opener and is at least as long. That is what lets a backtick block quote
a tilde one, and a four-backtick block quote a three-backtick one.
"""

from __future__ import annotations

from agentos.cli.tui.terminal.markdown_stream import MarkdownStreamRenderer

_CODE_TAG = "[#DDFF66 on #1a1a1a]"


def _render(source: str) -> str:
    renderer = MarkdownStreamRenderer(enabled=True)
    return renderer.feed(source) + renderer.flush()


def _is_code(rendered: str, text: str) -> bool:
    return f"{_CODE_TAG}{text}[/]" in rendered


def test_a_tilde_line_does_not_close_a_backtick_block() -> None:
    """The issue's own repro."""
    rendered = _render("before\n```python\ncode 1\n~~~\ncode 2\n```\nafter\n")

    assert _is_code(rendered, "code 1")
    assert _is_code(rendered, "code 2")
    assert _is_code(rendered, "~~~")  # content of the block, not a delimiter
    # The point of the bug: ordinary prose after the block was painted as code.
    assert not _is_code(rendered, "after")
    assert "\nafter\n" in rendered


def test_a_backtick_line_does_not_close_a_tilde_block() -> None:
    rendered = _render("before\n~~~\ncode 1\n```\ncode 2\n~~~\nafter\n")

    assert _is_code(rendered, "code 1")
    assert _is_code(rendered, "code 2")
    assert _is_code(rendered, "```")
    assert not _is_code(rendered, "after")


def test_a_shorter_run_does_not_close_a_longer_block() -> None:
    """A four-backtick block quoting a three-backtick one."""
    rendered = _render("before\n````\ncode\n```\nstill code\n````\nafter\n")

    assert _is_code(rendered, "code")
    assert _is_code(rendered, "```")
    assert _is_code(rendered, "still code")
    assert not _is_code(rendered, "after")


def test_a_longer_run_does_close_a_shorter_block() -> None:
    """CommonMark only requires *at least* the opener's length."""
    rendered = _render("before\n```\ncode\n`````\nafter\n")

    assert _is_code(rendered, "code")
    assert not _is_code(rendered, "after")


def test_a_plain_block_still_opens_and_closes() -> None:
    rendered = _render("before\n```\ncode\n```\nafter\n")

    assert _is_code(rendered, "code")
    assert not _is_code(rendered, "before")
    assert not _is_code(rendered, "after")
    # Both fence markers stay hidden.
    assert "```" not in rendered


def test_an_info_string_does_not_stop_a_block_closing() -> None:
    rendered = _render("```python\ncode\n```\nafter\n")

    assert _is_code(rendered, "code")
    assert not _is_code(rendered, "after")


def test_a_closing_fence_may_carry_trailing_whitespace() -> None:
    rendered = _render("```\ncode\n```   \nafter\n")

    assert _is_code(rendered, "code")
    assert not _is_code(rendered, "after")


def test_two_blocks_in_a_row_do_not_desynchronise() -> None:
    rendered = _render("```\na\n```\nmiddle\n~~~\nb\n~~~\nend\n")

    assert _is_code(rendered, "a")
    assert _is_code(rendered, "b")
    assert not _is_code(rendered, "middle")
    assert not _is_code(rendered, "end")


def test_a_second_renderer_starts_with_no_open_marker() -> None:
    """The marker is per-turn state; a fresh renderer must not inherit one."""
    first = MarkdownStreamRenderer(enabled=True)
    first.feed("```\nunclosed\n")
    first.flush()

    rendered = _render("plain line\n")

    assert not _is_code(rendered, "plain line")
