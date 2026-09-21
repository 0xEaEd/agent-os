"""``memory_search`` CJK bigrams must come from one unbroken run.

The bigrams were built over a flat list of every CJK character in the query,
which discarded whatever separated them: two characters that merely appeared in
the same query produced a term, so "東京 大阪" (Tokyo, Osaka) yielded
"京大" -- the tail of one word glued to the head of the next, a word the query
never contained (#3180). Those phantom terms feed ``_query_line_score`` and
``_truncate_line_around_query``, so an unrelated memory line could win the
excerpt over the line that actually matched.

Query strings are written as escapes so the intent survives any editor: the
comment on each names the words.
"""

from __future__ import annotations

from agentos.tools.builtin.memory_tools import (
    _memory_search_query_terms,
    _query_line_score,
)

TOKYO = "東京"
OSAKA = "大阪"
#: The cross-boundary pair: last char of TOKYO + first char of OSAKA.
PHANTOM = "京大"
CAT = "猫"
DOG = "犬"


def test_a_pair_split_by_a_space_is_not_a_term() -> None:
    terms = _memory_search_query_terms(f"{TOKYO} {OSAKA}")

    assert PHANTOM not in terms
    assert TOKYO in terms and OSAKA in terms


def test_single_characters_split_by_a_space_are_not_glued() -> None:
    terms = _memory_search_query_terms(f"{CAT} {DOG}")

    assert CAT + DOG not in terms
    assert CAT in terms and DOG in terms


def test_a_pair_split_by_latin_text_is_not_a_term() -> None:
    terms = _memory_search_query_terms(f"{TOKYO} and {OSAKA}")

    assert PHANTOM not in terms


def test_a_pair_split_by_punctuation_is_not_a_term() -> None:
    terms = _memory_search_query_terms(f"{TOKYO}、{OSAKA}")  # ideographic comma

    assert PHANTOM not in terms


def test_adjacent_characters_still_form_a_bigram() -> None:
    """In one unbroken run the pair is genuinely adjacent, so it stays."""
    terms = _memory_search_query_terms(TOKYO + OSAKA)

    assert PHANTOM in terms
    assert TOKYO in terms and OSAKA in terms


def test_every_character_of_a_run_is_still_a_term() -> None:
    terms = _memory_search_query_terms(TOKYO + OSAKA)

    for character in TOKYO + OSAKA:
        assert character in terms


def test_every_term_actually_occurs_in_the_query() -> None:
    """The invariant the phantom bigram broke: a term is a piece of the query."""
    for query in (f"{TOKYO} {OSAKA}", f"{CAT}, {DOG}", f"{TOKYO} and {OSAKA}"):
        for term in _memory_search_query_terms(query):
            assert term in query, f"{term!r} is not in {query!r}"


def test_a_phantom_line_no_longer_scores_on_the_glued_pair() -> None:
    """The defect as the excerpt picker sees it.

    The single characters still match -- they are genuinely in the query -- but
    the glued pair must not add a third hit on top of them.
    """
    terms = _memory_search_query_terms(f"{TOKYO} {OSAKA}")
    unrelated = f"{PHANTOM} is a university, unrelated to the query"

    assert _query_line_score(unrelated, terms) == 2, "the two unigrams, and nothing more"


def test_the_matching_line_still_scores() -> None:
    terms = _memory_search_query_terms(f"{TOKYO} {OSAKA}")

    assert _query_line_score(f"a trip from {TOKYO} to {OSAKA}", terms) > 0


def test_an_ascii_query_tokenizes_exactly_as_before() -> None:
    assert _memory_search_query_terms("Deploy the gateway service") == (
        "deploy",
        "gateway",
        "service",
    )
