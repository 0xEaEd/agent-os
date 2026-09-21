"""code_exec flags every delete command shell_policy blocks, however it is invoked.

``shell_policy``'s Windows denylist blocks ``rm``/``ri`` (PowerShell's other
built-in ``Remove-Item`` aliases) alongside ``del``/``erase``/``rd``/``rmdir``/
``Remove-Item``. ``code_exec``'s destructive scan is the approval gate for the
same commands reached through Python, and it drifted from that list twice:

* ``ri`` was missing from all four copies of its delete-command alternation, so
  ``os.system("powershell -c ri C:\\data")`` ran without approval.
* ``shell_policy`` lets a single opening quote follow a ``cmd /c`` /
  ``powershell -Command`` wrapper -- the usual way to hand PowerShell a command
  string -- but ``code_exec`` did not, so ``pwsh -NoProfile -Command "del C:\\x"``
  slipped past for *every* delete command, not just ``ri``.

The parity tests below state the contract directly: a command shell_policy
denies on Windows must be flagged by code_exec in each call form.
"""

from __future__ import annotations

import re

import pytest

from agentos.tools.builtin.code_exec import _check_code_destructive
from agentos.tools.builtin.shell_policy import DEFAULT_DENYLIST, DEFAULT_DENYLIST_WIN

DELETE_COMMANDS = ["rm", "ri", "rd", "del", "erase", "rmdir", "Remove-Item"]

CALL_FORMS = {
    "os_system": 'import os\nos.system("{cmd} C:\\\\data")',
    "os_popen": 'import os\nos.popen("{cmd} C:\\\\data")',
    "os_system_cmd_c": 'import os\nos.system("cmd /c {cmd} C:\\\\data")',
    "os_system_powershell_c": 'import os\nos.system("powershell -c {cmd} C:\\\\data")',
    "subprocess_argv": 'import subprocess\nsubprocess.run(["{cmd}", "C:\\\\data"])',
    "subprocess_argv_upper": 'import subprocess\nsubprocess.run(["{upper}", "C:\\\\data"])',
    "subprocess_powershell_argv": (
        'import subprocess\nsubprocess.run(["powershell", "-c", "{cmd}", "C:\\\\data"])'
    ),
    "subprocess_cmd_c_argv": (
        'import subprocess\nsubprocess.run(["cmd", "/c", "{cmd}", "C:\\\\data"])'
    ),
    "subprocess_shell_string": (
        'import subprocess\nsubprocess.run("{cmd} C:\\\\data", shell=True)'
    ),
    "after_a_separator": (
        'import subprocess\nsubprocess.run("cd C:\\\\ && {cmd} data", shell=True)'
    ),
    "via_getattr": 'import os\ngetattr(os, "system")("{cmd} C:\\\\data")',
}

QUOTED_WRAPPERS = {
    "pwsh_command_double_quoted": (
        'import os\nos.system("pwsh -NoProfile -Command \\"{cmd} C:\\\\data\\"")'
    ),
    "powershell_c_single_quoted": "import os\nos.system(\"powershell -c '{cmd} C:\\\\data'\")",
    "cmd_c_double_quoted": "import os\nos.system('cmd /c \"{cmd} C:\\\\data\"')",
    "subprocess_string_pwsh_quoted": (
        "import subprocess\n"
        "subprocess.run('pwsh -NoProfile -Command \"{cmd} C:\\\\data\"', shell=True)"
    ),
}


def _shell_policy_denies_on_windows(command: str) -> bool:
    return any(
        re.search(pattern, command, re.IGNORECASE)
        for pattern in (*DEFAULT_DENYLIST, *DEFAULT_DENYLIST_WIN)
    )


@pytest.mark.parametrize("command", DELETE_COMMANDS)
def test_shell_policy_blocks_each_delete_command_on_windows(command: str) -> None:
    """The premise of the parity below -- passes either way by design."""
    assert _shell_policy_denies_on_windows(f"{command} C:\\data")
    assert _shell_policy_denies_on_windows(f'powershell -c "{command} C:\\data"')


@pytest.mark.parametrize("form", list(CALL_FORMS))
@pytest.mark.parametrize("command", DELETE_COMMANDS)
def test_code_exec_flags_each_delete_command_shell_policy_blocks(command: str, form: str) -> None:
    code = CALL_FORMS[form].format(cmd=command, upper=command.upper())

    warning = _check_code_destructive(code)

    assert warning is not None, code
    assert warning.startswith("destructive Python operation detected")


@pytest.mark.parametrize("form", list(QUOTED_WRAPPERS))
@pytest.mark.parametrize("command", DELETE_COMMANDS)
def test_a_quoted_wrapper_payload_is_scanned_like_an_unquoted_one(command: str, form: str) -> None:
    """``pwsh -Command "del C:\\x"`` is how a command string is usually handed over."""
    code = QUOTED_WRAPPERS[form].format(cmd=command)
    shell_command = f'pwsh -NoProfile -Command "{command} C:\\data"'

    assert _shell_policy_denies_on_windows(shell_command)
    assert _check_code_destructive(code) is not None, code


@pytest.mark.parametrize(
    "code",
    [
        # ri / rd as data, not as the command
        'import subprocess\nsubprocess.run(["git", "branch", "ri"])',
        'import subprocess\nsubprocess.run(["kubectl", "get", "pods", "-n", "ri"])',
        'import subprocess\nsubprocess.run(["ripgrep", "TODO"])',
        'import os\nos.system("echo ri")',
        'import os\nos.system("rg ri src")',
        # a quoted wrapper whose payload is not a delete
        'import os\nos.system("pwsh -NoProfile -Command \\"Get-ChildItem C:\\\\data\\"")',
        "import os\nos.system(\"powershell -c 'Write-Output del'\")",
        "import os\nos.system('cmd /c \"echo rd\"')",
        'import subprocess\nsubprocess.run(["powershell", "-Command", "Get-Item ri.txt"])',
        # a quote that does not follow a wrapper is not a command position
        'import os\nos.system("echo \\"del C:\\\\data\\"")',
    ],
)
def test_benign_code_is_not_flagged(code: str) -> None:
    assert _check_code_destructive(code) is None, code
