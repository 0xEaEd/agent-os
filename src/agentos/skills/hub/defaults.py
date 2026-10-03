"""Shared defaults for Community skill sources and installer wiring."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from agentos.skills.hub.aeon import AeonSource
from agentos.skills.hub.bankr import BankrSource
from agentos.skills.hub.capminal import CapminalSource
from agentos.skills.hub.clawhub import ClawHubSource
from agentos.skills.hub.github import GitHubSource
from agentos.skills.hub.install_policy import InstallPolicy
from agentos.skills.hub.installer import SkillInstaller
from agentos.skills.hub.lockfile import Lockfile, default_lockfile_path
from agentos.skills.hub.router import SourceRouter
from agentos.skills.hub.source import SkillSource

log = structlog.get_logger(__name__)

_default_router: SourceRouter | None = None


def get_default_skill_router() -> SourceRouter:
    """Return the default Community source router shared by CLI, RPC, and tools."""

    global _default_router
    if _default_router is None:
        sources: list[SkillSource] = [
            ClawHubSource(token=os.environ.get("CLAWHUB_TOKEN")),
            # Bankr before GitHub: the router dedups merged results by name
            # (first source wins), and GitHub code search can surface the same
            # BankrBot/skills directories as bare, unenriched rows that would
            # otherwise shadow the Bankr rows carrying category/logo/setup.
            BankrSource(token=os.environ.get("GITHUB_TOKEN")),
            CapminalSource(token=os.environ.get("GITHUB_TOKEN")),
            AeonSource(token=os.environ.get("GITHUB_TOKEN")),
            GitHubSource(token=os.environ.get("GITHUB_TOKEN")),
        ]
        _default_router = SourceRouter(sources)
    return _default_router


def resolve_install_policy() -> InstallPolicy:
    """Read ``[skills.install_policy]`` for whichever process is installing.

    The gateway already holds its config; the CLI's no-gateway path and anything
    else that installs in-process load it from disk. A config that cannot be
    read resolves to an enabled policy with no command, which refuses every
    install: an operator who turned the policy on must not lose it to a typo
    elsewhere in the file.
    """
    try:
        from agentos.tools.builtin import control

        config: Any = control._gateway_config
        if config is None:
            from agentos.gateway.config import GatewayConfig

            config = GatewayConfig.load(os.environ.get("AGENTOS_GATEWAY_CONFIG_PATH"))
        section = config.skills.install_policy
        return InstallPolicy(
            enabled=bool(section.enabled),
            command=str(section.command),
            args=tuple(section.args),
            pass_env=tuple(section.pass_env),
            timeout_seconds=float(section.timeout_seconds),
            max_output_bytes=int(section.max_output_bytes),
        )
    except Exception:
        log.warning("skills.install_policy_unreadable", exc_info=True)
        return InstallPolicy(enabled=True)


def build_default_skill_installer(*, managed_dir: Path | None = None) -> SkillInstaller:
    """Build a default installer, optionally aligned to the active loader layer."""

    return SkillInstaller(
        router=get_default_skill_router(),
        managed_dir=managed_dir,
        install_policy=resolve_install_policy(),
    )


def installed_skill_names() -> set[str]:
    """Return skill names recorded as Community installs in the lockfile."""

    return set(Lockfile.load(default_lockfile_path()).installed.keys())


def installed_skill_identifiers() -> set[str]:
    """Return the source identifiers recorded as Community installs.

    The lockfile is keyed by the installed skill's *name* (from its SKILL.md
    frontmatter), which can differ from the catalog slug a browse card carries
    (e.g. Bankr's ``bankr-token-scam-analysis`` slug installs as
    ``token-scam-analysis``). Matching a browse result by identifier as well as
    by name keeps its "installed" badge correct across a page reload.
    """

    return {
        entry.identifier
        for entry in Lockfile.load(default_lockfile_path()).installed.values()
        if entry.identifier
    }
