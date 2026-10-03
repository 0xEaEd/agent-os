"""The operator install policy gates a staged skill, and fails closed.

``[skills.install_policy]`` hands the quarantined bundle to an external command
speaking OpenClaw's ``security.installPolicy`` protocol v1. These tests pin the
three decisions, the request the command receives, and that every way of not
answering — no command, a crash, a timeout, garbage — is a block.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agentos.skills.hub.install_policy import (
    InstallPolicy,
    build_policy_request,
    evaluate_install_policy,
    parse_policy_response,
)
from agentos.skills.hub.installer import SkillInstaller, install_security_fields
from agentos.skills.hub.lockfile import Lockfile
from agentos.skills.hub.source import SkillBundle, SkillMeta

pytestmark = pytest.mark.skipif(os.name == "nt", reason="policy fixtures are shebang scripts")

_SKILL_MD = "---\nname: demo\ndescription: Use when testing.\n---\n\n# Demo\n"


class FakeRouter:
    def __init__(self, meta: SkillMeta | None = None) -> None:
        self.meta = meta

    async def fetch(self, identifier: str, source_id: str) -> SkillBundle | None:
        return SkillBundle(name="demo", files={"SKILL.md": _SKILL_MD})

    async def inspect(self, identifier: str, source_id: str) -> SkillMeta | None:
        return self.meta


def _script(tmp_path: Path, body: str) -> str:
    """Write an executable policy command and return its absolute path."""
    path = tmp_path / "policy"
    path.write_text(f"#!{sys.executable}\nimport json, os, sys, time\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _answering(tmp_path: Path, response: dict) -> str:
    return _script(tmp_path, f"sys.stdin.read()\nprint({json.dumps(json.dumps(response))})")


def _installer(tmp_path: Path, policy: InstallPolicy | None, meta: SkillMeta | None = None):
    return SkillInstaller(
        router=FakeRouter(meta),
        managed_dir=tmp_path / "managed",
        quarantine_dir=tmp_path / "quarantine",
        lockfile_path=tmp_path / "lock.json",
        install_policy=policy,
    )


def _request() -> dict:
    return build_policy_request(
        name="demo", source_path="/tmp/q/demo", source_id="clawhub", identifier="demo"
    )


@pytest.mark.asyncio
async def test_allow_installs_and_records_the_decision(tmp_path: Path) -> None:
    command = _answering(tmp_path, {"protocolVersion": 1, "decision": "allow"})
    installer = _installer(tmp_path, InstallPolicy(enabled=True, command=command))

    result = await installer.install("demo", "clawhub")

    assert result.success is True
    assert install_security_fields(result)["policy_decision"] == "allow"
    entry = Lockfile.load(tmp_path / "lock.json").get("demo")
    assert entry is not None and entry.policy_decision == "allow"


@pytest.mark.asyncio
async def test_command_receives_protocol_v1_request_for_the_quarantined_bundle(
    tmp_path: Path,
) -> None:
    seen = tmp_path / "seen.json"
    command = _script(
        tmp_path,
        f"req = json.load(sys.stdin)\n"
        f"req['_staged'] = os.path.isfile(os.path.join(req['sourcePath'], 'SKILL.md'))\n"
        f"req['_env'] = sorted(os.environ)\n"
        f"open({str(seen)!r}, 'w').write(json.dumps(req))\n"
        f"print(json.dumps({{'protocolVersion': 1, 'decision': 'allow'}}))",
    )
    os.environ["AGENTOS_TEST_SECRET_FOR_POLICY"] = "x"
    try:
        installer = _installer(tmp_path, InstallPolicy(enabled=True, command=command))
        result = await installer.install("owner/demo", "clawhub")
    finally:
        del os.environ["AGENTOS_TEST_SECRET_FOR_POLICY"]

    assert result.success is True
    req = json.loads(seen.read_text())
    assert req["protocolVersion"] == 1
    assert req["targetType"] == "skill"
    assert req["targetName"] == "demo"
    assert req["sourcePathKind"] == "directory"
    assert req["_staged"] is True
    assert req["sourcePath"] == str((tmp_path / "quarantine" / "demo").resolve())
    assert req["origin"]["type"] == "clawhub"
    assert req["request"] == {
        "kind": "skill-install",
        "mode": "install",
        "requestedSpecifier": "clawhub:owner/demo",
    }
    # The gateway's environment is not the scanner's to read.
    assert "AGENTOS_TEST_SECRET_FOR_POLICY" not in req["_env"]


@pytest.mark.asyncio
async def test_block_is_not_overridable_by_force(tmp_path: Path) -> None:
    command = _answering(
        tmp_path,
        {
            "protocolVersion": 1,
            "decision": "block",
            "reason": "reads ~/.ssh",
            "findings": [{"ruleId": "exfil", "severity": "critical", "message": "ssh key read"}],
        },
    )
    installer = _installer(tmp_path, InstallPolicy(enabled=True, command=command))

    result = await installer.install("demo", "clawhub", force=True)

    assert result.success is False
    assert "reads ~/.ssh" in result.message
    assert result.overridable is False
    fields = install_security_fields(result)
    assert fields["policy_decision"] == "block"
    assert fields["policy_findings"][0]["ruleId"] == "exfil"
    assert "overridable" not in fields
    assert not (tmp_path / "managed" / "demo").exists()
    assert not (tmp_path / "quarantine" / "demo").exists()
    assert Lockfile.load(tmp_path / "lock.json").get("demo") is None


@pytest.mark.asyncio
async def test_warn_needs_force(tmp_path: Path) -> None:
    command = _answering(
        tmp_path, {"protocolVersion": 1, "decision": "warn", "reason": "unpinned dependency"}
    )
    installer = _installer(tmp_path, InstallPolicy(enabled=True, command=command))

    refused = await installer.install("demo", "clawhub")
    assert refused.success is False
    assert refused.overridable is True
    assert "unpinned dependency" in refused.message
    assert not (tmp_path / "managed" / "demo").exists()

    forced = await installer.install("demo", "clawhub", force=True)
    assert forced.success is True
    entry = Lockfile.load(tmp_path / "lock.json").get("demo")
    assert entry is not None
    assert entry.policy_decision == "warn"
    assert entry.policy_reason == "unpinned dependency"


@pytest.mark.asyncio
async def test_update_reports_update_mode_and_does_not_force_a_warn(tmp_path: Path) -> None:
    mode_file = tmp_path / "mode"
    flag = tmp_path / "warn-now"
    command = _script(
        tmp_path,
        f"req = json.load(sys.stdin)\n"
        f"open({str(mode_file)!r}, 'w').write(req['request']['mode'])\n"
        f"warn = os.path.exists({str(flag)!r})\n"
        f"print(json.dumps({{'protocolVersion': 1, 'decision': 'warn' if warn else 'allow',"
        f" 'reason': 'changed upstream'}}))",
    )
    installer = _installer(tmp_path, InstallPolicy(enabled=True, command=command))
    assert (await installer.install("demo", "clawhub")).success is True
    assert mode_file.read_text() == "install"

    flag.write_text("")
    results = await installer.update("demo")

    assert mode_file.read_text() == "update"
    assert results[0].success is False
    # The refused update leaves the installed copy in place.
    assert (tmp_path / "managed" / "demo" / "SKILL.md").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("sys.exit(3)", "exited with status 3"),
        ("print('not json')", "not JSON"),
        ("print('[]')", "not an object"),
        ("print(json.dumps({'protocolVersion': 2, 'decision': 'allow'}))", "protocolVersion"),
        ("print(json.dumps({'protocolVersion': 1, 'decision': 'maybe'}))", "unknown decision"),
        ("print(json.dumps({'protocolVersion': 1}))", "unknown decision"),
        ("time.sleep(30)", "timed out"),
        ("sys.stdout.write('x' * 5000)", "more than 1000 bytes"),
    ],
)
async def test_anything_but_a_valid_answer_fails_closed(
    tmp_path: Path, body: str, expected: str
) -> None:
    policy = InstallPolicy(
        enabled=True, command=_script(tmp_path, body), timeout_seconds=1.5, max_output_bytes=1000
    )

    decision = await evaluate_install_policy(policy, _request())

    assert decision.decision == "block"
    assert decision.failed_closed is True
    assert expected in decision.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("", "command is not set"),
        ("clawscan", "absolute path"),
        ("/nonexistent/agentos-test-policy", "does not exist"),
    ],
)
async def test_enabled_without_a_usable_command_blocks_every_install(
    tmp_path: Path, command: str, expected: str
) -> None:
    installer = _installer(tmp_path, InstallPolicy(enabled=True, command=command))

    result = await installer.install("demo", "clawhub", force=True)

    assert result.success is False
    assert expected in result.message
    assert not (tmp_path / "managed" / "demo").exists()


@pytest.mark.asyncio
async def test_disabled_policy_is_never_run(tmp_path: Path) -> None:
    installer = _installer(tmp_path, InstallPolicy(enabled=False, command="/nonexistent/policy"))

    result = await installer.install("demo", "clawhub")

    assert result.success is True
    assert result.policy is None
    assert "policy_decision" not in install_security_fields(result)


def test_findings_are_bounded() -> None:
    raw = json.dumps(
        {
            "protocolVersion": 1,
            "decision": "warn",
            "reason": "r" * 5000,
            "findings": [{"ruleId": "x", "message": "m" * 5000}] * 500 + ["junk"],
        }
    ).encode()

    decision = parse_policy_response(raw)

    assert decision.decision == "warn"
    assert len(decision.reason) == 1000
    assert len(decision.findings) == 100
    assert len(decision.findings[0]["message"]) == 1000


def test_request_matches_the_clawscan_validator_contract() -> None:
    """Fields ``clawscan openclaw-install-policy`` rejects a request without."""
    req = build_policy_request(
        name="demo",
        source_path="/tmp/q/demo",
        source_id="github",
        identifier="owner/repo:skills/demo",
        version="1.2.0",
        mode="update",
    )

    assert req["protocolVersion"] == 1
    assert req["targetType"] == "skill"
    assert req["targetName"] and req["sourcePath"]
    assert req["sourcePathKind"] in ("file", "directory")
    assert isinstance(req["origin"]["type"], str) and req["origin"]["type"]
    assert req["origin"]["version"] == "1.2.0"
    assert req["request"]["kind"] == "skill-install"
    assert req["request"]["mode"] in ("install", "update")


def test_config_section_defaults_to_disabled() -> None:
    from agentos.gateway.config import GatewayConfig

    section = GatewayConfig().skills.install_policy

    assert section.enabled is False
    assert section.command == ""
    assert section.pass_env == ["PATH"]


def test_unreadable_config_resolves_to_a_policy_that_blocks(monkeypatch) -> None:
    from agentos.gateway.config import GatewayConfig
    from agentos.skills.hub import defaults
    from agentos.tools.builtin import control

    def _boom(*args, **kwargs):
        raise ValueError("bad toml")

    monkeypatch.setattr(control, "_gateway_config", None)
    monkeypatch.setattr(GatewayConfig, "load", classmethod(_boom))

    policy = defaults.resolve_install_policy()

    assert policy.enabled is True
    assert policy.command == ""


def test_resolve_reads_the_running_gateway_config(monkeypatch) -> None:
    from agentos.gateway.config import GatewayConfig
    from agentos.skills.hub import defaults
    from agentos.tools.builtin import control

    config = GatewayConfig()
    config.skills.install_policy.enabled = True
    config.skills.install_policy.command = "/opt/clawscan"
    config.skills.install_policy.args = ["openclaw-install-policy"]
    monkeypatch.setattr(control, "_gateway_config", config)

    policy = defaults.resolve_install_policy()

    assert policy == InstallPolicy(
        enabled=True, command="/opt/clawscan", args=("openclaw-install-policy",)
    )
