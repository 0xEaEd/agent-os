"""``.env`` values are migrated the way the source runtimes' dotenv loaders read them."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.env import parse_env_file
from agentos.migration._dotenv import parse_env_value
from agentos.migration.hermes import (
    HermesMigrationOptions,
    HermesMigrator,
)
from agentos.migration.hermes import _load_env_file as hermes_load_env_file
from agentos.migration.openclaw import MigrationOptions, OpenClawMigrator
from agentos.migration.openclaw import _load_env_file as openclaw_load_env_file


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The reported shapes: a trailing comment after an unquoted / quoted value.
        ("sk-abc123 # prod key", "sk-abc123"),
        ("'sk-ant-x'  # old", "sk-ant-x"),
        ('"sk-oa-1" # work', "sk-oa-1"),
        ("sk-abc123\t# tab before the comment", "sk-abc123"),
        # Values the old parser already got right stay exactly as they were.
        ("sk-abc123", "sk-abc123"),
        ("  sk-abc123  ", "sk-abc123"),
        ('"sk-abc123"', "sk-abc123"),
        ("'sk-abc123'", "sk-abc123"),
        ("", ""),
        # A '#' is only a comment after whitespace; inside a token or a quote it is data.
        ("sk-a#b", "sk-a#b"),
        ('"sk # not a comment"', "sk # not a comment"),
        ("'a #b' # real comment", "a #b"),
        # The other quote character inside a quoted value is data too.
        ('"it\'s"', "it's"),
        ("'say \"hi\"'", 'say "hi"'),
        # No closing quote: keep the old end-trimming behaviour.
        ('"sk-abc123', "sk-abc123"),
    ],
)
def test_parse_env_value(raw: str, expected: str) -> None:
    assert parse_env_value(raw) == expected


def test_openclaw_loader_drops_inline_comments(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENAI_API_KEY=sk-abc123 # prod key\n"
        "ANTHROPIC_API_KEY='sk-ant-x'  # old\n"
        'GEMINI_API_KEY="k"\n',
        encoding="utf-8",
    )
    assert openclaw_load_env_file(env_path) == {
        "OPENAI_API_KEY": "sk-abc123",
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "GEMINI_API_KEY": "k",
    }


def test_hermes_loader_drops_inline_comments(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "export OPENROUTER_API_KEY=sk-or-1 # work account\nANTHROPIC_API_KEY='sk-ant-x'  # old\n",
        encoding="utf-8",
    )
    assert hermes_load_env_file(env_path) == {
        "OPENROUTER_API_KEY": "sk-or-1",
        "ANTHROPIC_API_KEY": "sk-ant-x",
    }


def test_openclaw_migrate_writes_the_bare_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / ".openclaw"
    source.mkdir()
    (source / "openclaw.json").write_text("{}", encoding="utf-8")
    (source / ".env").write_text(
        "OPENAI_API_KEY=sk-abc123 # prod key\nANTHROPIC_API_KEY='sk-ant-x'  # old\n",
        encoding="utf-8",
    )
    home = tmp_path / "agentos-home"
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(home))

    OpenClawMigrator(
        MigrationOptions(
            source=source,
            config_path=tmp_path / "agentos.toml",
            apply=True,
            migrate_secrets=True,
        )
    ).migrate()

    written = parse_env_file(home / ".env")
    assert written["OPENAI_API_KEY"] == "sk-abc123"
    assert written["ANTHROPIC_API_KEY"] == "sk-ant-x"


def test_hermes_migrate_writes_the_bare_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / ".hermes"
    source.mkdir()
    (source / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    (source / ".env").write_text(
        "OPENAI_API_KEY=sk-abc123 # prod key\nANTHROPIC_API_KEY='sk-ant-x'  # old\n",
        encoding="utf-8",
    )
    home = tmp_path / "agentos-home"
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(home))

    HermesMigrator(
        HermesMigrationOptions(
            source=source,
            config_path=tmp_path / "agentos.toml",
            apply=True,
            migrate_secrets=True,
        )
    ).migrate()

    written = parse_env_file(home / ".env")
    assert written["OPENAI_API_KEY"] == "sk-abc123"
    assert written["ANTHROPIC_API_KEY"] == "sk-ant-x"
