"""Offline tests for ``scripts/pilot_router/evaluate_jev.py``.

The script's network half (one Jev call per case) is not exercised here; the
row→report reducer ``summarize`` is pure and is pinned on hand-computed
fixtures, and ``--dry-run`` must build a request body with no API key and no
network.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "pilot_router" / "evaluate_jev.py"


def _load_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate_jev = _load_by_path("evaluate_jev_under_test", SCRIPT_PATH)


def _row(
    row_id: str,
    gold: str,
    pred: str,
    *,
    lang: str = "en",
    probs: dict[str, float] | None = None,
    source: str = "jev",
    reason: str = "",
    floor: bool = False,
    usage: dict[str, int] | None = None,
) -> dict:
    return {
        "id": row_id,
        "lang": lang,
        "tags": [],
        "gold_class": gold,
        "route_class": pred,
        "pred_class": pred,
        "source": source,
        "reason": reason,
        "probabilities": probs,
        "confidence": max(probs.values()) if probs else 0.0,
        "high_risk_noul": None,
        "high_risk_floor_applied": floor,
        "usage": usage,
    }


@pytest.fixture
def fake_rows() -> list[dict]:
    return [
        _row(
            "en_r0",
            "R0",
            "R0",
            probs={"R0": 0.9, "R1": 0.05, "R2": 0.03, "R3": 0.02},
            reason="jev R0 p=0.90 high_risk=0.01",
            usage={"input_tokens": 100, "output_tokens": 2},
        ),
        _row(
            "vi_r2_under",
            "R2",
            "R1",
            lang="vi",
            probs={"R0": 0.1, "R1": 0.6, "R2": 0.2, "R3": 0.1},
            reason="jev R1 p=0.60 high_risk=0.05",
            usage={"input_tokens": 200, "output_tokens": 2},
        ),
        _row(
            "vi_r1_unavailable",
            "R1",
            "R1",
            lang="vi",
            source="jev_unavailable",
            reason="jev_unavailable: http 429: rate limited",
        ),
        _row(
            "en_r3_floor",
            "R3",
            "R3",
            probs={"R0": 0.0, "R1": 0.0, "R2": 0.2, "R3": 0.8},
            reason="jev R3 p=0.80 high_risk=0.95",
            floor=True,
            usage={"input_tokens": 300, "output_tokens": 2},
        ),
    ]


# ---------------------------------------------------------------------------
# summarize (pure reducer)
# ---------------------------------------------------------------------------


def test_summarize_counts_and_accuracy(fake_rows) -> None:
    report = evaluate_jev.summarize(fake_rows)
    assert report["n"] == 4
    assert report["n_healthy"] == 3
    assert report["n_unavailable"] == 1
    # 3 of 4 correct (the unavailable row lands on the default tier R1 == gold).
    assert report["metrics"]["accuracy"] == pytest.approx(0.75)
    assert report["metrics"]["under_routing_rate"] == pytest.approx(0.25)
    assert report["metrics"]["over_routing_rate"] == pytest.approx(0.0)
    # R2 -> R1 is an adjacent drop: penalty 1 over 4 rows.
    assert report["metrics"]["severity_weighted_under_routing"] == pytest.approx(0.25)
    # Healthy-only view drops the degraded row: 2 of 3.
    assert report["metrics_healthy"]["n"] == 3
    assert report["metrics_healthy"]["accuracy"] == pytest.approx(2 / 3)


def test_summarize_confusion_matrix(fake_rows) -> None:
    report = evaluate_jev.summarize(fake_rows)
    mat = report["metrics"]["confusion_matrix"]
    assert mat[0][0] == 1  # gold R0 -> pred R0
    assert mat[2][1] == 1  # gold R2 -> pred R1
    assert mat[1][1] == 1  # gold R1 -> pred R1 (unavailable default)
    assert mat[3][3] == 1  # gold R3 -> pred R3
    assert sum(sum(r) for r in mat) == 4


def test_summarize_ece_and_nll_from_probabilities(fake_rows) -> None:
    report = evaluate_jev.summarize(fake_rows)
    # Three rows carry probabilities (0.9 correct, 0.6 wrong, 0.8 correct),
    # each landing in its own 10-wide bin: ECE = (0.1 + 0.6 + 0.2) / 3.
    assert report["calibration"]["n_scored"] == 3
    assert report["calibration"]["ece_bins"] == 10
    assert report["calibration"]["ece"] == pytest.approx(0.3)
    expected_nll = -(math.log(0.9) + math.log(0.2) + math.log(0.8)) / 3
    assert report["calibration"]["nll"] == pytest.approx(expected_nll)


def test_summarize_per_lang_slices(fake_rows) -> None:
    report = evaluate_jev.summarize(fake_rows)
    assert report["per_lang"]["en"]["n"] == 2
    assert report["per_lang"]["en"]["accuracy"] == pytest.approx(1.0)
    assert report["per_lang"]["vi"]["n"] == 2
    assert report["per_lang"]["vi"]["accuracy"] == pytest.approx(0.5)
    assert report["per_lang"]["vi"]["under_routing_rate"] == pytest.approx(0.5)


def test_summarize_unavailable_floor_and_usage(fake_rows) -> None:
    report = evaluate_jev.summarize(fake_rows)
    assert report["unavailable_by_reason"] == {"http 429: rate limited": 1}
    assert report["high_risk_floor_applied"] == 1
    assert report["usage"]["input_tokens"] == 600
    assert report["usage"]["output_tokens"] == 6
    assert report["usage"]["estimated_cost_usd"] == pytest.approx(600 / 1e6 * 0.042)


def test_summarize_empty_rows() -> None:
    report = evaluate_jev.summarize([])
    assert report["n"] == 0
    assert report["metrics"]["accuracy"] == 0.0
    assert report["unavailable_by_reason"] == {}
    assert report["per_lang"] == {}


def test_report_is_json_serializable_without_nan(fake_rows) -> None:
    # NaN (e.g. recall on an unsupported class) must be emitted as null so the
    # JSON stays standard-conformant.
    report = evaluate_jev.summarize(fake_rows[:1])
    text = json.dumps(evaluate_jev.json_safe(report), allow_nan=False)
    assert "NaN" not in text
    assert json.loads(text)["metrics"]["per_class_recall"]["R3"] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_dry_run_builds_request_body_without_key(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    rc = evaluate_jev.main(["--dry-run", "--limit", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    body = json.loads(out[out.index("{") :])
    assert body["model"] == "jev-latest"
    assert body["questions"]["route"]["type"] == "choice"
    assert set(body["questions"]["route"]["criteria"]) == {"R0", "R1", "R2", "R3"}
    assert body["questions"]["high_risk"]["type"] == "noul"
    assert body["state"]


def test_missing_key_exits_2_with_hint(monkeypatch, capsys) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    rc = evaluate_jev.main(["--limit", "1"])
    assert rc == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_tier_descriptions_mirror_router_eval() -> None:
    # The classifier-only eval must build its route criteria from the same
    # tier descriptions the engine-level eval (scripts/router_eval.py) uses.
    router_eval = _load_by_path(
        "router_eval_for_jev_test", REPO_ROOT / "scripts" / "router_eval.py"
    )
    assert {k: v["description"] for k, v in evaluate_jev.TIERS.items()} == {
        k: v["description"] for k, v in router_eval.TIERS.items()
    }
