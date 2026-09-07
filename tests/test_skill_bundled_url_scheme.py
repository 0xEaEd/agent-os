"""Bundled skill scripts must refuse non-``http(s)`` endpoints (Issue #1065).

``urllib.request.urlopen`` is scheme-agnostic: it speaks ``file:``, ``ftp:``
and ``data:`` as happily as HTTP. Every place a bundled script takes an
endpoint from argv or the environment is therefore an arbitrary local-file
read — ``--url file:///etc/passwd`` on a cron watcher reports the file's
contents back to the agent on every run, and ``--rpc file://…`` does the same
through a JSON-RPC client. Each entry point validates the scheme up front, the
way ``robinhood-chain-stocks`` already does.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED = _REPO_ROOT / "src" / "agentos" / "skills" / "bundled"
_WATCHERS = _BUNDLED / "cron-watchers" / "scripts"

#: Every scheme that reaches a filesystem, a non-HTTP service, or inline data.
_REJECTED = [
    "file:///etc/passwd",
    "file:///C:/Windows/System32/drivers/etc/hosts",
    "ftp://example.com/secret",
    "data:application/json,[]",
    "/etc/passwd",
    "",
    "   ",
    "http://",
]


def _load_flat(path: Path, name: str) -> ModuleType:
    """Import a flat script file directly, with its own directory importable.

    Loaded in-process rather than run through ``subprocess``: the scripts are
    not on ``PATH`` and spawning an interpreter per case is both slow and
    platform-sensitive on Windows CI.
    """
    entry = str(path.parent)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if added:
            sys.path.remove(entry)


def _load_package(package: str, scripts_dir: Path, module: str) -> ModuleType:
    entry = str(scripts_dir)
    added = entry not in sys.path
    if added:
        sys.path.insert(0, entry)
    try:
        return importlib.import_module(f"{package}.{module}")
    finally:
        if added:
            sys.path.remove(entry)


# --- the shared cron-watcher guard ---------------------------------------


@pytest.mark.parametrize("url", _REJECTED)
def test_watcher_guard_rejects_non_http(url: str) -> None:
    guard = _load_flat(_WATCHERS / "_url.py", "cron_watchers_url")
    with pytest.raises(ValueError):
        guard.require_http_url(url, "--url")


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/events",
        "http://localhost:8080/events",
        "HTTP://Example.COM/events",
    ],
)
def test_watcher_guard_accepts_http(url: str) -> None:
    guard = _load_flat(_WATCHERS / "_url.py", "cron_watchers_url")
    assert guard.require_http_url(url, "--url") == url.strip()


def test_watcher_guard_strips_surrounding_whitespace() -> None:
    guard = _load_flat(_WATCHERS / "_url.py", "cron_watchers_url")
    assert guard.require_http_url("  https://example.com/f  ", "--url") == "https://example.com/f"


# --- the watchers themselves ---------------------------------------------


@pytest.mark.parametrize(
    ("script", "module_name"),
    [
        ("watch_http_json.py", "watch_http_json_under_test"),
        ("watch_rss.py", "watch_rss_under_test"),
    ],
)
def test_watcher_refuses_file_url_without_opening_it(
    script: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    watcher = _load_flat(_WATCHERS / script, module_name)

    def explode(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("urlopen must not be reached for a file:// target")

    monkeypatch.setattr(watcher.urllib.request, "urlopen", explode)
    monkeypatch.setattr(sys, "argv", [script, "--url", "file:///etc/passwd", "--name", "probe"])

    assert watcher.main() == 1
    assert "Refusing to fetch" in capsys.readouterr().err


# --- the JSON-RPC skills --------------------------------------------------


def test_poolsfun_rpc_override_must_be_http() -> None:
    scripts = _BUNDLED / "poolsdotfun-token-launcher" / "scripts"
    chains = _load_package("poolsfun", scripts, "chains")
    with pytest.raises(ValueError, match="must be http"):
        chains.resolve_rpc_url("file:///etc/passwd")
    # The constant default and a real override still resolve.
    assert chains.resolve_rpc_url().startswith("http")
    assert chains.resolve_rpc_url("https://rpc.example.com") == "https://rpc.example.com"


def test_unilp_rpc_override_must_be_http() -> None:
    scripts = _BUNDLED / "senior-unilp-manager" / "scripts"
    chains = _load_package("unilp", scripts, "chains")
    chain = next(iter(chains.CHAINS.values())) if hasattr(chains, "CHAINS") else None
    assert chain is not None, "expected a chain registry to test against"
    with pytest.raises(ValueError, match="must be http"):
        chains.resolve_rpc_url(chain, "file:///etc/passwd")


def test_unilp_rpc_env_value_must_be_http(monkeypatch: pytest.MonkeyPatch) -> None:
    # The endpoint can also arrive from the environment, which a skill-config
    # write reaches just as an argv override does.
    scripts = _BUNDLED / "senior-unilp-manager" / "scripts"
    chains = _load_package("unilp", scripts, "chains")
    chain = next(iter(chains.CHAINS.values()))
    monkeypatch.setattr(chains, "load_env", lambda: None)
    for name in chain["rpcEnv"]:
        monkeypatch.setenv(name, "file:///etc/passwd")
    with pytest.raises(ValueError, match="must be http"):
        chains.resolve_rpc_url(chain)


def test_unilp_client_validates_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # RpcClient took `rpc_url or resolve_rpc_url(chain)`, so an override
    # skipped the resolver — and with it every check the resolver performs.
    scripts = _BUNDLED / "senior-unilp-manager" / "scripts"
    rpc = _load_package("unilp", scripts, "rpc")
    chains = _load_package("unilp", scripts, "chains")
    chain = next(iter(chains.CHAINS.values()))
    with pytest.raises(ValueError, match="must be http"):
        rpc.RpcClient(chain, rpc_url="file:///etc/passwd")
