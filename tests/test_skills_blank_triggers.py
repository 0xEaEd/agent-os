"""A blank trigger must never activate a skill.

``find_by_trigger`` tests each trigger with ``trigger.lower() in text_lower``. An empty
string is a substring of every message, so a skill declaring ``triggers: [""]`` -- or a
YAML list with a trailing ``-`` -- matched every user turn and pushed its instructions
into unrelated tasks (#2999). The ``None`` a trailing ``-`` produces also crashed the
matcher on ``.lower()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentos.skills.loader import SkillLoader


def _write_skill(root: Path, name: str, triggers_block: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Test skill {name}\n{triggers_block}---\nbody text\n",
        encoding="utf-8",
    )


def _loader(tmp_path: Path, triggers_block: str) -> SkillLoader:
    skills = tmp_path / "skills"
    skills.mkdir()
    _write_skill(skills, "notes", triggers_block)
    return SkillLoader(bundled_dir=skills, snapshot_path=tmp_path / "snapshot.json")


def test_an_empty_trigger_does_not_match_an_unrelated_turn(tmp_path: Path) -> None:
    loader = _loader(tmp_path, 'triggers: [""]\n')

    assert loader.find_by_trigger("What is 2 + 2?") == []


def test_an_empty_trigger_is_dropped_at_load(tmp_path: Path) -> None:
    loader = _loader(tmp_path, 'triggers: ["", "take a note"]\n')

    skill = next(s for s in loader.load_all() if s.name == "notes")
    assert skill.triggers == ["take a note"]


def test_a_whitespace_only_trigger_does_not_match(tmp_path: Path) -> None:
    loader = _loader(tmp_path, 'triggers: ["   "]\n')

    assert loader.find_by_trigger("What is 2 + 2?") == []


def test_a_trailing_empty_list_item_does_not_crash_the_matcher(tmp_path: Path) -> None:
    loader = _loader(tmp_path, "triggers:\n  - take a note\n  -\n")

    skill = next(s for s in loader.load_all() if s.name == "notes")
    assert skill.triggers == ["take a note"]
    assert loader.find_by_trigger("What is 2 + 2?") == []


def test_a_real_trigger_still_matches(tmp_path: Path) -> None:
    loader = _loader(tmp_path, 'triggers: ["take a note"]\n')

    matched = loader.find_by_trigger("Please take a note of this")

    assert [s.name for s in matched] == ["notes"]


def test_a_real_trigger_still_matches_case_insensitively(tmp_path: Path) -> None:
    loader = _loader(tmp_path, 'triggers: ["Take A Note"]\n')

    assert [s.name for s in loader.find_by_trigger("please TAKE A NOTE")] == ["notes"]


def test_a_scalar_trigger_is_still_accepted(tmp_path: Path) -> None:
    loader = _loader(tmp_path, "triggers: take a note\n")

    skill = next(s for s in loader.load_all() if s.name == "notes")
    assert skill.triggers == ["take a note"]


@pytest.mark.parametrize("block", ['triggers: ""\n', "triggers: []\n"])
def test_a_skill_with_no_usable_trigger_matches_nothing(tmp_path: Path, block: str) -> None:
    loader = _loader(tmp_path, block)

    assert loader.find_by_trigger("anything at all") == []
