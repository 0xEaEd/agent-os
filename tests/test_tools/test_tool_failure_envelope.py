from __future__ import annotations

import json
from pathlib import Path

import httpx

from agentos.tools.envelope import build_tool_failure_envelope
from agentos.tools.types import SafeToolError


def test_type_error_tool_failure_is_model_retriable_without_raw_traceback() -> None:
    envelope = build_tool_failure_envelope(
        TypeError("secret raw argument detail"),
        "write_file",
    )

    assert envelope["status"] == "error"
    assert envelope["tool"] == "write_file"
    assert envelope["error_class"] == "TypeError"
    assert envelope["retry_allowed"] is True
    assert "tool schema" in envelope["user_message"]
    assert "secret raw argument detail" not in envelope["user_message"]


def test_httpx_timeout_exception_is_retriable_with_specific_message() -> None:
    envelope = build_tool_failure_envelope(
        httpx.TimeoutException("secret endpoint"),
        "web_fetch",
    )

    assert envelope["error_class"] == "TimeoutException"
    assert envelope["retry_allowed"] is True
    assert "timed out" in envelope["user_message"]
    assert "secret endpoint" not in envelope["user_message"]


def test_httpx_connect_error_is_retriable_with_specific_message() -> None:
    envelope = build_tool_failure_envelope(
        httpx.ConnectError("secret host"),
        "web_fetch",
    )

    assert envelope["error_class"] == "ConnectError"
    assert envelope["retry_allowed"] is True
    assert "could not connect" in envelope["user_message"]
    assert "secret host" not in envelope["user_message"]


def test_json_decode_error_has_specific_message() -> None:
    envelope = build_tool_failure_envelope(
        json.JSONDecodeError("secret payload", "not-json", 0),
        "web_fetch",
    )

    assert envelope["error_class"] == "JSONDecodeError"
    assert envelope["retry_allowed"] is False
    assert "invalid response payload" in envelope["user_message"]
    assert "secret payload" not in envelope["user_message"]


def test_policy_denial_envelope_has_exactly_five_keys() -> None:
    envelope = build_tool_failure_envelope(
        PermissionError("raw denied detail"),
        "exec_command",
        policy_denial=True,
        error_class_override="PolicyDenied",
        user_message_override="Blocked by policy.",
    )

    assert set(envelope) == {
        "status",
        "tool",
        "error_class",
        "user_message",
        "retry_allowed",
    }
    assert envelope["status"] == "error"
    assert envelope["tool"] == "exec_command"
    assert envelope["error_class"] == "PolicyDenied"
    assert envelope["user_message"] == "Blocked by policy."
    assert envelope["retry_allowed"] is False


def test_safe_tool_error_instance_message_preserves_five_key_shape() -> None:
    envelope = build_tool_failure_envelope(
        SafeToolError("PDF file not found: input.pdf (resolved=/workspace/input.pdf)", "secret"),
        "pdf",
    )

    assert set(envelope) == {
        "status",
        "tool",
        "error_class",
        "user_message",
        "retry_allowed",
    }
    assert envelope["status"] == "error"
    assert envelope["tool"] == "pdf"
    assert envelope["error_class"] == "SafeToolError"
    assert "PDF file not found" in envelope["user_message"]
    assert "secret" not in envelope["user_message"]


def test_image_attachment_path_safe_tool_error_is_not_generic_internal_error() -> None:
    envelope = build_tool_failure_envelope(
        SafeToolError(
            "Image path is not accessible by the image tool: ab367.png. "
            "Pass a real local file path or HTTP(S) URL. If this is a chat attachment, "
            "answer from the attached image directly instead of calling the image tool.",
            "resolved=/secret/workspace/ab367.png",
        ),
        "image",
    )

    assert set(envelope) == {
        "status",
        "tool",
        "error_class",
        "user_message",
        "retry_allowed",
    }
    assert envelope["tool"] == "image"
    assert envelope["error_class"] == "SafeToolError"
    assert envelope["retry_allowed"] is False
    assert "not accessible by the image tool" in envelope["user_message"]
    assert "chat attachment" in envelope["user_message"]
    assert "internal error" not in envelope["user_message"]
    assert "secret" not in envelope["user_message"]


