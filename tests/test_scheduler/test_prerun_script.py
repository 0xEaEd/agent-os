"""Pre-run scripts on ``agent_turn`` jobs.

The point of the mode is that the model only runs when the script found
something: a quiet tick must cost nothing and leave nothing behind — no session
row, no transcript line, no turn.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agentos.scheduler.delivery import DeliveryChain
from agentos.scheduler.handlers import make_agent_run_handler
from agentos.scheduler.payloads import (
    AGENT_TURN_KIND,
    make_agent_turn_payload,
    normalize_contract,
    payload_args,
    payload_script,
)
from agentos.scheduler.types import CronJob, DeliveryConfig, SessionTarget


@pytest.fixture
def agentos_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path))
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_script(home: Path, name: str, body: str) -> Path:
    path = home / "scripts" / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)
    return path


class _FakeSessionManager:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.rows: dict[str, list[dict]] = {}

    async def get_or_create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs

    async def append_message(self, session_key, role, content):
        self.rows.setdefault(session_key, []).append({"role": role, "content": content})
        return SimpleNamespace(role=role, content=content)

    async def read_transcript(self, session_key):
        return list(self.rows.get(session_key, []))


class _FakeTurnRunner:
    def __init__(self, session_manager: _FakeSessionManager, text: str = "reported") -> None:
        self.session_manager = session_manager
        self.text = text
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)

        async def events():
            await self.session_manager.append_message(
                kwargs["session_key"], role="assistant", content=self.text
            )
            yield SimpleNamespace(kind="message", text=self.text)
            yield SimpleNamespace(kind="done")

        return events()


class _ExplodingTurnRunner:
    """Fails the test if the scheduler reaches a provider on this tick."""

    calls: list[dict] = []

    def run(self, **kwargs):
        raise AssertionError("a provider call was made on a tick that must cost nothing")


def _job(
    script: str,
    *,
    args: list[str] | None = None,
    task: str = "Report anything odd.",
) -> CronJob:
    return CronJob(
        id="triage",
        name="Triage",
        handler_key="agent_run",
        payload=make_agent_turn_payload(task, "main", script, "", args),
        session_target=SessionTarget.ISOLATED,
        timeout_seconds=30.0,
        delivery=DeliveryConfig(best_effort=True),
    )


def _handler(session_manager: _FakeSessionManager, turn_runner: _FakeTurnRunner):
    return make_agent_run_handler(
        DeliveryChain(),
        turn_runner_ref=lambda: turn_runner,
        session_manager_ref=lambda: session_manager,
    )


# ── the payload contract ────────────────────────────────────────────────────


def test_agent_turn_without_a_script_keeps_its_original_shape():
    """Existing jobs must not grow keys they never had."""
    assert make_agent_turn_payload("do it") == {
        "kind": AGENT_TURN_KIND,
        "task": "do it",
        "agent_id": "main",
    }


def test_agent_turn_carries_the_script_through_normalization():
    handler_key, payload, _, _ = normalize_contract(
        handler_key="agent_run",
        payload=make_agent_turn_payload("do it", "main", "watch.py", "/srv", ["--x", "1"]),
        session_target=SessionTarget.ISOLATED,
    )

    assert handler_key == "agent_run"
    assert payload_script(payload) == "watch.py"
    assert payload_args(payload) == ["--x", "1"]
    assert payload["workdir"] == "/srv"


def test_args_are_dropped_when_empty():
    payload = make_agent_turn_payload("do it", "main", "watch.py")

    assert "args" not in payload


# ── the handler ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_script_output_becomes_context_for_the_turn(agentos_home):
    _write_script(agentos_home, "watch.py", "print('3 new alerts')")
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    result = await _handler(session_manager, turn_runner)(_job("watch.py"))

    assert result.summary == "reported"
    sent = turn_runner.calls[0]["message"]
    assert "3 new alerts" in sent
    assert "Report anything odd." in sent
    assert "untrusted input" in sent


@pytest.mark.asyncio
async def test_empty_output_skips_the_turn_entirely(agentos_home):
    _write_script(agentos_home, "quiet.py", "pass")
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    result = await _handler(session_manager, turn_runner)(_job("quiet.py"))

    assert result.delivery_status == "skipped"
    assert turn_runner.calls == []
    # Nothing half-started: no session, no transcript line.
    assert session_manager.created == []
    assert session_manager.rows == {}


@pytest.mark.asyncio
async def test_wake_gate_skips_the_turn(agentos_home):
    _write_script(agentos_home, "gated.py", "print('checked')\nprint('{\"wakeAgent\": false}')")
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    result = await _handler(session_manager, turn_runner)(_job("gated.py"))

    assert result.delivery_status == "skipped"
    assert turn_runner.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["pass", "print('{\"wakeAgent\": false}')"])
async def test_a_quiet_tick_costs_zero_provider_calls(agentos_home, body):
    """The whole point of the mode: no news, no model call, no session."""
    _write_script(agentos_home, "quiet.py", body)
    session_manager = _FakeSessionManager()
    handler = make_agent_run_handler(
        DeliveryChain(),
        turn_runner_ref=lambda: _ExplodingTurnRunner(),
        session_manager_ref=lambda: session_manager,
    )

    result = await handler(_job("quiet.py"))

    assert result.delivery_status == "skipped"
    assert session_manager.created == []


@pytest.mark.asyncio
async def test_a_failing_script_is_reported_by_the_agent(agentos_home):
    """A broken collector must reach the user, not vanish."""
    _write_script(agentos_home, "broken.py", "import sys; sys.stderr.write('nope'); sys.exit(1)")
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    result = await _handler(session_manager, turn_runner)(_job("broken.py"))

    assert result.summary == "reported"
    sent = turn_runner.calls[0]["message"]
    assert "Script error" in sent
    assert "nope" in sent


@pytest.mark.asyncio
async def test_a_missing_script_is_reported_by_the_agent(agentos_home):
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    await _handler(session_manager, turn_runner)(_job("gone.py"))

    assert "Script not found" in turn_runner.calls[0]["message"]


@pytest.mark.asyncio
async def test_script_args_reach_the_script(agentos_home):
    _write_script(agentos_home, "argv.py", "import sys; print(' '.join(sys.argv[1:]))")
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    await _handler(session_manager, turn_runner)(_job("argv.py", args=["--repo", "owner/name"]))

    assert "--repo owner/name" in turn_runner.calls[0]["message"]


@pytest.mark.asyncio
async def test_an_arg_with_spaces_stays_one_argument(agentos_home):
    """argv is exec'd directly, so nothing re-splits or re-interprets it."""
    _write_script(agentos_home, "argv.py", "import sys; print(len(sys.argv[1:]), sys.argv[1])")
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)

    await _handler(session_manager, turn_runner)(_job("argv.py", args=["a b; rm -rf /"]))

    assert "1 a b; rm -rf /" in turn_runner.calls[0]["message"]


