"""Bounded, legible reads of provider HTTP error bodies.

A failing provider request does not answer with the tidy JSON its API
documents. It answers with whatever sat in front of it: a WAF's HTML block
page, a load balancer's plain-text 502, a proxy's stack trace. Those bodies
have no size contract, and AgentOS was reading them whole — ``response.aread()``
buffers the entire payload, and Anthropic's path then decoded all of it into
``ErrorEvent.message``, which flows straight into the agent's context.

A megabyte of markup in the transcript is worse than useless: it costs tokens,
it displaces real history, and it tells the model nothing it can act on. What a
model needs from a 403 is "403, blocked by a gateway", not the gateway's markup.

So this module bounds the read at the socket and then summarises what it got:
JSON keeps its ``error.message``, HTML collapses to its title and size, and
anything else is truncated with the cut made visible.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Enough to hold any real API error payload — the largest observed provider
# error JSON is a few hundred bytes — while refusing to buffer a web page.
DEFAULT_ERROR_BODY_LIMIT = 8192

# What survives into a message once the body turns out to be prose or markup.
DEFAULT_SUMMARY_CHARS = 600

_HTML_MARKERS = ("<!doctype html", "<html", "<head", "<body", "<title")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


async def read_bounded_body(response: Any, *, limit: int = DEFAULT_ERROR_BODY_LIMIT) -> bytes:
    """Read at most *limit* bytes from a streaming response.

    ``httpx.Response.aread()`` buffers the whole body; on an error response
    that size is set by whatever is in front of the provider. Reading through
    the byte iterator lets the read stop early, so an oversized body costs a
    bounded amount of memory rather than however much the sender chose.
    """

    iter_bytes = getattr(response, "aiter_bytes", None)
    if iter_bytes is None:
        # Not every response object streams — interceptors and test doubles
        # often expose only aread(). Buffering is worse, but losing the error
        # entirely is worse still, so fall back and clip.
        return await _read_whole(response, limit)

    chunks: list[bytes] = []
    total = 0
    try:
        async for chunk in iter_bytes():
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
    except Exception:  # noqa: BLE001 — a broken error body must not mask the error
        pass
    if not chunks:
        return await _read_whole(response, limit)
    return b"".join(chunks)[:limit]


async def _read_whole(response: Any, limit: int) -> bytes:
    reader = getattr(response, "aread", None)
    if reader is None:
        return b""
    try:
        body = await reader()
    except Exception:  # noqa: BLE001 — same reasoning as above
        return b""
    if isinstance(body, str):
        body = body.encode("utf-8", errors="replace")
    return bytes(body)[:limit]


def _decode(body: bytes | str) -> str:
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


def _looks_like_html(text: str) -> bool:
    head = text[:512].lstrip().lower()
    return any(marker in head for marker in _HTML_MARKERS)


def _summarize_html(text: str) -> str:
    """Describe an HTML error page instead of quoting it.

    A block page's title carries the whole signal — "403 Forbidden",
    "Attention Required! | Cloudflare" — and its markup carries none.
    """

    match = _TITLE_RE.search(text)
    title = ""
    if match:
        title = _TAG_RE.sub("", match.group(1))
        title = " ".join(title.split())[:200]
    if not title:
        stripped = _TAG_RE.sub(" ", text)
        title = " ".join(stripped.split())[:200]
    detail = f": {title}" if title else ""
    return f"HTML error page ({len(text)} bytes){detail}"


def summarize_error_body(
    body: bytes | str,
    *,
    max_chars: int = DEFAULT_SUMMARY_CHARS,
) -> str:
    """Reduce an error body to something worth putting in front of a model."""

    text = _decode(body).strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return _clip(message.strip(), max_chars)
        if isinstance(error, str) and error.strip():
            return _clip(error.strip(), max_chars)
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return _clip(message.strip(), max_chars)
        detail = _detail_text(payload.get("detail"))
        if detail:
            return _clip(detail, max_chars)

    if _looks_like_html(text):
        return _clip(_summarize_html(text), max_chars)

    return _clip(text, max_chars)


def _detail_text(detail: Any) -> str:
    """The readable part of a ``detail`` field, or ``""``.

    ``{"detail": ...}`` is what a FastAPI/Starlette app returns by default,
    which covers vLLM, Ollama's proxy and most self-hosted inference servers
    sitting in front of a model. It comes in two shapes: a plain string for an
    ``HTTPException``, and a list of validation errors for a 422, each with a
    ``msg`` and the field it came from. Without this the whole JSON document
    reached the model as raw text -- the one thing this module exists to
    prevent.
    """

    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, list):
        messages: list[str] = []
        for entry in detail:
            if isinstance(entry, str) and entry.strip():
                messages.append(entry.strip())
            elif isinstance(entry, dict):
                msg = entry.get("msg")
                if isinstance(msg, str) and msg.strip():
                    # ``loc`` names the field that failed; without it a list of
                    # "field required" lines says nothing actionable.
                    location = entry.get("loc")
                    if isinstance(location, list) and location:
                        where = ".".join(str(part) for part in location)
                        messages.append(f"{where}: {msg.strip()}")
                    else:
                        messages.append(msg.strip())
        return "; ".join(messages)
    return ""


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    # Make the cut visible: a silently truncated error reads like the provider
    # sent something incoherent.
    return f"{text[:max_chars]}… (truncated, {len(text)} chars)"
