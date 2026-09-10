"""Router-control target validation, hold storage, and replay payload helpers."""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from agentos.router_tiers import (
    normalize_target_id,
    normalize_tier_mapping,
)

# Zero means no turn-count cap; the hold expires after an idle TTL.
DEFAULT_HOLD_TURNS = 0
DEFAULT_HOLD_TTL_SECONDS = 600.0

# Hold provenance. A hold the MODEL installed for itself through the
# ``router_control`` tool is an in-turn escalation and must decay on its own —
# it keeps the idle TTL above. A hold the USER pinned (the composer route picker
# or a /c0-/c3 slash command) is a standing instruction: it holds until the user
# clears it, so it carries no TTL at all. `RouterControlHoldStore.set_hold`
# derives the TTL from the source unless a caller overrides it explicitly.
HOLD_SOURCE_TOOL = "router_control_tool"
HOLD_SOURCE_USER = "user"
# math.inf rather than a large finite number: `is_expired` compares
# ``now - last_activity >= ttl_seconds``, which is False for every finite
# elapsed time against inf, so a user hold never expires on its own.
USER_HOLD_TTL_SECONDS = math.inf


class RouterControlValidationError(ValueError):
    """Raised when a router-control target is not in the active router config."""


@dataclass(frozen=True)
class RouterControlTarget:
    target_id: str
    target_type: str
    tier: str
    model: str
    provider: str | None = None
    description: str | None = None
    thinking_level: str | None = None


@dataclass(frozen=True)
class RouterImageRoute:
    """One tier an image turn can be routed to. NOT a pin target.

    Deliberately not a ``RouterControlTarget``: these rows exist to be shown,
    not chosen. The router picks the vision route before holds are consulted, so
    a hold on one of these could never take effect, and giving them a
    ``target_id`` would invite exactly that mistake.
    """

    tier: str
    model: str
    provider: str | None = None
    description: str | None = None


@dataclass
class RouterControlHold:
    tier: str
    model: str
    provider: str | None
    target_id: str
    evidence: str
    started_at_monotonic: float
    last_activity_at_monotonic: float | None = None
    turns_remaining: int = DEFAULT_HOLD_TURNS
    ttl_seconds: float = DEFAULT_HOLD_TTL_SECONDS
    source: str = HOLD_SOURCE_TOOL

    @property
    def sticky(self) -> bool:
        """True when nothing but an explicit clear can end this hold."""

        return self.turns_remaining <= 0 and not math.isfinite(self.ttl_seconds)

    def is_expired(self, now_monotonic: float) -> tuple[bool, str | None]:
        if self.turns_remaining < 0:
            return True, "turn_count"
        last_activity = self.last_activity_at_monotonic
        if last_activity is None:
            last_activity = self.started_at_monotonic
        if now_monotonic - last_activity >= self.ttl_seconds:
            return True, "ttl"
        return False, None


def _router_tiers(router_cfg: object | None) -> dict[str, dict[str, Any]]:
    tiers = getattr(router_cfg, "tiers", {}) if router_cfg is not None else {}
    if not isinstance(tiers, dict):
        return {}
    return {
        str(name): dict(cfg)
        for name, cfg in tiers.items()
        if isinstance(cfg, dict)
    }


def _text_tiers(router_cfg: object | None) -> dict[str, dict[str, Any]]:
    return {
        name: cfg
        for name, cfg in normalize_tier_mapping(_router_tiers(router_cfg)).items()
        if not bool(cfg.get("image_only", False))
    }


def build_router_control_targets(router_cfg: object | None) -> list[RouterControlTarget]:
    """Return canonical text targets derived only from active router tiers."""

    targets: list[RouterControlTarget] = []
    text_tiers = _text_tiers(router_cfg)
    for tier, cfg in text_tiers.items():
        model = str(cfg.get("model") or "").strip()
        if not model:
            continue
        provider = str(cfg.get("provider") or "").strip() or None
        thinking = cfg.get("thinking_level", cfg.get("thinking"))
        targets.append(
            RouterControlTarget(
                target_id=f"tier:{tier}",
                target_type="tier",
                tier=tier,
                model=model,
                provider=provider,
                description=str(cfg.get("description") or "").strip() or None,
                thinking_level=str(thinking).strip() if thinking is not None else None,
            )
        )

    return targets


def build_router_image_routes(router_cfg: object | None) -> list[RouterImageRoute]:
    """Return the tiers an image turn can land on, in config order.

    The router's image branch selects among every ``supports_image`` tier — not
    only the ``image_only`` ones — and picks at random when several qualify
    (``engine/steps/agentos_router.py``). So the honest answer to "where does an
    image go?" is this whole list, and a caller that showed only ``image_model``
    would imply a determinism the router does not have.

    Kept out of :func:`build_router_control_targets` on purpose. That list feeds
    hold resolution and the ``router_control`` tool menu, where every entry must
    be a route a turn can actually be pinned to; an image tier is not one.
    """

    routes: list[RouterImageRoute] = []
    for tier, cfg in normalize_tier_mapping(_router_tiers(router_cfg)).items():
        if not bool(cfg.get("supports_image", False)):
            continue
        model = str(cfg.get("model") or "").strip()
        if not model:
            continue
        routes.append(
            RouterImageRoute(
                tier=tier,
                model=model,
                provider=str(cfg.get("provider") or "").strip() or None,
                description=str(cfg.get("description") or "").strip() or None,
            )
        )
    return routes


