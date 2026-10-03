"""Issue #3545: html_to_text mangled the HTML part before the agent read it.

``html_to_text`` produces the text an inbound HTML-only mail becomes, which
is the prompt the agent answers. Three things went wrong in it:

* only ``br``/``p``/``div``/``tr``/``li`` ended a line, so every other block
  element was deleted with nothing in its place -- a heading fused with the
  paragraph under it, and a row's ``<td>`` cells fused with each other while
  ``</tr>`` broke the rows correctly;
* ``_HTML_TAG_RE`` stops at the first ``>``, which inside a comment ends
  nothing, so the tail of ``<!-- a > b -->`` survived as body text;
* only ``script`` and ``style`` were dropped, so ``<title>`` was treated as
  visible text and prepended to the body.

Each one puts something in the prompt that the sender did not write, or
welds two of their words into one token.
"""

from __future__ import annotations

import pytest

from agentos.channels.email import html_to_text

# A table-based marketing mail, which is how most HTML mail is built.
NEWSLETTER = """\
<html>
<head><title>Acme Weekly</title><style>.b { color: red }</style></head>
<body>
<!-- campaign 4471; segment: trial > active -->
<h1>Quarterly Report</h1>
<p>Revenue is up.</p>
<table>
  <tr><th>Metric</th><th>Value</th></tr>
  <tr><td>Revenue</td><td>$1.2M</td></tr>
  <tr><td>Churn</td><td>2.1%</td></tr>
</table>
<h2>Action required</h2>
<div>Please confirm by Friday.</div>
</body>
</html>"""


# ── the issue's reproduction ───────────────────────────────────────────────


def test_a_heading_does_not_fuse_with_the_paragraph_under_it() -> None:
    assert html_to_text("<h1>Quarterly Report</h1><p>Revenue is up.</p>") == (
        "Quarterly Report\nRevenue is up."
    )


def test_table_cells_are_separated_and_rows_stay_on_their_own_line() -> None:
    html = "<table><tr><td>Name</td><td>Alice</td></tr><tr><td>Age</td><td>30</td></tr></table>"

    assert html_to_text(html) == "Name\tAlice\nAge\t30"


def test_header_cells_are_separated_too() -> None:
    html = "<table><tr><th>Item</th><th>Qty</th></tr><tr><td>Bolt</td><td>12</td></tr></table>"

    assert html_to_text(html) == "Item\tQty\nBolt\t12"


def test_a_comment_containing_an_angle_bracket_does_not_leak() -> None:
    assert html_to_text("<!-- tracking id: a > b --><p>Body text</p>") == "Body text"


def test_the_document_title_is_not_body_text() -> None:
    html = "<head><title>Newsletter</title></head><body><p>Hi</p></body>"

    assert html_to_text(html) == "Hi"


def test_a_bare_title_outside_a_head_is_dropped_too() -> None:
    assert html_to_text("<title>Newsletter</title><p>Hi</p>") == "Hi"


def test_a_realistic_table_based_mail_reads_as_the_sender_wrote_it() -> None:
    text = html_to_text(NEWSLETTER)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    assert lines == [
        "Quarterly Report",
        "Revenue is up.",
        "Metric\tValue",
        "Revenue\t$1.2M",
        "Churn\t2.1%",
        "Action required",
        "Please confirm by Friday.",
    ]
    assert "Acme Weekly" not in text, "the <title> is not part of the message"
    assert "campaign" not in text, "the comment is not part of the message"
    assert "color: red" not in text
    # The indentation and blank lines come from the mail's own source
    # formatting and are what this function has always passed through; the
    # point here is that no two of the sender's words share a token.
    assert "ReportRevenue" not in text
    assert "MetricValue" not in text


# ── every block element ends a line ────────────────────────────────────────


@pytest.mark.parametrize(
    "tag",
    ["h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "section",
     "article", "header", "footer", "main", "aside", "figure", "figcaption",
     "form", "fieldset", "address", "dt", "dd"],
)  # fmt: skip
def test_a_block_element_is_followed_by_a_line_break(tag: str) -> None:
    assert html_to_text(f"<{tag}>first</{tag}><p>second</p>") == "first\nsecond"


@pytest.mark.parametrize("tag", ["ul", "ol", "dl", "table", "thead", "tbody", "tfoot"])
def test_a_container_closing_ends_a_line(tag: str) -> None:
    assert html_to_text(f"<{tag}>first</{tag}>second") == "first\nsecond"


def test_a_horizontal_rule_ends_a_line() -> None:
    assert html_to_text("above<hr>below") == "above\nbelow"
    assert html_to_text("above<hr />below") == "above\nbelow"


# ── what must not change ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("Hello<br>World", "Hello\nWorld"),
        ("Hello<br />World", "Hello\nWorld"),
        ("<p>Para one</p><p>Para two</p>", "Para one\nPara two"),
        ("<div>a</div><div>b</div>", "a\nb"),
        ("<ul><li>one</li><li>two</li></ul>", "one\ntwo"),
        ("plain text, no html", "plain text, no html"),
        ("", ""),
    ],
)
def test_the_cases_that_already_worked_are_unchanged(html: str, expected: str) -> None:
    assert html_to_text(html) == expected


def test_inline_elements_still_do_not_break_the_sentence() -> None:
    html = '<p>Visit <a href="https://x.test">our site</a> <b>today</b></p>'

    assert html_to_text(html) == "Visit our site today"


def test_script_and_style_are_still_dropped_whole() -> None:
    assert html_to_text("<script>var a = 1 > 0;</script><p>Hi</p>") == "Hi"
    assert html_to_text("<style>.a > .b { color: red }</style><p>Hi</p>") == "Hi"


def test_a_script_containing_a_comment_opener_does_not_eat_the_body() -> None:
    """``script`` is dropped before comments are, so a ``<!--`` in a script
    cannot start a comment that swallows the message after it."""
    html = "<script>// <!-- legacy hide\nvar a = 1;</script><p>Body</p>"

    assert html_to_text(html) == "Body"


def test_header_is_a_block_element_and_not_the_document_head() -> None:
    """``\\b`` keeps the ``head`` drop off ``<header>``; its text is visible."""
    assert html_to_text("<header>Brand</header><p>Body</p>") == "Brand\nBody"


def test_entities_are_unescaped_after_tags_are_stripped() -> None:
    """Unescaping first would let ``&lt;b&gt;`` become a tag and be deleted."""
    assert html_to_text("<p>5 &lt; 7 and &amp;amp;</p>") == "5 < 7 and &amp;"


def test_blank_runs_are_still_collapsed() -> None:
    assert html_to_text("<p>a</p><br><br><br><br><p>b</p>") == "a\n\nb"


def test_an_outlook_conditional_comment_leaves_nothing_behind() -> None:
    html = (
        '<!--[if mso]><table width="600"><tr><td><![endif]-->'
        "<p>Hello there</p>"
        "<!--[if mso]></td></tr></table><![endif]-->"
    )

    assert html_to_text(html) == "Hello there"
