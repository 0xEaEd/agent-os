"""Issue #3432: URL-userinfo passwords were masked only for listed schemes.

``_URL_USERINFO_RE`` matched ``https?://`` and ``_DB_CONNSTR_RE`` five
database schemes. Every other scheme handed its password to the model verbatim
-- ``ws``/``wss`` (a gateway URL with basic auth), ``ftp``, ``sftp``, ``ssh``,
``smtp``, ``ldap``, and any database scheme outside that list -- and the
payload guard, which shared the database list, let the same URLs leave.

A password in URL userinfo is a credential whatever the scheme, so one
structural pattern now replaces both lists and backs both layers. An allowlist
only ever covers the schemes someone thought of.

Initial tests by 0xEaEd (#3437).
"""

from __future__ import annotations

import time

import pytest

from agentos.redact import redact_sensitive_text, secret_literal_marker

_SECRET = "s3cr3t-pw"

_UNLISTED_SCHEMES = [
    "wss://user:{pw}@gateway.host/ws",
    "ws://user:{pw}@gateway.host/ws",
    "ftp://user:{pw}@files.host/x",
    "sftp://user:{pw}@host/x",
    "ssh://user:{pw}@host",
    "smtp://user:{pw}@mail.host:587",
    "ldap://user:{pw}@directory.host",
    "clickhouse://user:{pw}@db:9000",
    "mariadb://user:{pw}@db",
    "cassandra://user:{pw}@db",
    "wss://:{pw}@gateway.host/ws",
    "ssh://user:{pw}@[2001:db8::1]:22",
]


# ── the report ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", _UNLISTED_SCHEMES)
def test_a_password_is_masked_whatever_the_scheme(url: str) -> None:
    """The issue's repro, across the schemes the allowlists never named."""
    redacted = redact_sensitive_text(url.format(pw=_SECRET), force=True)

    assert _SECRET not in redacted
    assert "***" in redacted


@pytest.mark.parametrize("url", _UNLISTED_SCHEMES)
def test_the_payload_guard_sees_the_same_urls(url: str) -> None:
    """The guard shared the database list, so it missed exactly what redaction
    missed. It now runs on the same pattern."""
    assert secret_literal_marker(f"send {url.format(pw=_SECRET)}") == "connection_string"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:{pw}@host/x",
        "http://user:{pw}@host",
        "postgres://u:{pw}@db/app",
        "rediss://:{pw}@cache:6379",
        "mongodb+srv://user:{pw}@cluster.net/db",
    ],
)
def test_the_schemes_that_already_worked_still_do(url: str) -> None:
    redacted = redact_sensitive_text(url.format(pw=_SECRET), force=True)

    assert _SECRET not in redacted
    assert "***" in redacted
    assert secret_literal_marker(url.format(pw=_SECRET)) == "connection_string"


def test_the_host_and_scheme_survive_so_the_line_stays_readable() -> None:
    """Masking the secret must not cost the operator the diagnostic."""
    redacted = redact_sensitive_text(f"wss://user:{_SECRET}@gateway.host:443/ws", force=True)

    assert redacted == "wss://user:***@gateway.host:443/ws"


def test_a_reference_in_the_password_slot_is_left_alone() -> None:
    """Same rule as every other scheme already had: ``$VAR`` points at a secret."""
    text = "wss://user:$GATEWAY_PASSWORD@gateway.host/ws"

    assert redact_sensitive_text(text, force=True) == text


# ── nothing benign starts being masked or blocked ──────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com:8080/path",  # a port, not a credential
        "http://host/a@b",  # an @ in the path
        "http://localhost:5173/@vite/client",  # a port, then an @ path
        "see http://plain.host/x for docs",
        "see https://example.com/docs, bob@example.com",
        "bob@example.com,https://example.com/x",
        "mail ops@example.com or open https://status.example.com:443/ now",
        "the scheme is written http:// and mail goes to admin@example.com",
        "clone git@github.com:owner/repo.git, not https://github.com/owner/repo",
        "http://[::1]:8080/path",
        "ssh://user@[2001:db8::1]:22",
        "ssh deploy@build-01",
        "mailto:user@host",  # no ://
        "git+ssh://git@github.com/owner/repo.git",  # user, no password
        "ftp://anonymous@files.example.com:21/pub",
        "key: value",
        "ratio 3:1 @ noon",
    ],
)
def test_ordinary_text_is_not_touched(text: str) -> None:
    """A structural match is wider than a scheme list, so the boundary matters
    more: a port, an ``@`` in a path, an email beside a URL, an IPv6 host, or a
    userinfo with no password must all pass through both layers unchanged."""
    assert redact_sensitive_text(text, force=True) == text
    assert secret_literal_marker(text) is None


# ── the pattern stays linear ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("x://y " + "a" * 80_000, id="scheme-then-long-run"),
        pytest.param("a" * 80_000 + " http://h", id="long-run-then-scheme"),
        pytest.param("s://" + "u" * 80_000, id="unterminated-userinfo"),
        pytest.param("a1" * (1 << 19) + " https://user:pw@host", id="1mib-run-then-url"),
    ],
)
def test_a_long_run_of_scheme_characters_does_not_go_quadratic(text: str) -> None:
    """Every character in ``[a-z0-9+.-]`` can start a scheme. Unanchored, each
    position in a long run began its own scan for ``://``: 80 kB took over 20 s.
    Anchored, 1 MiB takes about a tenth of a second; the bound is loose so a
    slow runner does not flake, and still far below the quadratic cost."""
    start = time.perf_counter()
    redact_sensitive_text(text, force=True)
    secret_literal_marker(text)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"took {elapsed:.2f}s on {len(text)} chars"
