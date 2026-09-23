"""deep-research coverage accounting: one source per URL, per sub-question.

Coverage is ``len(sources)`` against ``target_sources``, and the documented loop
records evidence once per round. Re-offering a URL the plan already holds is the
normal shape of that loop -- the fetch list reports how many sources are missing,
never which ones are in hand -- so counting it again reports a sub-question as
covered by sources it does not have, drops it from the fetch list, and omits it
from the report's own gap section.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "deep-research" / "scripts"


def _import_scripts():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import compile as compile_script  # type: ignore[import-not-found]
        import iterate  # type: ignore[import-not-found]
        import plan as plan_script  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return plan_script, iterate, compile_script


def _make_plan(tmp_path: Path, depth: str = "thorough") -> Path:
    plan_script, _, _ = _import_scripts()
    plan = plan_script.Plan(
        question="How did Manus differentiate in 2025?",
        depth=depth,
        created_at="2026-01-01T00:00:00Z",
        subquestions=plan_script.make_subquestions("test", depth),
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return plan_path


def _evidence(tmp_path: Path, name: str, items: list[dict[str, object]]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def _record(
    iterate,
    monkeypatch: pytest.MonkeyPatch,
    plan_path: Path,
    record_path: Path,
    round_num: int,
) -> dict[str, object]:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "iterate.py",
            "--plan",
            str(plan_path),
            "--round",
            str(round_num),
            "--record",
            str(record_path),
        ],
    )
    assert iterate.main() == 0
    return {}


def _source(url: str, **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "subquestion_id": "sq-001",
        "url": url,
        "title": "A",
        "excerpt": "Manus shipped X.",
        "relevance": 0.9,
        "fetched_at": "2026-05-06T10:14:00Z",
    }
    item.update(overrides)
    return item


def _load(plan_script, plan_path: Path):
    return plan_script.Plan.model_validate_json(plan_path.read_text(encoding="utf-8"))


def test_recording_the_same_url_again_does_not_raise_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_script, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path)
    record_path = _evidence(tmp_path, "ev.json", [_source("https://example.com/a")])

    for round_num in (1, 2, 3):
        _record(iterate, monkeypatch, plan_path, record_path, round_num)
        capsys.readouterr()

    plan = _load(plan_script, plan_path)
    sq = plan.subquestions[0]
    assert [source.url for source in sq.sources] == ["https://example.com/a"]
    assert sq.coverage() == pytest.approx(1 / 3)


def test_duplicate_round_reports_added_zero_and_counts_the_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path)
    record_path = _evidence(tmp_path, "ev.json", [_source("https://example.com/a")])

    _record(iterate, monkeypatch, plan_path, record_path, 1)
    first = json.loads(capsys.readouterr().out)
    assert first["added"] == 1
    assert first["duplicates"] == 0

    _record(iterate, monkeypatch, plan_path, record_path, 2)
    second = json.loads(capsys.readouterr().out)
    assert second["added"] == 0
    assert second["duplicates"] == 1
    assert second["overall_coverage"] == pytest.approx(first["overall_coverage"])


def test_a_revised_title_or_relevance_does_not_make_a_second_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Only the URL decides identity; the other fields are the host's judgement
    # and a later round may score the same page differently.
    plan_script, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path)
    first_path = _evidence(tmp_path, "ev1.json", [_source("https://example.com/a")])
    revised_path = _evidence(
        tmp_path,
        "ev2.json",
        [_source("https://example.com/a", title="A (revised)", relevance=0.4)],
    )

    _record(iterate, monkeypatch, plan_path, first_path, 1)
    capsys.readouterr()
    _record(iterate, monkeypatch, plan_path, revised_path, 2)
    payload = json.loads(capsys.readouterr().out)

    assert payload["duplicates"] == 1
    sources = _load(plan_script, plan_path).subquestions[0].sources
    assert len(sources) == 1
    assert sources[0].title == "A"


def test_surrounding_whitespace_does_not_defeat_the_url_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_script, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path)
    first_path = _evidence(tmp_path, "ev1.json", [_source("https://example.com/a")])
    padded_path = _evidence(tmp_path, "ev2.json", [_source("  https://example.com/a  ")])

    _record(iterate, monkeypatch, plan_path, first_path, 1)
    capsys.readouterr()
    _record(iterate, monkeypatch, plan_path, padded_path, 2)

    assert len(_load(plan_script, plan_path).subquestions[0].sources) == 1


def test_a_repeat_inside_one_record_file_is_recorded_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan_script, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path)
    record_path = _evidence(
        tmp_path,
        "ev.json",
        [_source("https://example.com/a"), _source("https://example.com/a")],
    )

    _record(iterate, monkeypatch, plan_path, record_path, 1)
    payload = json.loads(capsys.readouterr().out)

    assert payload["added"] == 1
    assert payload["duplicates"] == 1
    assert len(_load(plan_script, plan_path).subquestions[0].sources) == 1


def test_distinct_urls_still_accumulate_and_close_the_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Positive control through the same entry point: the guard must not block
    # real progress, and a genuinely covered sub-question must still flip done.
    plan_script, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path, depth="overview")
    record_path = _evidence(
        tmp_path,
        "ev.json",
        [
            _source("https://example.com/a", subquestion_id="sq-001"),
            _source("https://example.com/b", subquestion_id="sq-002"),
            _source("https://example.com/c", subquestion_id="sq-003"),
        ],
    )

    _record(iterate, monkeypatch, plan_path, record_path, 1)
    payload = json.loads(capsys.readouterr().out)

    assert payload["added"] == 3
    assert payload["duplicates"] == 0
    assert payload["done"] is True
    assert payload["overall_coverage"] == pytest.approx(1.0)


def test_a_duplicated_sub_question_stays_in_the_fetch_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, iterate, _ = _import_scripts()
    plan_path = _make_plan(tmp_path)
    record_path = _evidence(tmp_path, "ev.json", [_source("https://example.com/a")])

    for round_num in (1, 2, 3):
        _record(iterate, monkeypatch, plan_path, record_path, round_num)
        capsys.readouterr()

    monkeypatch.setattr(
        sys,
        "argv",
        ["iterate.py", "--plan", str(plan_path), "--round", "4", "--print-fetches"],
    )
    assert iterate.main() == 0
    fetches = json.loads(capsys.readouterr().out)["fetches"]

    entry = next(row for row in fetches if row["subquestion_id"] == "sq-001")
    assert entry["have"] == 1
    assert entry["needs"] == 2


def test_the_report_lists_the_under_covered_sub_question_as_a_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, iterate, compile_script = _import_scripts()
    plan_path = _make_plan(tmp_path)
    record_path = _evidence(tmp_path, "ev.json", [_source("https://example.com/a")])

    for round_num in (1, 2, 3):
        _record(iterate, monkeypatch, plan_path, record_path, round_num)
        capsys.readouterr()

    out_path = tmp_path / "report.md"
    monkeypatch.setattr(
        sys,
        "argv",
        ["compile.py", "--plan", str(plan_path), "--out", str(out_path)],
    )
    assert compile_script.main() == 0
    report = out_path.read_text(encoding="utf-8")

    gaps = report.split("## What this report does not cover", 1)[1]
    assert "**sq-001** (1/3 sources collected)" in gaps
    assert report.count("<https://example.com/a>") == 1
