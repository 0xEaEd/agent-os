"""Section outline for skill bodies too large to hand back whole.

``skill_view`` used to return every byte of a SKILL.md. That is fine for the
shipped set — 37 bundled skills, median 2.4k characters — but skills installed
from a hub or written for another agent run far larger: 56k characters for one
hub skill, 87k for one in ``~/.agents/skills``. At roughly four characters per
token that is 14k–22k tokens spent in a single tool result, paid again on every
re-read, and none of it is cached the way the system prompt is.

The fix is not to cut the text off. It is to return the opening sections whole
and hand back an index of the rest, so the agent can ask for the one section it
needs. Everything here is a pure function over the body text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentos.lines import split_lines_keepends

#: Bodies at or under this are returned whole. Sized from the shipped set: the
#: largest bundled skill is 21.6k characters and the median is 2.4k, so a 10k
#: ceiling leaves every small skill untouched and only engages for the imported
#: ones that motivated it.
DEFAULT_MAX_SKILL_VIEW_CHARS = 10_000

#: How far below the shallowest heading the index descends. Measured on real
#: skills: listing every heading of a 56k-character skill costs 3.5k characters
#: — an index nearly as expensive as the problem — while two levels costs 320 to
#: 1100 and still names every part worth asking for.
_OUTLINE_DEPTH = 2

#: Entries the index may name before it collapses to a single level. Depth is
#: normally cheap, but a 56k-character skill carries 95 headings two levels
#: down, and naming them costs 3.5k characters — the problem again in a
#: different shape. Collapsing loses no reach: ``section=`` resolves against
#: every heading, and asking for a parent indexes its children.
_MAX_OUTLINE_ENTRIES = 30

#: Written on its own line directly above a heading, this pins that section:
#: ``skill_view`` returns it whether or not the budget reaches that far. It is
#: an HTML comment so it renders as nothing wherever the file is read as
#: markdown. Position in the file otherwise decides what a model sees, which
#: makes a rule added to a large skill invisible for no reason the author can
#: tell from reading their own change.
ALWAYS_MARKER = "<!-- always -->"

#: The share of the view budget pinned sections may take between them. Pinning
#: everything would leave no head, and a skill that opens on its index reads as
#: a table of contents rather than as a skill.
_ALWAYS_BUDGET_SHARE = 0.5

_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t]*#*[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]*(```+|~~~+)")


@dataclass(frozen=True)
class Section:
    """One heading and everything under it, including deeper headings."""

    level: int
    title: str
    #: Offset of the heading line in the body.
    start: int
    #: Offset one past the last character owned by this section.
    end: int
    #: Titles of the enclosing sections, outermost first.
    ancestors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        return self.end - self.start

    @property
    def path(self) -> str:
        """``Parent > Child`` — how the index names an ambiguous title."""
        return " > ".join((*self.ancestors, self.title))


def parse_sections(body: str) -> list[Section]:
    """Split ``body`` at its markdown headings, in document order.

    Fenced code is skipped: a shell transcript or a nested markdown example
    routinely contains lines starting with ``#``, and treating those as headings
    would invent sections that cannot be read back.
    """
    if not body:
        return []

    found: list[tuple[int, str, int]] = []  # (level, title, start offset)
    offset = 0
    fence: str | None = None  # the opening run, e.g. "```" or "````"
    # ``split_lines_keepends``: ``str.splitlines()`` also ends a line on a
    # form feed, a vertical tab, a lone CR, NEL and U+2028/9, none of which
    # end a line in markdown -- so a ``#`` after one of them was read as a
    # heading and invented a section, the very thing the fence guard above
    # exists to prevent (#3588). ``keepends`` is kept: ``offset`` is the
    # running character position and has to count the terminators.
    for line in split_lines_keepends(body):
        stripped = line.strip()
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            run = fence_match.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and stripped == run:
                # CommonMark: a fence closes only on the same character, at
                # least as long, and alone on its line. Skills that document
                # markdown nest a ``` block inside a ```` one, and closing on
                # the inner fence would expose its headings as real sections.
                fence = None
            offset += len(line)
            continue
        if fence is None:
            heading = _HEADING_RE.match(line)
            if heading is not None:
                found.append((len(heading.group(1)), heading.group(2).strip(), offset))
        offset += len(line)

    sections: list[Section] = []
    for index, (level, title, start) in enumerate(found):
        # A section owns everything up to the next heading at its level or
        # shallower, so asking for a parent returns its children too.
        end = len(body)
        for next_level, _next_title, next_start in found[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        # Walk back for the innermost enclosing heading of each shallower
        # level: those, outermost first, are what disambiguates a title that
        # appears under more than one parent.
        trimmed: list[str] = []
        innermost = level
        for prev_level, prev_title, _prev_start in reversed(found[:index]):
            if prev_level < innermost:
                trimmed.append(prev_title)
                innermost = prev_level
        ancestors = tuple(reversed(trimmed))
        sections.append(
            Section(level=level, title=title, start=start, end=end, ancestors=ancestors)
        )
    return sections


def always_sections(body: str, sections: list[Section]) -> list[Section]:
    """The sections marked with :data:`ALWAYS_MARKER`, in document order.

    The marker is the nearest non-blank line above the heading. Requiring it to
    be the nearest line is what keeps a skill that *documents* the marker from
    pinning whatever heading happens to follow the discussion — and a marker
    shown inside a code fence pins nothing at all, because the heading under it
    is not a section in the first place.

    A section already inside a pinned one is dropped: a section owns its
    subsections, so marking both a parent and a child would render the child
    twice and charge the budget for it twice.
    """
    pinned: list[Section] = []
    for section in sections:
        # rstrip drops the blank lines an author leaves between the marker and
        # the heading, so what remains ends on the nearest non-blank line.
        preceding = body[: section.start].rstrip()
        nearest = preceding[preceding.rfind("\n") + 1 :].strip()
        if nearest != ALWAYS_MARKER:
            continue
        if any(outer.start <= section.start and section.end <= outer.end for outer in pinned):
            continue
        pinned.append(section)
    return pinned


def within_always_budget(pinned: list[Section], limit: int) -> list[Section]:
    """The pinned sections that fit their share of *limit*, in document order.

    A section that would push the total past the share is dropped rather than
    truncated: half a rule is worse than a line in the index saying where the
    whole one is.
    """
    if limit <= 0:
        return []
    share = int(limit * _ALWAYS_BUDGET_SHARE)
    kept: list[Section] = []
    used = 0
    for section in pinned:
        if used + section.size > share:
            continue
        kept.append(section)
        used += section.size
    return kept


def render_pinned(body: str, pinned: list[Section]) -> str:
    """Render pinned sections verbatim, under a line saying why they are here."""
    if not pinned:
        return ""
    parts = [
        "Marked by the skill as rules rather than reference, and so returned in "
        "full wherever they sit in it:"
    ]
    parts.extend(body[section.start : section.end].strip() for section in pinned)
    return "\n\n".join(parts)


def _base_level(sections: list[Section]) -> int:
    """The heading level that actually partitions the body.

    Most large skills open with a single ``# Title`` owning every byte, so the
    shallowest level is not a partition at all — indexing it yields one entry
    the size of the whole skill, and a head built from it is empty. Descend past
    any level that is one all-encompassing heading.
    """
    if not sections:
        return 1
    body_len = max(s.end for s in sections)
    level = min(s.level for s in sections)
    while True:
        at_level = [s for s in sections if s.level == level]
        deeper = [s.level for s in sections if s.level > level]
        spans_everything = len(at_level) == 1 and at_level[0].size >= body_len * 0.9
        if spans_everything and deeper:
            level = min(deeper)
            continue
        return level


def indexable(sections: list[Section]) -> list[Section]:
    """The sections the index names, and that ``section=`` can address."""
    if not sections:
        return []
    base = _base_level(sections)
    wide = [s for s in sections if base <= s.level <= base + _OUTLINE_DEPTH - 1]
    if len(wide) <= _MAX_OUTLINE_ENTRIES:
        return wide
    return [s for s in sections if s.level == base]


def find_section(sections: list[Section], query: str) -> Section | list[Section] | None:
    """Resolve ``query`` to one section.

    Returns the section, a list of candidates when the title is ambiguous, or
    ``None`` when nothing matches. Matching is deliberately forgiving — the
    agent is quoting a title back from an index, and a case or spacing
    difference should not cost it a whole extra tool call.
    """
    wanted = " ".join(query.split()).casefold()
    if not wanted:
        return None

    def norm(text: str) -> str:
        return " ".join(text.split()).casefold()

    for candidates in (
        [s for s in sections if norm(s.path) == wanted],
        [s for s in sections if norm(s.title) == wanted],
        [s for s in sections if wanted in norm(s.title)],
    ):
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            shallowest = min(s.level for s in candidates)
            top = [s for s in candidates if s.level == shallowest]
            return top[0] if len(top) == 1 else candidates
    return None


def head_sections(body: str, sections: list[Section], limit: int) -> tuple[str, int]:
    """Return the opening of ``body`` that fits ``limit``, cut on a boundary.

    Cutting mid-sentence would make the head read as the whole skill with a
    ragged ending. Whole sections are taken instead, so the head always ends
    where the author ended something.
    """
    if limit <= 0:
        return "", 0
    # The last heading that starts within the budget: everything before it is
    # whole sections, and the head ends exactly where a heading begins. Every
    # heading counts here, not just the indexed ones — the index decides what is
    # worth naming, while the head only needs the closest clean cut to the
    # budget, and a coarse one wastes most of it.
    fits = [s.start for s in sections if 0 < s.start <= limit]
    if fits:
        cut = max(fits)
    else:
        # Nothing to cut on — the opening section alone overruns. Fall back to a
        # paragraph boundary so the head at least ends on a blank line.
        cut = body.rfind("\n\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
    return body[:cut].rstrip(), cut


def _human(count: int) -> str:
    return f"{count:,}"


def render_outline(
    sections: list[Section],
    *,
    shown_through: int,
    skill_name: str,
    also_shown: list[Section] | None = None,
) -> str:
    """Render the index of sections, marking the ones already returned.

    *also_shown* names sections returned out of order — pinned ones, which sit
    past the cut but were rendered anyway. Without it the index would invite a
    second tool call for text the model is already holding.
    """
    entries = indexable(sections)
    if not entries:
        return ""
    base = _base_level(sections)
    pinned_starts = {section.start for section in (also_shown or [])}
    lines = ["Sections:"]
    for section in entries:
        indent = "  " * (section.level - base)
        shown = section.end <= shown_through or section.start in pinned_starts
        note = " (shown above)" if shown else ""
        lines.append(f"- {indent}{section.title} — {_human(section.size)} chars{note}")
    lines.append("")
    lines.append(
        f'Read one with skill_view(name="{skill_name}", section="<title>"). '
        "Ask for a parent to get its subsections with it."
    )
    return "\n".join(lines)


def render_linked_files(paths: list[str], skill_name: str) -> str:
    """Render the skill's supporting files, which are never inlined."""
    if not paths:
        return ""
    lines = ["Linked files:"]
    lines.extend(f"- {path}" for path in sorted(paths))
    lines.append("")
    lines.append(f'Read one with skill_view(name="{skill_name}", file_path="<path>").')
    return "\n".join(lines)
