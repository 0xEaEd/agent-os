"""deep-research compile: the report carries what SKILL.md says it carries.

SKILL.md's Output list names the Methodology block's source count and every
reference's relevance. Relevance is the entire output of the five-axis rubric in
references/sources.md, whose bar calls anything below 0.40 a dead end -- and the
same rubric tells the host to record a paywalled page with `relevance: 0`. Strip
the score at render time and the finished report, which is read by someone who
never saw plan.json, cannot tell that source from a primary filing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "deep-research" / "scripts"


def _import_scripts():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import compile as compile_script  # type: ignore[import-not-found]
        import plan as plan_script  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return plan_script, compile_script


def _plan_with_sources(plan_script, sources: list[dict[str, object]], *, rounds: int = 1):
    return plan_script.Plan(
        question="Did revenue grow?",
        depth="overview",
        created_at="2026-01-01T00:00:00Z",
        rounds=rounds,
        subquestions=[
            plan_script.SubQuestion(
                id="sq-001",
                question="Did revenue grow?",
                target_sources=3,
                sources=[plan_script.Source(**source) for source in sources],
            )
        ],
    )


def _render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_script, plan) -> str:
    _, compile_script = _import_scripts()
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    out_path = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["compile.py", "--plan", str(plan_path), "--out", str(out_path)],
    )
    assert compile_script.main() == 0
    return out_path.read_text(encoding="utf-8")


_STRONG: dict[str, object] = {
    "url": "https://sec.gov/filing",
    "title": "SEC 10-K",
    "excerpt": "Revenue rose 14%.",
    "relevance": 0.92,
    "fetched_at": "2026-05-06T10:14:00Z",
}
_DEAD_END: dict[str, object] = {
    "url": "https://paywalled.example/story",
    "title": "Paywalled story",
    "excerpt": "Revenue rose 40%.",
    "relevance": 0.0,
    "fetched_at": "2026-05-06T10:15:00Z",
}


def test_each_reference_carries_its_relevance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_script, _ = _import_scripts()
    report = _render(tmp_path, monkeypatch, plan_script, _plan_with_sources(plan_script, [_STRONG]))

    references = report.split("## References", 1)[1]
    assert "[^1]: <https://sec.gov/filing> — SEC 10-K" in references
    assert "[relevance 0.92]" in references


def test_a_zero_relevance_source_is_distinguishable_from_a_strong_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # references/sources.md tells the host to record a paywalled page with
    # relevance 0 and says below 0.40 is a dead end. The report has to show it.
    plan_script, _ = _import_scripts()
    report = _render(
        tmp_path,
        monkeypatch,
        plan_script,
        _plan_with_sources(plan_script, [_STRONG, _DEAD_END]),
    )

    references = report.split("## References", 1)[1]
    strong, dead_end = [line for line in references.splitlines() if line.startswith("[^")]
    assert "[relevance 0.92]" in strong
    assert "[relevance 0.00]" in dead_end


def test_relevance_is_printed_even_when_the_source_has_no_title_or_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_script, _ = _import_scripts()
    report = _render(
        tmp_path,
        monkeypatch,
        plan_script,
        _plan_with_sources(plan_script, [{"url": "https://example.com/bare", "relevance": 0.55}]),
    )

    references = report.split("## References", 1)[1]
    assert "[^1]: <https://example.com/bare> [relevance 0.55]" in references


def test_the_methodology_block_names_the_source_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_script, _ = _import_scripts()
    report = _render(
        tmp_path,
        monkeypatch,
        plan_script,
        _plan_with_sources(plan_script, [_STRONG, _DEAD_END], rounds=2),
    )

    methodology = report.split("## Methodology", 1)[1].split("## Findings", 1)[0]
    assert "2 research rounds" in methodology
    assert "2 recorded sources" in methodology


def test_the_source_count_is_zero_when_nothing_was_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_script, _ = _import_scripts()
    report = _render(
        tmp_path, monkeypatch, plan_script, _plan_with_sources(plan_script, [], rounds=0)
    )

    methodology = report.split("## Methodology", 1)[1].split("## Findings", 1)[0]
    assert "0 recorded sources" in methodology
    assert "## References" in report


def test_the_body_and_gap_sections_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Positive control: this is a References/Methodology render fix, so the
    # citations, the findings bullets and the gap accounting must be untouched.
    plan_script, _ = _import_scripts()
    report = _render(
        tmp_path,
        monkeypatch,
        plan_script,
        _plan_with_sources(plan_script, [_STRONG, _DEAD_END]),
    )

    findings = report.split("## Findings", 1)[1].split("## What this report", 1)[0]
    assert "- Revenue rose 14%. [^1]" in findings
    assert "- Revenue rose 40%. [^2]" in findings
    gaps = report.split("## What this report does not cover", 1)[1]
    assert "**Did revenue grow?** (2/3 sources collected)" in gaps
