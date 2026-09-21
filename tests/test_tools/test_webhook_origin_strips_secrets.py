"""Issue #3185: _webhook_origin handed the model more than the host.

The helper exists so a cron delivery view can say *where* a job reports
without handing the model anything it could re-post with -- a Slack/Discord/
Teams webhook URL is itself the credential. It took the host by splitting on
the first ``/``:

    scheme, rest = url.split("://", 1)
    return f"{scheme}://{rest.split('/', 1)[0]}"

That is only the path. Everything else the authority can carry survived:

* basic-auth userinfo -- ``https://key:token@host/x`` came back as
  ``https://key:token@host``
* the query and fragment of a URL with no path at all --
  ``https://host?token=secret`` came back whole

Both put a live secret into the view the model reads.
"""

from __future__ import annotations

import pytest

from agentos.tools.builtin.control import _webhook_origin


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # userinfo -- the leak the split could never remove
        ("https://api-key:secret-token@webhook.site/endpoint", "https://webhook.site"),
        ("https://user@host/path", "https://host"),
        ("https://user:pw@host:8443/path", "https://host:8443"),
        # no path, so the split had nothing to cut
        ("https://hooks.slack.com?token=xoxb-secret", "https://hooks.slack.com"),
        ("https://host#frag", "https://host"),
        ("https://user:pw@host?token=secret", "https://host"),
        # already correct, and must stay correct
        ("https://hooks.example.test/T000/B000/XYZSECRET", "https://hooks.example.test"),
        ("https://host:8443/path", "https://host:8443"),
        ("http://example.com", "http://example.com"),
    ],
)
def test_only_the_host_and_port_survive(url: str, expected: str) -> None:
    assert _webhook_origin(url) == expected


@pytest.mark.parametrize(
    "secret",
    ["secret-token", "xoxb-secret", "XYZSECRET", "pw"],
)
def test_no_secret_material_reaches_the_view(secret: str) -> None:
    """The property that matters, stated directly: whatever the URL carries,
    the summary must not contain it."""
    urls = [
        f"https://api-key:{secret}@webhook.site/endpoint",
        f"https://hooks.slack.com?token={secret}",
        f"https://hooks.example.test/T000/B000/{secret}",
        f"https://user:{secret}@host#{secret}",
    ]
    for url in urls:
        assert secret not in _webhook_origin(url), url


def test_an_ipv6_literal_keeps_its_brackets() -> None:
    """urlsplit reports the host without them; an origin without them is not
    a usable authority."""
    assert _webhook_origin("https://[::1]:8443/path") == "https://[::1]:8443"


def test_a_schemeless_authority_still_drops_its_userinfo() -> None:
    assert _webhook_origin("user:pw@host/path") == "host"


def test_a_schemeless_value_with_userinfo_and_query_keeps_only_the_host() -> None:
    assert _webhook_origin("garbage:token@host?q=1/x") == "host"


def test_an_empty_value_stays_empty() -> None:
    assert _webhook_origin("") == ""


def test_a_non_numeric_port_does_not_raise() -> None:
    """``urlsplit(...).port`` raises on a malformed port, and a view builder
    is the wrong place to raise from."""
    assert _webhook_origin("https://host:notaport/path") == "https://host"
