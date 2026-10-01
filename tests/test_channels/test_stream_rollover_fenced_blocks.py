"""Issue #3505: a rollover inside a fenced code block ate the source.

``split_text_for_limit`` balances a block across a split by closing it on the
head (``head = segment[:cut] + "\\n```"``) and reopening it on the tail
(``tail = reopen + segment[cut:].lstrip("\\n")``). Every streaming adapter
advanced its watermark by ``len(head)`` and then re-sliced the accumulated
text from it, so each rollover skipped four source characters and dropped
the reopening fence: ``x_105`` arrived as ``05``, and the messages after it
rendered as prose because their fences no longer matched.

Slack was fixed in #3304; Discord, Telegram and MS Teams carried the same
arithmetic. They now share ``split_stream_segment``, which reports what the
head really consumed and the reopener the next message must carry. Telegram's
flood fallback, which abandons the open message for a new one, re-derives that
reopener from what the open message shows (``open_fence_reopener``).
"""

from __future__ import annotations

import re
import sys
from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from test_discord_streaming_chunking import _streaming_channel
from test_telegram_streaming import _install_fake_api

from agentos.channels._util import (
    open_fence_reopener,
    split_stream_segment,
    split_text_for_limit,
)
from agentos.channels.telegram import (
    TelegramChannel,
    TelegramChannelConfig,
    TelegramFloodError,
)

#: A closing fence glued to the reopening fence of the next message.
JUNCTION = re.compile(r"\n```(?=```)```[^\n]*\n")


def code_block(lines: int, marker: str = "```") -> str:
    body = "".join(f"x_{i} = compute({i})\n" for i in range(lines))
    return f"{marker}python\n" + body + marker


