"""Shared translation of a source runtime's MCP server entry to AgentOS's."""

from __future__ import annotations

from typing import Any, Literal

# The spellings source configs use for the two HTTP transports, keyed as
# ``str(value).strip().lower().replace("-", "_")``.
_REMOTE_TRANSPORTS: dict[str, Literal["sse", "streamable_http"]] = {
    "sse": "sse",
    "streamable_http": "streamable_http",
    "streamablehttp": "streamable_http",
    "http": "streamable_http",
}


def remote_transport(raw: dict[str, Any]) -> Literal["sse", "streamable_http"]:
    """Transport for a server that has a ``url``.

    A server that names its transport keeps it. One that names none stays on
    ``sse``, which is what the migrators always wrote before.
    """
    named = raw.get("transport")
    if isinstance(named, str):
        return _REMOTE_TRANSPORTS.get(named.strip().lower().replace("-", "_"), "sse")
    return "sse"


def headers(raw: dict[str, Any]) -> dict[str, str]:
    """The request headers of a remote server, e.g. its ``Authorization``."""
    value = raw.get("headers")
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if v is not None}