def resolve_router_control_target(
    router_cfg: object | None,
    target_id: str,
) -> RouterControlTarget:
    normalized = normalize_target_id(target_id)
    if not normalized:
        raise RouterControlValidationError("router_control target_id is required")
    targets = {target.target_id: target for target in build_router_control_targets(router_cfg)}
    try:
        return targets[normalized]
    except KeyError as exc:
        raise RouterControlValidationError(
            f"router_control target_id {normalized!r} is not configured"
        ) from exc


def resolve_router_control_model_target(
    router_cfg: object | None,
    model: str,
) -> RouterControlTarget:
    """Build a target for a model the user named directly, off the tier list.

    Tiers carry more than a model id — ``thinking_level``, ``provider``, and the
    baseline the savings figure is computed against all live on the tier, not on
    the model. A bare model id therefore cannot stand alone: it is HOSTED on the
    default text tier and inherits that tier's settings. That also keeps the
    router step's ``tiers[decision.tier]`` lookup valid, which would raise a
    ``KeyError`` and kill the turn if a hold ever named a tier that is not
    configured.

    The model itself is not validated here. Whether an id exists is a question
    about the live provider catalog, which this module has no access to; the RPC
    layer checks it against the active provider before installing a hold.
    """

    chosen = str(model or "").strip()
    if not chosen:
        raise RouterControlValidationError("router_control model is required")

    text_tiers = _text_tiers(router_cfg)
    if not text_tiers:
        raise RouterControlValidationError("no text tier is configured to host a model choice")
    default_tier = str(getattr(router_cfg, "default_tier", "") or "").strip().lower()
    host_tier = default_tier if default_tier in text_tiers else next(iter(text_tiers))
    host_cfg = text_tiers[host_tier]
    thinking = host_cfg.get("thinking_level", host_cfg.get("thinking"))
    return RouterControlTarget(
        target_id=f"model:{chosen}",
        target_type="model",
        tier=host_tier,
        model=chosen,
        provider=str(host_cfg.get("provider") or "").strip() or None,
        description=None,
        thinking_level=str(thinking).strip() if thinking is not None else None,
    )


def render_router_control_prompt_block(router_cfg: object | None) -> str:
    targets = [
        target
        for target in build_router_control_targets(router_cfg)
        if target.target_type == "tier"
    ]
    if not targets:
        return ""
    rows = [
        {
            "target_id": target.target_id,
        }
        for target in targets
    ]
    menu = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return (
        "Use `router_control` only when the user explicitly asks to use a "
        "configured route or restore automatic routing. "
        "For set_hold, you must choose one target_id exactly from this menu; "
        "do not invent aliases or model ids. The menu is operational context, "
        "not a user-facing recommendation list.\n\n"
        f"router_control_targets={menu}"
    )