def chunks_of(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


async def stream(*pieces: str) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


def assert_carries_the_source(messages: list[str], source: str) -> None:
    """No character is lost, and each message's fences are balanced.

    Line breaks are counted separately: the splitter drops the one at a cut
    it made on a line boundary, since the closing fence supplies one, so at
    most one per junction may be missing. The bug deleted four characters
    mid-identifier, which no allowance covers.
    """
    rejoined = JUNCTION.sub("", "".join(messages))
    assert rejoined.replace("\n", "") == source.replace("\n", "")
    dropped = source.count("\n") - rejoined.count("\n")
    assert 0 <= dropped <= len(messages) - 1, f"{dropped} line breaks lost"
    for index, text in enumerate(messages):
        assert text.count("```") % 2 == 0, f"message {index + 1} has an unbalanced fence"


# ── the shared helper ──────────────────────────────────────────────────────


@pytest.mark.parametrize("marker", ["```", "~~~"])
def test_the_helper_reports_what_the_head_consumed(marker: str) -> None:
    source = code_block(400, marker)

    head, consumed, reopener = split_stream_segment(source, 2000)
    plain_head, plain_tail = split_text_for_limit(source, 2000)

    assert head == plain_head, "the text sent is unchanged"
    assert consumed < len(head), "the head carries a closer the source never had"
    assert reopener == f"{marker}python\n", "the reopener is the fence line, no source"
    assert reopener + source[consumed:] == plain_tail, "the rest is the source plus the reopener"


def test_the_helper_is_a_plain_split_when_no_fence_is_rebalanced() -> None:
    source = "word " * 2000

    head, consumed, reopener = split_stream_segment(source, 2000)

    assert consumed == len(head)
    assert reopener == ""
    assert head + source[consumed:] == source


def test_the_helper_respects_a_measure() -> None:
    """Telegram and Teams measure the rendered form, not the source."""
    source = "a" * 500

    head, consumed, _ = split_stream_segment(source, 100, measure=lambda text: len(text) * 2)

    assert len(head) <= 50
    assert consumed == len(head)


def test_the_reopener_follows_the_fence_left_open() -> None:
    assert open_fence_reopener("Intro.\n\n```python\nx = 1\n") == "```python\n"
    assert open_fence_reopener("~~~sh\nls\n") == "~~~sh\n"
    assert open_fence_reopener("```python\nx = 1\n```\nProse.\n") == ""
    assert open_fence_reopener("Prose only.\n") == ""


# ── Discord ────────────────────────────────────────────────────────────────


def _discord() -> tuple[Any, list[tuple[str, str, dict[str, Any]]]]:
    return _streaming_channel()


def _discord_messages(calls: list[tuple[str, str, dict[str, Any]]]) -> list[str]:
    """Each message's final text, in the order Discord created them."""
    order: list[str] = []
    for method, _url, payload in calls:
        if method == "POST":
            order.append(payload["content"])
        elif order:
            order[-1] = payload["content"]
    return order


async def test_discord_keeps_a_fenced_block_whole() -> None:
    source = code_block(400)
    channel, calls = _discord()

    await channel.send_streaming(
        stream(*chunks_of(source, 97)), channel_id="chan-1", update_interval_ms=0
    )

    messages = _discord_messages(calls)
    assert len(messages) >= 2, "the block must actually roll over"
    for _method, _url, payload in calls:
        assert len(payload["content"]) <= 2000
    assert_carries_the_source(messages, source)


async def test_discord_prose_is_unaffected() -> None:
    source = "".join(f"line {i:04d}: {'y' * 40}\n" for i in range(200))
    channel, calls = _discord()

    await channel.send_streaming(
        stream(*chunks_of(source, 97)), channel_id="chan-1", update_interval_ms=0
    )

    assert "".join(_discord_messages(calls)) == source


# ── Telegram ───────────────────────────────────────────────────────────────


def _telegram(fail: Any = None) -> tuple[Any, list[tuple[str, dict[str, Any]]]]:
    channel = TelegramChannel(TelegramChannelConfig(token="t", default_chat_id="-100123"))
    calls = _install_fake_api(channel, fail=fail)
    return channel, calls


def _telegram_messages(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    order: list[str] = []
    for method, payload in calls:
        if method == "sendMessage":
            order.append(str(payload.get("text", "")))
        elif method == "editMessageText" and order:
            order[-1] = str(payload.get("text", ""))
    return order


async def test_telegram_keeps_a_fenced_block_whole() -> None:
    """Telegram sends rendered HTML and measures it, so the cut lands
    elsewhere than Discord's; the watermark arithmetic is what is under test.
    The source is checked line by line, since the payload is ``<pre><code>``
    rather than the markdown that went in."""
    lines = 400
    source = code_block(lines)
    channel, calls = _telegram()

    await channel.send_streaming(
        stream(*chunks_of(source, 97)), chat_id="-100123", update_interval_ms=0
    )

    messages = _telegram_messages(calls)
    assert len(messages) >= 2, "the block must actually roll over"
    rendered = "".join(messages)
    for index in range(lines):
        statement = f"x_{index} = compute({index})"
        assert rendered.count(statement) == 1, f"line {index} lost or duplicated"
    for index, text in enumerate(messages):
        assert text.count("<pre>") == text.count("</pre>"), f"message {index + 1} is unbalanced"


def _flood_edits_carrying(trigger: str) -> Any:
    """Rate-limit every edit that carries *trigger*, so the stream falls back."""

    def fail(method: str, payload: dict[str, Any]) -> None:
        if method == "editMessageText" and trigger in str(payload.get("text", "")):
            raise TelegramFloodError("Too Many Requests", retry_after=0.0)

    return fail


async def test_telegram_flood_fallback_after_the_block_closed_posts_prose() -> None:
    """The open message continued a code block a rollover closed, and the block
    then closed inside it. When the final edit is rate-limited, the rest goes
    out as a new message -- which must not start with the reopener the open
    message began with, or the prose after the block renders as code."""
    block = code_block(400)
    channel, calls = _telegram(fail=_flood_edits_carrying("Afterword"))

    await channel.send_streaming(
        stream(block, "\n\nThe block is done.\n", "Afterword in plain prose.\n"),
        chat_id="-100123",
        update_interval_ms=0,
    )

    sent = [str(p["text"]) for method, p in calls if method == "sendMessage"]
    assert len(sent) >= 3, "the block must roll over and the fallback must post"
    assert "Afterword in plain prose." in sent[-1]
    assert "<pre>" not in sent[-1] and "<code>" not in sent[-1], sent[-1]


async def test_telegram_flood_fallback_inside_a_block_reopens_it() -> None:
    """The other way round: the open message ends inside a block it opened
    itself, so the fallback message has to reopen that block."""
    channel, calls = _telegram(fail=_flood_edits_carrying("y = 2"))

    await channel.send_streaming(
        stream("Intro.\n\n```python\nx = 1\n", "y = 2\n```\n"),
        chat_id="-100123",
        update_interval_ms=0,
    )

    sent = [str(p["text"]) for method, p in calls if method == "sendMessage"]
    assert len(sent) == 2
    assert "y = 2" in sent[-1]
    assert sent[-1].startswith('<pre><code class="language-python">y = 2'), sent[-1]
    assert sent[-1].count("<pre>") == 1, "a stray fence opened a block after the code"


# ── MS Teams ───────────────────────────────────────────────────────────────


@pytest.fixture
def botbuilder_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """Satisfy ``send_streaming``'s lazy ``botbuilder.schema`` import.

    The same stub ``test_msteams_streaming_closures`` installs, through
    ``monkeypatch`` so it does not leak into later tests.
    """
    if "botbuilder.schema" in sys.modules:
        return

    class _Activity:
        def __init__(self, **fields: Any) -> None:
            self.__dict__.update(fields)

    package = ModuleType("botbuilder")
    schema = ModuleType("botbuilder.schema")
    schema.Activity = _Activity  # type: ignore[attr-defined]
    package.schema = schema  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "botbuilder", package)
    monkeypatch.setitem(sys.modules, "botbuilder.schema", schema)


class _TeamsRecorder:
    """Records each Teams message by id, so an edit lands on its own message.

    The harness in ``test_msteams_streaming_closures`` keeps sends and edits
    in two flat lists, which cannot say *which* message an edit changed --
    and with the edit interval at zero every message is edited many times.
    """

    def __init__(self) -> None:
        self.messages: dict[str, str] = {}
        self.order: list[str] = []

    class _Turn:
        def __init__(self, outer: _TeamsRecorder) -> None:
            self._outer = outer

        async def send_activity(self, payload: Any) -> Any:
            outer = self._outer
            message_id = f"msg-{len(outer.order) + 1}"
            outer.order.append(message_id)
            outer.messages[message_id] = str(payload)
            return SimpleNamespace(id=message_id)

        async def update_activity(self, activity: Any) -> Any:
            outer = self._outer
            message_id = str(
                getattr(activity, "id", "") or (outer.order[-1] if outer.order else "")
            )
            outer.messages[message_id] = str(getattr(activity, "text", ""))
            return None

    async def continue_conversation(self, ref: Any, callback: Any, bot_id: Any = None) -> None:
        await callback(self._Turn(self))

    def texts(self) -> list[str]:
        return [self.messages[message_id] for message_id in self.order]


def _teams(monkeypatch: pytest.MonkeyPatch, limit: int) -> tuple[Any, _TeamsRecorder]:
    """The real adapter, with the message cap lowered and edits unthrottled."""
    from agentos.channels import msteams as module

    monkeypatch.setattr(module, "_MSTEAMS_MESSAGE_TEXT_LIMIT", limit)
    recorder = _TeamsRecorder()
    channel = module.MSTeamsChannel(config=module.MSTeamsChannelConfig(name="msteams"))
    channel._references["conv-1"] = SimpleNamespace()
    channel._adapter = recorder
    # Edit on every chunk: the corruption shows on the flush *after* a
    # rollover, when the watermark is used to re-slice, so a run that only
    # flushes once at the end never reaches it.
    channel.config.edit_interval_s = 0.0
    return channel, recorder


async def test_teams_keeps_a_fenced_block_whole(
    monkeypatch: pytest.MonkeyPatch, botbuilder_schema: None
) -> None:
    source = code_block(120)
    channel, recorder = _teams(monkeypatch, limit=900)

    await channel.send_streaming(stream(*chunks_of(source, 97)), reply_to="conv-1")

    messages = recorder.texts()
    assert len(messages) >= 2, "the block must actually roll over"
    assert_carries_the_source(messages, source)