def test_edit_match_error_preserves_closest_match_hint_in_envelope() -> None:
    from agentos.tools.types import EditMatchError

    err = EditMatchError("old_text not found in test.py. Closest match: line 1 (def foo())")
    envelope = build_tool_failure_envelope(err, "edit_file")
    assert envelope["status"] == "error"
    assert envelope["tool"] == "edit_file"
    assert envelope["error_class"] == "EditMatchError"
    assert "old_text not found in test.py" in envelope["user_message"]
    assert "Closest match: line 1" in envelope["user_message"]


# --- #2889 / #2890 / #2891: the other three sites of the same root cause ---


def test_projects_create_keeps_the_name_conflict_reason(monkeypatch) -> None:
    """The manager's authored ValueError must reach the model intact.

    Re-raised bare it was flattened to "The tool received an invalid
    argument." and the clashing name was lost.
    """
    import asyncio

    import pytest

    from agentos.tools.builtin import projects as projects_tool

    class _Mgr:
        async def create_project(self, **kwargs: object) -> object:
            raise ValueError("Project name already exists: quarterly-report")

    monkeypatch.setattr(projects_tool, "_get_session_manager", lambda: _Mgr())
    monkeypatch.setattr(projects_tool, "_resolve_agent_id", lambda agent_id: "a")

    with pytest.raises(SafeToolError) as excinfo:
        asyncio.run(projects_tool.projects_create(name="quarterly-report"))

    assert "Project name already exists: quarterly-report" in str(excinfo.value)
    envelope = build_tool_failure_envelope(excinfo.value, "projects_create")
    assert "Project name already exists: quarterly-report" in envelope["user_message"]


def test_grep_search_keeps_the_regex_diagnostic(tmp_path: Path) -> None:
    """The re module's diagnostic describes the caller's own pattern."""
    import asyncio

    import pytest

    from agentos.sandbox.config import SandboxSettings
    from agentos.sandbox.integration import configure_runtime, reset_runtime
    from agentos.tools.builtin import filesystem
    from agentos.tools.types import RegexPatternError, ToolContext, current_tool_context

    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    reset_runtime()
    configure_runtime(SandboxSettings(sandbox=False, denial_threshold=10), workspace=tmp_path)
    token = current_tool_context.set(
        ToolContext(workspace_dir=str(tmp_path), session_key="agent:main:test")
    )
    try:
        with pytest.raises(RegexPatternError) as excinfo:
            asyncio.run(filesystem.grep_search(pattern="("))
    finally:
        current_tool_context.reset(token)
        reset_runtime()

    envelope = build_tool_failure_envelope(excinfo.value, "grep_search")
    assert envelope["error_class"] == "RegexPatternError"
    assert "Invalid regex pattern" in envelope["user_message"]


def test_regex_pattern_error_is_still_a_value_error() -> None:
    """Existing callers that catch ValueError must keep working."""
    from agentos.tools.types import RegexPatternError

    assert issubclass(RegexPatternError, ValueError)
    assert issubclass(RegexPatternError, SafeToolError)


def test_unresolvable_host_is_reported_as_a_fetch_error_not_a_bad_argument(
    tmp_path: Path,
) -> None:
    """web_fetch reports a name that does not resolve like any other fetch failure."""
    import asyncio

    from agentos.sandbox.config import SandboxSettings
    from agentos.sandbox.integration import configure_runtime, reset_runtime
    from agentos.tools.builtin import web_fetch as web_fetch_mod
    from agentos.tools.types import ToolContext, current_tool_context

    def _raise(url: str) -> None:
        raise ValueError("Cannot resolve hostname: no-such-host.invalid")

    reset_runtime()
    configure_runtime(SandboxSettings(sandbox=False, denial_threshold=10), workspace=tmp_path)
    token = current_tool_context.set(
        ToolContext(workspace_dir=str(tmp_path), session_key="agent:main:test")
    )
    original = web_fetch_mod._check_ssrf
    web_fetch_mod._check_ssrf = _raise
    try:
        payload = json.loads(
            asyncio.run(web_fetch_mod.web_fetch(url="http://no-such-host.invalid/"))
        )
    finally:
        web_fetch_mod._check_ssrf = original
        current_tool_context.reset(token)
        reset_runtime()

    assert payload["status"] == 0
    assert payload["text"] == ""
    assert payload["error"] == "Cannot resolve hostname: no-such-host.invalid"
