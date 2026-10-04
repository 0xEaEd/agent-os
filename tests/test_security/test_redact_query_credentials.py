"""Issue #3607: a credential in a URL query string was never redacted.

``redact_sensitive_text`` masks a credential in a URL's *userinfo*
(``https://user:pw@host``, hardened in #3432) and left the query string of
the same URL alone. The name-driven pass recognises ``api_key``,
``access_token`` and ``password`` perfectly well -- it masks all three as a
bare ``name=value`` -- so the gap is not vocabulary.

``_ASSIGNMENT_RE`` cannot reach one. A URL's own ``scheme:`` matches that
pattern first::

    >>> [(m.group(1), m.group(4)) for m in _ASSIGNMENT_RE.finditer(
    ...     "https://x/v1?api_key=SECRETVALUE1234")]
    [('https', '//x/v1?api_key=SECRETVALUE1234')]

``https`` is not a credential name, so the span is returned unchanged -- and
consumed, so the ``api_key=`` inside it is never examined. A pass of its own
is the only way to see it.
"""

from __future__ import annotations

import pytest

from agentos.redact import redact_file_output, redact_sensitive_text, redact_terminal_output

SECRET = "Sup3rS3cret-3607xyz"


def _masked(text: str) -> str:
    return redact_sensitive_text(text) or text


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        f"GET https://api.example.com/v1/x?api_key={SECRET} HTTP/1.1",
        f"callback https://app.example.com/cb?access_token={SECRET}&state=1",
        f"jdbc:postgresql://db:5432/app?user=admin&password={SECRET}",
        f"https://api.example.com/v1?apiKey={SECRET}",
        f"https://api.example.com/v1?x=1&api-key={SECRET}&y=2",
        f"fetching https://h/p?client_secret={SECRET}",
    ],
)
def test_a_query_credential_is_masked(text: str) -> None:
    assert SECRET not in _masked(text)


def test_the_two_halves_of_one_url_are_treated_alike() -> None:
    """The asymmetry this is about: the same secret, two places in one URL."""
    userinfo = f"https://admin:{SECRET}@api.example.com/v1"
    query = f"https://api.example.com/v1?api_key={SECRET}"

    assert SECRET not in _masked(userinfo)
    assert SECRET not in _masked(query)


def test_only_the_value_is_replaced() -> None:
    masked = _masked(f"https://h/p?api_key={SECRET}&page=2")

    assert masked.startswith("https://h/p?api_key=")
    assert masked.endswith("&page=2"), "the next parameter is untouched"


def test_a_following_parameter_is_not_swallowed() -> None:
    """The value stops at ``&``, so one parameter cannot eat the next."""
    masked = _masked(f"https://h/p?token={SECRET}&redirect_uri=https://app/cb")

    assert "redirect_uri=https://app/cb" in masked


def test_it_reaches_exactly_the_surfaces_the_assignment_pass_does() -> None:
    """The query pass is under the same gate as `_redact_assignments`, so it
    inherits that policy rather than widening it: on by default, on for an
    env dump, off for an arbitrary command's output, off for source code."""
    line = f"curl -sS 'https://api.example.com/v1?api_key={SECRET}'"

    assert SECRET not in (redact_sensitive_text(line) or line), "default: on"
    assert SECRET not in redact_terminal_output(line, "env"), "env dump: on"
    assert SECRET not in (redact_file_output(line, path="/tmp/run.log") or line), "log: on"
    assert SECRET in redact_terminal_output(line, "curl"), "ordinary command output: off"
    assert SECRET in (redact_file_output(line, path="/tmp/run.py") or line), "source: off"


# ── what must not change ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "https://api.example.com/v1?page=2&limit=50",
        "https://api.example.com/search?q=api_key&sort=desc",
        "https://api.example.com/v1?user=admin&format=json",
        "https://example.com/docs?section=authentication",
    ],
)
def test_an_ordinary_query_string_is_untouched(text: str) -> None:
    assert _masked(text) == text


def test_a_reference_rather_than_a_literal_is_left_alone() -> None:
    """``?api_key=$API_KEY`` names where the value lives; masking it loses the
    only useful thing the line said."""
    for text in [
        "https://h/p?api_key=$API_KEY",
        "https://h/p?api_key=${API_KEY}",
        "https://h/p?api_key=<your-key>",
    ]:
        assert _masked(text) == text


def test_a_short_value_is_not_a_credential() -> None:
    assert _masked("https://h/p?password=1") == "https://h/p?password=1"


def test_an_already_masked_value_is_not_remasked() -> None:
    once = _masked(f"https://h/p?api_key={SECRET}")

    assert _masked(once) == once


def test_a_non_credential_name_keeps_its_value() -> None:
    assert _masked("https://h/p?callback=https://app/cb") == "https://h/p?callback=https://app/cb"


def test_a_bare_assignment_still_masks_as_before() -> None:
    assert SECRET not in _masked(f"api_key={SECRET}")
    assert SECRET not in _masked(f'{{"password": "{SECRET}"}}')
    assert SECRET not in _masked(f"password: {SECRET}")


def test_source_code_still_opts_out_of_the_name_driven_pass() -> None:
    """``code_file=True`` turns the assignment pass off; the query pass is
    under the same gate, so a URL in source is not rewritten either."""
    text = f"URL = 'https://h/p?api_key={SECRET}'"

    assert redact_sensitive_text(text, code_file=True) == text


def test_the_url_userinfo_pattern_is_unaffected() -> None:
    assert _masked("postgres://u:pw-that-is-long@db/app") == "postgres://u:***@db/app"
