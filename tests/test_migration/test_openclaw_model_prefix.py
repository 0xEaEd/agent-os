"""An OpenClaw ``<provider>/<model>`` reference becomes the provider's own model id."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from agentos.migration.openclaw import (
    MigrationOptions,
    OpenClawMigrator,
    _model_for_agentos_provider,
    _provider_from_model,
)


@pytest.mark.parametrize(
    ("source_model", "provider", "native"),
    [
        ("anthropic/claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"),
        ("openai/gpt-5", "openai", "gpt-5"),
        ("deepseek/deepseek-chat", "deepseek", "deepseek-chat"),
        ("minimax/MiniMax-M2", "minimax", "MiniMax-M2"),
        ("Anthropic/claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"),
        # The two prefixes that were already stripped keep working.
        ("openrouter/deepseek/deepseek-v3.1", "openrouter", "deepseek/deepseek-v3.1"),
        ("zai/glm-5", "zhipu", "glm-5"),
    ],
)
def test_prefixed_model_is_reduced_to_the_native_id(
    source_model: str, provider: str, native: str
) -> None:
    assert _provider_from_model(source_model) == provider
    model, details = _model_for_agentos_provider(source_model, provider)
    assert model == native
    assert details["source_model"] == source_model


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        # Already native: nothing to strip.
        ("claude-sonnet-4-5", "anthropic"),
        ("gpt-5", "openai"),
        ("deepseek-chat", "deepseek"),
        # A prefix that names a different provider than the one derived is data.
        ("anthropic/claude-sonnet-4-5", "openrouter"),
        # Nothing follows the prefix: keep the id rather than write an empty model.
        ("anthropic/", "anthropic"),
        ("anthropic/claude-sonnet-4-5", None),
    ],
)
def test_other_ids_are_left_alone(model: str, provider: str | None) -> None:
    assert _model_for_agentos_provider(model, provider) == (model, {})


def _migrate_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str) -> dict:
    source = tmp_path / ".openclaw"
    source.mkdir()
    (source / "openclaw.json").write_text(
        json.dumps({"agents": {"defaults": {"model": {"primary": model}}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path / "home"))
    config_path = tmp_path / "agentos.toml"
    OpenClawMigrator(MigrationOptions(source=source, config_path=config_path, apply=True)).migrate()
    return tomllib.loads(config_path.read_text(encoding="utf-8"))["llm"]


@pytest.mark.parametrize(
    ("source_model", "provider", "native"),
    [
        ("anthropic/claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5"),
        ("openai/gpt-5", "openai", "gpt-5"),
        ("deepseek/deepseek-chat", "deepseek", "deepseek-chat"),
    ],
)
def test_migrate_writes_the_native_model_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_model: str,
    provider: str,
    native: str,
) -> None:
    llm = _migrate_model(tmp_path, monkeypatch, source_model)
    assert llm["provider"] == provider
    assert llm["model"] == native


def test_migrate_keeps_a_model_with_no_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _migrate_model(tmp_path, monkeypatch, "claude-sonnet-4-5")
    assert llm["provider"] == "anthropic"
    assert llm["model"] == "claude-sonnet-4-5"
