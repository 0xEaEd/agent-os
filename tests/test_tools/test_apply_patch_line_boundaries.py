"""Issue #3570: the patch *text* was still split on every Unicode boundary.

#3176 established the rule for this toolchain -- a line ends at a newline,
because that is what ``git diff``, every editor and ``read_file`` count -- and
its fix gave ``_updated_text`` ``split_lines_keepends`` for the file being
patched. ``_parse_patch``, which reads the patch itself, kept
``str.splitlines()``.

So a ``+`` line whose *content* carried a form feed, a vertical tab, a lone
``\\r``, NEL or U+2028/9 was cut in two before the parser saw it. The tail did
not start with ``+``, and the whole block was rejected with "expected a '+'
prefix" quoting a line the caller had in fact prefixed. Nothing is written,
the offending character is invisible, and the message points nowhere -- so the
same patch gets retried.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from agentos.tools.builtin import patch as patch_tool
from agentos.tools.builtin.patch import PatchError, _parse_patch
from agentos.tools.types import ToolContext, current_tool_context

FORM_FEED = "\x0c"
VERTICAL_TAB = "\x0b"
CARRIAGE_RETURN = "\r"
NEL = "\u0085"
LINE_SEPARATOR = " "
PARAGRAPH_SEPARATOR = " "

BOUNDARY_PAYLOADS = [
    pytest.param("def render():" + FORM_FEED + "    return 1", id="form-feed"),
    pytest.param("col1" + VERTICAL_TAB + "col2", id="vertical-tab"),
    pytest.param("progress: 10%" + CARRIAGE_RETURN + "progress: 20%", id="lone-cr"),
    pytest.param("heading" + NEL + "body", id="nel"),
    pytest.param("title" + LINE_SEPARATOR + "subtitle", id="u2028"),
    pytest.param("para one" + PARAGRAPH_SEPARATOR + "para two", id="u2029"),
]


def _original_async(fn: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    return fn.__wrapped__.__wrapped__  # type: ignore[attr-defined, no-any-return]


def _add_file_patch(payload: str) -> str:
    return f"*** Begin Patch\n*** Add File: sample.py\n+{payload}\n*** End Patch\n"


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("payload", BOUNDARY_PAYLOADS)
def test_an_added_line_carrying_a_boundary_character_parses(payload: str) -> None:
    ops = _parse_patch(_add_file_patch(payload))

    assert len(ops) == 1
    assert ops[0].content == payload, "the character is content, not a line break"


@pytest.mark.parametrize("payload", BOUNDARY_PAYLOADS)
def test_the_patch_is_not_rejected_as_malformed(payload: str) -> None:
    """The symptom: an error naming a line the caller did prefix with '+'."""
    try:
        _parse_patch(_add_file_patch(payload))
    except PatchError as exc:  # pragma: no cover - the regression itself
        pytest.fail(f"a well-formed patch was rejected: {exc}")


def test_a_form_feed_survives_a_whole_add_file_body() -> None:
    """A Python source with a page break in it, written as one op."""
    body = ["def a():", "    return 1", FORM_FEED, "def b():", "    return 2"]
    patch = (
        "*** Begin Patch\n*** Add File: mod.py\n"
        + "".join(f"+{line}\n" for line in body)
        + "*** End Patch\n"
    )

    assert _parse_patch(patch)[0].content == "\n".join(body)


@pytest.mark.asyncio
async def test_apply_patch_writes_a_form_feed_through(tmp_path: Path) -> None:
    """End to end on the real tool, not just the parser."""
    payload = "def render():" + FORM_FEED + "    return 1"
    token = current_tool_context.set(ToolContext(workspace_dir=str(tmp_path)))
    apply_patch = _original_async(patch_tool.apply_patch)
    try:
        result = await apply_patch(_add_file_patch(payload))
    finally:
        current_tool_context.reset(token)

    assert result == "Applied patch: 1 file(s) added"
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == payload


# ── what must not change ───────────────────────────────────────────────────


def test_a_plain_patch_parses_exactly_as_before() -> None:
    ops = _parse_patch(_add_file_patch("def a(): return 1"))

    assert len(ops) == 1
    assert ops[0].path == "sample.py"
    assert ops[0].content == "def a(): return 1"


def test_a_crlf_patch_text_still_parses() -> None:
    r"""``split_lines`` drops the ``\r`` of a CRLF with its ``\n``, which is
    what ``splitlines()`` did -- a patch pasted from a Windows editor is
    unaffected."""
    patch = "*** Begin Patch\r\n*** Add File: x.py\r\n+hello\r\n*** End Patch\r\n"

    assert _parse_patch(patch)[0].content == "hello"


def test_a_patch_with_no_trailing_newline_still_parses() -> None:
    patch = "*** Begin Patch\n*** Add File: x.py\n+hello\n*** End Patch"

    assert _parse_patch(patch)[0].content == "hello"


def test_a_genuinely_malformed_block_is_still_rejected() -> None:
    patch = "*** Begin Patch\n*** Add File: x.py\nhello\n*** End Patch\n"

    with pytest.raises(PatchError, match="expected a '\\+' prefix"):
        _parse_patch(patch)


def test_an_update_file_hunk_still_parses() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: x.py\n"
        "@@\n"
        " context\n"
        "-old\n"
        "+new\n"
        "*** End Patch\n"
    )

    ops = _parse_patch(patch)

    assert ops[0].path == "x.py"
    assert ops[0].hunks


def test_a_bare_blank_content_line_is_still_a_blank_line() -> None:
    """#1691's behaviour, which shares the same split."""
    patch = "*** Begin Patch\n*** Add File: x.py\n+a\n\n+b\n*** End Patch\n"

    assert _parse_patch(patch)[0].content == "a\n\nb"
