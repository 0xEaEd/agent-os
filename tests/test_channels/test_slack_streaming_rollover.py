"""Issue #3068: ``SlackChannel.send_streaming`` had no message-length rollover.

``send()`` splits a long reply at ``_SLACK_MESSAGE_TEXT_LIMIT``; streaming
did not. Every ``chat.update`` carried the whole accumulated text, so a
streamed reply past Slack's 40000-character cap was rejected with
``msg_too_long`` part way through. Telegram, Discord and Teams freeze the
open message at the cap and roll the rest into a new one; Slack now does the
same, and the tests below pin that no API payload ever exceeds the limit and
that the text the user sees is the whole stream, in order, exactly once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from agentos.channels.slack import _SLACK_MESSAGE_TEXT_LIMIT, SlackChannel

LIMIT = _SLACK_MESSAGE_TEXT_LIMIT


class FakeSlack:
    """Records every post/update and renders what each message shows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: dict[str, str] = {}
        self._request = httpx.Request("POST", "https://slack.test/api")

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        payload = dict(kwargs.get("json", {}))
        self.calls.append((url, payload))
        if url == "/chat.postMessage":
            ts = f"1.{len(self.calls)}"
            self.messages[ts] = payload["text"]
            return httpx.Response(200, json={"ok": True, "ts": ts}, request=self._request)
        if url == "/chat.update":
            self.messages[payload["ts"]] = payload["text"]
            return httpx.Response(200, json={"ok": True}, request=self._request)
        raise AssertionError(url)

    @property
    def rendered(self) -> str:
        """Every message's final text, in posting order."""
        return "".join(self.messages[ts] for ts in sorted(self.messages, key=self._order))

    @staticmethod
    def _order(ts: str) -> int:
        return int(ts.split(".")[1])

    def texts(self, url: str) -> list[str]:
        return [payload["text"] for called, payload in self.calls if called == url]


def _channel(fake: FakeSlack) -> SlackChannel:
    channel = SlackChannel(token="xoxb-test", slack_channel_id="C123")
    client = AsyncMock()
    client.post = fake.post
    channel._client = client
    return channel


async def _stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


def _assert_within_limit(fake: FakeSlack) -> None:
    for url, payload in fake.calls:
        assert len(payload["text"]) <= LIMIT, f"{url} carried {len(payload['text'])} > {LIMIT}"


# ── the report ──────────────────────────────────────────────────────────────


async def test_the_issues_exact_reproduction() -> None:
    """``x``×1000 then ``y``×45000 used to send one 46000-character update."""
    fake = FakeSlack()

    ts = await _channel(fake).send_streaming(
        _stream("x" * 1000, "y" * 45000), update_interval_ms=10
    )

    _assert_within_limit(fake)
    assert [url for url, _ in fake.calls] == [
        "/chat.postMessage",
        "/chat.update",
        "/chat.postMessage",
    ]
    assert fake.rendered == "x" * 1000 + "y" * 45000
    assert ts == "1.3", "the ts returned is the last message opened"


async def test_no_update_ever_exceeds_the_limit_over_a_long_stream() -> None:
    """Many small chunks well past the cap: every payload stays under it and
    the messages, read in order, are the stream exactly once."""
    fake = FakeSlack()
    chunks = [f"line {i:05d}: " + "z" * 90 + "\n" for i in range(1200)]  # ~120k chars

    await _channel(fake).send_streaming(_stream(*chunks), update_interval_ms=0)

    _assert_within_limit(fake)
    assert fake.rendered == "".join(chunks)
    assert len(fake.messages) >= 4


async def test_a_frozen_message_is_never_edited_again() -> None:
    """Once a message rolls over, later updates go to the new one only."""
    fake = FakeSlack()

    await _channel(fake).send_streaming(
        _stream("a" * 1000, "b" * (LIMIT + 5000), "c" * 2000), update_interval_ms=0
    )

    updates = [(p["ts"], p["text"]) for u, p in fake.calls if u == "/chat.update"]
    first_ts = "1.1"
    edits_to_first = [text for ts, text in updates if ts == first_ts]
    later_edits = [ts for ts, _ in updates if ts != first_ts]
    assert edits_to_first, "the first message was edited up to the cap"
    assert all(len(t) <= LIMIT for t in edits_to_first)
    # After rollover the first message's text stops changing.
    frozen = fake.messages[first_ts]
    assert frozen == edits_to_first[-1]
    assert later_edits, "the rolled-over message receives the later chunks"
    assert fake.rendered == "a" * 1000 + "b" * (LIMIT + 5000) + "c" * 2000


