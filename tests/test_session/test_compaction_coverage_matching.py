"""Compaction coverage compares an obligation with a summary on equal terms.

``verify_summary_coverage`` asks "is this continuity fact in the summary?" with
a plain substring test. The obligation side of that test is not raw transcript
text, though — ``_clean_obligation_text`` collapses its whitespace and, past
``_MAX_OBLIGATION_VALUE_CHARS``, cuts it short and marks the cut with an
ellipsis. The summary side went in untouched, so two classes of obligation
could not match however faithfully the summary reproduced them:

* a value long enough to be capped, because the ellipsis is this module's own
  mark and never appears in the summary — those were reported missing 100% of
  the time;
* a value taken from a line with a double space or a tab, because the stored
  value had already had those collapsed.

Nothing raises. The coverage status drops to ``pass_with_backfill``,
``critical_carry_forward`` is padded with text the summary already holds, and
``missing_obligations`` — which the compaction report and the ``sessions`` RPC
both surface — names facts that are present. With ``coverage_blocking`` turned
on it is worse: the compaction is abandoned, so the session never frees any
context.
"""

from __future__ import annotations

from agentos.session.compaction import (
    CompactionConfig,
    CompactionRequest,
    compact_context,
)
from agentos.session.compaction_state import (
    build_structured_summary_from_text,
    extract_compaction_obligations,
)

#: Comfortably past the 240-character cap once the "Goal:" label is dropped.
_LONG_GOAL = "ship the release " + "with every migration rehearsed on staging first " * 6


def _user_goal(entries: list[dict]) -> object:
    obligations = extract_compaction_obligations(entries)
    return next(o for o in obligations if o.kind == "user_goal"), obligations


def test_a_capped_obligation_is_covered_by_a_summary_that_quotes_it_in_full() -> None:
    line = f"Goal: {_LONG_GOAL}"
    goal, obligations = _user_goal([{"role": "user", "content": line, "id": 1}])
    assert goal.value.endswith("..."), "precondition: the value was capped"

    # A summary that reproduces the user's line verbatim, in full.
    summary_text = f"The user asked for the following.\n{line}\nWork has started."
    assert _LONG_GOAL in summary_text

    summary, coverage = build_structured_summary_from_text(summary_text, obligations)

    assert coverage.missing_obligations == [], "the goal is right there in the summary"
    assert coverage.status == "pass"
    assert coverage.covered_obligations == coverage.checked_obligations
    assert summary.critical_carry_forward == [], (
        "carrying forward text the summary already holds spends the budget "
        "that genuinely missing facts need"
    )


def test_an_obligation_from_a_double_spaced_line_is_covered() -> None:
    line = "Goal: ship  the  release"
    goal, obligations = _user_goal([{"role": "user", "content": line, "id": 1}])
    assert goal.value == "ship the release", "precondition: whitespace was collapsed"

    _summary, coverage = build_structured_summary_from_text(f"Recap.\n{line}\n", obligations)

    assert coverage.missing_obligations == []
    assert coverage.status == "pass"


async def test_compaction_is_not_abandoned_over_a_double_space() -> None:
    """The whole chain, offline: no api_key, so the fallback summarizer runs.

    The fallback previews each message at 200 characters, so this short line
    reaches the summary verbatim — double spaces and all.
    """
    entries: list[dict] = [
        {"role": "user", "content": "Goal: ship  the  release", "token_count": 400}
    ]
    entries += [
        {"role": "assistant", "content": f"Working on step {i}. " * 40, "token_count": 400}
        for i in range(8)
    ]

    result = await compact_context(
        CompactionRequest(
            session_id="s1",
            entries=entries,
            context_window_tokens=2000,
            config=CompactionConfig(coverage_blocking=True),
        )
    )

    assert result.skip_reason is None, "compaction was abandoned over a fact it had kept"
    assert result.removed_count > 0
    assert result.coverage_status == "pass"
    assert "Goal: ship  the  release" in result.summary


# --- Guards: green before and after the fix, so they prove nothing on their own. ---


def test_positive_control_a_genuinely_absent_obligation_is_still_reported() -> None:
    """Without this the assertions above could pass on a check that never reports."""
    obligations = extract_compaction_obligations(
        [{"role": "user", "content": "Goal: migrate the billing ledger", "id": 1}]
    )

    _summary, coverage = build_structured_summary_from_text(
        "The assistant tidied up some unrelated logs.", obligations
    )

    assert coverage.missing_obligations == ["user_goal: migrate the billing ledger"]
    assert coverage.status == "pass_with_backfill"
    assert coverage.covered_obligations == 0


def test_guard_an_ordinary_short_obligation_still_matches_exactly() -> None:
    obligations = extract_compaction_obligations(
        [{"role": "user", "content": "Goal: migrate the billing ledger", "id": 1}]
    )

    _summary, coverage = build_structured_summary_from_text(
        "Recap: migrate the billing ledger, then verify.", obligations
    )

    assert coverage.missing_obligations == []
    assert coverage.status == "pass"
