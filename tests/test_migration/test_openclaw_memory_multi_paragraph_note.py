"""``agentos migrate openclaw --apply``: a daily note is one block, not one paragraph.

When the destination ``MEMORY.md`` already holds the user's own content, the
migrator appends the imported blocks that are not already there. A "block" is a
paragraph, except that a daily-memory entry --
``## Imported daily memory: <name>`` followed by the note -- is meant to stay
glued so header and body are never deduped against each other.

Only the *first* body paragraph was glued on. A note whose body has more than
one paragraph -- a heading and a line under it is enough, and that is how
people write notes -- was therefore shredded: its opening paragraph carried the
header, and the rest became loose paragraphs. If that opening paragraph matched
anything already in the destination (a bare ``## Preferences`` heading matches),
the header went with it and the note's facts landed at the end of the file with
no provenance, reading as part of whichever section happened to precede them.

The migration reports ``migrated`` either way, and it is a one-shot rewrite of
the user's memory file.

Every assertion here runs the migrator through ``OpenClawMigrator.migrate()``,
the call behind ``agentos migrate openclaw``, not the merge helper.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agentos.memory.curated import CuratedMemoryStore
from agentos.migration.openclaw import MigrationOptions, OpenClawMigrator


def _openclaw_source(root: Path, notes: dict[str, str]) -> Path:
    source = root / ".openclaw"
    memory_dir = source / "workspace" / "memory"
    memory_dir.mkdir(parents=True)
    for name, body in notes.items():
        (memory_dir / name).write_text(body, encoding="utf-8")
    (source / "openclaw.json").write_text("{}", encoding="utf-8")
    return source


def _destination(root: Path, monkeypatch: pytest.MonkeyPatch, existing: str) -> Path:
    home = root / "agentos-home"
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(home))
    (home / "workspace").mkdir(parents=True)
    destination = home / "workspace" / "MEMORY.md"
    destination.write_text(existing, encoding="utf-8")
    return destination


def _migrate(root: Path, source: Path) -> dict:
    report = OpenClawMigrator(
        MigrationOptions(source=source, config_path=root / "cfg.toml", apply=True)
    ).migrate()
    return next(item for item in report["items"] if item["kind"] == "memory")


def test_a_multi_paragraph_note_keeps_its_header_when_its_first_paragraph_is_shared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _openclaw_source(
        tmp_path,
        {"day-two.md": "## Preferences\n\nDeploy window is Tuesday 09:00 Jakarta.\n"},
    )
    destination = _destination(
        tmp_path, monkeypatch, "## Preferences\n\nUser likes concise answers.\n"
    )

    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["status"] == "migrated"
    assert "## Imported daily memory: day-two.md" in text, (
        "the note's provenance header was dropped because its first paragraph "
        "matched a heading the destination already had"
    )
    assert (
        "## Imported daily memory: day-two.md\n\n## Preferences\n\n"
        "Deploy window is Tuesday 09:00 Jakarta." in text
    ), "the note must stay contiguous under its own header"
    assert "User likes concise answers." in text, "existing memory is preserved verbatim"


def test_a_later_paragraph_of_a_note_is_not_deduped_away_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _openclaw_source(
        tmp_path,
        {"day-three.md": "Sprint retro moved.\n\nThe staging database is read-only.\n"},
    )
    destination = _destination(
        tmp_path, monkeypatch, "Unrelated note.\n\nThe staging database is read-only.\n"
    )

    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["status"] == "migrated"
    assert (
        "## Imported daily memory: day-three.md\n\nSprint retro moved.\n\n"
        "The staging database is read-only." in text
    ), "a note is appended whole; its second paragraph is not a dedupe unit of its own"


def test_two_notes_do_not_absorb_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _openclaw_source(
        tmp_path,
        {
            "day-one.md": "Alpha heading\n\nAlpha detail.\n",
            "day-two.md": "Beta heading\n\nBeta detail.\n",
        },
    )
    destination = _destination(tmp_path, monkeypatch, "Existing agentos note.\n")

    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["details"]["new_blocks_appended"] == 2, "one block per imported note"
    assert (
        "## Imported daily memory: day-one.md\n\nAlpha heading\n\nAlpha detail.\n\n"
        "## Imported daily memory: day-two.md\n\nBeta heading\n\nBeta detail." in text
    )


def _memory_add(destination: Path, entry: str) -> None:
    store = CuratedMemoryStore(memory_dir=destination.parent)
    store.load_from_disk()
    assert store.add("memory", entry)["success"] is True


@pytest.mark.parametrize("added_after_first_run", ["nothing", "own-section", "memory-add"])
def test_guard_re_running_the_migration_still_dedupes_to_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, added_after_first_run: str
) -> None:
    """The last imported note in the destination has nothing after it to stop it.

    Grouping a note up to the next daily-memory header also runs on the
    existing ``MEMORY.md``, where the last note swallows whatever was written
    below it after the first run. That must not make a re-run append the note
    again: it is still there, header and body back to back.
    """
    source = _openclaw_source(
        tmp_path,
        {
            "day-one.md": "Sprint retro moved to Friday.\n",
            "day-two.md": "## Preferences\n\nDeploy window is Tuesday 09:00 Jakarta.\n",
        },
    )
    destination = _destination(
        tmp_path, monkeypatch, "## Preferences\n\nUser likes concise answers.\n"
    )
    first = _migrate(tmp_path, source)
    assert first["details"]["new_blocks_appended"] == 2

    if added_after_first_run == "own-section":
        with destination.open("a", encoding="utf-8") as handle:
            handle.write("\n\n## My own notes\n\nBuy milk.\n")
    elif added_after_first_run == "memory-add":
        _memory_add(destination, "User prefers dark mode.")
        assert destination.read_text(encoding="utf-8").endswith(
            "Deploy window is Tuesday 09:00 Jakarta.\n§\nUser prefers dark mode."
        )
    before_rerun = destination.read_text(encoding="utf-8")

    item = _migrate(tmp_path, source)

    assert item["status"] == "skipped"
    assert item["details"]["deduplicated_against_existing"] is True
    assert destination.read_text(encoding="utf-8") == before_rerun


# --- Guards: green before and after the fix, so they prove nothing on their own. ---


def test_positive_control_a_single_paragraph_note_still_dedupes_against_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without this the assertions above could pass on a merge that never dedupes."""
    source = _openclaw_source(
        tmp_path,
        {"day-one.md": "shared fact\n", "day-two.md": "brand new fact\n"},
    )
    destination = _destination(
        tmp_path,
        monkeypatch,
        "Unique agentos note.\n\n## Imported daily memory: day-one.md\n\nshared fact\n",
    )

    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["details"]["new_blocks_appended"] == 1
    assert item["details"]["deduplicated_blocks_vs_existing"] >= 1
    assert text.count("shared fact") == 1
    assert "brand new fact" in text


