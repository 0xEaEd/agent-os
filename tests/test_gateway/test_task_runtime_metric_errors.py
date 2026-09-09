"""Regression tests for issue #1188: a failed metric record leaves a trace.

``_emit_metric`` used to swallow every failure with a bare ``pass``, so a
metric that never reached Prometheus looked identical to one that did.

The module logger is patched rather than captured with
``structlog.testing.capture_logs``: structlog is configured globally, and a
cached bound logger makes the capture context a no-op depending on what ran
earlier in the session.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from agentos.gateway import task_runtime
from agentos.observability import metrics as metrics_mod


def _boom(name: str, value: float = 1.0, **labels: object) -> None:
    raise RuntimeError("registry exploded")


def test_record_failure_is_logged_at_debug_with_the_metric_name(monkeypatch) -> None:
    monkeypatch.setattr(metrics_mod, "record_metric", _boom)

    with patch.object(task_runtime, "log") as mock_log:
        task_runtime._emit_metric("turn_cancellations_total", value=1, reason="timeout")

    mock_log.debug.assert_called_once_with(
        "metric_record_failed",
        metric="turn_cancellations_total",
        error_type="RuntimeError",
        error="registry exploded",
    )


def test_an_unimportable_metrics_module_is_logged_too(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "agentos.observability.metrics", None)

    with patch.object(task_runtime, "log") as mock_log:
        task_runtime._emit_metric("queue_full_errors_total", value=1)

    assert mock_log.debug.call_count == 1
    assert mock_log.debug.call_args.kwargs["metric"] == "queue_full_errors_total"


def test_a_record_failure_does_not_break_the_caller(monkeypatch) -> None:
    monkeypatch.setattr(metrics_mod, "record_metric", _boom)

    with patch.object(task_runtime, "log") as mock_log:
        task_runtime._emit_metric("in_flight_turns_total", value=1, session_key="agent:main:s1")

    # The metric log line the CI grep asserts on is still emitted.
    mock_log.info.assert_called_once_with(
        "in_flight_turns_total",
        metric="in_flight_turns_total",
        value=1,
        session_key="agent:main:s1",
    )


def test_a_successful_record_logs_no_failure(monkeypatch) -> None:
    recorded: list[tuple[str, float, dict]] = []

    def ok(name: str, value: float = 1.0, **labels: object) -> None:
        recorded.append((name, value, dict(labels)))

    monkeypatch.setattr(metrics_mod, "record_metric", ok)

    with patch.object(task_runtime, "log") as mock_log:
        task_runtime._emit_metric(
            "turn_cancellations_total",
            value=1,
            reason="timeout",
            session_key="agent:main:s1",
        )

    assert not mock_log.debug.called
    # session_key stays out of the exported labels; reason stays in.
    assert recorded == [("turn_cancellations_total", 1, {"reason": "timeout"})]


@pytest.mark.parametrize("label", ["session_key", "session_id", "turn_id"])
def test_unbounded_identifiers_are_still_dropped_from_exported_labels(
    monkeypatch, label: str
) -> None:
    recorded: list[dict] = []

    def ok(name: str, value: float = 1.0, **labels: object) -> None:
        recorded.append(dict(labels))

    monkeypatch.setattr(metrics_mod, "record_metric", ok)

    task_runtime._emit_metric("turn_cancellations_total", value=1, **{label: "x"})

    assert recorded == [{}]
