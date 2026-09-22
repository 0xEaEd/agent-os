"""``console.print`` parses ``[...]`` as Rich markup; the standalone-mode
``/model`` and ``/use`` slash commands used to interpolate the argument the
user just typed raw. This mirrors ``test_slash_gateway_rich_markup.py``'s
``/model``/``/use`` coverage for the gateway-mode twin of the same handler.

Two shapes matter: a ``[/]``-style closing tag raises
``rich.errors.MarkupError`` and crashes the session; an ordinary-looking
lowercase word like ``[redacted]`` is parsed as an (unregistered) style tag
and silently dropped from the output instead of raising.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentos.cli.chat.session_state import ChatSessionState
from agentos.cli.tui.adapters.slash_standalone import (
    StandaloneSlashContext,
    handle_standalone_slash_command,
)


class _FakeHoldStore:
    def __init__(self) -> None:
        self.holds: list[tuple[str, object]] = []

    def set_hold(self, session_key: str, target: object, *, evidence: str, source: str) -> None:
        self.holds.append((session_key, target))

    def clear(self, session_key: str) -> object | None:
        return None


def _context() -> StandaloneSlashContext:
    state = ChatSessionState(session_key="agent:main:cli:test", model="openai/test")
    turn_runner = SimpleNamespace(
        router_control_hold_store=_FakeHoldStore(),
        router_control_config=SimpleNamespace(enabled=True),
    )
    return StandaloneSlashContext(
        state=state,
        session_key="agent:main:cli:test",
        model="openai/test",
        tool_ctx=object(),
        slash_services=SimpleNamespace(),
        turn_runner=turn_runner,
        build_tool_ctx=lambda _s: object(),
        replace_session=lambda **_kwargs: None,
    )


@pytest.mark.asyncio
async def test_model_command_with_a_closing_tag_does_not_crash(capsys) -> None:
    context = _context()

    handled = await handle_standalone_slash_command("/model gpt[/]4", context)

    assert handled is True
    assert context.state.model == "gpt[/]4"
    out = capsys.readouterr().out
    assert "gpt[/]4" in out


@pytest.mark.asyncio
async def test_model_command_with_an_unregistered_style_name_is_not_swallowed(capsys) -> None:
    context = _context()

    handled = await handle_standalone_slash_command("/model sk-[redacted]-live", context)

    assert handled is True
    out = capsys.readouterr().out
    assert "sk-[redacted]-live" in out


@pytest.mark.asyncio
async def test_bare_model_query_with_a_closing_tag_does_not_crash(capsys) -> None:
    context = _context()
    context.state.model = "gpt[/]4"

    handled = await handle_standalone_slash_command("/model", context)

    assert handled is True
    out = capsys.readouterr().out
    assert "gpt[/]4" in out


@pytest.mark.asyncio
async def test_bare_model_query_with_an_unregistered_style_name_is_not_swallowed(capsys) -> None:
    context = _context()
    context.state.model = "sk-[redacted]-live"

    handled = await handle_standalone_slash_command("/model", context)

    assert handled is True
    out = capsys.readouterr().out
    assert "sk-[redacted]-live" in out


@pytest.mark.asyncio
async def test_use_command_with_a_closing_tag_does_not_crash(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentos.router_control as router_control

    monkeypatch.setattr(
        router_control,
        "resolve_router_control_model_target",
        lambda _cfg, model: SimpleNamespace(model=model),
    )
    context = _context()

    handled = await handle_standalone_slash_command("/use gpt[/]4", context)

    assert handled is True
    out = capsys.readouterr().out
    assert "gpt[/]4" in out


@pytest.mark.asyncio
async def test_use_command_with_an_unregistered_style_name_is_not_swallowed(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agentos.router_control as router_control

    monkeypatch.setattr(
        router_control,
        "resolve_router_control_model_target",
        lambda _cfg, model: SimpleNamespace(model=model),
    )
    context = _context()

    handled = await handle_standalone_slash_command("/use sk-[redacted]-live", context)

    assert handled is True
    out = capsys.readouterr().out
    assert "sk-[redacted]-live" in out


@pytest.mark.asyncio
async def test_model_command_without_brackets_still_renders(capsys) -> None:
    """Positive control: proves the render path is actually live."""
    context = _context()

    handled = await handle_standalone_slash_command("/model gpt-4", context)

    assert handled is True
    out = capsys.readouterr().out
    assert "gpt-4" in out