def test_a_note_that_only_prefixes_an_existing_paragraph_is_still_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Back to back means whole paragraphs, not a substring of a longer one."""
    source = _openclaw_source(tmp_path, {"day-two.md": "Deploy window is Tuesday.\n"})
    destination = _destination(
        tmp_path,
        monkeypatch,
        "## Imported daily memory: day-two.md\n\n"
        "Deploy window is Tuesday. Friday deploys are frozen.\n",
    )

    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["status"] == "migrated"
    assert text.rstrip().endswith(
        "## Imported daily memory: day-two.md\n\nDeploy window is Tuesday."
    )


# ---------------------------------------------------------------------------
# A note ends where its own file does, not at the next note header
# ---------------------------------------------------------------------------


def _sibling_memory(source: Path, body: str) -> None:
    sibling = source / "workspace-w1"
    sibling.mkdir()
    (sibling / "MEMORY.md").write_text(body, encoding="utf-8")


def test_a_sibling_memory_paragraph_already_in_the_destination_is_not_appended_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last primary note must not run on into the sibling ``MEMORY.md``
    joined after it; each sibling paragraph is still deduped on its own."""
    source = _openclaw_source(tmp_path, {"day-one.md": "Primary note.\n"})
    _sibling_memory(source, "## Preferences\n\nUser likes concise answers.\n")
    destination = _destination(
        tmp_path, monkeypatch, "## Preferences\n\nUser likes concise answers.\n"
    )

    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["status"] == "migrated"
    assert "## Imported daily memory: day-one.md\n\nPrimary note." in text
    assert text.count("## Preferences") == 1
    assert text.count("User likes concise answers.") == 1


def test_a_re_run_after_a_new_primary_note_does_not_duplicate_sibling_paragraphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the first run the sibling's paragraphs sit below the last note in
    the destination; they must still count as present on the next run."""
    source = _openclaw_source(tmp_path, {"day-one.md": "Primary note.\n"})
    _sibling_memory(source, "## Team\n\nShip on Fridays.\n")
    destination = _destination(tmp_path, monkeypatch, "User likes concise answers.\n")
    assert _migrate(tmp_path, source)["status"] == "migrated"

    (source / "workspace" / "memory" / "day-two.md").write_text("Second note.\n", encoding="utf-8")
    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["status"] == "migrated"
    assert item["details"]["new_blocks_appended"] == 1
    assert text.count("## Imported daily memory: day-one.md") == 1
    assert text.count("## Imported daily memory: day-two.md") == 1
    assert text.count("## Team") == 1
    assert text.count("Ship on Fridays.") == 1


def test_a_re_run_past_the_size_cap_adds_no_second_overflow_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overflow marker is its own paragraphs, not part of the note the
    cutoff leaves last. A note sorted first moves the cutoff, so a different
    note ends up last on the re-run; the marker must still dedupe."""
    notes = {
        f"day-{index:03d}.md": f"Note {index}.\n\n" + f"fact {index} " * 300 + "\n"
        for index in range(60)
    }
    source = _openclaw_source(tmp_path, notes)
    destination = _destination(tmp_path, monkeypatch, "User likes concise answers.\n")
    assert _migrate(tmp_path, source)["status"] == "migrated"
    assert destination.read_text(encoding="utf-8").count("## Migration overflow") == 1

    # Longer than one note, so the cutoff moves past a whole note.
    (source / "workspace" / "memory" / "aaa-new.md").write_text(
        "A note sorted before the others.\n\n" + "new fact " * 400 + "\n", encoding="utf-8"
    )
    item = _migrate(tmp_path, source)
    text = destination.read_text(encoding="utf-8")

    assert item["status"] == "migrated"
    assert item["details"]["new_blocks_appended"] == 1
    assert text.count("## Imported daily memory: aaa-new.md") == 1
    assert text.count("## Migration overflow") == 1


def test_a_long_whitespace_run_in_the_destination_does_not_stall_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paragraph-break pattern backtracked quadratically on a whitespace
    run (7.7 s for 40k spaces). The ceiling is loose on purpose: it fails on a
    regression of that shape, not on a slow runner."""
    source = _openclaw_source(tmp_path, {"day-one.md": "Primary note.\n"})
    _destination(tmp_path, monkeypatch, "Existing.\n" + " " * 40_000 + "tail\n")

    start = time.perf_counter()
    item = _migrate(tmp_path, source)

    assert item["status"] == "migrated"
    assert time.perf_counter() - start < 2.0
