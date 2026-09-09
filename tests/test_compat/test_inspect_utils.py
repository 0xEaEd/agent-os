"""Contract for the one shared ``accepts_keyword_arg``.

Four modules used to carry their own copy, and the copies disagreed on the
failure path: ``rpc_sessions`` returned True for an uninspectable callable,
``channel_dispatch`` and ``context_overflow`` returned False, and
``engine.runtime`` let the exception escape. These tests pin the single answer
so a future edit cannot re-introduce the drift silently.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from agentos.compat.inspect_utils import accepts_keyword_arg


def _explicit(a: int, *, token_count: int | None = None) -> None: ...


def _var_keyword(a: int, **kwargs: Any) -> None: ...


def _neither(a: int, b: int) -> None: ...


def _var_positional(*args: Any) -> None: ...


class _Callable:
    def __call__(self, *, thread_id: str | None = None) -> None: ...

    def method(self, *, channel_id: str | None = None) -> None: ...


class TestAccepts:
    def test_explicit_keyword_parameter(self) -> None:
        assert accepts_keyword_arg(_explicit, "token_count") is True

    def test_var_keyword_absorbs_anything(self) -> None:
        assert accepts_keyword_arg(_var_keyword, "anything_at_all") is True

    def test_bound_method(self) -> None:
        assert accepts_keyword_arg(_Callable().method, "channel_id") is True

    def test_callable_instance_uses_dunder_call(self) -> None:
        assert accepts_keyword_arg(_Callable(), "thread_id") is True

    def test_lambda(self) -> None:
        assert accepts_keyword_arg(lambda *, source=None: None, "source") is True


class TestRejects:
    def test_parameter_is_absent(self) -> None:
        assert accepts_keyword_arg(_neither, "token_count") is False

    def test_var_positional_does_not_absorb_a_keyword(self) -> None:
        assert accepts_keyword_arg(_var_positional, "token_count") is False

    def test_positional_name_still_counts_as_accepted(self) -> None:
        # ``a`` is positional-or-keyword, so it can be passed by keyword.
        assert accepts_keyword_arg(_neither, "a") is True


class TestUninspectableCallable:
    """The one behavior the four copies disagreed on."""

    @pytest.mark.parametrize("exc", [TypeError("no signature"), ValueError("no signature")])
    def test_signature_failure_reports_not_accepted(
        self, exc: Exception, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(_obj: Any) -> Any:
            raise exc

        monkeypatch.setattr(inspect, "signature", _boom)

        # False, not True and not a propagated exception: passing a keyword the
        # target may not take fails the call, while omitting an optional one
        # leaves the target on its own default.
        assert accepts_keyword_arg(_explicit, "token_count") is False


class TestSingleImplementation:
    """No module may keep a private copy of this predicate."""

    def test_no_module_redefines_accepts_keyword_arg(self) -> None:
        import ast
        from pathlib import Path

        import agentos

        src_root = Path(agentos.__file__).parent
        canonical = src_root / "compat" / "inspect_utils.py"
        offenders: list[str] = []
        for path in sorted(src_root.rglob("*.py")):
            if path == canonical:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.endswith(
                    "accepts_keyword_arg"
                ):
                    offenders.append(f"{path.relative_to(src_root.parent)}:{node.lineno}")
        assert offenders == [], (
            "accepts_keyword_arg has exactly one implementation, in "
            f"agentos/compat/inspect_utils.py; found copies at {offenders}"
        )
