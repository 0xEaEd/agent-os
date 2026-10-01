"""``add`` accepted an entry that the next read split into several.

Curated memory is a ``§``-delimited list: ``ENTRY_DELIMITER`` is ``"\\n§\\n"``,
``_write_file`` joins entries with it and ``_parse_entries`` splits on it. The
store is strict about what may be stored -- ``_scan`` for threat patterns, a
char budget, a duplicate check -- but nothing stopped an entry from containing
the delimiter itself.

``add`` then reported ``success`` and ``entry_count: 1`` while the file it wrote
held two entries, and ``remove`` could no longer match the entry by the text
that created it:

    add("user", "Deploy checklist:\\n§\\nrun the migration first")  -> success
    entries after reload -> ['Deploy checklist:', 'run the migration first']
    remove(same text) -> "No entry matched ..."

A second shape corrupts the *neighbour* rather than the entry: an entry ending
in ``\\n§`` supplies the missing newline to the delimiter written after it, so
the following entry starts with a stray ``§``.

Both are refused now. ``§`` inside a line, an indented ``§``, and an entry that
is a bare ``§`` all still store fine -- none of them splits anything.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from agentos.memory.curated import ENTRY_DELIMITER, CuratedMemoryStore


@pytest.fixture
def store(tmp_path: Path) -> CuratedMemoryStore:
    return CuratedMemoryStore(tmp_path)


def _reload(tmp_path: Path) -> list[str]:
    """Entries as a fresh process reads them back off disk."""
    fresh = CuratedMemoryStore(tmp_path)
    fresh._reload_target("user", skip_drift=True)
    return fresh.entries_for("user")


# ── the report ─────────────────────────────────────────────────────────────


def test_an_entry_holding_the_delimiter_is_refused(store, tmp_path):
    result = store.add("user", "Deploy checklist:\n§\nrun the migration first")

    assert result["success"] is False
    assert "§" in result["error"]
    assert _reload(tmp_path) == []


def test_an_entry_ending_in_a_lone_marker_is_refused(store, tmp_path):
    """This one corrupts the *next* entry, not itself."""
    assert store.add("user", "first entry")["success"] is True
    result = store.add("user", "Deploy checklist:\n§")

    assert result["success"] is False
    assert _reload(tmp_path) == ["first entry"]


def test_what_add_reports_matches_what_the_next_read_finds(store, tmp_path):
    """The defect in one assertion: the count claimed vs the count stored."""
    store.add("user", "Deploy checklist:\n§\nrun the migration first")
    store.add("user", "a real entry")

    assert len(_reload(tmp_path)) == store.entries_for("user").__len__() == 1


def test_a_stored_entry_can_always_be_removed_by_the_text_that_created_it(store, tmp_path):
    text = "Deploy checklist:\n§\nrun the migration first"
    if store.add("user", text)["success"]:  # refused now; was accepted before
        assert store.remove("user", text)["success"] is True


def test_replace_is_gated_too(store, tmp_path):
    assert store.add("user", "original entry")["success"] is True
    result = store.replace("user", "original entry", "new:\n§\nsplit")

    assert result["success"] is False
    assert _reload(tmp_path) == ["original entry"]


# ── the marker uses that are harmless ──────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("inline", "the unit cost is 5§ per item"),
        ("indented", "note:\n  §\nstill one entry"),
        ("padded with spaces", "note:\n § \nstill one entry"),
        ("a bare marker entry", "§"),
        ("plain text", "the user prefers short answers"),
    ],
)
def test_a_marker_that_cannot_split_anything_is_still_accepted(store, tmp_path, label, content):
    assert store.add("user", content)["success"] is True, label
    assert _reload(tmp_path) == [content], label


# ── the property ───────────────────────────────────────────────────────────


def test_fuzz_every_accepted_entry_survives_the_read(tmp_path):
    """4000 marker-heavy candidates. On `main` 252 of ~1481 accepted ones are lost."""
    fragments = ["§", "\n", " ", "a", "b", "\t", "x\n", "\n§\n", "§\n", "\n§"]
    rng = random.Random(5)
    store = CuratedMemoryStore(tmp_path)

    accepted = []
    for _ in range(4000):
        candidate = "".join(rng.choice(fragments) for _ in range(rng.randint(1, 8)))
        if store.add("user", candidate).get("success"):
            accepted.append(candidate.strip())

    on_disk = _reload(tmp_path)
    lost = [entry for entry in accepted if entry and entry not in on_disk]
    assert lost == [], lost[:5]


def test_the_round_trip_probe_matches_the_real_serializer(tmp_path):
    """The guard asks the real parser, so it cannot drift from the format."""
    head, tail = CuratedMemoryStore._ROUND_TRIP_PROBE
    for content, expected in [("a\n§\nb", True), ("a\n§", True), ("5§ x", False), ("§", False)]:
        assert CuratedMemoryStore._would_not_round_trip(content) is expected, content
        probe = ENTRY_DELIMITER.join([head, content, tail])
        split = CuratedMemoryStore._parse_entries(probe) != [head, content, tail]
        assert split is expected, content


@pytest.mark.parametrize(
    "content",
    ["x\r\n§\r\ny", "x\r§\ry", "x\n§\r\ny", "x\r\n§"],
    ids=["crlf", "cr", "mixed", "crlf-trailing"],
)
def test_a_marker_line_between_crlf_breaks_is_refused(store, tmp_path, content):
    r"""The file is read back in text mode, so ``\r\n`` around a ``§`` splits too."""
    assert store.add("user", "before")["success"] is True

    assert store.add("user", content)["success"] is False
    assert store.apply_batch("user", [{"action": "add", "content": content}])["success"] is False
    assert _reload(tmp_path) == ["before"]


# ── apply_batch: the path the memory tool steers the model to ───────────────


def _raw(store: CuratedMemoryStore) -> bytes:
    return store._path_for("user").read_bytes()


def test_batch_add_holding_the_delimiter_is_refused(store, tmp_path):
    assert store.add("user", "original entry")["success"] is True
    before = _raw(store)

    result = store.apply_batch(
        "user",
        [{"action": "add", "content": "Deploy checklist:\n§\nrun the migration first"}],
    )

    assert result["success"] is False
    assert result["error"].startswith("Operation 1: ")
    assert "§" in result["error"]
    assert _raw(store) == before
    assert _reload(tmp_path) == ["original entry"]


def test_batch_replace_holding_the_delimiter_is_refused(store, tmp_path):
    assert store.add("user", "original entry")["success"] is True
    before = _raw(store)

    result = store.apply_batch(
        "user",
        [
            {"action": "add", "content": "a clean entry"},
            {
                "action": "replace",
                "old_text": "original entry",
                "content": "Deploy checklist:\n§\nrun the migration first",
            },
        ],
    )

    assert result["success"] is False
    assert result["error"].startswith("Operation 2: ")
    assert _raw(store) == before
    assert _reload(tmp_path) == ["original entry"]


def test_batch_entry_ending_in_a_lone_marker_is_refused(store, tmp_path):
    """The neighbour-stealing shape, with the neighbour in the same batch."""
    assert store.add("user", "original entry")["success"] is True
    before = _raw(store)

    result = store.apply_batch(
        "user",
        [
            {"action": "add", "content": "first"},
            {"action": "add", "content": "Deploy:\n§"},
            {"action": "add", "content": "second"},
        ],
    )

    assert result["success"] is False
    assert result["error"].startswith("Operation 2: ")
    assert _raw(store) == before
    assert _reload(tmp_path) == ["original entry"]


def test_batch_with_harmless_markers_still_applies(store, tmp_path):
    result = store.apply_batch(
        "user",
        [
            {"action": "add", "content": "the unit cost is 5§ per item"},
            {"action": "add", "content": "note:\n  §\nstill one entry"},
            {"action": "add", "content": "§"},
        ],
    )

    assert result["success"] is True
    assert _reload(tmp_path) == ["the unit cost is 5§ per item", "note:\n  §\nstill one entry", "§"]


def test_batch_whitespace_only_content_still_reports_it_is_required(store):
    """Blank content is not a delimiter problem: the batch's own check answers it."""
    result = store.apply_batch("user", [{"action": "add", "content": "   \n "}])

    assert result["success"] is False
    assert "§" not in result["error"]
    assert "content is required" in result["error"]
