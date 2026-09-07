"""Policy for the browser ``eval`` action — JS evaluation in a page context.

Ported from NousResearch/hermes-agent ``tools/browser_tool`` (MIT, Copyright
(c) 2025 Nous Research) — see ``THIRD_PARTY_NOTICES.md``. The functions here are
the same two-tier eval guard Hermes ships:

* an **opt-in** denylist (``browser.restrict_evaluate``, default off) that refuses
  expressions touching sensitive browser primitives — matched both as bare
  identifiers and as string-literal property names so ``document["coo"+"kie"]``
  cannot slip past a check on ``document.cookie``;
* an SSRF pre-scan of the URL targets in the expression — plain,
  protocol-relative, and split across string literals — so a
  ``fetch('http://169.254.169.254/…')`` that never updates ``location.href`` is
  refused before it runs.

The denylist is *off by default* on purpose: gating on primitive *names*
cripples legitimate DOM extraction (Hermes reached the same conclusion). It is a
belt for operators who want it, not the load-bearing control — output redaction
(:func:`redact_browser_output`) and the network SSRF guards are.

Everything here is a pure function over the expression string plus process-wide
SSRF configuration; no browser is required to exercise it.
"""

from __future__ import annotations

import re
from typing import Any

from agentos.redact import redact_sensitive_text
from agentos.tools.ssrf import assert_not_metadata_endpoint, validate_http_url_for_fetch
from agentos.tools.types import SSRFBlockedError

# ---------------------------------------------------------------------------
# Denylist (opt-in): risky primitives, as regexes and as bare token names.
# ---------------------------------------------------------------------------

