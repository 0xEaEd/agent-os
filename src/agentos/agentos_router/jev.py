"""Jev router strategy: classify turns R0-R3 via typesafe.ai's System One API.

Experimental, opt-in alternative to the local ML router (``pilot-v1``) and the
LLM judge (``llm_judge``). Jev is not a text generator: one ``POST
/v1/systemone`` call answers typed questions and returns a **calibrated**
probability distribution plus a confidence score, so — unlike the judge, which
must pin ``confidence=1.0`` because its self-report is uncalibrated — the
engine's deterministic confidence gate (``_confidence_protected_tier``) is fed a
meaningful number here.

**Privacy.** The strategy sends the CURRENT turn text (head/tail truncated to
``jev.input_max_chars``) to typesafe.ai. Nothing else leaves the machine: no
system prompt, no history, no tool list. It is never the default strategy.

**Request shape.** One request, two questions evaluated in parallel:

* ``route`` — a ``choice`` over ``R0``–``R3`` whose criteria are objects
  (``what`` / ``not_for`` / ``examples``) built from the configured tier
  descriptions plus the boundary examples the LLM judge already carries;
* ``high_risk`` — a ``noul`` ("is this a destructive / production-affecting
  request?"). At or above ``jev.high_risk_threshold`` the turn is floored at R3
  in code, which is how the judge's "length is not difficulty" hard rule is
  enforced without relying on prose the classifier would read literally.

``state`` is the bare truncated turn: typesafe's guidance is atomic questions
combined in application code, and its accuracy falls with irrelevant context.
The engine already owns every history rule (kv-cache anti-downgrade, complaint
upgrade, confidence gate) in ``_finalize_decision``.

**Fail-soft.** A missing API key, HTTP 401/422/429/529, a network error, a
malformed body, or the inner timeout all degrade to the configured default
tier with ``routing_source="jev_unavailable"`` and a ``reason``. There is
deliberately **no in-band retry**: the router step has a ~10 s budget and a
graceful default-tier fallback, so backing off inside that window would only
trade a cheap degrade for an orphaned worker thread. ``require_router_runtime``
turns every degrade into a ``RuntimeError`` (Pilot parity).

**Transport.** Direct ``httpx`` against the documented endpoint (the vendor SDK
pulls in ``httpx2``). Tests inject a :class:`JevTransport` fake.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

from agentos.agentos_router.controller import (
    compute_difficulty,
    compute_margin,
    derive_prompt_policy,
    derive_thinking_mode,
    normalize_decisions,
    synthetic_one_hot,
)
from agentos.agentos_router.llm_judge import compute_flags
from agentos.agentos_router.strategy_common import (
    DEFAULT_SHORT_CIRCUIT_ALLOWLIST,
    find_valid_tier_prefer_higher,
    match_short_circuit,
    resolve_inner_timeout,
    run_coro_blocking,
    truncate_body,
)
from agentos.router_tiers import (
    DEFAULT_TEXT_TIER,
    ROUTE_CLASS_TO_TIER,
    TEXT_TIERS,
    TIER_TO_ROUTE_CLASS,
    normalize_text_tier,
)

log = structlog.get_logger(__name__)

__all__ = [
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "HIGH_RISK_QUESTION",
    "ROUTE_QUESTION",
    "SOURCE_HEALTHY",
    "SOURCE_UNAVAILABLE",
    "SYSTEMONE_PATH",
    "HttpxJevTransport",
    "JevApiError",
    "JevStrategy",
    "JevTransport",
    "JevVerdict",
    "build_jev_request",
    "build_route_criteria",
    "jev_credential_probe",
    "parse_jev_response",
    "probe_jev",
    "resolve_jev_api_key",
]

#: Healthy / degraded source tags surfaced as ``routing_source``.
SOURCE_HEALTHY = "jev"
SOURCE_UNAVAILABLE = "jev_unavailable"

DEFAULT_BASE_URL = "https://api.typesafe.ai"
SYSTEMONE_PATH = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_API_KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_INPUT_MAX_CHARS = 4000
DEFAULT_HIGH_RISK_THRESHOLD = 0.7
#: Bytes of an error body kept in ``JevApiError.message`` (never headers).
_ERROR_BODY_HEAD_CHARS = 200

ROUTE_QUESTION = "route"
HIGH_RISK_QUESTION = "high_risk"

_ROUTE_CLASSES: tuple[str, ...] = tuple(ROUTE_CLASS_TO_TIER)
_CLASS_INDEX: dict[str, int] = {cls: idx for idx, cls in enumerate(_ROUTE_CLASSES)}

ROUTE_INSTRUCTIONS = (
    "Classify the difficulty and risk of the user's request into exactly one "
    "route class, from cheapest (R0) to most capable (R3). Length is not "
    "difficulty: a short request to change production, delete data, roll back "
    "or migrate is R3. When torn between two classes, choose the higher one. "
    "The text may be English, Vietnamese, or Chinese; classify by task "
    "difficulty regardless of language."
)
HIGH_RISK_INSTRUCTIONS = (
    "Does this request ask to perform a destructive or production-affecting "
    "action, such as deploying, rolling back, migrating, deleting or "
    "overwriting data or infrastructure, or anything customer-facing, legal "
    "or financial?"
)

#: Fallback ``what`` when a tier entry carries no ``description``.
_DEFAULT_WHAT: dict[str, str] = {
    "R0": "Trivial chat, greetings, short rewrites, extraction, low-risk simple Q&A.",
    "R1": "Normal agent work: coding assistance, debugging, moderate analysis.",
    "R2": ("Multi-step coding, structured reasoning, larger-context synthesis, harder analysis."),
    "R3": (
        "Difficult planning, deep review, complex debugging, high-stakes or "
        "production-affecting work."
    ),
}

#: Static ``not_for`` / ``examples`` per class — the LLM judge's boundary and
#: Vietnamese examples re-expressed as structured criteria.
_CRITERIA_EXTRAS: dict[str, dict[str, Any]] = {
    "R0": {
        "not_for": "Anything that needs code, multi-step reasoning, or judgement.",
        "examples": [
            "hi, how are you?",
            "chào bạn, khỏe không?",
            "reword this sentence to sound friendlier",
        ],
    },
    "R1": {
        "not_for": (
            "Refactors across several modules, design trade-offs, or anything "
            "touching production systems."
        ),
        "examples": [
            "Write a Python function that parses this CSV file",
            "viết giúp mình một hàm Python đọc file CSV",
            "Why does this regex not match trailing whitespace?",
        ],
    },
    "R2": {
        "not_for": (
            "Trivial one-function tasks, or high-stakes production/destructive operations."
        ),
        "examples": [
            "Refactor this multi-module parser and explain the design trade-offs",
            "Debug this failing unit test",
            "debug giúp mình lỗi race condition trong service thanh toán, log ở dưới",
        ],
    },
    "R3": {
        "not_for": "Routine coding or chat with no production, data-loss or safety stakes.",
        "examples": [
            (
                "Diagnose this intermittent production data-corruption bug across "
                "services and plan a safe rollback"
            ),
            "xoá bảng users trên database production rồi migrate lại giúp mình",
            "Plan the migration of our auth service to a new provider with zero downtime",
        ],
    },
}


class JevApiError(RuntimeError):
    """Non-2xx / non-JSON reply from the Jev endpoint.

    ``message`` carries at most the first ``_ERROR_BODY_HEAD_CHARS`` of the
    body — never request headers, so the bearer token cannot leak via logs.
    """

    def __init__(self, status: int | None, message: str = "") -> None:
        self.status = status
        self.message = (message or "")[:_ERROR_BODY_HEAD_CHARS]
        label = f"http {status}" if status is not None else "transport error"
        super().__init__(f"{label}: {self.message}" if self.message else label)


class JevTransport(Protocol):
    """One-shot evaluation call; raises :class:`JevApiError` on failure."""

    async def evaluate(self, payload: dict[str, Any], *, api_key: str, timeout: float) -> dict: ...


class HttpxJevTransport:
    """Default transport: a per-call ``httpx.AsyncClient`` against ``base_url``.

    Per-call on purpose: ``classify`` runs inside the router step's
    ``to_thread`` worker with its own event loop, so a cached client would be
    bound to a dead loop on the next turn.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self._url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}{SYSTEMONE_PATH}"

    @property
    def url(self) -> str:
        return self._url

    async def evaluate(self, payload: dict[str, Any], *, api_key: str, timeout: float) -> dict:
        import httpx

        from agentos.env import trust_env

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=trust_env()) as client:
                response = await client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise JevApiError(None, exc.__class__.__name__) from exc
        if response.status_code >= 400:
            raise JevApiError(response.status_code, response.text)
        try:
            body = response.json()
        except ValueError as exc:
            raise JevApiError(response.status_code, "non-JSON body") from exc
        if not isinstance(body, dict):
            raise JevApiError(response.status_code, "non-object body")
        return body


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def resolve_jev_api_key(jev_cfg: object | None) -> tuple[str, str]:
    """Return ``(api_key, source)`` — source in ``config`` / ``env:<NAME>`` / ``missing``.

    Order: literal ``jev.api_key`` → ``$<jev.api_key_env>`` (default
    ``TYPESAFE_API_KEY``) → empty. Cheap enough to run per classify, so a key
    exported later (``agentos env set``) takes effect without a strategy
    rebuild. The key itself is never logged; callers log ``source`` only.
    """
    literal = str(getattr(jev_cfg, "api_key", None) or "").strip()
    if literal:
        return literal, "config"
    env_name = str(getattr(jev_cfg, "api_key_env", None) or "").strip() or DEFAULT_API_KEY_ENV
    from_env = os.environ.get(env_name, "").strip()
    if from_env:
        return from_env, f"env:{env_name}"
    return "", "missing"


