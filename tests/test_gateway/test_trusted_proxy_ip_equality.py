"""``trusted_proxy`` matches on the address, not on how it is spelled.

One IPv6 address has many valid textual forms. The operator writes
``auth.trusted_proxy`` by hand and the ASGI server reports the transport
peer; nothing makes the two agree on a form. Comparing the strings meant a
correctly configured IPv6 proxy read as untrusted, which silently drops
``X-Forwarded-For`` and collapses every client behind that proxy into one
rate-limit bucket. An IPv4-only deployment never sees it.
"""

from __future__ import annotations

import pytest

from agentos.gateway.access import peer_is_trusted_proxy


@pytest.mark.parametrize(
    ("configured", "peer"),
    [
        ("::1", "0:0:0:0:0:0:0:1"),
        ("0:0:0:0:0:0:0:1", "::1"),
        ("::1", "::0001"),
        ("2001:db8::1", "2001:0db8:0000:0000:0000:0000:0000:0001"),
        ("2001:0DB8::1", "2001:db8::1"),
        ("fe80::0:1", "fe80::1"),
    ],
)
def test_same_ipv6_address_in_any_form_is_trusted(configured: str, peer: str) -> None:
    assert peer_is_trusted_proxy(configured, peer) is True


@pytest.mark.parametrize(
    ("configured", "peer"),
    [
        ("10.0.0.1", "10.0.0.1"),
        ("::1", "[::1]"),
        ("::1", " ::1 "),
        ("10.0.0.1, ::1", "::1"),
        ("10.0.0.1,2001:db8::1", "2001:0db8::0001"),
    ],
)
def test_forms_that_already_worked_keep_working(configured: str, peer: str) -> None:
    assert peer_is_trusted_proxy(configured, peer) is True


@pytest.mark.parametrize(
    ("configured", "peer"),
    [
        ("::1", "::2"),
        ("10.0.0.1", "10.0.0.2"),
        ("2001:db8::1", "2001:db8::2"),
        # An IPv4-mapped form is a distinct address object; admitting it would
        # widen the set, so it stays out until someone asks for it.
        ("10.0.0.1", "::ffff:10.0.0.1"),
        ("", "::1"),
        (None, "::1"),
        ("::1", ""),
        ("::1", None),
    ],
)
def test_a_different_address_is_never_trusted(configured: str | None, peer: str | None) -> None:
    assert peer_is_trusted_proxy(configured, peer) is False


def test_a_non_ip_entry_keeps_exact_string_matching() -> None:
    """A hostname or junk entry behaves exactly as before: literal match only."""
    assert peer_is_trusted_proxy("proxy.internal", "proxy.internal") is True
    assert peer_is_trusted_proxy("proxy.internal", "proxy.internal.evil.test") is False
    assert peer_is_trusted_proxy("not-an-ip", "::1") is False


def test_a_zone_scoped_peer_falls_back_to_string_comparison() -> None:
    """``fe80::1%eth0`` is not parseable; it must not raise, and must not widen."""
    assert peer_is_trusted_proxy("fe80::1%eth0", "fe80::1%eth0") is True
    assert peer_is_trusted_proxy("fe80::1", "fe80::1%eth0") is False
