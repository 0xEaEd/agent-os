"""Helpers shared by the remote Pilot Router strategies (LLM judge, Jev).

Everything here is pure and import-light: no provider client, no ML runtime.
The LLM judge (``llm_judge.py``) and the Jev classifier (``jev.py``) both call a
network service from inside the router step, so they share the same three
contracts:

* an **inner timeout** that is provably below ``routing_timeout_seconds`` (the
  outer ``asyncio.wait_for`` in ``engine/runtime.py`` wraps a NON-cancellable
  ``to_thread`` worker, so the inner timeout must be the operative one or the
  worker thread — and its in-flight HTTP call — is orphaned);
* the **head/tail truncation** of the turn body sent over the wire;
* the **greeting/ack short-circuit** that answers trivial turns without a call.

``llm_judge.py`` re-exports the historical private names (``_truncate_body``,
``_run_coro_blocking``, ...) so existing imports keep working.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentos.router_tiers import DEFAULT_TEXT_TIER, TEXT_TIERS

__all__ = [
    "DEFAULT_ROUTING_TIMEOUT_SECONDS",
    "DEFAULT_SHORT_CIRCUIT_ALLOWLIST",
    "ELISION_MARKER",
    "SHORT_CIRCUIT_MAX_CHARS",
    "TRUNCATION_HEAD_CHARS",
    "TRUNCATION_TAIL_CHARS",
    "find_valid_tier_prefer_higher",
    "match_short_circuit",
    "resolve_inner_timeout",
    "run_coro_blocking",
    "truncate_body",
]

# Fallback for ``routing_timeout_seconds`` when a duck-typed/partial router_cfg
# omits it (real AgentOSRouterConfig always carries the attribute, defaulting to
# 10.0 — gateway/config.py). Both the strategies' internal-timeout derivation
# (resolve_inner_timeout) and the outer router-step budget (engine/runtime.py)
# MUST read this same fallback: the "inner timeout must win over the
# un-cancellable outer wait_for" guarantee depends on the two sites agreeing.
DEFAULT_ROUTING_TIMEOUT_SECONDS = 10.0


def resolve_inner_timeout(router_cfg: object | None, *, explicit: float | None) -> float:
    """Derive a strategy-internal timeout strictly below the router-step budget.

    ``explicit`` is the operator-supplied per-strategy override (e.g.
    ``judge_timeout_seconds`` / ``jev.timeout_seconds``); ``None`` derives the
    value from ``routing_timeout_seconds`` alone. The result is always
    ``< routing_timeout_seconds`` so the inner timeout — not the un-cancellable
    outer ``wait_for`` — is the operative one.
    """
    budget = float(
        getattr(router_cfg, "routing_timeout_seconds", None) or DEFAULT_ROUTING_TIMEOUT_SECONDS
    )
    # Aim ~0.5s / 20% below budget, but for tiny budgets the 0.5s floor could
    # meet or exceed budget; the final min() with budget*0.9 keeps the ceiling
    # STRICTLY below the outer budget so the inner timeout always wins even at
    # small configured values.
    ceiling = min(max(0.5, min(budget * 0.8, budget - 0.5)), budget * 0.9)
    if explicit:
        # Clamp an operator-supplied timeout under the outer budget even when
        # they set it >= routing_timeout_seconds (config only validates gt=0.0).
        # Apply the 0.1s lower bound only when it does not exceed the ceiling:
        # for a tiny budget the ceiling can be < 0.1s, so a fixed 0.1 floor
        # would push the inner timeout back up to == budget and orphan the
        # worker thread. Clamp the floor to the ceiling so the result always
        # stays strictly below the outer budget.
        floor = min(0.1, ceiling)
        timeout = min(max(floor, float(explicit)), ceiling)
    else:
        timeout = ceiling
    # Defensive invariant: the inner timeout must stay strictly below the outer
    # budget or the orphaned-worker guarantee is void.
    assert timeout < budget, (timeout, budget)
    return timeout


# ---------------------------------------------------------------------------
# Body truncation
# ---------------------------------------------------------------------------

ELISION_MARKER = "\n[... {omitted} chars omitted ...]\n"
TRUNCATION_HEAD_CHARS = 800
TRUNCATION_TAIL_CHARS = 1200


def truncate_body(text: str, max_chars: int) -> str:
    """Head/tail truncate ``text`` to roughly ``max_chars`` with an elision marker.

    ``max_chars`` may be configured below HEAD+TAIL (its floor is 1000, but the
    fixed head/tail budget is 2000). Splitting unconditionally would overlap the
    head and tail slices — duplicating the middle and yielding a negative
    ``omitted`` count. When the budget is too small to fit a head+tail split
    plus the elision marker, just hard-truncate to ``max_chars``.
    """
    if len(text) <= max_chars:
        return text
    marker_len = len(ELISION_MARKER.format(omitted=len(text)))
    if max_chars < TRUNCATION_HEAD_CHARS + TRUNCATION_TAIL_CHARS + marker_len:
        return text[:max_chars]
    head = text[:TRUNCATION_HEAD_CHARS]
    tail = text[-TRUNCATION_TAIL_CHARS:]
    omitted = len(text) - len(head) - len(tail)
    return head + ELISION_MARKER.format(omitted=omitted) + tail


# ---------------------------------------------------------------------------
# Greeting / ack short-circuit
# ---------------------------------------------------------------------------

DEFAULT_SHORT_CIRCUIT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "thanks",
        "thank you",
        "thx",
        "ty",
        "ok",
        "okay",
        "k",
        "yes",
        "no",
        "cool",
        "nice",
        "great",
        "good morning",
        "good night",
        "bye",
        "goodbye",
        # Vietnamese
        "chào",
        "chào bạn",
        "xin chào",
        "cảm ơn",
        "cám ơn",
        "cảm ơn nhé",
        "cảm ơn bạn",
        "ừ",
        "ừm",
        "vâng",
        "dạ",
        "được",
        "ok cảm ơn",
        "tạm biệt",
        # Chinese
        "你好",
        "谢谢",
        "好的",
        "嗯",
        "再见",
    }
)
SHORT_CIRCUIT_MAX_CHARS = 20


def match_short_circuit(message: str, allowlist: frozenset[str]) -> bool:
    """Whether ``message`` is an exact (case-folded, trailing-punctuation-
    stripped) greeting/ack of at most ``SHORT_CIRCUIT_MAX_CHARS`` characters."""
    stripped = message.strip()
    if not stripped or len(stripped) > SHORT_CIRCUIT_MAX_CHARS:
        return False
    normalized = stripped.casefold().rstrip("!.?~ ")
    return normalized in allowlist


# ---------------------------------------------------------------------------
# Tier clamping
# ---------------------------------------------------------------------------


def find_valid_tier_prefer_higher(start_tier: str, valid_tiers: list[str]) -> str:
    """Clamp ``start_tier`` into ``valid_tiers``, never collapsing downward.

    Prefers the nearest valid tier at or above the desired one. When the
    desired tier is above every valid tier, clamps to the HIGHEST valid tier
    (scanning downward) — a high-risk/hard turn must not silently collapse to
    the cheapest available model. This deliberately differs from
    ``tiers_util._find_valid_tier`` (see its module docstring).
    """
    if not valid_tiers:
        return DEFAULT_TEXT_TIER
    tiers = list(TEXT_TIERS)
    start_idx = tiers.index(start_tier) if start_tier in tiers else 1
    for idx in range(start_idx, len(tiers)):
        if tiers[idx] in valid_tiers:
            return tiers[idx]
    for tier in reversed(tiers):
        if tier in valid_tiers:
            return tier
    return valid_tiers[0]


# ---------------------------------------------------------------------------
# Sync bridge for onboarding probes
# ---------------------------------------------------------------------------


def run_coro_blocking(coro: Any) -> Any:
    """Run ``coro`` to completion from either sync or async context.

    The onboarding probes keep a synchronous signature (both the interactive CLI
    prompt code and the WebUI/RPC ``upsert_router`` path call them as plain
    functions). Both callers reach them with NO running loop in the calling
    thread (the RPC handler dispatches ``upsert_router`` onto a worker thread via
    ``asyncio.to_thread``), so ``asyncio.run`` drives the coroutine inline in the
    common case. The already-running-loop branch is a defensive fallback: it
    dispatches the coroutine onto a dedicated worker thread with its own event
    loop rather than letting a bare ``asyncio.run`` raise ``RuntimeError`` — but
    it still blocks the calling thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro)).result()
