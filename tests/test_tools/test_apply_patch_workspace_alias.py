from __future__ import annotations

from pathlib import Path

import pytest

from agentos.tools.builtin.patch import _validate_path


def test_validate_path_resolves_workspace_alias_to_the_patch_root(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    assert _validate_path("/workspace/src/app.py", root) == root / "src" / "app.py"
    assert _validate_path("/workspace/test.txt", root) == root / "test.txt"


def test_validate_path_keeps_relative_and_in_root_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path.resolve()

    assert _validate_path("src/app.py", root) == root / "src" / "app.py"
    assert _validate_path(str(root / "a.txt"), root) == root / "a.txt"


def test_validate_path_still_rejects_traversal_through_the_alias(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("/workspace/../../etc/passwd", tmp_path.resolve())
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("/etc/passwd", tmp_path.resolve())