def _api_key_env_name(jev_cfg: object | None) -> str:
    return str(getattr(jev_cfg, "api_key_env", None) or "").strip() or DEFAULT_API_KEY_ENV


def jev_credential_probe(router_cfg: object | None) -> str | None:
    """Registry hook: a problem string when no usable key is configured, else None.

    Never touches the network — boot preflight and ``agentos doctor`` call it.
    """
    jev_cfg = getattr(router_cfg, "jev", None)
    key, _source = resolve_jev_api_key(jev_cfg)
    if key:
        return None
    env_name = _api_key_env_name(jev_cfg)
    return (
        f"no TypeSafe API key: set {env_name} (`agentos env set {env_name}`, prompted) "
        "or agentos_router.jev.api_key"
    )


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------


def build_route_criteria(tiers: object) -> dict[str, dict[str, Any]]:
    """Build the ``route`` choice criteria (always all four R keys).

    ``what`` comes from the live tier ``description`` (falling back to a
    built-in blurb); ``not_for`` / ``examples`` are static per class. All four
    classes are always emitted so the choice space is stable across tier
    configs — clamping to ``valid_tiers`` happens after the answer.
    """
    tier_map = tiers if isinstance(tiers, dict) else {}
    criteria: dict[str, dict[str, Any]] = {}
    for tier_name in TEXT_TIERS:
        route_class = TIER_TO_ROUTE_CLASS.get(tier_name, tier_name)
        entry = tier_map.get(tier_name)
        description = ""
        if isinstance(entry, dict) and not entry.get("image_only"):
            description = str(entry.get("description", "") or "").strip()
        extras = _CRITERIA_EXTRAS.get(route_class, {})
        criteria[route_class] = {
            "what": description or _DEFAULT_WHAT.get(route_class, route_class),
            "not_for": extras.get("not_for", ""),
            "examples": list(extras.get("examples", [])),
        }
    return criteria


