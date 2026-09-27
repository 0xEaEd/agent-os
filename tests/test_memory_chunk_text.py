"""``chunk_text`` must never re-emit the same chunk start line.

The backward overlap scan in ``chunk_text`` walks from a just-emitted
chunk's end back toward its start, stopping once it has accumulated
``chunk_overlap`` tokens. When one line in that span (a long URL, a base64
data URI, a minified single-line JSON/JS blob) is on its own already >=
``chunk_overlap``, or ``chunk_overlap`` exceeds the whole chunk, the scan
used to walk all the way back to the chunk's own start and stop there --
the next chunk then started at the exact same line, one line longer than
the last, over and over, duplicating that line's content into every
following chunk until enough later lines finally pushed the scan past it
(#3348). Store keys each duplicate as a distinct row (``_chunk_id`` hashes
in ``end_line``/``text_hash``), so nothing downstream deduplicates them --
each is a real, wasted embedding call and a near-duplicate search result.
"""

from __future__ import annotations

from agentos.memory.embedding import chunk_text


def test_an_oversized_line_is_not_duplicated_across_chunks() -> None:
    long_line = "x" * 8000 + "\n"
    short_lines = "".join(f"line {i} of normal text here\n" for i in range(300))
    text = long_line + short_lines

    chunks = chunk_text(text, chunk_tokens=400, chunk_overlap=50)

    starts = [start for start, _end, _body in chunks]
    assert len(starts) == len(set(starts)), f"duplicate chunk start lines: {starts}"

    reincluding = [body for _s, _e, body in chunks if body.startswith("x" * 50)]
    assert len(reincluding) == 1, (
        f"the oversized line must appear in exactly one chunk, got {len(reincluding)}"
    )


def test_chunk_starts_always_advance() -> None:
    """General guard, not just the oversized-line shape: a chunk's start line
    must never repeat, whatever caused the backward scan to stall."""
    long_line = "x" * 8000 + "\n"
    short_lines = "".join(f"line {i} of normal text here\n" for i in range(300))
    text = long_line + short_lines

    chunks = chunk_text(text, chunk_tokens=400, chunk_overlap=50)

    for i in range(len(chunks) - 1):
        assert chunks[i][0] < chunks[i + 1][0], (
            f"chunk {i} start {chunks[i][0]} did not advance past chunk {i + 1} start "
            f"{chunks[i + 1][0]}"
        )


def test_degenerate_overlap_larger_than_chunk_budget_still_advances() -> None:
    """chunk_overlap > chunk_tokens is a config a caller could still pass;
    it must degrade to no-overlap chunking, not loop on one start line."""
    text = "".join(f"x{i}\n" for i in range(50))

    chunks = chunk_text(text, chunk_tokens=5, chunk_overlap=50)

    starts = [start for start, _end, _body in chunks]
    assert starts == sorted(set(starts)), f"non-advancing or unsorted starts: {starts}"
    # The chunker must still cover the whole input.
    assert chunks[-1][1] == len(text.splitlines())


def test_ordinary_text_still_overlaps_normally() -> None:
    """The fix must not remove overlap for the common case -- only the
    pathological non-advancing case changes."""
    text = "".join(f"line {i} of ordinary text with a few words in it\n" for i in range(200))

    chunks = chunk_text(text, chunk_tokens=400, chunk_overlap=50)

    assert len(chunks) > 1
    for i in range(len(chunks) - 1):
        end_of_current = chunks[i][1]
        start_of_next = chunks[i + 1][0]
        assert start_of_next <= end_of_current, "adjacent chunks should still overlap"
        assert start_of_next > chunks[i][0], "the next chunk must advance past this chunk's start"


def test_empty_text_is_unchanged() -> None:
    """Matches the already-settled #3172 wontfix: an empty document's
    (1, 0) range is deliberately not this fix's concern."""
    assert chunk_text("") == [(1, 0, "")]
