import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "src" / "agentos" / "skills" / "bundled" / "deep-research" / "scripts"
COMPILE_SCRIPT = SCRIPTS / "compile.py"
ITERATE_SCRIPT = SCRIPTS / "iterate.py"


def test_compile_rejects_corrupted_plan_json(tmp_path: Path) -> None:
    bad_plan = tmp_path / "bad_plan.json"
    bad_plan.write_text("not json content", encoding="utf-8")
    out = tmp_path / "report.md"

    res = subprocess.run(
        [sys.executable, str(COMPILE_SCRIPT), "--plan", str(bad_plan), "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "not valid JSON or plan schema" in res.stderr


def test_iterate_rejects_corrupted_plan_json(tmp_path: Path) -> None:
    bad_plan = tmp_path / "bad_plan.json"
    bad_plan.write_text('{"missing": "fields"}', encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(ITERATE_SCRIPT), "--plan", str(bad_plan), "--print-fetches"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "not valid JSON or plan schema" in res.stderr


def test_iterate_rejects_corrupted_record_json(tmp_path: Path) -> None:
    good_plan = tmp_path / "good_plan.json"
    # Create valid plan
    sys.path.insert(0, str(SCRIPTS))
    try:
        from plan import Plan
    finally:
        sys.path.pop(0)

    p = Plan(
        question="Q",
        depth="overview",
        created_at="2026-09-18T00:00:00Z",
        rounds=0,
        subquestions=[],
    )
    good_plan.write_text(p.model_dump_json(), encoding="utf-8")

    bad_record = tmp_path / "bad_record.json"
    bad_record.write_text("not json", encoding="utf-8")

    res = subprocess.run(
        [
            sys.executable,
            str(ITERATE_SCRIPT),
            "--plan",
            str(good_plan),
            "--record",
            str(bad_record),
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "not valid JSON" in res.stderr

