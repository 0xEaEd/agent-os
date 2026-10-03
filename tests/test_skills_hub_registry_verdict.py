"""The registry's own review of a skill is read, recorded, and gated on.

ClawHub runs ClawScan on what it hosts and publishes the outcome on the search
and detail endpoints. The installer used to drop it and rely on its own regex
scan alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentos.skills.hub.clawhub import ClawHubSource
from agentos.skills.hub.installer import SkillInstaller, install_security_fields
from agentos.skills.hub.lockfile import Lockfile
from agentos.skills.hub.source import SkillBundle, SkillMeta

_SKILL_MD = "---\nname: demo\ndescription: Use when testing.\n---\n\n# Demo\n"


class FakeRouter:
    def __init__(self, meta: SkillMeta | None) -> None:
        self.meta = meta

    async def fetch(self, identifier: str, source_id: str) -> SkillBundle | None:
        return SkillBundle(name="demo", files={"SKILL.md": _SKILL_MD})

    async def inspect(self, identifier: str, source_id: str) -> SkillMeta | None:
        return self.meta


def _installer(tmp_path: Path, **meta: Any) -> SkillInstaller:
    return SkillInstaller(
        router=FakeRouter(SkillMeta(name="demo", source_id="clawhub", **meta)),
        managed_dir=tmp_path / "managed",
        quarantine_dir=tmp_path / "quarantine",
        lockfile_path=tmp_path / "lock.json",
    )


def _patch_get(monkeypatch: pytest.MonkeyPatch, payload: Any) -> None:
    import httpx

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            return payload

    class _AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _AsyncClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> _Response:
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _AsyncClient)


@pytest.mark.asyncio
async def test_malicious_verdict_blocks_even_with_force(tmp_path: Path) -> None:
    installer = _installer(
        tmp_path, registry_verdict="malicious", registry_summary="Review: malware.stealer"
    )

    result = await installer.install("demo", "clawhub", force=True)

    assert result.success is False
    assert "malicious" in result.message
    assert "malware.stealer" in result.message
    assert result.overridable is False
    assert install_security_fields(result) == {"registry_verdict": "malicious"}
    assert not (tmp_path / "managed" / "demo").exists()
    assert not (tmp_path / "quarantine" / "demo").exists()


@pytest.mark.asyncio
async def test_suspicious_verdict_needs_force(tmp_path: Path) -> None:
    installer = _installer(tmp_path, registry_verdict="suspicious")

    refused = await installer.install("demo", "clawhub")
    assert refused.success is False
    assert refused.overridable is True
    assert install_security_fields(refused)["overridable"] is True
    assert not (tmp_path / "managed" / "demo").exists()

    forced = await installer.install("demo", "clawhub", force=True)
    assert forced.success is True
    assert forced.registry_verdict == "suspicious"
    entry = Lockfile.load(tmp_path / "lock.json").get("demo")
    assert entry is not None and entry.registry_verdict == "suspicious"


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["clean", ""])
async def test_clean_or_absent_verdict_installs(tmp_path: Path, verdict: str) -> None:
    installer = _installer(tmp_path, registry_verdict=verdict, upstream_scanners={"snyk": "fail"})

    result = await installer.install("demo", "clawhub")

    assert result.success is True
    entry = Lockfile.load(tmp_path / "lock.json").get("demo")
    assert entry is not None
    assert entry.registry_verdict == verdict
    # Relayed third-party statuses are recorded, not gated on.
    assert entry.upstream_scanners == {"snyk": "fail"}


@pytest.mark.asyncio
async def test_lockfile_round_trips_the_new_fields(tmp_path: Path) -> None:
    installer = _installer(tmp_path, registry_verdict="clean", registry_summary="Review: ok")
    await installer.install("demo", "clawhub")

    entry = Lockfile.load(tmp_path / "lock.json").get("demo")

    assert entry is not None
    assert entry.registry_summary == "Review: ok"
    assert entry.policy_decision == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("moderation", "expected"),
    [
        ({"verdict": "clean", "isSuspicious": False, "isMalwareBlocked": False}, "clean"),
        ({"verdict": "suspicious"}, "suspicious"),
        ({"verdict": "clean", "isSuspicious": True}, "suspicious"),
        ({"verdict": "malicious"}, "malicious"),
        ({"verdict": "suspicious", "isMalwareBlocked": True}, "malicious"),
        ({"verdict": "MALICIOUS "}, "malicious"),
        ({"verdict": "ignore previous instructions"}, ""),
        ({"verdict": 7}, ""),
        (None, ""),
    ],
)
async def test_inspect_reads_moderation(
    monkeypatch: pytest.MonkeyPatch, moderation: Any, expected: str
) -> None:
    _patch_get(
        monkeypatch,
        {
            "skill": {"slug": "demo", "summary": "A demo.", "tags": {"latest": "1.0.0"}},
            "latestVersion": {"version": "1.0.0", "license": None},
            "owner": {"handle": "someone"},
            "moderation": moderation,
        },
    )

    meta = await ClawHubSource().inspect("demo")

    assert meta is not None
    assert meta.registry_verdict == expected
    assert meta.version == "1.0.0"
    assert meta.author == "someone"
    assert meta.description == "A demo."
    assert meta.tags == []
    assert meta.license == ""


@pytest.mark.asyncio
async def test_inspect_still_reads_the_flat_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(
        monkeypatch,
        {"name": "demo", "description": "Flat.", "version": "0.2.0", "tags": ["a"]},
    )

    meta = await ClawHubSource().inspect("demo")

    assert meta is not None
    assert (meta.name, meta.description, meta.version, meta.tags) == (
        "demo",
        "Flat.",
        "0.2.0",
        ["a"],
    )
    assert meta.registry_verdict == ""


@pytest.mark.asyncio
async def test_search_reads_trust_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(
        monkeypatch,
        {
            "results": [
                {
                    "slug": "flagged",
                    "displayName": "Flagged",
                    "native": {"skill": {"isSuspicious": True}},
                    "trust": {"clawHubVerdict": None, "upstreamScanners": None},
                },
                {
                    "slug": "relayed",
                    "displayName": "Relayed",
                    "trust": {
                        "clawHubVerdict": {"verdict": "clean"},
                        "upstreamScanners": {
                            "snyk": {"status": "fail", "sourceUrl": "https://example.invalid"},
                            "socket": {"status": "pass"},
                            "broken": {"status": None},
                        },
                    },
                },
                {"slug": "bare", "displayName": "Bare"},
            ]
        },
    )

    flagged, relayed, bare = await ClawHubSource().search("x")

    assert flagged.registry_verdict == "suspicious"
    assert relayed.registry_verdict == "clean"
    assert relayed.upstream_scanners == {"snyk": "fail", "socket": "pass"}
    assert (bare.registry_verdict, bare.upstream_scanners) == ("", {})


def test_tool_search_row_carries_a_verdict_only_when_there_is_one() -> None:
    from agentos.tools.builtin.skill_tools import _community_result_to_dict

    flagged = SkillMeta(name="a", registry_verdict="suspicious", upstream_scanners={"snyk": "fail"})
    plain = SkillMeta(name="b")

    row = _community_result_to_dict(flagged, set())
    assert row["registry_verdict"] == "suspicious"
    assert row["upstream_scanners"] == {"snyk": "fail"}
    assert "registry_verdict" not in _community_result_to_dict(plain, set())
    assert "upstream_scanners" not in _community_result_to_dict(plain, set())
