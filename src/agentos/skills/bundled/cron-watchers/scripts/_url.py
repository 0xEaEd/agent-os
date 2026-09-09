"""Shared URL guard for cron watcher scripts.

A watcher's ``--url`` comes from a cron job definition, which an agent can
write. ``urlopen`` is scheme-agnostic, so without this check a watcher pointed
at ``file:///etc/passwd`` reads the file and reports its contents on every run.
"""

from __future__ import annotations

import urllib.parse


def require_http_url(url: str, label: str) -> str:
    """Return *url* if it is an ``http(s)`` URL with a host, else raise ValueError.

    ``urllib.request.urlopen`` also speaks ``file:``, ``ftp:`` and ``data:``, so
    an unchecked endpoint turns this script into an arbitrary local-file reader:
    ``file:///etc/passwd`` reads the file and hands the contents back to the
    agent. Parsed with ``urlsplit`` rather than a ``startswith`` prefix test, so
    neither ``HTTP://`` casing nor leading whitespace decides the answer.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError(f"empty {label}")
    try:
        parsed = urllib.parse.urlsplit(cleaned)
    except ValueError as exc:
        raise ValueError(f"invalid {label} {url!r}: {exc}") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(
            f"invalid {label} scheme {parsed.scheme!r} in {url!r}: must be http:// or https://"
        )
    if not parsed.netloc:
        raise ValueError(f"{label} {url!r} has no host: must be http:// or https://")
    return cleaned
