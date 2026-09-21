"""A command's argument is parsed by the rule that matched the command.

``CommandRegistry.match`` finds the command word with ``split(maxsplit=1)`` --
any whitespace -- and ``gateway/channel_dispatch.py`` uses the same rule to
decide the turn is a command at all, so the message is intercepted and never
reaches the model. Extracting the argument with a literal ``" "`` disagreed with
both for a tab or a newline, and the two commands whose argument went missing
then did the opposite of what was asked: an empty ``/rename`` argument clears
the session name, and anything ``/plan`` does not read as ``off`` turns plan
mode on.

Every case below runs through the real ``dispatch`` and reads the params it
actually handed the RPC layer.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentos.channels.command_registry import DEFAULT_COMMAND_REGISTRY
from agentos.channels.types import IncomingMessage
from agentos.gateway.protocol import make_ok_res
from agentos.gateway.routing import build_channel_route_envelope


def _envelope(bot_username: str | None = None):
    metadata = {"bot_username": bot_username} if bot_username else {}
    msg = IncomingMessage(sender_id="u1", channel_id="c1", content="test", metadata=metadata)
    return build_channel_route_envelope(
        msg,
        session_key="agent:main:telegram:u1",
        session_prefix="telegram",
        agent_id="main",
    )


async def _dispatched_params(content: str, *, bot_username: str | None = None) -> dict[str, Any]:
    """Return the params ``dispatch`` sent for *content*."""
    captured: dict[str, Any] = {}

    class FakeDispatcher:
        async def dispatch(self, req_id: str, method: str, params: Any, _ctx: Any) -> Any:
            captured["method"] = method
            captured["params"] = params
            return make_ok_res(req_id, {})

    reply = await DEFAULT_COMMAND_REGISTRY.dispatch(
        envelope=_envelope(bot_username),
        message_content=content,
        rpc_dispatcher=FakeDispatcher(),
        context_factory=lambda _envelope: object(),
    )
    assert reply is not None, f"{content!r} was not dispatched as a command"
    return captured["params"]


@pytest.mark.parametrize("separator", [" ", "\t", "\n", "\r\n", "  ", " \t "])
async def test_rename_keeps_the_whole_name_whatever_separates_it(separator: str) -> None:
    params = await _dispatched_params(f"/rename{separator}My Project")

    assert params["name"] == "My Project"


@pytest.mark.parametrize("separator", [" ", "\t", "\n"])
async def test_plan_off_still_leaves_plan_mode(separator: str) -> None:
    params = await _dispatched_params(f"/plan{separator}off")

    assert params["mode"] == "off"


@pytest.mark.parametrize("separator", [" ", "\t", "\n"])
async def test_use_carries_the_model_id(separator: str) -> None:
    params = await _dispatched_params(f"/use{separator}claude-opus-5")

    assert params["model"] == "claude-opus-5"


async def test_a_bare_rename_still_clears_the_name() -> None:
    # The documented behaviour for a genuinely absent argument, which this fix
    # must not take away: sessions.rename reads "" as "clear the custom name".
    params = await _dispatched_params("/rename")

    assert params["name"] == ""


async def test_a_bare_plan_still_turns_plan_mode_on() -> None:
    params = await _dispatched_params("/plan")

    assert params["mode"] == "on"


async def test_an_argument_with_inner_whitespace_is_preserved() -> None:
    # Only the first whitespace run is the separator; the rest belongs to the
    # argument, and sessions.rename is what normalizes it.
    params = await _dispatched_params("/rename\tQ3\tReport 2026")

    assert params["name"] == "Q3\tReport 2026"


async def test_a_multiline_argument_keeps_its_later_lines() -> None:
    params = await _dispatched_params("/rename\nQ3 Report\nsecond line")

    assert params["name"] == "Q3 Report\nsecond line"


async def test_the_bot_suffix_form_still_resolves_its_argument() -> None:
    # `/cmd@botname` is how Telegram addresses a bot in a group. The head is
    # discarded either way, so this path is unchanged -- guarded so it stays so.
    params = await _dispatched_params("/rename@mybot My Project", bot_username="mybot")

    assert params["name"] == "My Project"


@pytest.mark.parametrize("content", ["/rename\tMy Project", "/plan\toff", "/use\nclaude-opus-5"])
def test_the_matcher_accepts_what_the_argument_parse_must_handle(content: str) -> None:
    # The two halves have to agree: the gate consumes these as commands, so the
    # turn never reaches the model and a dropped argument has no fallback.
    assert DEFAULT_COMMAND_REGISTRY.match(_envelope(), content) is not None
