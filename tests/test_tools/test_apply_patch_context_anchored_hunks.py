"""apply_patch accepts the context-anchored hunks OpenAI models write.

GPT models emit the ``*** Begin Patch`` format with hunks that open on a bare
``@@`` (or ``@@ <a line to search past>``) and carry no line numbers -- the
hunk is found by its context and removed lines. ``_parse_patch`` only knew the
numbered ``@@@ -a,b +c,d @@@`` header, so every such patch was refused with
"expected a '@@@ ' hunk header": ``apply_patch`` failed on every call from a
GPT-routed session. The model's retry with a bare ``@@@`` was refused too.

Each case drives the tool through ``build_tool_handler`` the way the model
reaches it, and checks the file on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentos.sandbox.config import SandboxSettings
from agentos.sandbox.integration import configure_runtime, reset_runtime
from agentos.tool_boundary import ToolCall, ToolResult
from agentos.tools import get_default_registry
from agentos.tools.dispatch import build_tool_handler
from agentos.tools.types import CallerKind, ToolContext


@pytest.fixture(autouse=True)
def _runtime(tmp_path: Path):
    """``sandbox=False`` so the ``@sandboxed`` gate runs the handler inline."""
    configure_runtime(
        SandboxSettings(sandbox=False, security_grading=False, allow_legacy_mode=True),
        workspace=tmp_path,
    )
    try:
        yield
    finally:
        reset_runtime()


async def _run(workspace: Path, patch: str) -> ToolResult:
    ctx = ToolContext(
        caller_kind=CallerKind.CLI,
        channel_kind="cli",
        channel_id="cli:test",
        session_key="agent:main:webchat:cafe0002",
        workspace_dir=str(workspace),
    )
    handler = build_tool_handler(get_default_registry(), ctx)
    return await handler(
        ToolCall(tool_use_id="patch-1", tool_name="apply_patch", arguments={"patch": patch})
    )


async def _apply(workspace: Path, patch: str) -> None:
    result = await _run(workspace, patch)
    assert result.is_error is False, result.content


async def _refusal(workspace: Path, patch: str) -> str:
    result = await _run(workspace, patch)
    assert result.is_error is True, result.content
    envelope = json.loads(result.content)
    assert envelope["error_class"] == "PatchError"
    return str(envelope["user_message"])


# ── the reported shape ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bare_at_at_hunks_across_several_files_apply(tmp_path: Path) -> None:
    """The patch from the failing session: several ``@@`` hunks, three files."""
    skill = (
        "# Check\n"
        "\n"
        "Portfolio project root: `~/old/root`.\n"
        "\n"
        "## Steps\n"
        "- Default to offline. Run `python3 scripts/check.py --offline`.\n"
        "- For live prices run `python3 scripts/check.py --live`.\n"
        "- Keep the output short.\n"
        "- Save a snapshot only when asked: `--live --save-snapshot`.\n"
    )
    for name in ("check", "review", "update"):
        (tmp_path / f"{name}.md").write_text(skill, encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n"
        "*** Update File: check.md\n"
        "@@\n"
        "-Portfolio project root: `~/old/root`.\n"
        "+Portfolio project root: `~/new/root`.\n"
        "@@\n"
        "-- Default to offline. Run `python3 scripts/check.py --offline`.\n"
        "-- For live prices run `python3 scripts/check.py --live`.\n"
        "+- Default to offline. Run `python3 ~/new/root/scripts/check.py --offline`.\n"
        "+- For live prices run `python3 ~/new/root/scripts/check.py --live`.\n"
        "@@\n"
        "-- Save a snapshot only when asked: `--live --save-snapshot`.\n"
        "+- Save a snapshot only when asked: `check.py --live --save-snapshot`.\n"
        "*** Update File: review.md\n"
        "@@\n"
        "-Portfolio project root: `~/old/root`.\n"
        "+Portfolio project root: `~/new/root`.\n"
        "*** Update File: update.md\n"
        "@@\n"
        "-Portfolio project root: `~/old/root`.\n"
        "+Portfolio project root: `~/new/root`.\n"
        "*** End Patch",
    )

    assert (tmp_path / "check.md").read_text(encoding="utf-8") == (
        "# Check\n"
        "\n"
        "Portfolio project root: `~/new/root`.\n"
        "\n"
        "## Steps\n"
        "- Default to offline. Run `python3 ~/new/root/scripts/check.py --offline`.\n"
        "- For live prices run `python3 ~/new/root/scripts/check.py --live`.\n"
        "- Keep the output short.\n"
        "- Save a snapshot only when asked: `check.py --live --save-snapshot`.\n"
    )
    for name in ("review", "update"):
        assert (tmp_path / f"{name}.md").read_text(encoding="utf-8") == skill.replace(
            "~/old/root", "~/new/root"
        )


@pytest.mark.asyncio
async def test_the_bare_triple_at_retry_applies_too(tmp_path: Path) -> None:
    """Told hunks open with '@@@', the model retried with a bare '@@@'."""
    target = tmp_path / "holdings.json"
    target.write_text('{\n  "qty": 1,\n  "px": 2\n}\n', encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: holdings.json\n@@@\n"
        '-  "qty": 1,\n+  "qty": 17300,\n*** End Patch\n',
    )

    assert target.read_text(encoding="utf-8") == '{\n  "qty": 17300,\n  "px": 2\n}\n'


# ── locating ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_anchor_steers_the_match_past_an_earlier_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def a():\n    return 1\n\n\ndef b():\n    return 1\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n@@ def b():\n"
        "-    return 1\n+    return 2\n*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == (
        "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    )


@pytest.mark.asyncio
async def test_repeated_snippets_resolve_to_successive_occurrences(tmp_path: Path) -> None:
    target = tmp_path / "list.txt"
    target.write_text("x\ny\nx\ny\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: list.txt\n"
        "@@\n-x\n+first\n"
        "@@\n-x\n+second\n"
        "*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "first\ny\nsecond\ny\n"


@pytest.mark.asyncio
async def test_the_first_hunk_may_omit_its_header(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("a = 1\nb = 2\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n a = 1\n-b = 2\n+b = 3\n*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "a = 1\nb = 3\n"


@pytest.mark.asyncio
async def test_a_pure_addition_goes_after_its_anchor(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("import os\n\nx = 1\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n@@ import os\n+import sys\n*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "import os\nimport sys\n\nx = 1\n"


@pytest.mark.asyncio
async def test_a_pure_addition_without_an_anchor_appends(tmp_path: Path) -> None:
    target = tmp_path / "log.csv"
    target.write_text("date,qty\n2026-09-24,1\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: log.csv\n@@\n+2026-09-28,2\n*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "date,qty\n2026-09-24,1\n2026-09-28,2\n"


@pytest.mark.asyncio
async def test_an_addition_and_an_edit_starting_on_the_same_line(tmp_path: Path) -> None:
    """Both hunks resolve to line 2; the later one must be spliced first."""
    target = tmp_path / "app.py"
    target.write_text("import os\nx = 1\ny = 2\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n"
        "@@ import os\n+import sys\n"
        "@@\n-x = 1\n+x = 10\n"
        "*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "import os\nimport sys\nx = 10\ny = 2\n"


@pytest.mark.asyncio
async def test_end_of_file_pins_the_match_to_the_last_lines(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("pass\nx = 1\npass\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n@@\n-pass\n+done\n"
        "*** End of File\n*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "pass\nx = 1\ndone\n"


@pytest.mark.asyncio
async def test_crlf_endings_survive_an_anchored_hunk(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_bytes(b"a\r\nb\r\nc\r\n")

    await _apply(
        tmp_path, "*** Begin Patch\n*** Update File: app.txt\n@@\n b\n-c\n+d\n*** End Patch\n"
    )

    assert target.read_bytes() == b"a\r\nb\r\nd\r\n"


@pytest.mark.asyncio
async def test_a_unified_diff_header_applies(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("print('old')\n", encoding="utf-8")

    await _apply(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n@@ -1,1 +1,1 @@\n"
        "-print('old')\n+print('new')\n*** End Patch\n",
    )

    assert target.read_text(encoding="utf-8") == "print('new')\n"


# ── refusals ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_that_is_not_in_the_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("a = 1\n", encoding="utf-8")

    message = await _refusal(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n@@\n-a = 2\n+a = 3\n*** End Patch\n",
    )

    assert "Hunk context not found" in message
    assert "'a = 2'" in message
    assert target.read_text(encoding="utf-8") == "a = 1\n"


@pytest.mark.asyncio
async def test_a_missing_anchor_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("a = 1\n", encoding="utf-8")

    message = await _refusal(
        tmp_path,
        "*** Begin Patch\n*** Update File: app.py\n@@ def nope():\n-a = 1\n+a = 3\n*** End Patch\n",
    )

    assert "Anchor line not found: '@@ def nope():'" in message
    assert target.read_text(encoding="utf-8") == "a = 1\n"


@pytest.mark.asyncio
async def test_a_later_file_failing_leaves_the_earlier_one_untouched(tmp_path: Path) -> None:
    (tmp_path / "one.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "two.txt").write_text("b\n", encoding="utf-8")

    await _refusal(
        tmp_path,
        "*** Begin Patch\n"
        "*** Update File: one.txt\n@@\n-a\n+A\n"
        "*** Update File: two.txt\n@@\n-missing\n+B\n"
        "*** End Patch\n",
    )

    assert (tmp_path / "one.txt").read_text(encoding="utf-8") == "a\n"
