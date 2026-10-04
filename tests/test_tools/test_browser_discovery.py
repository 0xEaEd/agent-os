"""Finding ``agent-browser`` and hiding the tool when it cannot be found.

Two halves of one failure: a gateway launched by the desktop app (or launchd /
systemd) gets a bare PATH, so a binary installed with ``npm install -g`` under
nvm or Homebrew was never found; and the turn runner then offered the
``browser`` tool to the model anyway, so the first call failed with
"The browser engine is not available".
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_tools.browser_fake_engine import posix_only

from agentos.tools import agent_browser


@pytest.fixture(autouse=True)
def _reset() -> Any:
    agent_browser.reset_browser_runtime()
    yield
    agent_browser.reset_browser_runtime()


@pytest.fixture
def bare_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The environment a GUI-launched gateway sees: bare PATH, no nvm vars."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    for name in ("NVM_BIN", "NVM_DIR", "PNPM_HOME", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    # Keep the real machine's Homebrew / /usr/local out of the search.
    real = agent_browser._fallback_bin_dirs
    monkeypatch.setattr(
        agent_browser,
        "_fallback_bin_dirs",
        lambda: [d for d in real() if d.startswith(str(home))],
    )
    return home


def _install(bin_dir: Path) -> str:
    bin_dir.mkdir(parents=True, exist_ok=True)
    binary = bin_dir / "agent-browser"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return str(binary)


def _config(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"enabled": True, "binary_path": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


@posix_only
class TestFallbackDiscovery:
    def test_finds_an_nvm_install_outside_path(self, bare_env: Path) -> None:
        installed = _install(bare_env / ".nvm" / "versions" / "node" / "v24.16.0" / "bin")
        agent_browser.configure_browser(_config())
        assert agent_browser.resolve_binary() == installed
        assert agent_browser.browser_available() is True

    def test_newest_nvm_node_wins(self, bare_env: Path) -> None:
        versions = bare_env / ".nvm" / "versions" / "node"
        _install(versions / "v9.11.2" / "bin")
        _install(versions / "v24.1.0" / "bin")
        newest = _install(versions / "v24.16.0" / "bin")
        agent_browser.configure_browser(_config())
        assert agent_browser.resolve_binary() == newest

    def test_honors_nvm_dir(self, bare_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        nvm_dir = bare_env / "custom-nvm"
        installed = _install(nvm_dir / "versions" / "node" / "v22.0.0" / "bin")
        monkeypatch.setenv("NVM_DIR", str(nvm_dir))
        agent_browser.configure_browser(_config())
        assert agent_browser.resolve_binary() == installed

    def test_finds_an_npm_global_prefix_install(self, bare_env: Path) -> None:
        installed = _install(bare_env / ".npm-global" / "bin")
        agent_browser.configure_browser(_config())
        assert agent_browser.resolve_binary() == installed

    def test_path_wins_over_fallback(self, bare_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _install(bare_env / ".nvm" / "versions" / "node" / "v24.16.0" / "bin")
        on_path = _install(bare_env / "on-path")
        monkeypatch.setenv("PATH", str(bare_env / "on-path"))
        agent_browser.configure_browser(_config())
        assert agent_browser.resolve_binary() == on_path

    def test_unavailable_when_installed_nowhere(self, bare_env: Path) -> None:
        agent_browser.configure_browser(_config())
        assert agent_browser.resolve_binary() is None
        assert agent_browser.browser_available() is False


def _runner_tool_names() -> set[str]:
    from agentos.engine.runtime import TurnRunner
    from agentos.tools.registry import get_default_registry
    from agentos.tools.types import CallerKind, ToolContext

    runner = object.__new__(TurnRunner)
    runner._tool_registry = get_default_registry()
    ctx = ToolContext(caller_kind=CallerKind.WEB, agent_id="main")
    ctx = TurnRunner._apply_runtime_capability_denies(runner, ctx)
    tool_defs = runner._tool_registry.to_tool_definitions(ctx)
    tool_defs = TurnRunner._filter_tool_defs_by_capability(runner, tool_defs)
    return {tool.name for tool in tool_defs}


@posix_only
class TestTurnSurface:
    def test_turn_hides_browser_when_engine_missing(self, bare_env: Path) -> None:
        import agentos.tools.builtin.browser  # noqa: F401 - registers the tool

        agent_browser.configure_browser(_config())
        assert agent_browser.browser_available() is False
        assert "browser" not in _runner_tool_names()

    def test_turn_offers_browser_when_engine_found(self, bare_env: Path) -> None:
        import agentos.tools.builtin.browser  # noqa: F401 - registers the tool

        _install(bare_env / ".nvm" / "versions" / "node" / "v24.16.0" / "bin")
        agent_browser.configure_browser(_config())
        assert "browser" in _runner_tool_names()

    def test_turn_hides_browser_when_disabled(self, bare_env: Path) -> None:
        import agentos.tools.builtin.browser  # noqa: F401 - registers the tool

        _install(bare_env / ".nvm" / "versions" / "node" / "v24.16.0" / "bin")
        agent_browser.configure_browser(_config(enabled=False))
        assert "browser" not in _runner_tool_names()
