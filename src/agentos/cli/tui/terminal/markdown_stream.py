"""Incremental markdown renderer for the terminal chat stream.

The chat renderer writes sanitized model output straight to the terminal
(write-once, no ``Rich.Live`` re-render — see ``stream.py`` for why that
contract exists). This module layers a *line-buffered* markdown pass on
top of that contract:

  * Tokens are accumulated until a newline arrives, so every emitted line
    is final and never needs repainting.
  * Block-level constructs (``#``/``##``/``###`` headings, ``>`` quotes,
    ``---`` rules, fenced code blocks, tables, list items) are detected
    from the line prefix and styled with the brand palette.
  * Inline spans (``**bold**``, ``*italic*``, ``~~strike~~``, inline
    ``code``, links) are styled inside the line after block styling.
  * Fenced code blocks stream their body lines immediately in a uniform
    code style — no waiting for the closing fence, no syntax-highlight
    delay, no re-render. The fence markers themselves are hidden so the
    block reads as one continuous region.
  * ``NO_COLOR`` (or a non-color console) downgrades every transform to a
    plain-text passthrough so piped output stays greppable.

The renderer is stateful and single-use per assistant turn; create one
via ``MarkdownStreamRenderer(enabled=...)`` and feed deltas through
``feed``. ``flush`` must be called at end-of-turn to emit any trailing
partial line.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field

from rich.cells import cell_len
from rich.markup import escape as _rich_escape

from agentos.cli.ui import (
    ACCENT,
    ACCENT_DIM,
    ACCENT_SOFT,
    console,
)

__all__ = ["MarkdownStreamRenderer", "markdown_enabled", "render_markup_to_ansi"]


# ---------------------------------------------------------------------------
# Style vocabulary (brand palette, terminal-safe)
# ---------------------------------------------------------------------------

_HEADING_STYLE = f"bold {ACCENT}"
_QUOTE_STYLE = "dim"
_RULE_STYLE = ACCENT_DIM
_CODE_STYLE = f"{ACCENT_SOFT} on #1a1a1a"
_INLINE_CODE_STYLE = f"bold {ACCENT_SOFT}"
_LIST_MARKER_STYLE = ACCENT
_BOLD_STYLE = "bold"
_ITALIC_STYLE = "italic"
_STRIKE_STYLE = "strike"
_LINK_STYLE = f"underline {ACCENT_SOFT}"
_LINK_URL_STYLE = "dim"
# Think blocks (<think>…</think> from reasoning models): deliberately
# *less* prominent than quotes — no accent color at all, just a near-gray
# bar and dim italic text, so the reasoning reads as background context
# and never competes with the actual reply.
_THINK_BAR_STYLE = "#3a3a3a"
_THINK_TEXT_STYLE = "dim italic"


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_RULE_RE = re.compile(r"^(?:\*\s*){3,}$|^(?:-\s*){3,}$|^(?:_\s*){3,}$")
# The whole opening run is captured, because what closes a fenced block is
# the opener's own marker: CommonMark requires the closing fence to use the
# same character and be at least as long. A bare toggle over "any fence
# line" let a ``~~~`` inside a ```` ``` ```` block end it, after which the
# real closing fence opened a *new* block -- prose rendered as code and code
# as prose, for the rest of the turn.
_FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")

# Reasoning-model think tags. The block forms own a whole line; the
# single-line form wraps content inline (``<think>…</think>``).
_THINK_OPEN_RE = re.compile(r"^\s*<think>\s*$")
_THINK_CLOSE_RE = re.compile(r"^\s*</think>\s*$")
_THINK_INLINE_RE = re.compile(r"^\s*<think>(.*?)</think>\s*$")
# Stray think-tag artifacts: models occasionally emit a bare ``<>`` (or
# ``</>``) line — a mangled ``</think>`` — which must never reach the
# screen as literal text. The artifact is hidden in every state, and while
# a think block is open it also closes the block (without that valve,
# everything after would render dim forever). Matching is anchored to the
# whole line so inline occurrences in prose, tables, and code (``a <> b``)
# are left untouched; fenced code is checked before this and always stays
# literal anyway.
_STRAY_THINK_ARTIFACT_RE = re.compile(r"^\s*</?>\s*$")


def _styled(text: str, style: str) -> str:
    return f"[{style}]{text}[/]"


# ---------------------------------------------------------------------------
# Inline span rendering
# ---------------------------------------------------------------------------


def _is_escaped(text: str, index: int) -> bool:
    """True when the character at *index* follows an odd run of backslashes.

    An escaped backtick is literal and cannot open a code span. Counting the
    whole run keeps a double backslash before a backtick a real delimiter:
    there the first backslash escapes the second, not the backtick.
    """
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _find_closing_backtick_run(text: str, start: int, length: int) -> int:
    """Index of the next backtick run of *exactly* ``length`` at or after
    ``start``, or -1 -- the same scan as ``channels/_telegram_formatting.py``
    (#3173).

    CommonMark closes a code span on a run of the opener's own length, so a
    span opened with two backticks can quote a single one. A one-backtick
    pattern closed it on that inner backtick instead, deleting it and
    printing the outer pair (#3426). A run of the wrong length is content
    and is skipped whole.
    """
    cursor = start
    while cursor < len(text):
        if text[cursor] != "`":
            cursor += 1
            continue
        run_end = cursor
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        if run_end - cursor == length:
            return cursor
        cursor = run_end
    return -1


def _protect_code_spans(text: str) -> tuple[str, list[str]]:
    """Swap each code span for a placeholder, returning the raw contents.

    This runs before escapes are parked, so nothing inside a span -- the
    ``\\.`` of a regex, the backslashes of a Windows path -- is unescaped:
    CommonMark does not process backslash escapes in code. An escaped
    backtick cannot *open* a span; the closer is matched raw, as in the
    reference implementation.
    """
    contents: list[str] = []
    out: list[str] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] != "`" or _is_escaped(text, cursor):
            out.append(text[cursor])
            cursor += 1
            continue
        marker_end = cursor
        while marker_end < len(text) and text[marker_end] == "`":
            marker_end += 1
        length = marker_end - cursor
        closing = _find_closing_backtick_run(text, marker_end, length)
        if closing < 0:
            out.append(text[cursor:marker_end])
            cursor = marker_end
            continue
        out.append(f"\x00CODE{len(contents)}\x00")
        contents.append(text[marker_end:closing])
        cursor = closing + length
    return "".join(out), contents


#: CommonMark's escapable set: a backslash before ASCII punctuation makes
#: that character literal. A backslash before anything else (``\d`` in a
#: regex, ``C:\Users``) is an ordinary backslash and stays.
_ESCAPED_PUNCT_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_ESC_PLACEHOLDER_RE = re.compile(r"\x00ESC(\d+)\x00")
_CODE_PLACEHOLDER_RE = re.compile(r"\x00CODE(\d+)\x00")


def _park_escaped_punctuation(text: str) -> tuple[str, list[str]]:
    """Replace ``\\<punctuation>`` with placeholders, returning the characters.

    The marker has to be out of the way before the span patterns run, or
    they match it anyway -- which is why ``\\*not italic\\*`` came back
    italicised with its backslashes still in it (#3426).
    """
    parked: list[str] = []

    def _park(match: re.Match[str]) -> str:
        parked.append(match.group(1))
        return f"\x00ESC{len(parked) - 1}\x00"

    return _ESCAPED_PUNCT_RE.sub(_park, text), parked


def _unpark(pattern: re.Pattern[str], values: list[str], text: str) -> str:
    """Put parked *values* back in one pass. A lookalike placeholder that was
    already in the model's text has no value and is left as it is."""

    def _value(m: re.Match[str]) -> str:
        index = int(m.group(1))
        return values[index] if index < len(values) else m.group(0)

    return pattern.sub(_value, text)


#: A backslash directly before a ``[`` that cannot open a Rich tag.
_BACKSLASH_BEFORE_PLAIN_BRACKET_RE = re.compile(r"\\(?=\[(?![a-z#/@][^[]*?\]))")


def _escape_literal(text: str, *, tag_follows: bool = True) -> str:
    """Rich-escape *text* so it renders byte-for-byte.

    Rich's ``escape`` only touches a ``[`` that looks like a tag, but its
    parser turns *every* ``\\[`` into ``[``, so a code span quoting the regex
    ``^\\[\\d+\\]$`` lost its first backslash. Doubling the backslash in
    front of such a bracket keeps it.

    A trailing backslash run depends on what follows: before a tag Rich
    reads ``n`` backslashes as ``n // 2`` (and an odd run escapes the tag),
    elsewhere they are literal. ``escape`` pads only a single backslash, so
    a code span ending in two printed one and one ending in three leaked its
    ``[/]``. The whole run is doubled when *tag_follows*, kept otherwise.
    """
    body = text.rstrip("\\")
    trailing = len(text) - len(body)
    escaped = _rich_escape(_BACKSLASH_BEFORE_PLAIN_BRACKET_RE.sub(r"\\\\", body))
    return escaped + "\\" * (trailing * 2 if tag_follows else trailing)


# ---- emphasis: CommonMark's delimiter-run algorithm -----------------------
#
# Emphasis used to be a set of regexes, one per span kind, with the enclosing
# match kept when two overlapped. No regex can see that an inner span closes
# on the same ``*`` run as the outer one: ``**Note: this is *important***``
# ends in one ``***`` run that closes the italic *and* the bold, and
# ``*italic with **bold** inside*`` holds a ``**`` the italic pattern could
# not cross. Both printed delimiters (#3426). CommonMark pairs delimiter runs
# instead, which is what is implemented here: each run of ``*`` (or a ``~~``
# pair, GFM's strikethrough) is a delimiter that can open, close or both,
# depending on what flanks it, and every closer is matched back to the
# nearest compatible opener.


@dataclass
class _Delim:
    """A ``*`` run or a ``~~`` pair, and the spans it opens and closes."""

    char: str
    length: int  # characters still unmatched
    origin: int  # the run's length in the source, for the rule of three
    order: int  # position among the line's delimiters
    can_open: bool
    can_close: bool
    #: Styles this run opens, innermost first.
    opens: list[str] = field(default_factory=list)
    #: How many spans this run closes.
    closes: int = 0


@dataclass
class _Link:
    text: str
    url: str


@dataclass
class _Open:
    style: str


class _Close:
    pass


_CLOSE = _Close()

_Node = str | _Delim | _Link
#: A parsed line, flattened: text, a link, or a span's open/close tag.
_Event = str | _Link | _Open | _Close


def _is_punctuation(char: str) -> bool:
    """CommonMark's punctuation: ASCII punctuation and Unicode P* and S*.

    A placeholder's NUL stands for what it replaced -- an escaped
    punctuation character or a code span's backtick -- so it counts too.
    """
    return char == "\x00" or unicodedata.category(char)[0] in "PS"


def _scan_delimiters(text: str, start: int, end: int, order: int, nodes: list[_Node]) -> int:
    """Append ``text[start:end]`` to *nodes* as text and delimiters; return
    the next delimiter order.

    A run's flanking is judged on the characters around it in the whole
    line, so the ``)`` of a link before it counts (the line's start and end
    count as whitespace): ``*`` can open when left-flanking and close when
    right-flanking. Only a ``~`` run of exactly two is a strikethrough
    delimiter, so ``~5 min`` and ``~~~`` stay text.
    """
    cursor = start
    while cursor < end:
        char = text[cursor]
        if char not in "*~":
            cursor += 1
            continue
        run_end = cursor
        while run_end < end and text[run_end] == char:
            run_end += 1
        run = run_end - cursor
        if char == "~" and run != 2:
            cursor = run_end
            continue
        before = text[cursor - 1] if cursor else " "
        after = text[run_end] if run_end < len(text) else " "
        left_flanking = not after.isspace() and (
            not _is_punctuation(after) or before.isspace() or _is_punctuation(before)
        )
        right_flanking = not before.isspace() and (
            not _is_punctuation(before) or after.isspace() or _is_punctuation(after)
        )
        if start < cursor:
            nodes.append(text[start:cursor])
        nodes.append(_Delim(char, run, run, order, left_flanking, right_flanking))
        order += 1
        cursor = start = run_end
    if start < end:
        nodes.append(text[start:end])
    return order


def _can_pair(opener: _Delim, closer: _Delim) -> bool:
    """CommonMark's rule of three: when either run can both open and close,
    their lengths may not add up to a multiple of three unless both lengths
    are. It is what keeps ``*a**b*`` one italic around ``a**b``."""
    if opener.char == "~":
        return True
    if not (opener.can_close or closer.can_open):
        return True
    if (opener.origin + closer.origin) % 3:
        return True
    return opener.origin % 3 == 0 and closer.origin % 3 == 0


def _match_emphasis(delimiters: list[_Delim]) -> None:
    """CommonMark's *process emphasis*, over the whole line.

    Each closer, left to right, takes the nearest opener of its character
    that the rule of three allows. Two characters are used when both runs
    have two left (bold), otherwise one (italic); a ``~~`` pair is struck.
    A delimiter between the two that found no partner stays text. A run
    with characters left keeps matching, which is how one ``***`` closes an
    italic and a bold at once.

    Matches are recorded on the delimiters, not applied to the line, so a
    match costs nothing beyond the search: the stack holds only the runs
    still open, and the spec's openers_bottom stops a closer that already
    failed from searching the same openers again.
    """
    bottom: dict[tuple[str, bool, int], int] = {}
    stack: list[_Delim] = []
    for closer in delimiters:
        key = (closer.char, closer.can_open, closer.origin % 3)
        while closer.can_close and closer.length:
            floor = bottom.get(key, -1)
            index = len(stack) - 1
            while index >= 0 and stack[index].order > floor:
                opener = stack[index]
                if opener.char == closer.char and _can_pair(opener, closer):
                    break
                index -= 1
            else:
                bottom[key] = closer.order - 1
                break
            if closer.char == "~":
                used, style = 2, _STRIKE_STYLE
            elif opener.length >= 2 and closer.length >= 2:
                used, style = 2, _BOLD_STYLE
            else:
                used, style = 1, _ITALIC_STYLE
            opener.opens.append(style)
            closer.closes += 1
            opener.length -= used
            closer.length -= used
            del stack[index + 1 :]
            if not opener.length:
                del stack[index]
        if closer.can_open and closer.length:
            stack.append(closer)


@dataclass
class _InlineParse:
    """A line split into events, with the code spans and escapes it parked."""

    events: list[_Event]
    code_contents: list[str]
    escaped_chars: list[str]

    def markup(self, segment: str, tag_follows: bool) -> str:
        """Rich markup for a text *segment*, its parked pieces restored."""
        # An escape comes back as its bare character *before* Rich-escaping,
        # because Rich's escape depends on what follows: a restored ``[`` has
        # to be seen next to the ``bold]`` it would otherwise open as a tag.
        # Code spans are spliced in between the escaped pieces for the same
        # reason -- a piece ending in a restored ``\`` must see the tag.
        segment = _unpark(_ESC_PLACEHOLDER_RE, self.escaped_chars, segment)
        pieces = _CODE_PLACEHOLDER_RE.split(segment)
        out: list[str] = []
        text_piece = pieces[0]
        for i in range(1, len(pieces), 2):
            index = int(pieces[i])
            if index >= len(self.code_contents):
                text_piece += f"\x00CODE{index}\x00" + pieces[i + 1]
                continue
            out.append(_escape_literal(text_piece))
            out.append(f"[{_INLINE_CODE_STYLE}]{_escape_literal(self.code_contents[index])}[/]")
            text_piece = pieces[i + 1]
        out.append(_escape_literal(text_piece, tag_follows=tag_follows))
        return "".join(out)

    def plain(self, segment: str) -> str:
        """The characters a text *segment* puts on screen."""
        segment = _unpark(_ESC_PLACEHOLDER_RE, self.escaped_chars, segment)
        return _unpark(_CODE_PLACEHOLDER_RE, self.code_contents, segment)

    def render(self, events: list[_Event], *, tag_follows: bool) -> str:
        out: list[str] = []
        for i, event in enumerate(events):
            if isinstance(event, str):
                # Every other event starts with a tag.
                follows = tag_follows if i == len(events) - 1 else True
                out.append(self.markup(event, follows))
            elif isinstance(event, _Link):
                out.append(
                    f"[{_LINK_STYLE}]{self.markup(event.text, True)}[/]"
                    f" [{_LINK_URL_STYLE}]({self.markup(event.url, False)})[/]"
                )
            elif isinstance(event, _Open):
                out.append(f"[{event.style}]")
            else:
                out.append("[/]")
        return "".join(out)

    def display(self) -> str:
        out: list[str] = []
        for event in self.events:
            if isinstance(event, str):
                out.append(self.plain(event))
            elif isinstance(event, _Link):
                out.append(f"{self.plain(event.text)} ({self.plain(event.url)})")
        return "".join(out)


def _parse_inline(text: str) -> _InlineParse:
    """Split one (already block-stripped) line into styled events.

    Code spans are protected first and backslash escapes parked second --
    the order ``channels/_telegram_formatting.py`` uses (#3307) -- so an
    escaped backtick cannot open a span and nothing inside one is
    unescaped. A link is claimed next and is opaque to emphasis: a ``*`` in
    its text or destination never pairs with one outside it, so emphasis
    can enclose a link but never break one. Link text is shown as written.
    """
    text, code_contents = _protect_code_spans(text)
    text, escaped_chars = _park_escaped_punctuation(text)

    nodes: list[_Node] = []
    order = 0
    cursor = 0
    for m in _LINK_RE.finditer(text):
        order = _scan_delimiters(text, cursor, m.start(), order, nodes)
        nodes.append(_Link(m.group(1), m.group(2)))
        cursor = m.end()
    _scan_delimiters(text, cursor, len(text), order, nodes)

    _match_emphasis([node for node in nodes if isinstance(node, _Delim)])

    # A run closes its spans before what is left of it, and opens its own
    # after: a closer uses the characters on its inner (left) side, an
    # opener those on its right.
    events: list[_Event] = []

    def _text(part: str) -> None:
        if events and isinstance(events[-1], str):
            events[-1] += part
        elif part:
            events.append(part)

    for node in nodes:
        if isinstance(node, _Delim):
            events.extend([_CLOSE] * node.closes)
            _text(node.char * node.length)
            events.extend(_Open(style) for style in reversed(node.opens))
        elif isinstance(node, str):
            _text(node)
        else:
            events.append(node)
    return _InlineParse(events, code_contents, escaped_chars)


def _render_inline(text: str, *, tag_follows: bool = True) -> str:
    """Apply inline markdown styling to a single (already block-stripped)
    line of text.

    Plain segments are Rich-escaped; styled segments wrap their escaped
    content. Building markup this way (instead of escaping the whole line
    up front) keeps the ``\\[`` escape sequences from colliding with the
    tag insertion, which previously produced unbalanced markup for e.g.
    ``[text](url)`` links.

    *tag_follows* says whether the caller puts markup straight after the
    result, which decides how a trailing backslash is escaped.
    """
    parsed = _parse_inline(text)
    return parsed.render(parsed.events, tag_follows=tag_follows)


# ---------------------------------------------------------------------------
# Block-level line rendering
# ---------------------------------------------------------------------------


def _closes_fence(marker: str, line: str) -> bool:
    """Whether *line* is the closing fence for a block opened with *marker*.

    CommonMark: the closing fence uses the same character as the opener, is
    at least as long, and carries no info string -- which is what lets a
    ```` ``` ```` block quote a ``~~~`` one, and a ````` ```` ````` block
    quote a ```` ``` ```` one.
    """
    stripped = line.strip()
    return len(stripped) >= len(marker) and stripped == marker[0] * len(stripped)


def _render_code_line(line: str) -> str:
    """A line inside a fenced code block: uniform code style, escaped."""
    return _styled(_rich_escape(line) or " ", _CODE_STYLE)


def _render_think_line(line: str) -> str:
    """A line inside a ``<think>`` block: near-gray bar + dim italic text.

    Intentionally plainer than the quote style — the accent palette is
    reserved for the actual reply, so the reasoning stays visibly present
    but visually recessive.
    """
    bar = _styled("▎", _THINK_BAR_STYLE)
    if not line.strip():
        return bar
    return f"{bar} {_styled(_rich_escape(line), _THINK_TEXT_STYLE)}"


# ---------------------------------------------------------------------------
# Table block rendering
# ---------------------------------------------------------------------------
#
# A markdown table cannot be aligned row-by-row during streaming (column
# widths are not known until the last row arrives), so table lines are
# buffered while the block is open and rendered as a unit when it closes.
# This is the one intentional exception to the write-once contract, and it
# is the same trade-off every streaming terminal markdown renderer makes.

# GFM's delimiter cell is *one or more* hyphens with an optional leading
# and/or trailing colon, so `-`, `--`, `:-`, `-:` and `:-:` are all valid.
# Demanding three eliminated the compact spellings, and a table written that
# way was not recognised as a table at all: the raw pipes and dashes were
# printed to the terminal as prose.
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_TABLE_MIN_COL_WIDTH = 5
_TABLE_PAD = 1  # spaces on each side of a cell


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|") and line.rstrip().endswith("|")


def _split_table_row(line: str) -> list[str]:
    """Split a ``| a | b |`` row into trimmed cell strings."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not (stripped.endswith(r"\|") and not stripped.endswith(r"\\\|")):
        stripped = stripped[:-1]

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    code_marker_length = 0
    cursor = 0
    while cursor < len(stripped):
        char = stripped[cursor]
        if escaped:
            current.append(char)
            escaped = False
            cursor += 1
            continue
        if char == "\\":
            escaped = True
            current.append(char)
            cursor += 1
            continue
        if char == "`":
            marker_end = cursor
            while marker_end < len(stripped) and stripped[marker_end] == "`":
                marker_end += 1
            marker_length = marker_end - cursor
            if code_marker_length == 0:
                code_marker_length = marker_length
            elif code_marker_length == marker_length:
                code_marker_length = 0
            current.append(stripped[cursor:marker_end])
            cursor = marker_end
            continue
        if char == "|" and code_marker_length == 0:
            cells.append("".join(current).strip().replace(r"\|", "|"))
            current = []
        else:
            current.append(char)
        cursor += 1
    cells.append("".join(current).strip().replace(r"\|", "|"))
    return cells


def _is_table_separator_row(line: str) -> bool:
    # ``fullmatch`` states the intent the trailing ``$`` was carrying. Same
    # result for every cell reaching here (they are stripped, so the one case
    # ``$`` is laxer about -- a trailing newline -- cannot occur), but it keeps
    # the check correct if the anchor is ever dropped from the pattern.
    cells = _split_table_row(line)
    return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.fullmatch(c) for c in cells)


def _parse_table_alignment(line: str, ncols: int) -> list[str]:
    """Parse the ``:---`` / ``:--:`` / ``---:`` separator row into per-column
    alignment (``left``/``center``/``right``)."""
    aligns: list[str] = []
    for cell in _split_table_row(line):
        left = cell.startswith(":")
        right = cell.endswith(":")
        if left and right:
            aligns.append("center")
        elif right:
            aligns.append("right")
        else:
            aligns.append("left")
    while len(aligns) < ncols:
        aligns.append("left")
    return aligns[:ncols]


def _cell_plain(text: str) -> str:
    """Strip inline markdown markers for width measurement.

    Read from the same parse `_render_inline` draws from, so a cell is
    measured exactly as drawn.
    """
    return _parse_inline(text).display()


def _cell_width(text: str) -> int:
    """Display width of a cell with inline markdown removed (CJK-aware)."""
    return cell_len(_cell_plain(text))


def _whole_cell_span(parsed: _InlineParse) -> tuple[str, list[_Event] | str] | None:
    """If the whole cell is one inline span (``**x**``, ``*x*``, ``~~x~~``,
    ```x```), return its style and content -- the inner events, or a code
    span's raw text -- so the padded cell can be wrapped uniformly. This
    keeps alignment intact instead of leaving the padding unstyled.
    """
    events = parsed.events
    if len(events) == 1 and isinstance(events[0], str):
        m = _CODE_PLACEHOLDER_RE.fullmatch(events[0])
        if m and int(m.group(1)) < len(parsed.code_contents):
            return _INLINE_CODE_STYLE, parsed.code_contents[int(m.group(1))]
        return None
    if len(events) < 2 or not isinstance(events[0], _Open) or events[-1] is not _CLOSE:
        return None
    # The first tag must be the one the last closes, not ``*a* and *b*``.
    depth = 0
    for event in events[:-1]:
        if isinstance(event, _Open):
            depth += 1
        elif event is _CLOSE:
            depth -= 1
            if not depth:
                return None
    return events[0].style, events[1:-1]


def _render_cell_content(text: str, width: int, align: str, *, header: bool) -> str:
    """Render one table cell padded to ``width`` display columns.

    When the entire cell is a single inline span the span's style wraps the
    *padded* content so the alignment padding is styled too; otherwise
    inline spans are styled individually and padding is added as plain
    space around the rendered content.
    """
    parsed = _parse_inline(text)
    natural = cell_len(parsed.display())
    overflow = max(0, width - natural)
    if align == "right":
        left_pad, right_pad = overflow, 0
    elif align == "center":
        left_pad, right_pad = overflow // 2, overflow - overflow // 2
    else:
        left_pad, right_pad = 0, overflow
    lpad = " " * left_pad
    rpad = " " * right_pad

    whole = _whole_cell_span(parsed)
    if whole is not None:
        span_style, inner_events = whole
        # Code is literal; an emphasis span's content may nest another span
        # or hold an escape, so it is rendered like any other inline text.
        # The closing tag only comes straight after when there is no padding.
        if isinstance(inner_events, str):
            content = _escape_literal(inner_events, tag_follows=not rpad)
        else:
            content = parsed.render(inner_events, tag_follows=not rpad)
        inner = lpad + content + rpad
        rendered = f"[{span_style}]{inner}[/]"
    else:
        # The padding, or the header's closing tag, comes straight after.
        rendered = lpad + parsed.render(parsed.events, tag_follows=header and not rpad) + rpad
    if header:
        return f"[bold]{rendered}[/]"
    return rendered


def _wrap_cell_text(text: str, width: int) -> list[str]:
    """Wrap a *plain* cell string to at most ``width`` display columns.

    Splits on spaces; a token longer than the width is hard-split by
    display columns. Returns at least one (possibly empty) line. Cells are
    wrapped on the plain text (markers stripped) so wrapping never cuts a
    markdown span in half — the wrapped lines render as plain text.
    """
    if width < 1:
        width = 1
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if cell_len(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # Hard-split an over-long token by display columns.
        while cell_len(word) > width:
            cut = 0
            acc = 0
            for ch in word:
                w = cell_len(ch)
                if acc + w > width:
                    break
                acc += w
                cut += 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current or not lines:
        lines.append(current)
    return lines


def _allocate_table_widths(
    rows: list[list[str]],
    aligns: list[str],
    max_total: int,
) -> list[int]:
    """Compute per-column display widths, shrinking to fit ``max_total``.

    Natural widths win until the budget is exhausted; the widest columns
    are then shrunk one column at a time (never below
    ``_TABLE_MIN_COL_WIDTH``) so narrow columns keep their full width.
    """
    ncols = len(aligns)
    widths = [
        max([_TABLE_MIN_COL_WIDTH] + [_cell_width(r[i]) for r in rows if i < len(r)])
        for i in range(ncols)
    ]

    def total() -> int:
        # total = sum(widths) + padding (2 per cell) + separators (ncols+1)
        return sum(widths) + ncols * 2 * _TABLE_PAD + (ncols + 1)

    while total() > max_total:
        widest = max(range(ncols), key=lambda i: widths[i])
        if widths[widest] <= _TABLE_MIN_COL_WIDTH:
            break
        widths[widest] -= 1
    return widths


def _render_table_block(table_lines: list[str]) -> str:
    """Render a buffered table block as an aligned, styled unit.

    The block starts with a header row and separator row; remaining rows
    are body rows. Columns are width-allocated against the console width
    and cells are padded (alignment-aware) so every column lines up.
    """
    if len(table_lines) < 2:
        # Degenerate block (no separator row) — render as plain lines.
        return "\n".join(_render_table_line(ln) for ln in table_lines)

    header = _split_table_row(table_lines[0])
    ncols = len(header)
    aligns = _parse_table_alignment(table_lines[1], ncols)
    body = [_split_table_row(ln) for ln in table_lines[2:] if not _is_table_separator_row(ln)]
    all_rows = [header, *body]

    # Budget: console width, minus a small safety margin so the table never
    # touches the terminal edge (which would cause an unwanted wrap).
    budget = max(40, console.width - 2)
    widths = _allocate_table_widths(all_rows, aligns, budget)

    pipe = f"[{_RULE_STYLE}]|[/]"
    pad = " " * _TABLE_PAD

    def _render_row(cells: list[str], *, is_header: bool) -> list[str]:
        # Normalize ragged rows to the column count.
        normalized = [(cells[i] if i < len(cells) else "") for i in range(ncols)]
        # Wrap any cell whose plain width exceeds its column budget.
        wrapped = [
            _wrap_cell_text(_cell_plain(normalized[i]), widths[i])
            if _cell_width(normalized[i]) > widths[i]
            else [normalized[i]]
            for i in range(ncols)
        ]
        height = max(len(w) for w in wrapped)
        out_lines: list[str] = []
        for sub in range(height):
            parts: list[str] = []
            for i in range(ncols):
                if sub < len(wrapped[i]):
                    cell_text = wrapped[i][sub]
                    # Wrapped continuation lines are plain (markers were
                    # stripped by the wrapper); style the header row only.
                    if len(wrapped[i]) > 1 and cell_len(_cell_plain(cell_text)) > widths[i]:
                        # A hard-split fragment — render plain.
                        natural = cell_len(_cell_plain(cell_text))
                        content = _rich_escape(cell_text) + " " * max(0, widths[i] - natural)
                    else:
                        content = _render_cell_content(
                            cell_text,
                            widths[i],
                            aligns[i],
                            header=is_header and len(wrapped[i]) == 1,
                        )
                    parts.append(pad + content + pad)
                else:
                    parts.append(pad + " " * widths[i] + pad)
            out_lines.append(pipe + pipe.join(parts) + pipe)
        return out_lines

    lines: list[str] = []
    lines.extend(_render_row(header, is_header=True))
    # Separator: dashes fill each column (padding included), dimmed.
    sep_parts = [_styled("─" * (widths[i] + 2 * _TABLE_PAD), _RULE_STYLE) for i in range(ncols)]
    lines.append(pipe + pipe.join(sep_parts) + pipe)
    for row in body:
        lines.extend(_render_row(row, is_header=False))
    return "\n".join(lines)


def _render_table_line(line: str) -> str:
    """Fallback for a degenerate (unparseable) table line: dim the pipes."""
    stripped = line.rstrip()
    escaped = _rich_escape(stripped)
    escaped = escaped.replace("|", f"[{_RULE_STYLE}]|[/]")
    return escaped


def _render_list_line(line: str) -> str:
    m = _LIST_RE.match(line)
    if not m:
        return _render_inline(line, tag_follows=False)
    indent, marker, body = m.groups()
    styled_marker = f"[{_LIST_MARKER_STYLE}]{_rich_escape(marker)}[/]"
    return f"{_rich_escape(indent)}{styled_marker} {_render_inline(body, tag_follows=False)}"


def _render_quote_line(line: str) -> str:
    m = _QUOTE_RE.match(line)
    if not m:
        return _render_inline(line, tag_follows=False)
    body = m.group(1)
    return f"[{_QUOTE_STYLE}]▎ {_render_inline(body)}[/]"


def _render_heading_line(line: str) -> str:
    m = _HEADING_RE.match(line)
    if not m:
        return _render_inline(line, tag_follows=False)
    _hashes, body = m.groups()
    return _styled(_render_inline(body), _HEADING_STYLE)


def _render_rule_line(_line: str) -> str:
    # Replace any --- / *** / ___ run with a full-width brand rule. Keep it
    # short so narrow terminals don't wrap it.
    return _styled("─" * 40, _RULE_STYLE)


# ---------------------------------------------------------------------------
# Streaming state machine
# ---------------------------------------------------------------------------


class MarkdownStreamRenderer:
    """Line-buffered markdown renderer for one assistant turn.

    Feed sanitized model deltas through :meth:`feed`; each call returns the
    styled markup for any lines completed by that delta. Call :meth:`flush`
    at end-of-turn to emit the final partial line.

    When ``enabled`` is False every transform is bypassed and input is
    returned verbatim — this is the ``NO_COLOR`` / piped-output path.
    """

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._pending = ""
        self._in_fence = False
        # The open block's own fence run, so the closing fence can be matched
        # against it rather than against "any fence line".
        self._fence_marker = ""
        # Inside a ``<think>`` block (reasoning models). Body lines render
        # dim-italic with a near-gray bar until the closing tag.
        self._in_think = False
        # Think-opener guard. A bare ``<think>`` line is only treated as a
        # reasoning block opener (a) before the first content line of the
        # turn, or (b) right after a block-level boundary (tool row, status
        # row, fence close, table block, previous think close). Genuine
        # reasoning streams only ever open think blocks in those positions;
        # a ``<think>`` line appearing after prose is the model *quoting*
        # the tag (e.g. answering "what tag is used for X?") and must
        # render literally instead of swallowing the reply.
        self._seen_content = False
        self._block_boundary = True
        # Blockquote continuation state: a `>` line opens a quote block;
        # consecutive non-empty lines keep it so multiline quotes style
        # uniformly. Cleared by a blank line or a non-quote block line.
        self._in_quote = False
        # Buffered table lines while a table block is open. Tables render
        # as a unit when the block closes (see _render_table_block).
        self._table_lines: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """True when the markdown transform is active.

        Callers must bypass *all* post-processing (including the
        markup→ANSI render) when this is False so raw text reaches the
        terminal byte-for-byte.
        """
        return self._enabled

    def feed(self, delta: str) -> str:
        """Consume a delta; return styled markup for completed lines."""
        if not self._enabled:
            return delta
        self._pending += delta
        out: list[str] = []
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            rendered = self._render_line(line)
            if rendered is None:
                # Table line buffered for the block render — emit nothing.
                continue
            # Empty string is a real display line (blank separator between
            # paragraphs, or a hidden fence marker): keep the newline so
            # paragraph spacing is preserved.
            out.append(rendered + "\n")
        return "".join(out)

    def mark_boundary(self) -> None:
        """Mark a block-level boundary in the prose stream.

        Called by the streaming renderer whenever a non-markdown payload
        (tool row, status row) is emitted inline. Genuine think blocks only
        ever open right after such boundaries (or at turn start), so this
        is what lets the opener guard accept them while rejecting
        ``<think>`` lines quoted inside prose.
        """
        self._block_boundary = True

    def flush(self) -> str:
        """Emit any trailing partial line at end-of-turn."""
        if not self._enabled:
            pending, self._pending = self._pending, ""
            return pending
        out: list[str] = []
        if self._pending:
            line, self._pending = self._pending, ""
            rendered = self._render_line(line)
            if rendered is not None and rendered:
                out.append(rendered)
        # A table block that never saw its closing boundary still renders.
        table_out = self._flush_table()
        if table_out:
            if out and not out[-1].endswith("\n"):
                out[-1] += "\n"
            out.append(table_out)
        return "".join(out)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _flush_table(self) -> str:
        """Render and clear any buffered table block."""
        if not self._table_lines:
            return ""
        lines, self._table_lines = self._table_lines, []
        # A table block is a block-level boundary: a think block opening
        # right after it is a genuine reasoning block, not a quoted tag.
        self._block_boundary = True
        return _render_table_block(lines)

    def _can_open_think(self) -> bool:
        """Think-opener guard: accept a bare ``<think>`` line only at turn
        start or right after a block-level boundary (see ``__init__``)."""
        return self._block_boundary or not self._seen_content

    def _render_line(self, line: str) -> str | None:
        """Render one logical line, maintaining the think-opener guard flags.

        Returns ``None`` when the line was buffered for a table block
        (emit nothing), otherwise the display text for the line — which
        may be an empty string for blank separator lines and hidden fence
        markers (the caller still emits the newline so paragraph spacing
        is preserved).
        """
        was_in_think = self._in_think
        rendered = self._render_line_inner(line)
        stripped = line.strip()
        if not stripped or rendered is None:
            # Blank lines and buffered table rows leave the guard flags
            # untouched: a blank line after prose must NOT re-arm the
            # think-opener guard (that's the quoted-tag case).
            return rendered
        if was_in_think or self._in_think:
            # Think body, an accepted opener, or a genuine close — none of
            # these are prose content. A close re-arms the boundary so a
            # following think block opens.
            if was_in_think and not self._in_think:
                self._block_boundary = True
            return rendered
        if _FENCE_RE.match(line) or _STRAY_THINK_ARTIFACT_RE.match(line):
            self._block_boundary = True
            return rendered
        # A genuine content line: the turn has prose and the boundary is
        # gone, so a following bare ``<think>`` is quoted text.
        self._seen_content = True
        self._block_boundary = False
        return rendered

    def _render_line_inner(self, line: str) -> str | None:
        # Fence open/close; the fence line itself is hidden so the block
        # reads as one continuous region. What closes the block is the
        # opener's own marker, not any fence line -- see _closes_fence.
        fence = _FENCE_RE.match(line)
        if self._in_fence:
            if fence is not None and _closes_fence(self._fence_marker, line):
                self._in_fence = False
                self._fence_marker = ""
                return ""
            # A fence of the other character, or a shorter run: body text.
            return _render_code_line(line)
        if fence is not None:
            self._in_fence = True
            self._fence_marker = fence.group("fence")
            table_out = self._flush_table()
            return (table_out + "\n") if table_out else ""
        # Stray think-tag artifacts (a bare ``<>`` / ``</>`` line) are
        # hidden in every state; inside a think block they also close it.
        # Checked after fences so code stays literal, and anchored to the
        # whole line so prose/table content like ``a <> b`` is untouched.
        if _STRAY_THINK_ARTIFACT_RE.match(line):
            if self._in_think:
                self._in_think = False
            return ""
        # Think blocks: tags own their line and are hidden; body lines
        # render dim-italic with a near-gray bar. Checked after fences so
        # a ``<think>`` inside a code fence stays literal code. The opener
        # guard rejects tags quoted inside prose (they render literally).
        if self._can_open_think():
            inline_think = _THINK_INLINE_RE.match(line)
            if inline_think:
                return _render_think_line(inline_think.group(1))
            if _THINK_OPEN_RE.match(line):
                self._in_think = True
                table_out = self._flush_table()
                return (table_out + "\n") if table_out else ""
        if self._in_think:
            if _THINK_CLOSE_RE.match(line):
                self._in_think = False
                return ""
            return _render_think_line(line)
        # Table lines buffer until the block closes.
        if _is_table_line(line):
            self._table_lines.append(line)
            return None
        table_out = self._flush_table()
        prefix = (table_out + "\n") if table_out else ""
        stripped = line.strip()
        if not stripped:
            # Blank line ends any open quote block.
            self._in_quote = False
            return prefix
        if _RULE_RE.match(stripped):
            self._in_quote = False
            return prefix + _render_rule_line(line)
        if _HEADING_RE.match(line):
            self._in_quote = False
            return prefix + _render_heading_line(line)
        if _QUOTE_RE.match(line):
            self._in_quote = True
            return prefix + _render_quote_line(line)
        # Lazily-wrapped quote continuation: inside a quote block, treat a
        # plain-text line as a continuation; a list/table/heading line
        # breaks out of the quote.
        if self._in_quote and not _LIST_RE.match(line) and not line.lstrip().startswith("|"):
            return prefix + _render_quote_line(f"> {line}")
        self._in_quote = False
        if _LIST_RE.match(line):
            return prefix + _render_list_line(line)
        return prefix + _render_inline(line, tag_follows=False)


# ---------------------------------------------------------------------------
# Module-level helpers used by stream.py
# ---------------------------------------------------------------------------


def markdown_enabled() -> bool:
    """True when terminal markdown styling should be applied.

    Disabled by ``NO_COLOR`` or when the console has no color system
    (piped output, dumb terminal), so the raw stream stays greppable.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return console.color_system is not None


def render_markup_to_ansi(markup: str) -> str:
    """Render a Rich markup string to ANSI, with no Rich-side wrapping.

    Uses ``soft_wrap=True`` so the terminal (or prompt_toolkit pane) owns
    line wrapping — the same contract the raw stream relied on before this
    renderer existed. ``highlight=False`` and ``emoji=False`` keep the
    model's text byte-faithful (no auto-linkification, no ``:emoji:``
    expansion) so the only styling is what this module explicitly added.
    """
    if not markup:
        return ""
    with console.capture() as capture:
        console.print(markup, end="", soft_wrap=True, highlight=False, emoji=False)
    return capture.get()
