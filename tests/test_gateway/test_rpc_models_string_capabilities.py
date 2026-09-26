"""Issue #3418: a string capabilities filter matched nothing.

``collect_models`` did ``required = set(capabilities_filter)``. A bare string
is iterable, so ``set("chat")`` is ``{'c','h','a','t'}`` -- four single
letters. No model's capability list contains a single letter, so
``issubset`` failed for every model and ``models.list`` answered with an
empty list.

That is the shape worth fixing: not an exception, a **wrong answer**. The
caller sees "no models available" and has nothing to go on.

Scope note: the issue also reported a ``TypeError`` on a non-iterable filter
and an ``AttributeError`` on non-dict ``params``. Both are malformed
arguments whose exceptions the RPC dispatcher already converts into a
structured error response, which is the #2877 / #2480 ruling, so they are
deliberately not touched here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentos.gateway.rpc import RpcContext, get_dispatcher


def _ctx(rows: list[dict]) -> RpcContext:
    ctx = RpcContext(conn_id="test")
    ctx.config = SimpleNamespace(llm=SimpleNamespace(provider="deepseek"))
    ctx.model_catalog = SimpleNamespace(
        list_models=lambda: [SimpleNamespace(model_dump=lambda row=row: row) for row in rows]
    )
    return ctx


def _two_models() -> RpcContext:
    return _ctx(
        [
            {"model_id": "deepseek-chat", "provider": "deepseek", "supports_reasoning": False},
            {"model_id": "deepseek-r1", "provider": "deepseek", "supports_reasoning": True},
        ]
    )


def _list(ctx: RpcContext, capabilities: object) -> list[dict]:
    result = asyncio.run(
        get_dispatcher().dispatch("r1", "models.list", {"capabilities": capabilities}, ctx)
    )
    assert result.error is None, result.error
    return result.payload


def test_a_string_capability_filters_instead_of_matching_nothing() -> None:
    """The issue's repro: every model has ``chat``, so every model matches."""
    models = _list(_two_models(), "chat")

    assert [m["id"] for m in models] == ["deepseek-chat", "deepseek-r1"]


def test_a_string_capability_still_excludes_models_without_it() -> None:
    """The filter has to still filter -- returning everything would be the
    same bug with a friendlier face."""
    models = _list(_two_models(), "reasoning")

    assert [m["id"] for m in models] == ["deepseek-r1"]


def test_a_string_and_a_single_element_list_agree() -> None:
    """The two spellings of one capability must answer identically."""
    assert _list(_two_models(), "reasoning") == _list(_two_models(), ["reasoning"])


def test_a_list_filter_is_unchanged() -> None:
    models = _list(_two_models(), ["chat", "reasoning"])

    assert [m["id"] for m in models] == ["deepseek-r1"]


def test_an_unknown_capability_string_still_matches_nothing() -> None:
    """An empty result is the right answer here, and must stay reachable."""
    assert _list(_two_models(), "telepathy") == []


@pytest.mark.parametrize("empty", ["", [], None])
def test_an_empty_filter_returns_every_model(empty: object) -> None:
    """Falsy filters skip the filter entirely, as before."""
    models = _list(_two_models(), empty)

    assert [m["id"] for m in models] == ["deepseek-chat", "deepseek-r1"]
