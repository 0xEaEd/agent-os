"""Malformed ``params`` must fail as INVALID_REQUEST in every interpreter mode.

Several handlers narrowed ``params: dict | None`` with
``assert isinstance(params, dict)``. Assertions are stripped under ``python -O``
and ``PYTHONOPTIMIZE``, so on an optimized interpreter that line is a comment
and a non-mapping payload reaches the subscripts below it — surfacing as an
unhandled ``TypeError`` (``INTERNAL_ERROR``) instead of the ``INVALID_REQUEST``
the protocol promises. ``require_params_dict`` raises unconditionally, so the
guard survives ``-O``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import agentos.gateway  # noqa: F401  - ensure handler modules are imported
from agentos.gateway.access import ConnectionSurface
from agentos.gateway.auth import AccessContext
from agentos.gateway.rpc import RpcContext, get_dispatcher, require_params_dict

GATEWAY_SRC = Path(agentos.gateway.__file__).parent


def _ctx() -> RpcContext:
    return RpcContext(
        conn_id="conn-1",
        access=AccessContext(
            surface=ConnectionSurface.CONTROL, admitted=True, credential_verified=True
        ),
    )


class TestRequireParamsDict:
    def test_returns_the_mapping_unchanged(self) -> None:
        params = {"key": "agent:main:abc"}
        assert require_params_dict(params) is params

    @pytest.mark.parametrize("params", [None, [], ["key"], "key", 7, object()])
    def test_rejects_every_non_mapping(self, params: object) -> None:
        # ValueError is what RpcRegistry.dispatch maps to INVALID_REQUEST.
        with pytest.raises(ValueError, match="params must be an object"):
            require_params_dict(params)


class TestNoStrippableParamGuards:
    """No gateway handler may rely on an ``assert`` to validate ``params``."""

    def test_no_assert_isinstance_params_in_gateway_sources(self) -> None:
        offenders: list[str] = []
        for path in sorted(GATEWAY_SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assert):
                    continue
                test = node.test
                if (
                    isinstance(test, ast.Call)
                    and isinstance(test.func, ast.Name)
                    and test.func.id == "isinstance"
                    and test.args
                    and isinstance(test.args[0], ast.Name)
                    and test.args[0].id == "params"
                ):
                    rel = path.relative_to(GATEWAY_SRC.parent.parent)
                    offenders.append(f"{rel}:{node.lineno}")
        assert offenders == [], (
            "assert-based params validation is stripped by python -O; "
            f"use require_params_dict instead: {offenders}"
        )


class TestDispatcherRejectsNonMappingParams:
    @pytest.mark.parametrize(
        "method",
        ["sessions.patch", "sessions.rename", "env.set", "env.import"],
    )
    @pytest.mark.parametrize("params", [None, [], "key"])
    async def test_invalid_request_instead_of_internal_error(
        self, method: str, params: object
    ) -> None:
        res = await get_dispatcher().dispatch("r1", method, params, _ctx())
        assert res.error is not None, res
        assert res.error.code == "INVALID_REQUEST", res.error
