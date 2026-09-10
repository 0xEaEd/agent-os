"""Tests for the /c0-/c3 tier-hold slash commands and router.hold.* RPC."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agentos.engine.commands import DEFAULT_REGISTRY, Surface
from agentos.gateway.access import CHANNEL_RPC_METHODS
from agentos.gateway.rpc import RpcContext, get_dispatcher
from agentos.router_control import (
    HOLD_SOURCE_TOOL,
    HOLD_SOURCE_USER,
    RouterControlHoldStore,
    resolve_router_control_target,
)

_TIERS = ("c0", "c1", "c2", "c3")


def _router_cfg(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        tiers={
            "c0": {"model": "gemini-flash-lite", "provider": "openrouter"},
            "c3": {"model": "claude-opus-4-8", "provider": "openrouter"},
            "image_model": {
                "model": "gpt-4o",
                "provider": "openrouter",
                "supports_image": True,
                "image_only": True,
            },
        },
    )


def _ctx(
    cfg: SimpleNamespace | None = None,
    store: RouterControlHoldStore | None = None,
    *,
    models: list[dict] | None = None,
    provider: str = "opencap",
):
    runner = SimpleNamespace(
        router_control_hold_store=store if store is not None else RouterControlHoldStore(),
        router_control_config=cfg if cfg is not None else _router_cfg(),
    )
    ctx = RpcContext(conn_id="test", turn_runner=runner)
    ctx.config = SimpleNamespace(llm=SimpleNamespace(provider=provider))
    # `models.list` reads the catalog off the context; a stand-in keeps the
    # model-pin validation path exercised without a live provider.
    catalog_rows = models if models is not None else [{"id": "grok-5", "provider": provider}]
    ctx.model_catalog = SimpleNamespace(
        list_models=lambda: [
            SimpleNamespace(
                model_dump=lambda row=row: {
                    "model_id": row["id"],
                    "display_name": row["id"],
                    "provider": row["provider"],
                }
            )
            for row in catalog_rows
        ]
    )
    return ctx


async def _dispatch(method: str, params: dict, ctx: RpcContext):
    return await get_dispatcher().dispatch("r1", method, params, ctx)


# ---------------------------------------------------------------------------
# Registry: /c0-/c3 and /auto must be visible on web + channel surfaces
# ---------------------------------------------------------------------------


def test_tier_commands_registered_for_web_chat() -> None:
    names = {cmd.name for cmd in DEFAULT_REGISTRY.for_surface(Surface.WEB_CHAT)}
    for tier in _TIERS:
        assert f"/{tier}" in names, f"/{tier} missing from web_chat catalog"
    assert "/auto" in names


def test_tier_commands_registered_for_channel() -> None:
    names = {cmd.name for cmd in DEFAULT_REGISTRY.for_surface(Surface.CHANNEL)}
    for tier in _TIERS:
        assert f"/{tier}" in names, f"/{tier} missing from channel catalog"
    assert "/auto" in names


def test_tier_command_execution_maps_to_router_hold_rpc() -> None:
    cmd = DEFAULT_REGISTRY.find("/c3")
    assert cmd is not None
    execution = cmd.execution_for(Surface.WEB_CHAT)
    assert execution is not None
    assert execution.rpc_method == "router.hold.set"

    channel_exec = cmd.execution_for(Surface.CHANNEL)
    assert channel_exec is not None
    assert channel_exec.rpc_params is not None
    params = channel_exec.rpc_params(SimpleNamespace(session_key="agent:main:main"))
    assert params == {"key": "agent:main:main", "tier": "c3"}


def test_auto_command_execution_maps_to_router_hold_clear() -> None:
    cmd = DEFAULT_REGISTRY.find("/auto")
    assert cmd is not None
    execution = cmd.execution_for(Surface.WEB_CHAT)
    assert execution is not None
    assert execution.rpc_method == "router.hold.clear"


# ---------------------------------------------------------------------------
# RPC: router.hold.set
# ---------------------------------------------------------------------------


def test_router_hold_set_pins_tier() -> None:
    store = RouterControlHoldStore()
    ctx = _ctx(store=store)

    params = {"key": "agent:main:main", "tier": "c3"}
    result = asyncio.run(_dispatch("router.hold.set", params, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["tier"] == "c3"
    assert result.payload["model"] == "claude-opus-4-8"

    hold = store.get_valid("agent:main:main")
    assert hold is not None
    assert hold.tier == "c3"
    assert hold.target_id == "tier:c3"


def test_router_hold_set_rejects_unconfigured_tier() -> None:
    ctx = _ctx()

    params = {"key": "agent:main:main", "tier": "c9"}
    result = asyncio.run(_dispatch("router.hold.set", params, ctx))

    assert result.error is not None


def test_router_hold_set_rejects_disabled_router() -> None:
    ctx = _ctx(cfg=_router_cfg(enabled=False))

    params = {"key": "agent:main:main", "tier": "c3"}
    result = asyncio.run(_dispatch("router.hold.set", params, ctx))

    assert result.error is not None


def test_router_hold_set_requires_tier_param() -> None:
    ctx = _ctx()

    result = asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main"}, ctx))

    assert result.error is not None


# ---------------------------------------------------------------------------
# RPC: router.hold.clear
# ---------------------------------------------------------------------------


def test_router_hold_clear_removes_hold() -> None:
    store = RouterControlHoldStore()
    ctx = _ctx(store=store)
    asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main", "tier": "c3"}, ctx))
    assert store.get_valid("agent:main:main") is not None

    result = asyncio.run(_dispatch("router.hold.clear", {"key": "agent:main:main"}, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["cleared"] is True
    assert store.get_valid("agent:main:main") is None


def test_router_hold_clear_without_hold_reports_not_cleared() -> None:
    ctx = _ctx()

    result = asyncio.run(_dispatch("router.hold.clear", {"key": "agent:main:main"}, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["cleared"] is False


# ---------------------------------------------------------------------------
# Sticky user pins
# ---------------------------------------------------------------------------


def test_router_hold_set_marks_the_pin_as_a_sticky_user_hold() -> None:
    store = RouterControlHoldStore()
    ctx = _ctx(store=store)

    result = asyncio.run(
        _dispatch("router.hold.set", {"key": "agent:main:main", "tier": "c3"}, ctx)
    )

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["source"] == HOLD_SOURCE_USER
    assert result.payload["sticky"] is True
    hold = store.get_valid("agent:main:main")
    assert hold is not None
    assert hold.sticky is True


def test_user_pin_outlives_the_tool_hold_idle_ttl() -> None:
    """A user pin must not decay; only an explicit clear ends it."""

    store = RouterControlHoldStore()
    ctx = _ctx(store=store)
    asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main", "tier": "c3"}, ctx))

    # Far past the 600s idle TTL a tool hold would have expired at.
    later = 10_000_000.0
    assert store.get_valid("agent:main:main", now_monotonic=later) is not None


def test_model_installed_hold_still_expires_on_the_idle_ttl() -> None:
    """The tool path keeps its self-expiring lifetime — only users pin."""

    store = RouterControlHoldStore()
    target = resolve_router_control_target(_router_cfg(), "tier:c3")
    store.set_hold("agent:main:main", target, evidence="escalating", now_monotonic=0.0)

    hold = store.get_valid("agent:main:main", now_monotonic=0.0)
    assert hold is not None
    assert hold.source == HOLD_SOURCE_TOOL
    assert hold.sticky is False
    assert store.get_valid("agent:main:main", now_monotonic=601.0) is None


def test_sticky_hold_payload_is_valid_json() -> None:
    """`math.inf` would serialize as a bare `Infinity`, which JSON.parse rejects."""

    ctx = _ctx()

    result = asyncio.run(
        _dispatch("router.hold.set", {"key": "agent:main:main", "tier": "c3"}, ctx)
    )

    assert result.payload is not None
    assert result.payload["ttlSeconds"] is None
    encoded = json.dumps(result.payload)
    assert "Infinity" not in encoded


# ---------------------------------------------------------------------------
# Pinning a directly-named model
# ---------------------------------------------------------------------------


def test_router_hold_set_pins_a_named_model() -> None:
    store = RouterControlHoldStore()
    ctx = _ctx(store=store)

    result = asyncio.run(
        _dispatch("router.hold.set", {"key": "agent:main:main", "model": "grok-5"}, ctx)
    )

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["model"] == "grok-5"
    assert result.payload["targetType"] == "model"
    assert result.payload["sticky"] is True


def test_a_model_pin_rides_on_the_default_tier() -> None:
    """The router step indexes `tiers[hold.tier]`; an unhosted model would crash it."""

    store = RouterControlHoldStore()
    cfg = _router_cfg()
    cfg.default_tier = "c3"
    ctx = _ctx(cfg=cfg, store=store)

    asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main", "model": "grok-5"}, ctx))

    hold = store.get_valid("agent:main:main")
    assert hold is not None
    assert hold.tier == "c3"
    assert hold.tier in cfg.tiers
    assert hold.model == "grok-5"


def test_router_hold_set_rejects_a_model_the_active_provider_lacks() -> None:
    """Routing runs through one provider; an unreachable id must fail at choice time."""

    ctx = _ctx()

    result = asyncio.run(
        _dispatch("router.hold.set", {"key": "agent:main:main", "model": "no-such-model"}, ctx)
    )

    assert result.error is not None


def test_router_hold_set_rejects_a_model_from_another_provider() -> None:
    ctx = _ctx(
        models=[{"id": "grok-5", "provider": "opencap"}, {"id": "far-away", "provider": "ollama"}],
    )

    result = asyncio.run(
        _dispatch("router.hold.set", {"key": "agent:main:main", "model": "far-away"}, ctx)
    )

    assert result.error is not None


def test_router_hold_set_refuses_both_tier_and_model() -> None:
    ctx = _ctx()

    result = asyncio.run(
        _dispatch(
            "router.hold.set",
            {"key": "agent:main:main", "tier": "c3", "model": "grok-5"},
            ctx,
        )
    )

    assert result.error is not None


def test_router_hold_set_requires_one_of_tier_or_model() -> None:
    ctx = _ctx()

    result = asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main"}, ctx))

    assert result.error is not None


def test_use_command_sends_its_argument_as_the_model() -> None:
    cmd = DEFAULT_REGISTRY.find("/use")
    assert cmd is not None
    channel_exec = cmd.execution_for(Surface.CHANNEL)
    assert channel_exec is not None
    assert channel_exec.rpc_method == "router.hold.set"
    assert channel_exec.rpc_params is not None
    params = channel_exec.rpc_params(SimpleNamespace(session_key="agent:main:main"), "grok-5")
    assert params == {"key": "agent:main:main", "model": "grok-5"}


# ---------------------------------------------------------------------------
# RPC: router.hold.get
# ---------------------------------------------------------------------------


def test_router_hold_get_is_control_only() -> None:
    """The picker read is a control-surface concern, not a channel command."""

    assert "router.hold.get" not in CHANNEL_RPC_METHODS


def test_router_hold_get_distinguishes_a_model_pin_from_its_host_tier() -> None:
    store = RouterControlHoldStore()
    ctx = _ctx(store=store)
    asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main", "model": "grok-5"}, ctx))

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, ctx))

    assert result.payload is not None
    hold = result.payload["hold"]
    # The tier is reported (it is where the settings came from) but targetType
    # is what says the user chose a model, not that tier.
    assert hold["targetType"] == "model"
    assert hold["model"] == "grok-5"
    assert hold["tier"] in ("c0", "c3")


def test_router_hold_get_reports_the_active_provider() -> None:
    """Clients need it to list only the models that can actually be pinned."""

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, _ctx()))

    assert result.payload is not None
    assert result.payload["provider"] == "opencap"


def test_router_hold_get_reports_the_active_pin_and_pinnable_tiers() -> None:
    store = RouterControlHoldStore()
    ctx = _ctx(store=store)
    asyncio.run(_dispatch("router.hold.set", {"key": "agent:main:main", "tier": "c3"}, ctx))

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["enabled"] is True
    assert result.payload["hold"]["tier"] == "c3"
    assert {tier["tier"] for tier in result.payload["tiers"]} == {"c0", "c3"}


def test_router_hold_get_reports_image_tiers_apart_from_the_pinnable_ones() -> None:
    """The picker needs to show where an image goes before one is sent.

    Separate from ``tiers`` because an image tier cannot be pinned: the router
    picks the vision route before holds are consulted.
    """

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, _ctx()))

    assert result.error is None, result.error
    assert result.payload is not None
    assert {tier["tier"] for tier in result.payload["tiers"]} == {"c0", "c3"}
    assert result.payload["imageTiers"] == [
        {
            "tier": "image_model",
            "model": "gpt-4o",
            "provider": "openrouter",
            "description": None,
        }
    ]


def test_router_hold_get_refuses_to_pin_a_reported_image_tier() -> None:
    """Reporting the row must not make it pinnable through the same RPC."""

    ctx = _ctx()

    result = asyncio.run(
        _dispatch("router.hold.set", {"key": "agent:main:main", "tier": "image_model"}, ctx)
    )

    assert result.error is not None
    assert result.error.code == "router.unknown_tier"


def test_router_hold_get_reports_empty_image_tiers_when_none_support_images() -> None:
    cfg = SimpleNamespace(
        enabled=True,
        tiers={"c0": {"model": "gemini-flash-lite", "provider": "openrouter"}},
    )

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, _ctx(cfg=cfg)))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["imageTiers"] == []


def test_router_hold_get_reports_no_pin_when_unpinned() -> None:
    ctx = _ctx()

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["enabled"] is True
    assert result.payload["hold"] is None


def test_router_hold_get_hides_a_model_installed_hold() -> None:
    """A tool escalation is in-turn state, not a selection the user made."""

    store = RouterControlHoldStore()
    ctx = _ctx(store=store)
    target = resolve_router_control_target(_router_cfg(), "tier:c3")
    store.set_hold("agent:main:main", target, evidence="escalating")

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["hold"] is None


def test_router_hold_get_reports_disabled_router_without_erroring() -> None:
    """The composer probes this on every mount; "no router" is a state, not a fault."""

    ctx = _ctx(cfg=_router_cfg(enabled=False))

    result = asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, ctx))

    assert result.error is None, result.error
    assert result.payload is not None
    assert result.payload["enabled"] is False
    assert result.payload["hold"] is None
    assert result.payload["tiers"] == []


def test_router_hold_get_does_not_refresh_the_hold_it_reads() -> None:
    """Polling the picker must not keep a tool hold alive forever."""

    store = RouterControlHoldStore()
    ctx = _ctx(store=store)
    target = resolve_router_control_target(_router_cfg(), "tier:c3")
    store.set_hold("agent:main:main", target, evidence="escalating", now_monotonic=0.0)

    asyncio.run(_dispatch("router.hold.get", {"key": "agent:main:main"}, ctx))

    assert store.get_valid("agent:main:main", now_monotonic=601.0) is None
