"""A gateway auth secret that came from the environment must stay out of the file.

``GatewayConfig.load`` and ``config_commit`` already record that ``auth.token`` /
``auth.password`` were supplied by ``AGENTOS_AUTH_TOKEN`` / ``AGENTOS_AUTH_PASSWORD``
(``mark_env_sourced_auth_secrets``), so ``to_toml_dict`` never writes them back.
``onboarding.config_store.load_config`` did not, so every command that loads with it
and persists — ``config set --config``, ``agents add``, onboarding, the migrations —
copied an env-only token into ``config.toml``. Because values in the file beat the
environment, rotating ``AGENTOS_AUTH_TOKEN`` afterwards silently changed nothing.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import tomli_w
from typer.testing import CliRunner

from agentos.cli.main import app
from agentos.gateway.config import GatewayConfig
from agentos.onboarding.config_store import load_config, persist_config

runner = CliRunner()

_ENV_NAMES = (
    "AGENTOS_AUTH_TOKEN",
    "AGENTOS_GATEWAY_AUTH__TOKEN",
    "AGENTOS_AUTH_PASSWORD",
    "AGENTOS_GATEWAY_AUTH__PASSWORD",
    "AGENTOS_LLM_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_bytes(tomli_w.dumps({"llm": {"provider": "openai", "model": "gpt-4o"}}).encode())
    return path


def _auth_on_disk(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("auth", {})


def _set(key: str, value: str, path: Path):
    return runner.invoke(app, ["config", "set", key, value, "--config", str(path)])


@pytest.mark.parametrize(
    ("env_name", "field"),
    [
        ("AGENTOS_AUTH_TOKEN", "token"),
        ("AGENTOS_GATEWAY_AUTH__TOKEN", "token"),
        ("AGENTOS_AUTH_PASSWORD", "password"),
        ("AGENTOS_GATEWAY_AUTH__PASSWORD", "password"),
    ],
)
def test_an_unrelated_config_set_does_not_write_the_env_secret(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, env_name: str, field: str
) -> None:
    monkeypatch.setenv(env_name, "env-only-secret")

    result = _set("llm.model", "gpt-4.1", config_file)

    assert result.exit_code == 0, result.output
    assert field not in _auth_on_disk(config_file)
    assert "env-only-secret" not in config_file.read_text(encoding="utf-8")


def test_an_unrelated_config_set_does_not_write_the_env_llm_api_key(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``config set`` rebuilds the model from a dict, which re-reads the env."""
    monkeypatch.setenv("AGENTOS_LLM_API_KEY", "sk-env-only")

    result = _set("llm.model", "gpt-4.1", config_file)

    assert result.exit_code == 0, result.output
    assert "sk-env-only" not in config_file.read_text(encoding="utf-8")
    assert load_config(config_file).llm.api_key == "sk-env-only"


def test_an_explicit_llm_api_key_is_written_even_when_the_env_is_set(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOS_LLM_API_KEY", "sk-env-only")

    assert _set("llm.api_key", "sk-typed", config_file).exit_code == 0

    assert tomllib.loads(config_file.read_text(encoding="utf-8"))["llm"]["api_key"] == "sk-typed"


def test_the_unrelated_key_is_still_written(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: the command did persist, so the absence above is real."""
    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "env-only-secret")

    assert _set("llm.model", "gpt-4.1", config_file).exit_code == 0

    assert tomllib.loads(config_file.read_text(encoding="utf-8"))["llm"]["model"] == "gpt-4.1"


def test_a_rotated_env_token_still_takes_effect_after_config_set(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "old-token")
    assert _set("llm.model", "gpt-4.1", config_file).exit_code == 0

    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "rotated-token")

    assert GatewayConfig.load(config_file).auth.token == "rotated-token"


def test_an_explicit_auth_token_is_written_even_when_the_env_is_set(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting the key on purpose is not "env-sourced" — it must persist."""
    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "env-only-secret")

    assert _set("auth.token", "typed-by-operator", config_file).exit_code == 0

    assert _auth_on_disk(config_file)["token"] == "typed-by-operator"


def test_a_token_already_in_the_file_survives_an_unrelated_set(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _set("auth.token", "in-the-file", config_file).exit_code == 0
    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "different-env-token")

    assert _set("llm.model", "gpt-4.1", config_file).exit_code == 0

    assert _auth_on_disk(config_file)["token"] == "in-the-file"


def test_load_then_persist_leaves_the_env_secret_out_of_an_existing_file(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared loader, which ``agents add`` / onboarding / migrations use."""
    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "env-only-secret")
    monkeypatch.setenv("AGENTOS_AUTH_PASSWORD", "env-only-password")

    cfg = load_config(config_file)
    assert cfg.auth.token == "env-only-secret"  # the running config still has it
    persist_config(cfg, path=config_file)

    on_disk = _auth_on_disk(config_file)
    assert "token" not in on_disk
    assert "password" not in on_disk


def test_load_then_persist_leaves_the_env_secret_out_of_a_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTOS_AUTH_TOKEN", "env-only-secret")
    target = tmp_path / "fresh.toml"

    persist_config(load_config(target), path=target)

    assert "token" not in _auth_on_disk(target)
    assert "env-only-secret" not in target.read_text(encoding="utf-8")
