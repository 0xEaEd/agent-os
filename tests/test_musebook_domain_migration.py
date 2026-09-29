"""Regression test for #3435: musebook skill points to the live .me domain."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
SKILL_DIR = BASE_DIR / "src/agentos/skills/bundled/musebook"
PUBLISHERS = BASE_DIR / "src/agentos/skills/publishers.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_musebook_base_url_points_to_live_domain():
    """The script must use the live .me domain, not the expired .lol domain."""
    text = _text(SKILL_DIR / "scripts/muse.py")
    assert "musebook.lol" not in text, "muse.py still contains musebook.lol"
    assert "musebook.me" in text, "muse.py does not reference musebook.me"


def test_musebook_skill_frontmatter_uses_live_domain():
    """The bundled skill metadata must point to the live .me domain."""
    text = _text(SKILL_DIR / "SKILL.md")
    assert "musebook.lol" not in text, "SKILL.md still contains musebook.lol"
    assert "musebook.me" in text, "SKILL.md does not reference musebook.me"


def test_musebook_publisher_url_uses_live_domain():
    """The recognized publisher record must use the live .me domain."""
    text = _text(PUBLISHERS)
    assert "musebook.lol" not in text, "publishers.py still contains musebook.lol"
    assert "musebook.me" in text, "publishers.py does not reference musebook.me"


def test_musebook_reference_spec_uses_live_domain():
    """The reference spec should not ship the old .lol domain as an live URL."""
    text = _text(SKILL_DIR / "references/muse.txt")
    # The reference file is verbatim from the board. It may mention the old
    # domain only to tell readers it is currently unreachable, so we assert
    # that every actual API/website URL is on the live .me domain.
    for line in text.splitlines():
        if "https://" in line and "musebook.lol" in line:
            raise AssertionError(f"live URL still points to .lol: {line}")
    assert "musebook.me" in text, "muse.txt does not reference musebook.me"
