"""Backward-compatible alias for :mod:`agentos.compat.install_method`.

The implementation moved to ``agentos.compat`` so the gateway's update RPC can
share it without a ``gateway -> cli`` import edge. Import the real module for
anything that needs to monkeypatch its internals; this shim only re-exports
the public names.
"""

from __future__ import annotations

from agentos.compat.install_method import (
    DIST_NAME,
    UPGRADE_EXTRAS,
    InstallMethod,
    UpgradePlan,
    build_upgrade_plan,
    detect_install_method,
    hardened_path_env,
    installed_from_directory,
    release_spec,
    resolve_tool,
    runtime_python_tag,
)

__all__ = [
    "DIST_NAME",
    "UPGRADE_EXTRAS",
    "InstallMethod",
    "UpgradePlan",
    "build_upgrade_plan",
    "detect_install_method",
    "hardened_path_env",
    "installed_from_directory",
    "release_spec",
    "resolve_tool",
    "runtime_python_tag",
]
