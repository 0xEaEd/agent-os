"""The ``LIKE`` fallback for sub-trigram terms folded only ASCII case.

#2897 moved ``session_search`` onto the ``trigram`` tokenizer so a query
matches wherever it occurs, in any script and in any case, and answered terms
below the three-character trigram floor (``go``, ``db``, a two-character CJK
word) with a ``LIKE`` scan "instead of by nothing".

SQLite's ``LIKE`` is case-insensitive for ASCII and case-*sensitive* for every
other character, so that fallback kept the promise only for the ASCII half of
its own example: ``db`` found ``DB``, but ``бд`` did not find ``БД`` and ``är``
did not find ``ÄR``. The tool reported "No matches found." for a transcript it
holds -- the same silent miss #2897 set out to remove, in the path that fix
added.
"""

from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from agentos.session.manager import SessionManager
from agentos.session.storage import SessionStorage, _like_case_forms

pytestmark = pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 34, 0),
    reason="FTS5 trigram tokenizer needs SQLite 3.34+",
)

RU = "мигрируем БД на postgres в пятницу"
EN = "the DB migration plan for postgres"
DE = "wir haben ÄRGER mit dem Deployment"
EL = "το ΣΧ διάγραμμα είναι έτοιμο"
TEXTS = [RU, EN, DE, EL]


@pytest_asyncio.fixture
async def storage():
    store = SessionStorage(":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest_asyncio.fixture
async def session(storage):
    manager = SessionManager(storage, inject_time_prefix=False)
    session = await manager.create("agent:main:webchat:aaaa0001", agent_id="main")
    for text in TEXTS:
        await manager.append_message(session.session_key, role="user", content=text)
    return session


# ── the report ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("бд", "БД"),  # Cyrillic, the exact shape of #2897's own ``db`` example
        ("БД", "БД"),
        ("är", "ÄRGER"),  # Latin-1 supplement
        ("ÄR", "ÄRGER"),
        ("σχ", "ΣΧ"),  # Greek
        ("ΣΧ", "ΣΧ"),
    ],
)
@pytest.mark.asyncio
async def test_a_short_non_ascii_term_matches_in_either_case(storage, session, typed, stored):
    hits = await storage.search_transcript(typed)
    assert len(hits) == 1, f"{typed!r} found nothing, but the transcript holds {stored!r}"
    marked = hits[0]["snippet"]
    assert f">>>{typed}<<<".lower() in marked.lower(), marked


@pytest.mark.parametrize("typed", ["db", "DB", "Db"])
@pytest.mark.asyncio
async def test_the_ascii_half_of_the_same_example_still_matches(storage, session, typed):
    """Positive control: ASCII already worked; the fix must not regress it."""
    assert len(await storage.search_transcript(typed)) == 1


# ── the fallback's own contract ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        ("бд", {"бд", "Бд", "бД", "БД"}),
        ("db", {"db", "Db", "dB", "DB"}),
        ("数据", {"数据"}),  # caseless: one form, no fan-out
        ("", set()),  # unreachable via _WORD_RE, but must not yield a "%%" pattern
    ],
)
def test_case_forms_are_bounded_and_complete(term, expected):
    forms = _like_case_forms(term)
    assert set(forms) == expected
    assert len(forms) == len(set(forms)), "forms must not repeat a pattern"
    assert len(forms) <= 4, "a sub-floor term is at most two characters"


@pytest.mark.asyncio
async def test_a_caseless_cjk_term_still_matches(storage):
    """CJK has no case, so the expansion must leave that path untouched."""
    manager = SessionManager(storage, inject_time_prefix=False)
    key = (await manager.create("agent:main:webchat:aaaa0002", agent_id="main")).session_key
    await manager.append_message(key, role="user", content="我们讨论了数据库迁移计划")
    assert len(await storage.search_transcript("数据")) == 1


@pytest.mark.asyncio
async def test_a_like_wildcard_in_a_short_term_stays_literal(storage):
    """The case expansion runs before escaping, so ``%`` must not leak through."""
    manager = SessionManager(storage, inject_time_prefix=False)
    key = (await manager.create("agent:main:webchat:aaaa0003", agent_id="main")).session_key
    await manager.append_message(key, role="user", content="a plain line with no percent sign")
    assert await storage.search_transcript("%") == []
