"""Structured cron schedules honour the ``timezone`` alias like the shorthand path.

Regression for #1603: ``{"schedule": {"kind": "cron", ..., "timezone": X}}``
dropped ``X`` on the floor and silently scheduled the job in UTC, while
``{"expression": ..., "timezone": X}`` kept it. Both spellings now resolve,
and the same conflict / type checks ``_top_level_tz`` applies to the
top-level pair apply inside ``schedule``.
"""

from __future__ import annotations

import pytest

from agentos.scheduler.schedule_normalizer import coerce_schedule, coerce_schedule_from_params
from agentos.scheduler.types import ScheduleKind


def test_structured_cron_honours_timezone_alias() -> None:
    kind, expr, tz = coerce_schedule_from_params(
        {"schedule": {"kind": "cron", "expr": "0 9 * * 1-5", "timezone": "Asia/Shanghai"}}
    )

    assert kind == ScheduleKind.CRON
    assert expr == "0 9 * * 1-5"
    assert tz == "Asia/Shanghai"


def test_coerce_schedule_honours_timezone_alias_directly() -> None:
    # ``coerce_schedule`` is also reached without the params wrapper.
    assert coerce_schedule(
        {"kind": "cron", "expr": "0 9 * * *", "timezone": " Asia/Shanghai "}
    ) == (ScheduleKind.CRON, "0 9 * * *", "Asia/Shanghai")


def test_structured_cron_accepts_matching_tz_and_timezone() -> None:
    _, _, tz = coerce_schedule_from_params(
        {
            "schedule": {
                "kind": "cron",
                "expr": "0 9 * * *",
                "tz": "Asia/Shanghai",
                "timezone": "Asia/Shanghai",
            }
        }
    )
    assert tz == "Asia/Shanghai"


def test_structured_cron_prefers_tz_over_a_blank_timezone() -> None:
    _, _, tz = coerce_schedule_from_params(
        {"schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "Asia/Shanghai", "timezone": ""}}
    )
    assert tz == "Asia/Shanghai"


def test_structured_cron_rejects_conflicting_tz_and_timezone() -> None:
    with pytest.raises(ValueError, match=r"schedule\.tz conflicts with schedule\.timezone"):
        coerce_schedule_from_params(
            {
                "schedule": {
                    "kind": "cron",
                    "expr": "0 9 * * *",
                    "tz": "Asia/Shanghai",
                    "timezone": "America/Los_Angeles",
                }
            }
        )


def test_structured_timezone_alias_rejects_conflicting_top_level_tz() -> None:
    with pytest.raises(ValueError, match=r"schedule\.tz conflicts with tz"):
        coerce_schedule_from_params(
            {
                "schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
                "tz": "America/Los_Angeles",
            }
        )


def test_structured_timezone_alias_rejects_conflicting_top_level_timezone() -> None:
    with pytest.raises(ValueError, match=r"schedule\.tz conflicts with tz"):
        coerce_schedule_from_params(
            {
                "schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
                "timezone": "America/Los_Angeles",
            }
        )


def test_structured_timezone_alias_agrees_with_top_level_tz() -> None:
    _, _, tz = coerce_schedule_from_params(
        {
            "schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": "Asia/Shanghai"},
            "tz": "Asia/Shanghai",
        }
    )
    assert tz == "Asia/Shanghai"


def test_top_level_tz_fills_a_blank_structured_timezone() -> None:
    _, _, tz = coerce_schedule_from_params(
        {
            "schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": "  "},
            "tz": "Asia/Shanghai",
        }
    )
    assert tz == "Asia/Shanghai"


@pytest.mark.parametrize(
    "bad", [5, 0, ["Asia/Shanghai"], [], {"name": "Asia/Shanghai"}, {}, True, False]
)
def test_structured_cron_rejects_non_string_timezone(bad: object) -> None:
    with pytest.raises(ValueError, match=r"schedule\.timezone must be a string IANA timezone"):
        coerce_schedule_from_params(
            {"schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": bad}}
        )


def test_structured_cron_rejects_non_string_timezone_even_with_top_level_tz() -> None:
    # A top-level tz used to paper over a garbage ``schedule.tz``; the alias
    # gets the same strictness as ``schedule.tz`` on its own.
    with pytest.raises(ValueError, match=r"schedule\.timezone must be a string IANA timezone"):
        coerce_schedule_from_params(
            {
                "schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": 5},
                "tz": "Asia/Shanghai",
            }
        )


@pytest.mark.parametrize("bad", [5, 0, [], {}, False])
def test_structured_cron_rejects_non_string_tz(bad: object) -> None:
    # Falsy non-strings used to slip through ``raw.get("tz") or ""`` and
    # schedule the job in UTC; the alias must not inherit that hole.
    with pytest.raises(ValueError, match=r"schedule\.tz must be a string IANA timezone"):
        coerce_schedule_from_params({"schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": bad}})


def test_structured_cron_treats_null_tz_and_timezone_as_unset() -> None:
    assert coerce_schedule({"kind": "cron", "expr": "0 9 * * *", "tz": None, "timezone": None}) == (
        ScheduleKind.CRON,
        "0 9 * * *",
        "",
    )


def test_structured_timezone_alias_is_validated() -> None:
    with pytest.raises(ValueError, match=r"schedule\.tz invalid"):
        coerce_schedule_from_params(
            {"schedule": {"kind": "cron", "expr": "0 9 * * *", "timezone": "Mars/Base"}}
        )


def test_timezone_alias_is_ignored_for_non_cron_kinds() -> None:
    # ``every`` / ``at`` carry no tz today; a stray alias is not an error,
    # exactly like a stray ``tz`` on those kinds.
    kind, value, tz = coerce_schedule_from_params(
        {"schedule": {"kind": "every", "every_seconds": 300, "timezone": "Asia/Shanghai"}}
    )
    assert (kind, value, tz) == (ScheduleKind.EVERY, "300", "")
