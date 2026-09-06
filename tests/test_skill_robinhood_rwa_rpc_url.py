"""Regression tests for issue #968.

``rwa_lookup.py`` passed ``--rpc-url`` straight into
``urllib.request.urlopen``, which also speaks ``file:`` and ``ftp:``. Nothing
checked the scheme, so ``--rpc-url file:///etc/hosts`` made the process read
and parse a local file. The endpoint can be steered by model output in an agent
workflow, so the scheme is pinned to ``http``/``https`` -- both at the CLI
boundary and at the one call site that reaches ``urlopen``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src/agentos/skills/bundled/robinhood-rwa-addresses/scripts/rwa_lookup.py"
)

_spec = importlib.util.spec_from_file_location("rwa_lookup_rpc_url", _SCRIPT)
assert _spec is not None and _spec.loader is not None
rwa_lookup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rwa_lookup)


REJECTED = [
    pytest.param("file:///etc/hosts", id="file"),
    pytest.param("file://localhost/etc/hosts", id="file-with-host"),
    pytest.param("FILE:///etc/hosts", id="file-uppercase"),
    pytest.param("ftp://example.invalid/x", id="ftp"),
    pytest.param("data:text/plain,hello", id="data"),
    pytest.param("/etc/hosts", id="bare-path"),
    pytest.param("", id="empty"),
]


@pytest.mark.parametrize("rpc_url", REJECTED)
def test_validate_rpc_url_rejects_non_http_schemes(rpc_url: str) -> None:
    with pytest.raises(ValueError, match="must start with http:// or https://"):
        rwa_lookup.validate_rpc_url(rpc_url)


@pytest.mark.parametrize(
    "rpc_url",
    ["http://127.0.0.1:8545", "https://rpc.mainnet.chain.robinhood.com", "HTTPS://EXAMPLE/x"],
)
def test_validate_rpc_url_accepts_http_and_https(rpc_url: str) -> None:
    assert rwa_lookup.validate_rpc_url(rpc_url) == rpc_url.strip()


def test_validate_rpc_url_accepts_the_shipped_default() -> None:
    assert rwa_lookup.validate_rpc_url(rwa_lookup.DEFAULT_RPC_URL) == rwa_lookup.DEFAULT_RPC_URL


@pytest.mark.parametrize("rpc_url", REJECTED)
def test_rpc_batch_refuses_to_open_a_non_http_url(rpc_url: str, monkeypatch) -> None:
    """The guard lives at the call site too, so no caller can route around it."""
    opened: list[object] = []

    def fail_open(*args: object, **kwargs: object) -> object:
        opened.append(args)
        raise AssertionError("urlopen must not be reached for a rejected scheme")

    monkeypatch.setattr(rwa_lookup.urllib.request, "urlopen", fail_open)

    with pytest.raises(ValueError, match="must start with http:// or https://"):
        rwa_lookup._rpc_batch(rpc_url, [{"id": "1", "method": "eth_getCode"}], 1.0)

    assert opened == []


def test_cli_rejects_a_file_url_before_doing_any_work(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do-not-read-me\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--query",
            "Apple",
            "--rpc-url",
            secret.as_uri(),
            "--no-cards",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "must start with http:// or https://" in result.stderr
    assert "do-not-read-me" not in result.stdout
    assert "do-not-read-me" not in result.stderr
