"""Issue #3182: a ``detail`` error body reached the model as raw JSON.

``summarize_error_body`` inspected ``error`` and ``message`` and nothing
else. ``{"detail": ...}`` is what a FastAPI/Starlette application returns by
default -- which covers vLLM, Ollama's proxy, and most self-hosted inference
servers sitting in front of a model -- so those bodies fell through every
JSON branch and were emitted verbatim by the final ``_clip(text, ...)``.

The module exists precisely to stop a raw payload reaching the transcript,
so the one provider shape it did not know was the one it could not help with.
"""

from __future__ import annotations

import json

import pytest

from agentos.provider.error_body import summarize_error_body


def test_a_string_detail_is_extracted() -> None:
    """The issue's repro."""
    body = json.dumps({"detail": "Authentication token expired"})

    assert summarize_error_body(body) == "Authentication token expired"


def test_a_validation_detail_names_the_field_that_failed() -> None:
    """FastAPI's 422 shape. ``msg`` alone reads as 'field required' with no
    subject; ``loc`` is what makes it actionable."""
    body = json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "messages"],
                    "msg": "field required",
                    "type": "value_error.missing",
                }
            ]
        }
    )

    assert summarize_error_body(body) == "body.messages: field required"


def test_several_validation_errors_are_joined() -> None:
    body = json.dumps({"detail": [{"msg": "too long"}, {"msg": "bad model"}]})

    assert summarize_error_body(body) == "too long; bad model"


def test_a_list_of_plain_strings_is_joined() -> None:
    body = json.dumps({"detail": ["plain one", "plain two"]})

    assert summarize_error_body(body) == "plain one; plain two"


def test_no_raw_json_survives_into_the_summary() -> None:
    """The property the module is for: whatever shape the body took, the
    summary must not still be a JSON document."""
    for body in (
        json.dumps({"detail": "Authentication token expired"}),
        json.dumps({"detail": [{"loc": ["body"], "msg": "field required"}]}),
        json.dumps({"detail": ["one"]}),
    ):
        summary = summarize_error_body(body)
        assert "{" not in summary and "detail" not in summary, body


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # `error` and `message` are checked first and must keep winning.
        ({"error": {"message": "upstream said no"}, "detail": "ignored"}, "upstream said no"),
        ({"error": "flat error", "detail": "ignored"}, "flat error"),
        ({"message": "top level", "detail": "ignored"}, "top level"),
    ],
)
def test_the_existing_fields_still_take_precedence(body: dict, expected: str) -> None:
    assert summarize_error_body(json.dumps(body)) == expected


@pytest.mark.parametrize(
    "detail",
    [{}, None, "", "   ", [], [{}], [{"type": "x"}], 42],
)
def test_an_unusable_detail_falls_through_unchanged(detail: object) -> None:
    """No new branch may swallow a body it cannot actually summarise -- the
    raw text is a worse answer than the old one only if it is also wrong."""
    body = json.dumps({"detail": detail})

    assert summarize_error_body(body) == body


def test_html_and_plain_text_bodies_are_unaffected() -> None:
    assert summarize_error_body("plain text error") == "plain text error"
    assert "502 Bad Gateway" in summarize_error_body(
        "<html><head><title>502 Bad Gateway</title></head></html>"
    )


def test_a_long_detail_is_still_clipped() -> None:
    body = json.dumps({"detail": "x" * 900})

    summary = summarize_error_body(body, max_chars=100)

    assert summary.startswith("x" * 100)
    assert "truncated" in summary