async def test_the_split_lands_on_a_line_boundary_not_mid_word() -> None:
    """The shared splitter is used, so a rollover does not cut a word."""
    fake = FakeSlack()
    lines = ["word " * 20 + "\n"] * 800  # ~81k chars

    await _channel(fake).send_streaming(_stream(*lines), update_interval_ms=0)

    _assert_within_limit(fake)
    for text in fake.messages.values():
        assert text.endswith("\n") or text == "".join(lines)[-len(text) :]
    assert fake.rendered == "".join(lines)


async def test_a_single_oversized_first_chunk_opens_several_messages() -> None:
    """The very first flush can already exceed the cap; it is posted as
    consecutive messages, none over the limit, and the return is the last."""
    fake = FakeSlack()

    ts = await _channel(fake).send_streaming(_stream("q" * (LIMIT * 2 + 10)), update_interval_ms=0)

    _assert_within_limit(fake)
    assert all(url == "/chat.postMessage" for url, _ in fake.calls)
    assert len(fake.calls) == 3
    assert fake.rendered == "q" * (LIMIT * 2 + 10)
    assert ts == "1.3"


async def test_rollover_keeps_the_thread() -> None:
    """Every message of one streamed reply belongs in the same thread, as
    ``send()`` already guarantees for its segments."""
    fake = FakeSlack()

    await _channel(fake).send_streaming(
        _stream("a" * 1000, "b" * (LIMIT + 100)),
        channel="C9",
        thread_ts="1700.42",
        update_interval_ms=0,
    )

    posts = [p for u, p in fake.calls if u == "/chat.postMessage"]
    assert len(posts) == 2
    assert all(p["thread_ts"] == "1700.42" and p["channel"] == "C9" for p in posts)


async def test_content_exactly_at_the_limit_does_not_roll_over() -> None:
    fake = FakeSlack()

    await _channel(fake).send_streaming(
        _stream("a" * 100, "b" * (LIMIT - 100)), update_interval_ms=0
    )

    assert fake.texts("/chat.postMessage") == ["a" * 100]
    assert fake.messages == {"1.1": "a" * 100 + "b" * (LIMIT - 100)}


# ── what must not change ────────────────────────────────────────────────────


async def test_a_short_stream_is_one_message_edited_in_place() -> None:
    fake = FakeSlack()

    ts = await _channel(fake).send_streaming(_stream("hello ", "world"), update_interval_ms=0)

    assert fake.texts("/chat.postMessage") == ["hello "]
    assert fake.texts("/chat.update") == ["hello world"]
    assert fake.messages == {"1.1": "hello world"}
    assert ts == "1.1"


async def test_the_final_flush_is_skipped_when_nothing_arrived_after_the_last_edit() -> None:
    """Mirrors Telegram and Discord: no ``chat.update`` that repeats the last
    one verbatim at end of stream."""
    fake = FakeSlack()

    await _channel(fake).send_streaming(_stream("a", "b"), update_interval_ms=0)

    assert fake.texts("/chat.update") == ["ab"]


async def test_an_empty_stream_posts_nothing_and_returns_none() -> None:
    fake = FakeSlack()

    ts = await _channel(fake).send_streaming(_stream(), update_interval_ms=0)

    assert fake.calls == []
    assert ts is None


async def test_a_slack_level_error_on_a_rollover_post_still_surfaces() -> None:
    """The rolled-over ``chat.postMessage`` gets the same ``ok: false``
    handling as the opening one."""
    fake = FakeSlack()
    real_post = fake.post

    async def flaky_post(url: str, **kwargs: Any) -> httpx.Response:
        if url == "/chat.postMessage" and any(u == "/chat.update" for u, _ in fake.calls):
            fake.calls.append((url, dict(kwargs.get("json", {}))))
            return httpx.Response(
                200, json={"ok": False, "error": "msg_too_long"}, request=fake._request
            )
        return await real_post(url, **kwargs)

    channel = _channel(fake)
    channel._client.post = flaky_post

    with pytest.raises(RuntimeError, match="msg_too_long"):
        await channel.send_streaming(_stream("a" * 1000, "b" * (LIMIT + 100)), update_interval_ms=0)