#: Direct-spelling patterns. Each carries a human-readable reason so a refusal
#: can name *what* it blocked without echoing the expression back.
_RISKY_EVAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdocument\s*\.\s*cookie\b", re.I), "document.cookie"),
    (re.compile(r"\b(?:localStorage|sessionStorage)\b", re.I), "web storage"),
    (re.compile(r"\bindexedDB\b", re.I), "IndexedDB"),
    (re.compile(r"\bcaches\s*\.\s*(?:open|match|keys)\b", re.I), "Cache Storage"),
    (
        re.compile(r"\bnavigator\s*\.\s*(?:clipboard|credentials|serviceWorker)\b", re.I),
        "navigator sensitive API",
    ),
    (
        re.compile(r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(", re.I),
        "network request",
    ),
    (re.compile(r"\bnavigator\s*\.\s*sendBeacon\s*\(", re.I), "network beacon"),
    (re.compile(r"\bdocument\s*\.\s*forms\b.*\bvalue\b", re.I | re.S), "form value extraction"),
    (
        re.compile(
            r"\bquerySelector(?:All)?\s*\([^)]*(?:input|textarea|password)[^)]*\).*\bvalue\b",
            re.I | re.S,
        ),
        "form value extraction",
    ),
)

#: Token names re-checked against decoded string literals, to catch bracket /
#: concatenation obfuscation (``document["coo" + "kie"]``).
_SENSITIVE_EVAL_TOKENS: tuple[tuple[str, str], ...] = (
    ("cookie", "document.cookie"),
    ("localStorage", "web storage"),
    ("sessionStorage", "web storage"),
    ("indexedDB", "IndexedDB"),
    ("caches", "Cache Storage"),
    ("clipboard", "navigator sensitive API"),
    ("credentials", "navigator sensitive API"),
    ("serviceWorker", "navigator sensitive API"),
    ("fetch", "network request"),
    ("XMLHttpRequest", "network request"),
    ("WebSocket", "network request"),
    ("EventSource", "network request"),
    ("sendBeacon", "network beacon"),
)

#: JS string literals — single, double, or backtick quoted, with escapes.
_JS_STRING_LITERAL_RE = re.compile(
    r"""'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`""",
    re.S,
)

#: ``http(s)://…`` literals embedded in the expression (fetch/XHR/navigation
#: targets the model may have written). The post-eval page-URL recheck can't see
#: a direct fetch that never touches ``location.href``, so pre-screen here.
_JS_URL_LITERAL_RE = re.compile(r"""https?://[^\s'"`)\]<>]+""", re.IGNORECASE)

#: Protocol-relative targets — ``fetch('//169.254.169.254/…')`` inherits the
#: page's scheme and reaches the same address, so it has to be screened too.
#: Matched only against a whole string literal: a ``//`` in the middle of some
#: text is a path separator or a line comment far more often than a target, and
#: the shape that reaches ``fetch`` is a literal that *is* the URL.
_JS_PROTOCOL_RELATIVE_RE = re.compile(r"""//[^\s'"`)\]<>/][^\s'"`)\]<>]*""")


def _decode_js_string_literal(literal: str) -> str:
    """Best-effort decode of a single quoted JS string literal to its value."""
    if len(literal) < 2:
        return literal
    body = literal[1:-1]
    # Only the escapes that matter for hiding a token name; anything exotic is
    # left as-is because the concatenation pass below still catches it.
    return (
        body.replace("\\\\", "\\")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\\`", "`")
        .replace("\\/", "/")
    )


def _decoded_js_string_literals(expression: str) -> list[str]:
    """Return the decoded values of every string literal in *expression*."""
    return [_decode_js_string_literal(match) for match in _JS_STRING_LITERAL_RE.findall(expression)]


def _sensitive_eval_token_reason(expression: str) -> str | None:
    """Reason if a sensitive primitive appears as an identifier or literal name.

    A denylist that only searches direct spellings like ``document.cookie``
    misses ``document["cookie"]`` and ``document["coo" + "kie"]``. Treat token
    names as risky whether they appear as identifiers or as decoded
    string-literal property names, and also scan the concatenation of every
    literal to catch simple split obfuscation.
    """
    string_literals = _decoded_js_string_literals(expression)
    concatenated = "".join(string_literals).lower()
    for token, reason in _SENSITIVE_EVAL_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", expression, re.I):
            return reason
        token_lower = token.lower()
        if any(token_lower in literal.lower() for literal in string_literals):
            return reason
        if token_lower in concatenated:
            return reason
    return None


def risky_eval_reason(expression: str) -> str | None:
    """Return a human-readable reason if *expression* uses risky primitives."""
    if not expression:
        return None
    for pattern, reason in _RISKY_EVAL_PATTERNS:
        if pattern.search(expression):
            return reason
    return _sensitive_eval_token_reason(expression)


def enforce_eval_policy(
    expression: str,
    *,
    restrict_evaluate: bool,
    allow_unsafe_evaluate: bool,
) -> str | None:
    """Return a refusal message when the opt-in denylist blocks *expression*.

    ``restrict_evaluate`` off (the default) → never blocks here. ``allow_unsafe``
    is the escape hatch when the denylist is on but a page is trusted. Network
    egress to private addresses is enforced separately by
    :func:`expression_targets_private_url` and does not depend on this policy.
    """
    if not restrict_evaluate or allow_unsafe_evaluate:
        return None
    reason = risky_eval_reason(expression)
    if not reason:
        return None
    return (
        f"Blocked: browser eval tried to use a sensitive JavaScript primitive "
        f"({reason}) while browser.restrict_evaluate is enabled. Use snapshot / "
        f"screenshot for normal inspection, or set browser.restrict_evaluate = "
        f"false (or browser.allow_unsafe_evaluate = true) to permit programmatic "
        f"evaluation."
    )


def _url_is_blocked(url: str) -> bool:
    """True when *url* targets a private/internal or cloud-metadata address."""
    try:
        assert_not_metadata_endpoint(url)
    except Exception:  # noqa: BLE001 - any raise means blocked
        return True
    try:
        validate_http_url_for_fetch(url)
    except Exception:  # noqa: BLE001 - private/internal/unsupported → blocked
        return True
    return False


def _derived_url_is_blocked(url: str) -> bool:
    """Like :func:`_url_is_blocked`, but an unresolvable host is not evidence.

    A *derived* candidate — reassembled from string fragments, or read from a
    protocol-relative literal — is a guess about intent, and the guess and an
    ordinary string look identical until the host is resolved:
    ``'//double/slash'`` appended to a base URL has exactly the shape of
    ``'//127.0.0.1/x'``. Failing closed on a name that resolves to nothing
    would refuse plain concatenation, so only a positive answer counts here —
    the host resolved, and it is private or metadata. This is the same rule
    :func:`agentos.tools.ssrf.assert_not_metadata_endpoint` already applies.
    """
    try:
        assert_not_metadata_endpoint(url)
        validate_http_url_for_fetch(url)
    except SSRFBlockedError:
        return True
    except Exception:  # noqa: BLE001 - unresolvable/malformed names prove nothing
        return False
    return False


def _url_candidates(text: str, *, protocol_relative: bool) -> list[str]:
    """URL spellings in *text*, each normalized to an ``http(s)://`` form.

    ``protocol_relative`` additionally reads *text* as a whole ``//host/…``
    target. It is anchored rather than searched so that ordinary text keeps its
    ordinary meaning: ``https://example.com//a`` and a ``// comment`` both hold
    a ``//`` that names no host.
    """
    found = [str(match).rstrip(".,;") for match in _JS_URL_LITERAL_RE.findall(text)]
    stripped = text.strip()
    if protocol_relative and _JS_PROTOCOL_RELATIVE_RE.fullmatch(stripped):
        found.append("http:" + stripped.rstrip(".,;"))
    return found


def expression_targets_private_url(expression: str) -> str | None:
    """Return the first private/internal URL target in *expression*, if any.

    Best-effort scan; returns the first candidate that targets a
    private/internal address or the always-blocked cloud-metadata floor, else
    ``None``. Three spellings all have to reach ``_url_is_blocked``, because
    this scan is the only network guard the eval action gets — the post-eval
    page-URL recheck fires only when the page navigates, so a plain ``fetch``
    never touches it:

    * plain ``http(s)://…`` written straight into the expression;
    * protocol-relative ``//host/…``, which inherits the page's scheme and so
      reaches exactly the same address — read only from a string literal that
      is entirely the URL, since a bare ``//`` in JavaScript source is a line
      comment;
    * a protocol split across literals (``'htt' + 'p://169.254.169.254/'``),
      caught by re-scanning the concatenation of every decoded literal, the
      same technique :func:`_sensitive_eval_token_reason` already uses.
    """
    if not isinstance(expression, str):
        return None

    seen: set[str] = set()
    for candidate in _url_candidates(expression, protocol_relative=False):
        seen.add(candidate)
        if _url_is_blocked(candidate):
            return candidate

    literals = _decoded_js_string_literals(expression)
    derived = [c for lit in literals for c in _url_candidates(lit, protocol_relative=True)]
    derived += _url_candidates("".join(literals), protocol_relative=True)
    for candidate in derived:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _derived_url_is_blocked(candidate):
            return candidate
    return None


def redact_browser_output(value: Any) -> Any:
    """Recursively mask credentials in browser-originated data.

    Snapshots, console messages, JS errors, and eval results can carry
    page-rendered API keys, cookies, or bearer tokens. Tool output is a model
    boundary, so redaction is forced here even if global log redaction is off.
    """
    if isinstance(value, str):
        return redact_sensitive_text(value, force=True)
    if isinstance(value, list):
        return [redact_browser_output(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_browser_output(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_browser_output(item) for key, item in value.items()}
    return value
