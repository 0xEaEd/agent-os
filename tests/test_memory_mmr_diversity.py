"""MMR diversity must discriminate between snippets in every script.

``_jaccard_similarity`` tokenized with ``[a-zA-Z0-9]+`` plus a CJK
ideograph/kana pass. A Cyrillic, Greek, Hangul, Arabic, Hebrew, Devanagari or
Thai snippet produced **no tokens at all**, so any two of them hit the
``not ta and not tb`` branch and scored a perfect 1.0 -- the MMR penalty for an
exact duplicate. ``memory_tools._memory_search_query_terms`` already widened
the same ASCII class to ``[^\\W_]+`` for exactly this reason, and its docstring
names this function as the shape it copied the CJK handling from.
"""

from __future__ import annotations

import pytest

from agentos.memory.retrieval import _jaccard_similarity, _mmr_rerank
from agentos.memory.types import MemorySearchResult, MemorySource

#: (label, two unrelated snippets written in the same non-Latin script)
UNRELATED_NON_LATIN: tuple[tuple[str, str, str], ...] = (
    ("cyrillic", "Развёртывание кластера завершено", "Резервное копирование базы данных"),
    ("hangul", "배포 클러스터가 완료되었습니다", "데이터베이스 백업 정책"),
    ("arabic", "اكتمل نشر المجموعة", "سياسة النسخ الاحتياطي لقاعدة البيانات"),
    ("greek", "Η ανάπτυξη ολοκληρώθηκε", "Πολιτική αντιγράφων ασφαλείας"),
    ("hebrew", "פריסת האשכול הושלמה", "מדיניות גיבוי מסד הנתונים"),
    ("devanagari", "क्लस्टर परिनियोजन पूर्ण हुआ", "डेटाबेस बैकअप नीति"),
)


@pytest.mark.parametrize(
    ("label", "left", "right"),
    UNRELATED_NON_LATIN,
    ids=[case[0] for case in UNRELATED_NON_LATIN],
)
def test_unrelated_non_latin_snippets_are_not_identical(label: str, left: str, right: str) -> None:
    assert _jaccard_similarity(left, right) < 0.5


@pytest.mark.parametrize(
    ("label", "left", "_right"),
    UNRELATED_NON_LATIN,
    ids=[case[0] for case in UNRELATED_NON_LATIN],
)
def test_identical_non_latin_snippets_still_score_one(label: str, left: str, _right: str) -> None:
    """The widening must not cost the detector its positive case."""
    assert _jaccard_similarity(left, left) == 1.0


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # ASCII and CJK must tokenize exactly as they did before.
        ("cluster deployment finished", "cluster deployment finished", 1.0),
        ("cluster deployment finished", "database backup policy", 0.0),
        ("集群部署已完成", "集群部署已完成", 1.0),
        ("集群部署已完成", "数据库备份策略", 0.0),
    ],
)
def test_ascii_and_cjk_similarity_is_unchanged(left: str, right: str, expected: float) -> None:
    assert _jaccard_similarity(left, right) == expected


def _result(chunk_id: str, snippet: str, score: float) -> MemorySearchResult:
    return MemorySearchResult(
        chunk_id=chunk_id,
        path=f"memory/{chunk_id}.md",
        source=MemorySource.memory,
        start_line=1,
        end_line=1,
        snippet=snippet,
        score=score,
    )


def test_mmr_keeps_distinct_cyrillic_results() -> None:
    """The visible consequence: distinct results survive diversity selection.

    With every pair scoring 1.0 the MMR penalty is maximal for every remaining
    candidate, so selection degenerates to plain score order and the top result
    of a *different* topic is pushed out by a near-duplicate of the first.
    """
    results = [
        _result("a", "Развёртывание кластера завершено", 1.0),
        _result("b", "Развёртывание кластера завершено успешно", 0.95),
        _result("c", "Резервное копирование базы данных", 0.90),
    ]

    selected = _mmr_rerank(results, lam=0.7, k=2)

    assert [r.chunk_id for r in selected] == ["a", "c"]
