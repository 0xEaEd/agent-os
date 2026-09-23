"""Pure request builder + response parser for the Jev strategy."""

from __future__ import annotations

from agentos.agentos_router.jev import (
    HIGH_RISK_QUESTION,
    ROUTE_QUESTION,
    build_jev_request,
    build_route_criteria,
    parse_jev_response,
)

TIERS = {
    "c0": {"model": "m0", "description": "trivial chat"},
    "c1": {"model": "m1", "description": "normal work"},
    "c2": {"model": "m2", "description": "hard work"},
    "c3": {"model": "m3", "description": "very hard, high stakes"},
    "image_model": {"model": "mv", "image_only": True, "description": "vision"},
}


def test_criteria_come_from_tier_descriptions_with_extras() -> None:
    criteria = build_route_criteria(TIERS)
    assert list(criteria) == ["R0", "R1", "R2", "R3"]
    assert criteria["R0"]["what"] == "trivial chat"
    assert criteria["R3"]["what"] == "very hard, high stakes"
    for entry in criteria.values():
        assert set(entry) == {"what", "not_for", "examples"}
        assert entry["not_for"]
        assert entry["examples"]
    # Vietnamese boundary examples survive the migration from the judge prompt.
    assert any("xoá bảng users" in ex for ex in criteria["R3"]["examples"])


def test_criteria_fall_back_when_description_missing_or_tiers_absent() -> None:
    partial = {"c0": {"model": "m0"}, "c2": {"model": "m2", "description": ""}}
    criteria = build_route_criteria(partial)
    assert list(criteria) == ["R0", "R1", "R2", "R3"]
    assert criteria["R0"]["what"]
    assert criteria["R2"]["what"]
    assert build_route_criteria(None)["R1"]["what"]


def test_request_shape_and_truncation() -> None:
    body = build_jev_request(
        message="x" * 9000, model="jev-1.13.0", input_max_chars=4000, tiers=TIERS
    )
    assert body["model"] == "jev-1.13.0"
    assert "chars omitted" in body["state"]
    assert len(body["state"]) < 9000
    questions = body["questions"]
    assert set(questions) == {ROUTE_QUESTION, HIGH_RISK_QUESTION}
    assert questions[ROUTE_QUESTION]["type"] == "choice"
    assert set(questions[ROUTE_QUESTION]["criteria"]) == {"R0", "R1", "R2", "R3"}
    assert questions[HIGH_RISK_QUESTION]["type"] == "noul"
    assert "instructions" in questions[HIGH_RISK_QUESTION]


def test_request_uses_prebuilt_criteria_when_given() -> None:
    criteria = {"R0": {"what": "a"}, "R1": {"what": "b"}, "R2": {"what": "c"}, "R3": {"what": "d"}}
    body = build_jev_request(message="hi", criteria=criteria)
    assert body["questions"][ROUTE_QUESTION]["criteria"] is criteria


def _response(choice: str = "R2", confidence: float = 0.81, noul: float | None = 0.1) -> dict:
    answers: dict = {
        ROUTE_QUESTION: {
            "type": "choice",
            "choice": choice,
            "confidence": confidence,
            "probabilities": {"R0": 0.02, "R1": 0.1, "R2": 0.8, "R3": 0.08},
        }
    }
    if noul is not None:
        answers[HIGH_RISK_QUESTION] = {"type": "noul", "noul": noul}
    return {
        "model": "jev-1.13.0",
        "answers": answers,
        "usage": {"input_tokens": 392, "output_tokens": 65},
    }


def test_parse_happy_path() -> None:
    verdict = parse_jev_response(_response())
    assert verdict is not None
    assert verdict.route_class == "R2"
    assert verdict.confidence == 0.81
    assert verdict.probabilities == {"R0": 0.02, "R1": 0.1, "R2": 0.8, "R3": 0.08}
    assert verdict.high_risk == 0.1
    assert verdict.model == "jev-1.13.0"
    assert verdict.usage == {"input_tokens": 392, "output_tokens": 65}


def test_parse_lowercase_choice_and_missing_noul() -> None:
    verdict = parse_jev_response(_response(choice="r3", noul=None))
    assert verdict is not None
    assert verdict.route_class == "R3"
    assert verdict.high_risk is None


def test_parse_rejects_missing_or_invalid_route() -> None:
    assert parse_jev_response(None) is None
    assert parse_jev_response({"answers": {}}) is None
    assert parse_jev_response({"answers": {ROUTE_QUESTION: {"choice": "R9"}}}) is None
    assert parse_jev_response({"answers": {ROUTE_QUESTION: "R1"}}) is None


def test_parse_synthesizes_one_hot_when_probabilities_missing() -> None:
    verdict = parse_jev_response({"answers": {ROUTE_QUESTION: {"choice": "R1"}}})
    assert verdict is not None
    assert verdict.probabilities == {"R0": 0.0, "R1": 1.0, "R2": 0.0, "R3": 0.0}
    # Confidence falls back to the top-1 probability.
    assert verdict.confidence == 1.0


def test_parse_clamps_out_of_range_values() -> None:
    payload = _response(confidence=1.7, noul=-0.2)
    payload["answers"][ROUTE_QUESTION]["probabilities"]["R2"] = "nope"
    verdict = parse_jev_response(payload)
    assert verdict is not None
    assert verdict.confidence == 1.0
    assert verdict.high_risk == 0.0
    assert verdict.probabilities["R2"] == 0.0
