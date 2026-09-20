"""The Discord channel-context caches must stay bounded (#2088).

``_channel_types`` and ``_thread_parent_channels`` are written on every
CHANNEL_*/THREAD_*/GUILD_CREATE/THREAD_LIST_SYNC gateway event and were plain
dicts with no eviction, so an active guild grew them for the life of the
process. They are now ``BoundedRegistry`` instances with a channel-count
ceiling, and a lookup on the message path counts as a use so live channels
survive churn.
"""

from __future__ import annotations

from agentos.channels import discord as discord_mod
from agentos.channels.discord import DiscordChannel, DiscordChannelConfig
from agentos.util.bounded_registry import BoundedRegistry


def _channel(monkeypatch, cap: int) -> DiscordChannel:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(discord_mod, "_MAX_CACHED_CHANNEL_CONTEXTS", cap)
    return DiscordChannel(DiscordChannelConfig(token="token"))


def _thread(idx: int) -> dict[str, object]:
    return {"id": f"thread-{idx}", "type": 11, "parent_id": f"parent-{idx}"}


async def test_channel_events_evict_oldest_entries_past_the_cap(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    channel = _channel(monkeypatch, cap=3)

    for idx in range(5):
        await channel._handle_dispatch("THREAD_CREATE", _thread(idx))

    assert list(channel._channel_types) == ["thread-2", "thread-3", "thread-4"]
    assert list(channel._thread_parent_channels) == ["thread-2", "thread-3", "thread-4"]


async def test_guild_create_and_thread_list_sync_stay_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    channel = _channel(monkeypatch, cap=4)

    await channel._handle_dispatch(
        "GUILD_CREATE",
        {
            "id": "guild-1",
            "channels": [{"id": f"chan-{i}", "type": 0} for i in range(10)],
            "threads": [_thread(i) for i in range(10)],
        },
    )
    await channel._handle_dispatch(
        "THREAD_LIST_SYNC",
        {"guild_id": "guild-1", "threads": [_thread(i) for i in range(10, 20)]},
    )

    assert len(channel._channel_types) == 4
    assert len(channel._thread_parent_channels) == 4
    assert list(channel._channel_types) == ["thread-16", "thread-17", "thread-18", "thread-19"]


async def test_update_of_a_known_channel_refreshes_it_instead_of_growing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    channel = _channel(monkeypatch, cap=2)

    await channel._handle_dispatch("THREAD_CREATE", _thread(0))
    await channel._handle_dispatch("THREAD_CREATE", _thread(1))
    await channel._handle_dispatch("THREAD_UPDATE", _thread(0))
    await channel._handle_dispatch("THREAD_CREATE", _thread(2))

    assert list(channel._channel_types) == ["thread-0", "thread-2"]
    assert list(channel._thread_parent_channels) == ["thread-0", "thread-2"]


async def test_message_lookup_marks_the_channel_recently_used(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    channel = _channel(monkeypatch, cap=2)

    await channel._handle_dispatch("THREAD_CREATE", _thread(0))
    await channel._handle_dispatch("THREAD_CREATE", _thread(1))
    await channel._handle_dispatch(
        "MESSAGE_CREATE",
        {
            "id": "msg-1",
            "channel_id": "thread-0",
            "guild_id": "guild-1",
            "author": {"id": "user-1"},
            "content": "still active",
        },
    )
    await channel._handle_dispatch("THREAD_CREATE", _thread(2))

    msg = await channel.receive()
    assert msg.metadata["conversation_kind"] == "thread"
    assert msg.metadata["native_parent_channel_id"] == "parent-0"
    assert list(channel._channel_types) == ["thread-0", "thread-2"]
    assert list(channel._thread_parent_channels) == ["thread-0", "thread-2"]


def test_caches_are_bounded_registries_with_the_channel_ceiling() -> None:
    channel = DiscordChannel(DiscordChannelConfig(token="token"))

    assert isinstance(channel._channel_types, BoundedRegistry)
    assert isinstance(channel._thread_parent_channels, BoundedRegistry)
    assert channel._channel_types.max_entries == discord_mod._MAX_CACHED_CHANNEL_CONTEXTS
    assert channel._channel_types.ttl_seconds is None
    assert discord_mod._MAX_CACHED_CHANNEL_CONTEXTS > 0
