"""Issue #3420: skills.search clamped only the top of the limit range.

``limit = min(int(params.get("limit", 20)), 500)`` caps the ceiling and lets
anything below it through, so a non-positive value reached the sources
unchecked. That is not an error there -- it is a *wrong answer*:

* ``CapminalSource.search`` ends in ``return results[:limit]``, and
  ``results[:-5]`` is every result except the last five. A caller asking for
  ``limit=-5`` got a silently truncated set, not an empty one and not a
  failure.
* ``ClawhubSource.search`` puts the value straight into the remote API's own
  ``limit`` query parameter.
* ``limit=0`` yields nothing at all, with no indication why.

``search.query`` already refuses anything outside ``1..20``; this endpoint
clamps instead, matching the ceiling clamp it already applies.
"""

from __future__ import annotations

import pytest

from agentos.gateway import rpc_skills
from agentos.skills.hub.source import SkillMeta


class _StubRouter:
    """Records the limit it is handed, and slices with it the way a real
    source does -- so a negative value truncates here too."""

    def __init__(self, results: list[SkillMeta]) -> None:
        self._results = results
        self.calls: list[dict] = []

    async def search(self, query: str, limit: int = 20, source_id: str | None = None):
        self.calls.append({"query": query, "limit": limit, "source_id": source_id})
        return self._results[:limit]


class _Ctx:
    def __init__(self, router: _StubRouter) -> None:
        self._skill_router = router
        self.skill_loader = None


def _no_lockfile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpc_skills, "_installed_names", lambda: set())
    monkeypatch.setattr(rpc_skills, "installed_skill_identifiers", lambda: set())
    monkeypatch.setattr(rpc_skills, "_installed_lock_entries", dict)


def _results(count: int) -> list[SkillMeta]:
    return [SkillMeta(name=f"skill-{i}", source_id="capminal") for i in range(count)]


async def _search(monkeypatch: pytest.MonkeyPatch, limit: object, *, total: int = 8):
    _no_lockfile(monkeypatch)
    router = _StubRouter(_results(total))
    payload = await rpc_skills._handle_skills_search(
        {"query": "test", "limit": limit}, _Ctx(router)
    )
    return router, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [-5, -1, 0])
async def test_a_non_positive_limit_is_raised_to_one(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    """The issue's repro: what reaches the sources must be usable."""
    router, _ = await _search(monkeypatch, limit)

    assert router.calls[0]["limit"] == 1


@pytest.mark.asyncio
async def test_a_negative_limit_no_longer_truncates_the_result_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consequence, asserted rather than the clamp alone: `results[:-5]`
    on eight results returned three of them, which reads as a complete answer
    to a caller with no way to know otherwise."""
    router, payload = await _search(monkeypatch, -5, total=8)

    assert router.calls[0]["limit"] == 1
    assert len(payload["results"]) == 1  # not 3, and not 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "expected"), [(1, 1), (20, 20), (500, 500), (5000, 500)])
async def test_the_usable_range_and_the_ceiling_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, limit: int, expected: int
) -> None:
    """The 500 cap exists so the browse gallery can request whole catalogs;
    the floor must not disturb it."""
    router, _ = await _search(monkeypatch, limit)

    assert router.calls[0]["limit"] == expected


@pytest.mark.asyncio
async def test_a_missing_limit_still_defaults_to_twenty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_lockfile(monkeypatch)
    router = _StubRouter(_results(3))

    await rpc_skills._handle_skills_search({"query": "test"}, _Ctx(router))

    assert router.calls[0]["limit"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", ["abc", None, {}])
async def test_an_unparseable_limit_still_falls_back_to_twenty(
    monkeypatch: pytest.MonkeyPatch, limit: object
) -> None:
    """The existing except-branch keeps its behaviour; the floor is applied
    inside the try, not instead of it."""
    router, _ = await _search(monkeypatch, limit)

    assert router.calls[0]["limit"] == 20
