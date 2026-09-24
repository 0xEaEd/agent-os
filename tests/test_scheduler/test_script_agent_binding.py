"""A cron script runs as the agent's, not the operator's.

Without this a scheduled ``agentos trade send`` would reach the gateway as an
unmarked connection. The script child carries a gateway-minted agent token,
an exec window is open while it runs, and its process group is cleaned up
when it ends.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agentos.gateway.agent_surface import AGENT_TOKEN_ENV, get_agent_surface
from agentos.scheduler.scripts import run_job_script


@pytest.fixture
def agentos_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AGENTOS_STATE_DIR", str(tmp_path))
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_surface():
    get_agent_surface().reset()
    yield
    get_agent_surface().reset()


def _write_script(home: Path, name: str, body: str) -> Path:
    path = home / "scripts" / name
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.mark.asyncio
async def test_child_carries_a_token_bound_to_the_job_session(agentos_home: Path) -> None:
    _write_script(agentos_home, "who.py", f"import os; print(os.environ['{AGENT_TOKEN_ENV}'])")

    ok, output = await run_job_script(
        "who.py", timeout=30, session_key="cron:job:triage", agent_id="trading"
    )

    assert ok, output
    binding = get_agent_surface().resolve_token(output.strip())
    assert binding is not None
    assert binding.via == "token"
    assert binding.session_key == "cron:job:triage"
    assert binding.agent_id == "trading"


@pytest.mark.asyncio
async def test_child_is_still_an_agent_without_a_session(agentos_home: Path) -> None:
    """The handlers pass no session yet; the token still marks the connection."""
    _write_script(agentos_home, "who.py", f"import os; print(os.environ['{AGENT_TOKEN_ENV}'])")

    ok, output = await run_job_script("who.py", timeout=30)

    assert ok, output
    binding = get_agent_surface().resolve_token(output.strip())
    assert binding is not None and binding.via == "token"
    assert binding.session_key is None and binding.agent_id is None


@pytest.mark.asyncio
async def test_exec_window_is_open_while_the_script_runs(agentos_home: Path) -> None:
    surface = get_agent_surface()
    # The script reports back through the surface: a connection that presents
    # nothing while it runs is the agent's, bound to the job's session.
    seen: list[tuple[int, str, str | None]] = []
    real_window = surface.exec_window

    def spying_window(session_key):
        cm = real_window(session_key)

        class _Spy:
            def __enter__(self):
                cm.__enter__()
                binding = surface.bind({})
                seen.append((surface.active_windows(), binding.via, binding.session_key))

            def __exit__(self, *exc):
                return cm.__exit__(*exc)

        return _Spy()

    surface.exec_window = spying_window  # type: ignore[method-assign]
    try:
        _write_script(agentos_home, "noop.py", "print('hi')")
        ok, _ = await run_job_script("noop.py", timeout=30, session_key="cron:job:x")
    finally:
        del surface.exec_window
    assert ok
    assert seen == [(1, "window", "cron:job:x")]
    assert surface.active_windows() == 0
    assert surface.bind({}).via == "unbound"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX thing")
@pytest.mark.asyncio
async def test_a_detached_child_dies_with_the_script(agentos_home: Path, tmp_path: Path) -> None:
    import asyncio

    pid_file = tmp_path / "sleeper.pid"
    _write_script(
        agentos_home,
        "detach.sh",
        f"(nohup sh -c 'trap \"\" TERM; echo $$ > {pid_file}; sleep 30' >/dev/null 2>&1 &)\n"
        "sleep 0.3\necho done\n",
    )

    ok, output = await run_job_script("detach.sh", timeout=30, session_key="cron:job:x")

    assert ok, output
    pid = int(pid_file.read_text().strip())
    for _ in range(50):
        if not _pid_alive(pid):
            break
        await asyncio.sleep(0.05)
    assert not _pid_alive(pid), f"detached child {pid} outlived the script"


@pytest.mark.skipif(os.name != "posix", reason="process groups are a POSIX thing")
@pytest.mark.asyncio
async def test_timeout_kills_the_whole_group(agentos_home: Path, tmp_path: Path) -> None:
    import asyncio

    pid_file = tmp_path / "sleeper.pid"
    _write_script(
        agentos_home,
        "hang.sh",
        f"(sh -c 'echo $$ > {pid_file}; sleep 30' &)\nsleep 30\n",
    )

    ok, output = await run_job_script("hang.sh", timeout=1, session_key="cron:job:x")

    assert not ok and "timed out" in output
    pid = int(pid_file.read_text().strip())
    for _ in range(50):
        if not _pid_alive(pid):
            break
        await asyncio.sleep(0.05)
    assert not _pid_alive(pid)