@pytest.mark.asyncio
async def test_a_job_without_a_script_runs_the_turn_directly(agentos_home):
    session_manager = _FakeSessionManager()
    turn_runner = _FakeTurnRunner(session_manager)
    job = CronJob(
        id="plain",
        name="Plain",
        handler_key="agent_run",
        payload=make_agent_turn_payload("just do it"),
        session_target=SessionTarget.ISOLATED,
        delivery=DeliveryConfig(best_effort=True),
    )

    await _handler(session_manager, turn_runner)(job)

    assert turn_runner.calls[0]["message"] == "just do it"


@pytest.mark.asyncio
async def test_prerun_script_runs_as_the_agent(agentos_home):
    """The pre-run child carries a gateway-minted agent token (see scripts.py)."""
    from agentos.gateway.agent_surface import AGENT_TOKEN_ENV, get_agent_surface

    get_agent_surface().reset()
    try:
        _write_script(
            agentos_home, "who.py", f"import os; print(os.environ.get('{AGENT_TOKEN_ENV}', ''))"
        )
        session_manager = _FakeSessionManager()
        turn_runner = _FakeTurnRunner(session_manager)

        await _handler(session_manager, turn_runner)(_job("who.py"))

        surface = get_agent_surface()
        lines = turn_runner.calls[0]["message"].splitlines()
        bindings = [b for b in (surface.resolve_token(ln.strip()) for ln in lines) if b]
        assert len(bindings) == 1 and bindings[0].via == "token"
    finally:
        get_agent_surface().reset()
