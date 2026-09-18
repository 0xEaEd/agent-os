from __future__ import annotations

import pytest

from agentos.tools.builtin.code_exec import execute_code
from agentos.tools.types import ToolError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_timeout",
    ["nan", float("nan"), float("inf"), float("-inf"), 0, -5, "not_a_number"],
)
async def test_execute_code_rejects_invalid_timeout(bad_timeout: object) -> None:
    with pytest.raises(ToolError, match="Invalid timeout"):
        await execute_code(code="print('hello')", timeout=bad_timeout)  # type: ignore[arg-type]
