"""Operator-owned install policy — an external command that vets a staged skill.

The installer's own scanner is a handful of regexes. An operator who wants a
real review (ClawScan, Tencent AIG, an in-house checker) points
``[skills.install_policy]`` at an executable; it is handed the quarantined
bundle before anything is moved into the managed layer and answers allow, warn
or block.

The wire format is OpenClaw's ``security.installPolicy.exec`` protocol v1, so a
command written for that host — ``clawscan openclaw-install-policy`` in
particular — works here unchanged: one JSON request on stdin, one JSON response
on stdout.

Everything that is not a well-formed answer is a block. A policy that cannot
start, times out, exits nonzero or prints something else has not approved the
install, and an enabled policy with no usable command approves nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

PROTOCOL_VERSION = 1

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024

_MAX_FINDINGS = 100
_MAX_TEXT_CHARS = 1000
_READ_CHUNK = 64 * 1024
_DECISIONS = ("allow", "warn", "block")


@dataclass(frozen=True)
class InstallPolicy:
    """Resolved ``[skills.install_policy]`` settings."""

    enabled: bool = False
    command: str = ""
    args: tuple[str, ...] = ()
    # The child gets these variables and nothing else: the gateway's
    # environment holds provider keys and wallet secrets a scanner has no
    # business reading.
    pass_env: tuple[str, ...] = ("PATH",)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES


@dataclass
class PolicyDecision:
    """What the policy command said about one staged install."""

    decision: str  # "allow" | "warn" | "block"
    reason: str = ""
    findings: list[dict[str, str]] = field(default_factory=list)
    #: True when the block is ours, not the command's: it never produced a
    #: usable answer.
    failed_closed: bool = False


def _fail_closed(reason: str) -> PolicyDecision:
    log.warning("install_policy.failed_closed", reason=reason)
    return PolicyDecision(decision="block", reason=reason, failed_closed=True)


def build_policy_request(
    *,
    name: str,
    source_path: str,
    source_id: str,
    identifier: str,
    version: str = "",
    mode: str = "install",
) -> dict[str, Any]:
    """Build the protocol v1 request for a staged skill directory."""
    from agentos import __version__

    origin: dict[str, str] = {"type": source_id, "slug": identifier}
    if version:
        origin["version"] = version
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "agentosVersion": __version__,
        "targetType": "skill",
        "targetName": name,
        "sourcePath": source_path,
        "sourcePathKind": "directory",
        # Every hub source is a third party's branch tip fetched over the
        # network; none of them pins content.
        "source": {
            "kind": source_id,
            "authority": "third-party",
            "mutable": True,
            "network": True,
        },
        "origin": origin,
        "request": {
            "kind": "skill-install",
            "mode": mode,
            "requestedSpecifier": f"{source_id}:{identifier}",
        },
        "skill": {"installId": source_id},
    }


def _clip(value: Any) -> str:
    return str(value)[:_MAX_TEXT_CHARS] if value is not None else ""


def parse_policy_response(raw: bytes) -> PolicyDecision:
    """Turn the command's stdout into a decision, blocking on anything malformed."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _fail_closed("Install policy returned output that is not JSON.")
    if not isinstance(data, dict):
        return _fail_closed("Install policy returned JSON that is not an object.")
    if data.get("protocolVersion") != PROTOCOL_VERSION:
        return _fail_closed(
            f"Install policy answered with protocolVersion "
            f"{data.get('protocolVersion')!r}; expected {PROTOCOL_VERSION}."
        )
    decision = data.get("decision")
    if decision not in _DECISIONS:
        return _fail_closed(f"Install policy returned an unknown decision: {decision!r}.")

    findings: list[dict[str, str]] = []
    raw_findings = data.get("findings")
    if isinstance(raw_findings, list):
        for item in raw_findings[:_MAX_FINDINGS]:
            if not isinstance(item, dict):
                continue
            findings.append(
                {
                    key: _clip(item.get(key))
                    for key in ("ruleId", "severity", "message", "evidence")
                    if item.get(key) is not None
                }
            )

    reason = _clip(data.get("reason")).strip()
    if not reason and decision != "allow":
        reason = f"Install policy decision: {decision} (no reason given)."
    return PolicyDecision(decision=decision, reason=reason, findings=findings)


async def _run(policy: InstallPolicy, payload: bytes) -> tuple[int, bytes] | None:
    """Run the command; ``None`` when its output outgrew the cap."""
    env = {key: os.environ[key] for key in policy.pass_env if key in os.environ}
    proc = await asyncio.create_subprocess_exec(
        policy.command,
        *policy.args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # Scanner chatter is not part of the protocol, and an unread pipe
        # would stall a child that writes a lot of it.
        stderr=asyncio.subprocess.DEVNULL,
        env=env,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(payload)
            await proc.stdin.drain()
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            # A command that answers without reading its request is odd but
            # not wrong; let its exit status and stdout speak.
            pass

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await proc.stdout.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > policy.max_output_bytes:
                return None
            chunks.append(chunk)
        return await proc.wait(), b"".join(chunks)
    finally:
        # Covers the timeout cancellation and the output cap alike: never
        # leave a scanner running against a quarantine dir about to be removed.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()


async def evaluate_install_policy(policy: InstallPolicy, request: dict[str, Any]) -> PolicyDecision:
    """Ask the configured command about a staged install. Fails closed."""
    command = policy.command.strip()
    if not command:
        return _fail_closed(
            "Install policy is enabled but [skills.install_policy] command is not set."
        )
    # An absolute path, so what runs is the file the operator named and not
    # whatever a PATH lookup resolves to at install time.
    if not os.path.isabs(command):
        return _fail_closed(f"Install policy command must be an absolute path: {command!r}.")
    if not os.path.isfile(command):
        return _fail_closed(f"Install policy command does not exist: {command!r}.")

    payload = json.dumps(request).encode("utf-8")
    try:
        outcome = await asyncio.wait_for(_run(policy, payload), timeout=policy.timeout_seconds)
    except TimeoutError:
        return _fail_closed(f"Install policy timed out after {policy.timeout_seconds:g} seconds.")
    except OSError as exc:
        return _fail_closed(f"Install policy command could not be started: {exc}.")

    if outcome is None:
        return _fail_closed(
            f"Install policy wrote more than {policy.max_output_bytes} bytes of output."
        )
    returncode, stdout = outcome
    if returncode != 0:
        return _fail_closed(f"Install policy command exited with status {returncode}.")
    return parse_policy_response(stdout)