class RouterControlHoldStore:
    """Short-lived in-memory router-control holds keyed by session key.

    The router step now runs inside ``asyncio.to_thread`` (runtime.py), where
    ``get_valid(..., decrement=True)`` mutates the store (popping expired or
    turn-exhausted holds and decrementing ``turns_remaining``). Meanwhile the
    gateway event loop mutates the same store via the ``/c0``-``/c3`` slash
    commands (``router.hold.set`` -> :meth:`set_hold`, ``router.hold.clear`` ->
    :meth:`clear`). ``__deepcopy__`` returns ``self`` so the worker thread shares
    the identical instance, so these calls genuinely run on multiple OS threads
    concurrently. Every access takes ``_lock`` so ``get_valid``'s check-then-act
    (read the hold, then decrement/pop) cannot interleave with a concurrent
    ``set_hold`` (which would let the worker's pop delete a freshly installed
    hold, or decrement/pop against the wrong hold) — silently losing a tier pin.
    """

    def __init__(self) -> None:
        self._holds: dict[str, RouterControlHold] = {}
        self._lock = threading.Lock()

    def __deepcopy__(self, memo: dict[int, object]) -> RouterControlHoldStore:
        # TurnRunner copies routing metadata before running the bounded router step,
        # but the hold store is session state and must preserve identity so applying
        # a hold can refresh its idle TTL for follow-up turns.
        memo[id(self)] = self
        return self

    def build_targets(self, router_cfg: object | None) -> list[RouterControlTarget]:
        return build_router_control_targets(router_cfg)

    def set_hold(
        self,
        session_key: str,
        target: RouterControlTarget,
        *,
        evidence: str,
        now_monotonic: float | None = None,
        turns_remaining: int = DEFAULT_HOLD_TURNS,
        ttl_seconds: float | None = None,
        source: str = HOLD_SOURCE_TOOL,
    ) -> RouterControlHold:
        """Install a hold for ``session_key``, replacing any existing one.

        ``ttl_seconds`` defaults to the source's own lifetime — infinite for a
        user pin, the idle TTL for a tool escalation — so callers that do not
        care about expiry cannot accidentally give a user pin the tool's TTL
        (or vice versa) by omitting the argument.
        """

        now = time.monotonic() if now_monotonic is None else now_monotonic
        if ttl_seconds is None:
            ttl_seconds = (
                USER_HOLD_TTL_SECONDS if source == HOLD_SOURCE_USER else DEFAULT_HOLD_TTL_SECONDS
            )
        hold = RouterControlHold(
            tier=target.tier,
            model=target.model,
            provider=target.provider,
            target_id=target.target_id,
            evidence=str(evidence or "").strip(),
            started_at_monotonic=now,
            last_activity_at_monotonic=now,
            turns_remaining=turns_remaining,
            ttl_seconds=ttl_seconds,
            source=source,
        )
        with self._lock:
            self._holds[session_key] = hold
        return hold

    def get_user_hold(
        self,
        session_key: str,
        *,
        now_monotonic: float | None = None,
    ) -> RouterControlHold | None:
        """Return the live USER hold for ``session_key``, else None.

        A pure peek: it never decrements a turn budget nor refreshes the idle
        TTL, so callers outside the router step (tool-surface gating, the
        ``router.hold.get`` RPC) cannot keep a hold alive just by observing it.
        """

        hold = self.get_valid(session_key, now_monotonic=now_monotonic)
        if hold is None or hold.source != HOLD_SOURCE_USER:
            return None
        return hold

    def clear(self, session_key: str) -> RouterControlHold | None:
        with self._lock:
            return self._holds.pop(session_key, None)

    def get_valid(
        self,
        session_key: str,
        *,
        now_monotonic: float | None = None,
        decrement: bool = False,
    ) -> RouterControlHold | None:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        with self._lock:
            hold = self._holds.get(session_key)
            if hold is None:
                return None
            expired, _reason = hold.is_expired(now)
            if expired:
                self._holds.pop(session_key, None)
                return None
            if decrement:
                hold.last_activity_at_monotonic = now
                had_turn_limit = hold.turns_remaining > 0
                if hold.turns_remaining > 0:
                    hold.turns_remaining -= 1
                if hold.turns_remaining < 0 or (had_turn_limit and hold.turns_remaining == 0):
                    self._holds.pop(session_key, None)
            return hold


def router_control_success_payload(
    *,
    action: str,
    target: RouterControlTarget | None,
    replay_required: bool,
    evidence: str,
) -> str:
    payload = {
        "status": "router_control",
        "accepted": True,
        "action": action,
        "target_tier": target.tier if target else None,
        "target_model": target.model if target else None,
        "target_provider": target.provider if target else None,
        "target_id": target.target_id if target else None,
        "replay_required": replay_required,
        "evidence": evidence,
    }
    return json.dumps(payload, ensure_ascii=False)


def router_control_rejection_payload(*, reason: str, evidence: str = "") -> str:
    return json.dumps(
        {
            "status": "router_control",
            "accepted": False,
            "action": "reject",
            "replay_required": False,
            "reason": reason,
            "evidence": evidence,
        },
        ensure_ascii=False,
    )


def router_control_payload(content: object) -> dict[str, Any] | None:
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None
    elif isinstance(content, dict):
        payload = content
    else:
        return None
    if isinstance(payload, dict) and payload.get("status") == "router_control":
        return payload
    return None


def router_control_payload_terminates_turn(content: object) -> bool:
    payload = router_control_payload(content)
    return bool(payload and payload.get("accepted") is True and payload.get("replay_required"))


def router_control_replay_event_from_payload(
    content: object,
    *,
    replay_depth: int = 0,
) -> Any | None:
    payload = router_control_payload(content)
    if not payload or payload.get("accepted") is not True or not payload.get("replay_required"):
        return None
    from agentos.engine.types import RouterControlReplayEvent

    return RouterControlReplayEvent(
        action=str(payload.get("action") or ""),
        target_tier=payload.get("target_tier"),
        target_model=payload.get("target_model"),
        target_provider=payload.get("target_provider"),
        target_id=payload.get("target_id"),
        replay_depth=replay_depth,
    )


def router_control_payload_asdict(content: object) -> dict[str, Any]:
    payload = router_control_payload(content)
    if payload is not None:
        return payload
    if is_dataclass(content) and not isinstance(content, type):
        return {field.name: getattr(content, field.name) for field in fields(content)}
    return {}