def build_jev_request(
    *,
    message: str,
    model: str = DEFAULT_MODEL,
    input_max_chars: int = DEFAULT_INPUT_MAX_CHARS,
    tiers: object = None,
    criteria: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the ``/v1/systemone`` body for one turn."""
    return {
        "state": truncate_body(message, max(1000, int(input_max_chars))),
        "model": model or DEFAULT_MODEL,
        "questions": {
            ROUTE_QUESTION: {
                "type": "choice",
                "instructions": ROUTE_INSTRUCTIONS,
                "criteria": criteria if criteria is not None else build_route_criteria(tiers),
            },
            HIGH_RISK_QUESTION: {
                "type": "noul",
                "instructions": HIGH_RISK_INSTRUCTIONS,
            },
        },
    }


@dataclass(frozen=True)
class JevVerdict:
    route_class: str
    confidence: float
    probabilities: dict[str, float]
    high_risk: float | None
    model: str
    usage: dict[str, int]


def _clamp01(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return min(1.0, max(0.0, number))


def parse_jev_response(payload: object) -> JevVerdict | None:
    """Parse a ``/v1/systemone`` body; ``None`` when the route answer is unusable.

    A missing or unparsable ``high_risk`` noul alone does not invalidate the
    verdict — it is recorded as ``None`` and the floor simply does not fire.
    """
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return None
    route = answers.get(ROUTE_QUESTION)
    if not isinstance(route, dict):
        return None
    route_class = str(route.get("choice", "") or "").strip().upper()
    if route_class not in _CLASS_INDEX:
        return None

    raw_probs = route.get("probabilities")
    probabilities: dict[str, float] = {}
    if isinstance(raw_probs, dict):
        for cls in _ROUTE_CLASSES:
            prob = _clamp01(raw_probs.get(cls))
            probabilities[cls] = prob if prob is not None else 0.0
    if not probabilities or sum(probabilities.values()) <= 0.0:
        # No usable distribution: synthesize one-hot on the choice so the
        # controller helpers still receive a well-formed vector.
        probabilities = {cls: (1.0 if cls == route_class else 0.0) for cls in _ROUTE_CLASSES}

    confidence = _clamp01(route.get("confidence"))
    if confidence is None:
        confidence = probabilities.get(route_class, 0.0)

    high_risk: float | None = None
    noul = answers.get(HIGH_RISK_QUESTION)
    if isinstance(noul, dict):
        high_risk = _clamp01(noul.get("noul"))

    usage_raw = payload.get("usage")
    usage: dict[str, int] = {}
    if isinstance(usage_raw, dict):
        for key in ("input_tokens", "output_tokens"):
            try:
                usage[key] = int(usage_raw.get(key, 0) or 0)
            except (TypeError, ValueError):
                usage[key] = 0

    return JevVerdict(
        route_class=route_class,
        confidence=confidence,
        probabilities=probabilities,
        high_risk=high_risk,
        model=str(payload.get("model", "") or ""),
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class JevStrategy:
    """History-aware router strategy backed by one Jev System One call.

    Implements the same ``classify()`` contract as ``PilotStrategy`` and
    ``LLMJudgeStrategy``; every failure mode collapses to the configured default
    tier with ``routing_source="jev_unavailable"``.
    """

    requires_history = True
    source = SOURCE_HEALTHY

    def __init__(
        self,
        router_cfg: object | None = None,
        *,
        transport: JevTransport | None = None,
        confidence_threshold: float = 0.5,
        require_router_runtime: bool = False,
    ) -> None:
        self._router_cfg = router_cfg
        jev_cfg = getattr(router_cfg, "jev", None)
        self._jev_cfg = jev_cfg
        self._confidence_threshold = float(confidence_threshold)
        self._require_router_runtime = bool(require_router_runtime)

        self._model = str(getattr(jev_cfg, "model", None) or DEFAULT_MODEL)
        self._base_url = str(getattr(jev_cfg, "base_url", None) or DEFAULT_BASE_URL)
        self._input_max_chars = max(
            1000, int(getattr(jev_cfg, "input_max_chars", None) or DEFAULT_INPUT_MAX_CHARS)
        )
        threshold = getattr(jev_cfg, "high_risk_threshold", None)
        self._high_risk_threshold = (
            float(threshold) if threshold is not None else DEFAULT_HIGH_RISK_THRESHOLD
        )
        short_circuit = getattr(jev_cfg, "short_circuit_enabled", None)
        self._short_circuit_enabled = True if short_circuit is None else bool(short_circuit)
        # Off by default: Jev returns calibrated confidence, so an unsure R0 is
        # already handled by the engine gate; a blanket "tools present => not
        # below R1" floor would make c0 unreachable in every tool-bearing chat.
        self._agentic_floor_enabled = bool(getattr(jev_cfg, "agentic_floor_enabled", False))
        self._short_circuit_allowlist = DEFAULT_SHORT_CIRCUIT_ALLOWLIST
        self._timeout = resolve_inner_timeout(
            router_cfg, explicit=getattr(jev_cfg, "timeout_seconds", None)
        )
        # Criteria are built once from the live tiers; the dispatch cache key
        # fingerprints ``tiers`` so a tier edit rebuilds the strategy.
        self._criteria = build_route_criteria(getattr(router_cfg, "tiers", None))
        self._transport: JevTransport = (
            transport if transport is not None else HttpxJevTransport(self._base_url)
        )

    # -- classify contract ---------------------------------------------------

    async def classify(
        self,
        message: str,
        valid_tiers: list[str],
        routing_history: list[dict] | None = None,
        prev_assistant_text: str | None = None,
        prev_assistant_usage: dict | None = None,
        history_user_texts: list[str] | None = None,
        flags_text_override: str | None = None,
        tool_defs: list | None = None,
    ) -> tuple[str, float, str, dict]:
        """Classify the CURRENT turn into AgentOS tier format via Jev."""
        flags = compute_flags(flags_text_override or message)
        flags["agentic"] = bool(tool_defs)

        short = self._short_circuit(message, valid_tiers, flags)
        if short is not None:
            return short

        api_key, key_source = resolve_jev_api_key(self._jev_cfg)
        if not api_key:
            env_name = _api_key_env_name(self._jev_cfg)
            return self._unavailable(
                valid_tiers, flags, reason=f"api key missing ({env_name})", key_source=key_source
            )

        payload = build_jev_request(
            message=message,
            model=self._model,
            input_max_chars=self._input_max_chars,
            criteria=self._criteria,
        )
        try:
            body = await asyncio.wait_for(
                self._transport.evaluate(payload, api_key=api_key, timeout=self._timeout),
                timeout=self._timeout,
            )
        except TimeoutError:
            log.warning("jev.timeout", timeout_seconds=self._timeout)
            return self._unavailable(
                valid_tiers, flags, reason="jev timeout", key_source=key_source
            )
        except JevApiError as exc:
            log.warning("jev.call_failed", status=exc.status, error=str(exc))
            return self._unavailable(valid_tiers, flags, reason=str(exc), key_source=key_source)
        except Exception as exc:  # noqa: BLE001 - degrade to jev_unavailable
            log.warning("jev.call_failed", error=exc.__class__.__name__, exc_info=True)
            return self._unavailable(
                valid_tiers, flags, reason=exc.__class__.__name__, key_source=key_source
            )

        verdict = parse_jev_response(body)
        if verdict is None:
            log.warning("jev.malformed_response")
            return self._unavailable(
                valid_tiers, flags, reason="malformed jev response", key_source=key_source
            )
        return self._map_verdict(verdict, valid_tiers, flags, key_source)

    # -- internals -----------------------------------------------------------

    def _map_verdict(
        self,
        verdict: JevVerdict,
        valid_tiers: list[str],
        flags: dict[str, bool],
        key_source: str,
    ) -> tuple[str, float, str, dict]:
        route_class = verdict.route_class
        confidence = verdict.confidence
        floored_class = route_class

        high_risk_floor = (
            verdict.high_risk is not None and verdict.high_risk >= self._high_risk_threshold
        )
        if high_risk_floor:
            floored_class = _max_class(floored_class, "R3")
        agentic_floor = (
            self._agentic_floor_enabled
            and bool(flags.get("agentic"))
            and _CLASS_INDEX[floored_class] < 1
        )
        if agentic_floor:
            floored_class = _max_class(floored_class, "R1")

        # A floor that raised the class must not be undone by the engine's
        # confidence gate (which snaps low-confidence non-default tiers back to
        # default_tier), so lift confidence to the gate threshold.
        confidence_floor = floored_class != route_class and confidence < self._confidence_threshold
        if confidence_floor:
            confidence = self._confidence_threshold

        desired_tier = ROUTE_CLASS_TO_TIER.get(floored_class, DEFAULT_TEXT_TIER)
        tier = desired_tier
        if tier not in valid_tiers:
            tier = find_valid_tier_prefer_higher(tier, valid_tiers)
        final_route_class = TIER_TO_ROUTE_CLASS.get(tier, floored_class)

        prob_list = [verdict.probabilities.get(cls, 0.0) for cls in _ROUTE_CLASSES]
        thinking_mode = derive_thinking_mode(prob_list, flags)
        prompt_policy = derive_prompt_policy(prob_list, flags)
        thinking_mode, prompt_policy = normalize_decisions(thinking_mode, prompt_policy)

        high_risk_text = "n/a" if verdict.high_risk is None else f"{verdict.high_risk:.2f}"
        extra: dict[str, Any] = {
            "route_class": route_class,
            "top1_label": route_class,
            "final_route_class": final_route_class,
            "confidence": confidence,
            "thinking_mode": thinking_mode,
            "prompt_policy": prompt_policy,
            "flags": dict(flags),
            "reason": (
                f"jev {route_class} p={verdict.probabilities.get(route_class, 0.0):.2f} "
                f"high_risk={high_risk_text}"
            ),
            "probabilities": dict(verdict.probabilities),
            "margin": compute_margin(prob_list),
            "difficulty": compute_difficulty(prob_list),
            "high_risk_noul": verdict.high_risk,
            "high_risk_floor_applied": high_risk_floor,
            "agentic_floor_applied": agentic_floor,
            "confidence_floor_applied": confidence_floor,
            "model_version": verdict.model,
            "usage": dict(verdict.usage),
            "api_key_source": key_source,
        }
        return tier, confidence, self.source, extra

    def _short_circuit(
        self,
        message: str,
        valid_tiers: list[str],
        flags: dict[str, bool],
    ) -> tuple[str, float, str, dict] | None:
        if not self._short_circuit_enabled:
            return None
        if self._agentic_floor_enabled and flags.get("agentic"):
            # With the floor on, an agentic workstream is never routed below R1;
            # the short-circuit only ever yields R0, so defer to the classifier.
            return None
        if not match_short_circuit(message, self._short_circuit_allowlist):
            return None
        tier = find_valid_tier_prefer_higher(ROUTE_CLASS_TO_TIER["R0"], valid_tiers)
        final_route_class = TIER_TO_ROUTE_CLASS.get(tier, "R0")
        extra = self._build_extra(
            route_class="R0",
            final_route_class=final_route_class,
            confidence=1.0,
            flags=flags,
            reason="greeting/ack short-circuit",
            key_source="unused",
        )
        return tier, 1.0, self.source, extra

    def _unavailable(
        self,
        valid_tiers: list[str],
        flags: dict[str, bool],
        *,
        reason: str,
        key_source: str,
    ) -> tuple[str, float, str, dict]:
        if self._require_router_runtime:
            raise RuntimeError(f"jev router unavailable: {reason}")
        default = (
            normalize_text_tier(getattr(self._router_cfg, "default_tier", None))
            or DEFAULT_TEXT_TIER
        )
        tier = find_valid_tier_prefer_higher(default, valid_tiers)
        route_class = TIER_TO_ROUTE_CLASS.get(tier, "R1")
        extra = self._build_extra(
            route_class=route_class,
            final_route_class=route_class,
            confidence=0.0,
            flags=flags,
            reason=f"{SOURCE_UNAVAILABLE}: {reason}",
            key_source=key_source,
        )
        return tier, 0.0, SOURCE_UNAVAILABLE, extra

    def _build_extra(
        self,
        *,
        route_class: str,
        final_route_class: str,
        confidence: float,
        flags: dict[str, bool],
        reason: str,
        key_source: str,
    ) -> dict[str, Any]:
        probs = synthetic_one_hot(ROUTE_CLASS_TO_TIER.get(final_route_class, DEFAULT_TEXT_TIER))
        thinking_mode = derive_thinking_mode(probs, flags)
        prompt_policy = derive_prompt_policy(probs, flags)
        thinking_mode, prompt_policy = normalize_decisions(thinking_mode, prompt_policy)
        return {
            "route_class": route_class,
            "top1_label": route_class,
            "final_route_class": final_route_class,
            "confidence": confidence,
            "thinking_mode": thinking_mode,
            "prompt_policy": prompt_policy,
            "flags": dict(flags),
            "reason": reason,
            "probabilities": None,
            "margin": None,
            "difficulty": None,
            "high_risk_noul": None,
            "high_risk_floor_applied": False,
            "agentic_floor_applied": False,
            "confidence_floor_applied": False,
            "model_version": None,
            "usage": None,
            "api_key_source": key_source,
        }


def _max_class(a: str, b: str) -> str:
    return a if _CLASS_INDEX.get(a, 0) >= _CLASS_INDEX.get(b, 0) else b


# ---------------------------------------------------------------------------
# Onboarding probe
# ---------------------------------------------------------------------------


def probe_jev(
    api_key: str | None,
    *,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout: float = 15.0,
    transport: JevTransport | None = None,
) -> str | None:
    """Run one cheap test classification against Jev.

    Returns ``None`` on success, or a short error string on failure. Shared by
    every onboarding surface (interactive CLI, WebUI/RPC via ``upsert_router``)
    so a key is verified — not merely shape-checked — before it is persisted.
    Sync-callable from either context via :func:`run_coro_blocking`.
    """
    from types import SimpleNamespace

    key = str(api_key or "").strip()
    if not key:
        return f"no API key (set {DEFAULT_API_KEY_ENV} or enter one)"
    router_cfg = SimpleNamespace(
        tiers={},
        default_tier="c1",
        routing_timeout_seconds=timeout + 0.5,
        jev=SimpleNamespace(
            api_key=key,
            api_key_env=DEFAULT_API_KEY_ENV,
            base_url=base_url,
            model=model,
            input_max_chars=DEFAULT_INPUT_MAX_CHARS,
            high_risk_threshold=DEFAULT_HIGH_RISK_THRESHOLD,
            timeout_seconds=timeout,
            short_circuit_enabled=False,
            agentic_floor_enabled=False,
        ),
    )
    strategy = JevStrategy(router_cfg=router_cfg, transport=transport)
    try:
        _tier, _confidence, source, extra = run_coro_blocking(
            strategy.classify("hello, please classify this test turn", list(TEXT_TIERS))
        )
    except Exception as exc:  # noqa: BLE001 - surface any connectivity failure
        return str(exc) or exc.__class__.__name__
    if source == SOURCE_UNAVAILABLE:
        reason = str(extra.get("reason", "") or "")
        return reason.removeprefix(f"{SOURCE_UNAVAILABLE}: ") or "jev did not return a decision"
    return None
