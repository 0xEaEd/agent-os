"""RPC handlers for user-directed Pilot Router tier holds.

Backs the /c0-/c3 and /auto slash commands and the chat composer's route
picker: a session-scoped tier hold set through the same
``RouterControlHoldStore`` the LLM-facing ``router_control`` tool uses, so
both paths share precedence semantics inside the router step.

Expiry is where the two paths deliberately part. A hold set here is marked
``HOLD_SOURCE_USER`` and is sticky — it survives until ``router.hold.clear``,
because a user who picks a route is stating an intent, not asking for a
ten-minute nudge. The model's own escalations keep the idle TTL so they decay
on their own. ``router.hold.get`` lets a reconnecting client read the pin back
rather than guessing, since the store is process memory the UI cannot see.
"""

from __future__ import annotations

from typing import Any

from agentos.gateway.access import CONTROL_AND_CHANNEL, CONTROL_ONLY
from agentos.gateway.rpc import RpcContext, RpcHandlerError, get_dispatcher
from agentos.router_control import (
    HOLD_SOURCE_USER,
    RouterControlHold,
    RouterControlHoldStore,
    RouterControlTarget,
    RouterControlValidationError,
    build_router_control_targets,
    build_router_image_routes,
    resolve_router_control_model_target,
    resolve_router_control_target,
)
from agentos.session.keys import canonicalize_session_key

_d = get_dispatcher()


def _require_key(params: dict | None) -> str:
    if not isinstance(params, dict) or "key" not in params:
        raise ValueError("params.key is required")
    key = params["key"]
    if not isinstance(key, str):
        raise ValueError("params.key must be a string")
    return canonicalize_session_key(key)


def _hold_payload(hold: RouterControlHold) -> dict[str, Any]:
    """Serialize a hold for the wire.

    ``ttlSeconds`` is None for a sticky hold rather than its real
    ``math.inf``: ``json.dumps`` would emit a bare ``Infinity`` token, which is
    not valid JSON and throws in ``JSON.parse`` on the browser side. Clients
    read ``sticky`` for the "never expires" case and treat ``ttlSeconds`` as the
    countdown only when it is a number.
    """

    sticky = hold.sticky
    # A model pin still reports the tier hosting it — clients need that to render
    # "borrowed from c1" — so `targetType` is what distinguishes "the user picked
    # tier c1" from "the user picked a model that happens to sit on c1".
    return {
        "tier": hold.tier,
        "model": hold.model,
        "provider": hold.provider,
        "targetId": hold.target_id,
        "targetType": "model" if hold.target_id.startswith("model:") else "tier",
        "source": hold.source,
        "sticky": sticky,
        "ttlSeconds": None if sticky else hold.ttl_seconds,
    }


def _router_state(ctx: RpcContext) -> tuple[Any, RouterControlHoldStore]:
    runner = ctx.turn_runner
    store = getattr(runner, "router_control_hold_store", None)
    if not isinstance(store, RouterControlHoldStore):
        raise RpcHandlerError(
            "router.unavailable",
            "Router hold store is unavailable on this gateway",
        )
    cfg = getattr(runner, "router_control_config", None)
    if cfg is None or not getattr(cfg, "enabled", False):
        raise RpcHandlerError(
            "router.disabled",
            "Pilot Router is disabled or unavailable",
        )
    return cfg, store


async def _resolve_named_model(ctx: RpcContext, cfg: Any, model: str) -> RouterControlTarget:
    """Resolve a directly-named model, refusing anything the provider cannot run.

    Only the ACTIVE provider's catalog counts. Every turn goes through the one
    configured ``llm.provider`` — a tier's ``provider`` field is metadata, not a
    client selector — so a model belonging to some other provider in the catalog
    would be sent to the active one under a name it does not know. Rejecting it
    here turns a per-turn provider error into one legible message at the moment
    of choosing.
    """

    from agentos.gateway.rpc_models import active_provider_id, collect_models

    provider = active_provider_id(ctx)
    known = await collect_models(ctx, provider_filter=provider or None)
    match = next((m for m in known if str(m.get("id", "")) == model), None)
    if match is None:
        raise RpcHandlerError(
            "router.unknown_model",
            f"Model '{model}' is not available from the active provider"
            + (f" ({provider})" if provider else ""),
            details={"model": model, "provider": provider},
        )
    try:
        return resolve_router_control_model_target(cfg, model)
    except RouterControlValidationError as exc:
        raise RpcHandlerError(
            "router.unknown_model",
            str(exc),
            details={"model": model},
        ) from exc


