"""The raised skills-block budget has to reach installs that already have a config.

``max_skills_prompt_chars`` is materialised into every saved ``config.toml``, so
lifting the default alone would leave every existing install pinned to the old
value — and that value cannot fit the shipped skills' descriptions, which is the
whole reason it was raised.
"""

from __future__ import annotations

import pytest

from agentos.gateway.config_migration import (
    LEGACY_MAX_SKILLS_PROMPT_CHARS,
    STALE_MAX_SKILLS_PROMPT_CHARS,
    migrate_config_payload,
)
from agentos.skills.injector import DEFAULT_MAX_SKILLS_PROMPT_CHARS


def test_a_config_carrying_the_old_default_is_lifted() -> None:
    result = migrate_config_payload(
        {"skills": {"max_skills_prompt_chars": LEGACY_MAX_SKILLS_PROMPT_CHARS}}
    )

    assert result.payload["skills"]["max_skills_prompt_chars"] == DEFAULT_MAX_SKILLS_PROMPT_CHARS
    assert any("max_skills_prompt_chars" in change for change in result.changes)


@pytest.mark.parametrize("stale", sorted(STALE_MAX_SKILLS_PROMPT_CHARS))
def test_every_materialised_default_is_lifted(stale: int) -> None:
    """A config carrying any past default was written by us; it follows the default.

    The current default is in the set too so that nothing rewrites a config
    that already matches it — the assertion covers both halves.
    """
    result = migrate_config_payload({"skills": {"max_skills_prompt_chars": stale}})

    assert result.payload["skills"]["max_skills_prompt_chars"] == DEFAULT_MAX_SKILLS_PROMPT_CHARS
    lifted = stale != DEFAULT_MAX_SKILLS_PROMPT_CHARS
    assert any("max_skills_prompt_chars" in change for change in result.changes) is lifted


def test_the_current_default_is_a_known_default() -> None:
    """The next lift must add the current value to the stale set, or the
    configs materialised today will be pinned tomorrow — the bug this file
    exists to stop."""
    assert DEFAULT_MAX_SKILLS_PROMPT_CHARS in STALE_MAX_SKILLS_PROMPT_CHARS


def test_a_deliberately_chosen_budget_is_left_alone() -> None:
    """Only the exact old default is refreshed — the same rule the model ids use."""
    for chosen in (4000, 12000, 25000, 40000, True):
        result = migrate_config_payload({"skills": {"max_skills_prompt_chars": chosen}})

        assert result.payload["skills"]["max_skills_prompt_chars"] == chosen
        assert not any("max_skills_prompt_chars" in change for change in result.changes)


def test_a_config_without_the_key_is_untouched() -> None:
    """An unset key already resolves to the current default; do not materialise it."""
    result = migrate_config_payload({"skills": {"filter_enabled": False}})

    assert "max_skills_prompt_chars" not in result.payload["skills"]
    assert not any("max_skills_prompt_chars" in change for change in result.changes)


def test_a_config_with_no_skills_section_survives() -> None:
    result = migrate_config_payload({"llm": {"provider": "openrouter"}})

    assert "skills" not in result.payload


def test_the_lifted_value_fits_the_shipped_skills_in_full_mode() -> None:
    """Pin the point of the migration, not just the number.

    A future default that still cannot fit the shipped set would make this
    migration cosmetic; assert the value it lifts to actually buys descriptions.
    """
    from pathlib import Path

    from agentos.skills.injector import SkillInjector
    from agentos.skills.loader import SkillLoader

    bundled = Path(__file__).resolve().parents[2] / "src" / "agentos" / "skills" / "bundled"
    loader = SkillLoader(bundled_dir=bundled, snapshot_path=Path("/nonexistent/snapshot.json"))
    visible = [s for s in loader.load_all() if not s.disable_model_invocation]

    rendered = SkillInjector().inject_full("", visible)

    assert len(rendered) <= DEFAULT_MAX_SKILLS_PROMPT_CHARS
    assert len(rendered) > LEGACY_MAX_SKILLS_PROMPT_CHARS, "otherwise the lift was unnecessary"
    assert "<description>" in rendered
