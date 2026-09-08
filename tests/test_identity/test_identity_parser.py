"""IDENTITY.md parsing — inline markdown must not eat parts of a value.

`_strip_markdown_inline` removes bold/italic/code markers before a field value
is stored. Without a flanking condition the underscore pattern also matched
inside ordinary words, so `snake_case_bot` was stored as `snakecasebot`
(issue #1428).
"""

from __future__ import annotations

import pytest

from agentos.identity.parser import parse_identity


def _identity(**fields: str) -> str:
    lines = "\n".join(f"- {key}: {value}" for key, value in fields.items())
    return f"# Identity\n{lines}\n"


@pytest.mark.parametrize(
    "value",
    [
        "my_agent_name",
        "snake_case_bot",
        "a_b_c_d",
        "x_y",
        "foo_bar_",
        "_leading",
        "trailing_",
    ],
)
def test_underscores_inside_a_value_are_preserved(value: str) -> None:
    """CommonMark forbids intra-word emphasis with ``_``, so these are literal.

    Two underscores anywhere on the line used to pair up as an emphasis run and
    both were deleted, which `snake_case` naming produces routinely.
    """
    assert parse_identity(_identity(name=value)).name == value


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("_cyberpunk_", "cyberpunk"),
        ("__bold__", "bold"),
        ("**strong**", "strong"),
        ("*em*", "em"),
        ("`code`", "code"),
        ("a _b_ c", "a b c"),
    ],
)
def test_real_inline_markdown_is_still_stripped(written: str, expected: str) -> None:
    """The helper still has a job: emphasis at a word boundary is markup."""
    assert parse_identity(_identity(name=written)).name == expected


def test_asterisk_emphasis_inside_a_word_still_collapses() -> None:
    """``*`` carries no intra-word restriction, so this is left as it was.

    CommonMark treats ``a*b*c`` as emphasis, unlike the underscore spelling.
    Pinning it here records that the asterisk branch was deliberately not
    changed alongside the underscore fix.
    """
    assert parse_identity(_identity(name="fast*and*loud")).name == "fastandloud"


def test_several_underscored_fields_survive_together() -> None:
    """The whole document parses, not just the field under test."""
    parsed = parse_identity(
        _identity(name="my_agent_name", creature="snake_case_bot", theme="_cyberpunk_")
    )

    assert parsed.name == "my_agent_name"
    assert parsed.creature == "snake_case_bot"
    assert parsed.theme == "cyberpunk"
