"""Prefix commands let a delete reach `execute_code` without an approval prompt.

`_check_code_destructive` is the gate in front of `execute_code`'s approval
flow: when it returns `None` the tool skips `_check_exec_approval` entirely and
runs the code, so a miss is not a weaker warning — it is no prompt at all.

`_PREFIX_CMD_PATTERN` models the commands that can stand in front of the real
one (`sudo`, `env`, `nice`, `nohup`, ...). Two gaps let a delete through:

* **No path qualification.** `env rm -rf /data` was caught, `/usr/bin/env rm -rf
  /data` was not — although the shell branch of `_SHELL_WRAPPER_PATTERN` right
  above already allows exactly that with its own ``(?:\\S*[/\\\\])?``, so
  `/bin/bash -c '...'` was handled. The absolute spelling is the one shebangs
  and CI scripts use.
* **Missing wrappers.** `exec`, `command`, `builtin`, `setsid`, `stdbuf`,
  `ionice`, `chroot` and `busybox` were not modelled at all. `exec rm -rf /data`
  is an ordinary thing to write.

These are the same class #2096 and the two 2026-09 sweeps closed for shell
wrappers and PowerShell flags, on the prefix-command side of the same
expression.
"""

from __future__ import annotations

import time

import pytest

from agentos.tools.builtin.code_exec import _check_code_destructive


def _detected(command: str) -> bool:
    return _check_code_destructive(f'os.system("{command}")') is not None


# ── the report ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/env rm -rf /data",
        "/bin/nice rm -rf /data",
        "/usr/bin/sudo rm -rf /data",
        "/usr/bin/timeout 5 rm -rf /data",
    ],
)
def test_a_path_qualified_prefix_is_still_a_prefix(command):
    """The shell branch already allowed a path; the prefix branch did not."""
    assert _detected(command), command


@pytest.mark.parametrize(
    "command",
    [
        "exec rm -rf /data",
        "command rm -rf /data",
        "builtin rm -rf /data",
        "setsid rm -rf /data",
        "stdbuf -o0 rm -rf /data",
        "ionice -c3 rm -rf /data",
        "chroot /mnt rm -rf /data",
        "busybox rm -rf /data",
    ],
)
def test_the_unmodelled_wrappers_are_modelled(command):
    assert _detected(command), command


def test_the_bare_spelling_was_already_caught():
    """Positive control: the gap was the qualification, not the command."""
    assert _detected("env rm -rf /data")
    assert _detected("nice rm -rf /data")


# ── nothing benign starts being flagged ────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "echo hi",
        "git commit -m 'remove stale docs'",
        "grep -r rm .",
        "/usr/bin/env python3 script.py",
        "command -v python",
        "exec 3<&0",
        "busybox --help",
        "chroot /mnt /bin/true",
        "stdbuf -o0 tail -f log",
        "ionice -c3 tar cf a.tar b",
        "setsid tmux",
        "sudo systemctl restart nginx",
        "timeout 5 ping example.com",
        "echo 'rm is a word in this string'",
    ],
)
def test_a_benign_command_is_not_flagged(command):
    assert not _detected(command), command


# ── the constraint the existing comments call out ──────────────────────────


@pytest.mark.parametrize(
    ("label", "template"),
    [
        ("repeated prefixes, no delete", "{}echo hi"),
        ("path-qualified repeats", "{}echo hi"),
    ],
)
def test_a_long_prefix_run_does_not_backtrack(label, template):
    """`_FLAG_VALUE`'s comment warns a flag run with no delete behind it can
    backtrack exponentially. The added alternatives must not reintroduce that.
    """
    filler = "/usr/bin/env /bin/nice exec command " * 80
    source = f'os.system("{template.format(filler)}")'

    start = time.perf_counter()
    _check_code_destructive(source)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"{label}: {elapsed:.2f}s"