@_d.method("router.hold.set", CONTROL_AND_CHANNEL)
async def _handle_router_hold_set(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Pin the route, by tier or by a directly-named model.

    ``tier`` picks one of the configured routes wholesale. ``model`` names a
    model outside that list; it rides on the default tier so the settings a tier
    carries and a model does not — thinking level, provider, the savings
    baseline — stay defined. Exactly one of the two is required.
    """

    key = _require_key(params)
    tier = str((params or {}).get("tier") or "").strip().lower()
    model = str((params or {}).get("model") or "").strip()
    if not tier and not model:
        raise ValueError("params.tier or params.model is required")
    if tier and model:
        raise ValueError("params.tier and params.model are mutually exclusive")

    cfg, store = _router_state(ctx)
    if model:
        target = await _resolve_named_model(ctx, cfg, model)
        evidence = f"user pinned model {model}"
    else:
        try:
            target = resolve_router_control_target(cfg, f"tier:{tier}")
        except RouterControlValidationError as exc:
            raise RpcHandlerError(
                "router.unknown_tier",
                f"Tier '{tier}' is not configured on the Pilot Router",
                details={"tier": tier},
            ) from exc
        evidence = f"user pinned tier {tier}"

    hold = store.set_hold(key, target, evidence=evidence, source=HOLD_SOURCE_USER)
    return _hold_payload(hold)


@_d.method("router.hold.get", CONTROL_ONLY)
async def _handle_router_hold_get(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Report the session's user pin plus the tiers that can be pinned.

    CONTROL_ONLY, unlike its set/clear siblings: those are projected from
    channel slash commands, while this exists to paint a control-surface
    picker. Channels have no such surface, and ``CHANNEL_RPC_METHODS`` is
    deliberately limited to methods a slash command actually reaches.

    Unlike set/clear this never raises when the router is off. The chat
    composer calls it on every mount and session switch purely to render its
    route picker, and a gateway with no router configured is an ordinary state
    to display (a disabled control), not an error worth a toast. Callers read
    ``enabled`` to tell "no router" from "router on, nothing pinned".

    ``hold`` reports only a USER pin. A hold the model installed for itself via
    ``router_control`` is transient in-turn state, not a setting the user chose,
    so surfacing it here would make the picker claim a selection nobody made.

    ``provider`` is the active provider id, so a client can list the models it
    is actually allowed to pin without having to know that routing runs through
    a single provider.

    ``imageTiers`` is a separate, DISPLAY-only list: the tiers an image turn can
    be routed to. It is not merged into ``tiers`` because these cannot be
    pinned — the router picks the vision route before holds are consulted — and
    a client that pinned one would install a hold that never takes effect. It
    exists so the picker can show what an image will be handed to before one is
    sent, instead of naming a tier the menu has no row for.
    """

    from agentos.gateway.rpc_models import active_provider_id

    key = _require_key(params)
    try:
        cfg, store = _router_state(ctx)
    except RpcHandlerError:
        return {"enabled": False, "hold": None, "tiers": [], "imageTiers": [], "provider": ""}

    hold = store.get_user_hold(key)
    return {
        "enabled": True,
        "provider": active_provider_id(ctx),
        "hold": _hold_payload(hold) if hold is not None else None,
        "tiers": [
            {
                "tier": target.tier,
                "model": target.model,
                "provider": target.provider,
                "description": target.description,
            }
            for target in build_router_control_targets(cfg)
            if target.target_type == "tier"
        ],
        "imageTiers": [
            {
                "tier": route.tier,
                "model": route.model,
                "provider": route.provider,
                "description": route.description,
            }
            for route in build_router_image_routes(cfg)
        ],
    }


@_d.method("router.hold.clear", CONTROL_AND_CHANNEL)
async def _handle_router_hold_clear(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    key = _require_key(params)
    _cfg, store = _router_state(ctx)
    cleared = store.clear(key)
    return {"cleared": cleared is not None}
