"""Issue #3568: a DSN password reached the decision log verbatim.

``build_intent_summary`` exists so a decision log can carry task shape
without carrying the prompt, and its docstring promises it removes "common
secrets, emails, URLs, and machine-local absolute paths". It ran four
patterns of its own and none of them reached a password in URL userinfo:
``_URL_RE`` only matches ``http(s)``, ``_SECRET_ASSIGN_RE`` needs a
``name=value``, and ``_LONG_SECRET_RE`` needs a long bare run that a real
password is not.

What hid it is that some DSNs *looked* covered -- ``_EMAIL_RE`` matched
``user:password@db.internal`` as an email address and replaced it with
``[email]``. That only holds while the host is spelled like a mail domain;
the same DSN against an IP, a bare service name or a port, which is what a
container or compose setup produces, went through untouched.

``redact.py`` settled the general shape in #3432: a structural match on
``<scheme>://[user]:password@host`` for any scheme, username optional. This
function now uses that one definition instead of a fifth private regex. The
decision log is written to disk and mined later by history aggregation, so
the leak was a credential at rest in a file nobody thinks of as sensitive.
"""

from __future__ import annotations

import pytest

from agentos.observability.decision_log import build_intent_summary
from agentos.redact import mask_url_userinfo, redact_sensitive_text

PASSWORD = "Sup3rS3cret-3568"

DSNS = [
    f"postgres://appuser:{PASSWORD}@10.0.0.7:5432/app",
    f"postgresql://appuser:{PASSWORD}@db/app",
    f"mysql://root:{PASSWORD}@mysql:3306/app",
    f"redis://:{PASSWORD}@10.0.0.7:6379/0",
    f"rediss://:{PASSWORD}@cache:6380",
    f"amqps://svc:{PASSWORD}@rabbit:5671",
    f"mongodb+srv://u:{PASSWORD}@cluster0.abcd/db",
    f"wss://bot:{PASSWORD}@gw/ws",
    f"ftp://deploy:{PASSWORD}@files/x",
    f"https://ci:{PASSWORD}@registry.internal/v2",
]


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("dsn", DSNS)
def test_a_dsn_password_never_reaches_the_log(dsn: str) -> None:
    summary = build_intent_summary(f"connect to {dsn}")

    assert PASSWORD not in summary


@pytest.mark.parametrize("dsn", DSNS)
def test_the_summary_agrees_with_the_redactor_about_what_is_a_credential(dsn: str) -> None:
    """One definition, two markers: whatever ``redact_sensitive_text`` masks
    here, the summary masks too."""
    text = f"connect to {dsn}"
    redacted = redact_sensitive_text(text) or text

    assert (PASSWORD in redacted) == (PASSWORD in build_intent_summary(text))


def test_the_host_shape_no_longer_decides() -> None:
    """The accidental coverage that hid this: an email-shaped host was eaten
    by ``_EMAIL_RE``, an IP or a bare service name was not."""
    for host in ["db.internal", "10.0.0.7:5432", "db", "postgres:5432", "[::1]:5432"]:
        summary = build_intent_summary(f"connect to postgres://u:{PASSWORD}@{host}/app")
        assert PASSWORD not in summary, host


def test_the_marker_is_the_logs_own_vocabulary() -> None:
    """``[secret]``, not ``***`` and not ``[email]`` -- this function labels
    what it removed."""
    summary = build_intent_summary(f"connect to postgres://appuser:{PASSWORD}@10.0.0.7:5432/app")

    assert summary == "connect to postgres://appuser:[secret]@10.0.0.7:5432/app"


# ── what must not change ───────────────────────────────────────────────────


def test_an_ordinary_sentence_is_untouched() -> None:
    assert build_intent_summary("run the test suite and report failures") == (
        "run the test suite and report failures"
    )


def test_a_plain_url_is_still_a_url() -> None:
    assert build_intent_summary("fetch https://example.com/docs") == "fetch [url]"


def test_an_email_is_still_an_email() -> None:
    assert build_intent_summary("email bob@example.com about it") == "email [email] about it"


def test_an_assignment_is_still_an_assignment() -> None:
    value = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    summary = build_intent_summary(f"set AWS_SECRET_ACCESS_KEY={value}")

    assert value not in summary
    assert "[secret]" in summary


def test_a_path_still_keeps_its_basename() -> None:
    """#3357's behaviour: the log says which file, not where."""
    assert build_intent_summary("read /home/u/project/notes.md") == "read [path:notes.md]"


def test_a_reference_rather_than_a_literal_is_left_alone() -> None:
    """``${PGPASSWORD}`` is not a secret, and masking it loses the only
    useful thing the line said."""
    text = "connect to postgres://u:${PGPASSWORD}@db/app"

    assert mask_url_userinfo(text, mask="[secret]") == text


def test_the_summary_is_still_bounded() -> None:
    assert len(build_intent_summary("word " * 400)) <= 500


def test_text_with_no_scheme_is_returned_unchanged_by_the_helper() -> None:
    """The ``"://" not in text`` short circuit, which keeps the common case
    off the regex entirely."""
    assert mask_url_userinfo("no urls here at all") == "no urls here at all"


def test_the_helper_defaults_to_the_redactors_own_mask() -> None:
    masked = mask_url_userinfo(f"postgres://u:{PASSWORD}@db/app")

    assert masked == "postgres://u:***@db/app"
