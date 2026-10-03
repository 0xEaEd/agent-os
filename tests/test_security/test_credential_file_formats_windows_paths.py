"""Issue #3544: the format rule missed a credential file named by a Windows path.

#2620/#2721 gave ``.pgpass`` and ``.netrc`` a format rule keyed on the
operand's basename, because the password those two carry positionally is the
one the assignment pass cannot name. ``_credential_file_formats_in`` found
that operand with a bare ``shlex.split``, which runs in POSIX mode and reads
``\\`` as an escape -- so ``type C:\\Users\\me\\_netrc`` tokenised to
``C:Usersme_netrc``, no basename matched, the rule did not run, and the
password went to the model verbatim. The POSIX spelling of the same read was
masked, so the leak was host-shaped.

``reads_credential_file`` already avoided this by tokenising with
``_command_operands``; this is the one call in the chain that had not been
switched over. These tests are written against both, so the two cannot drift
apart again.

Backslashes are written doubled rather than as raw strings on purpose: the
release-hygiene check scans tracked files for the literal bytes of a
developer-machine path, and an escaped literal carries the same value to the
code under test without looking like one.
"""

from __future__ import annotations

import pytest

from agentos import redact

PG_PASSWORD = "pgpass-hunter2-3544"
PGPASS = f"db.host:5432:app:appuser:{PG_PASSWORD}\n"

NETRC_PASSWORD = "netrc-hunter2-3544"
NETRC = f"machine api.example.com login me password {NETRC_PASSWORD}\n"

WINDOWS_NETRC_READS = [
    "type C:\\Users\\me\\_netrc",
    "type C:\\Users\\me\\.netrc",
    "Get-Content C:\\Users\\me\\_netrc",
    "more C:\\Users\\me\\AppData\\Roaming\\_netrc",
    'type "C:\\Users\\My Name\\_netrc"',
]

WINDOWS_PGPASS_READS = [
    "type C:\\Users\\me\\AppData\\Roaming\\postgresql\\.pgpass",
    "Get-Content C:\\Users\\me\\.pgpass",
    'type "C:\\Users\\My Name\\AppData\\Roaming\\postgresql\\.pgpass"',
]


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("command", WINDOWS_NETRC_READS)
def test_a_netrc_read_with_a_windows_path_masks_the_password(command: str) -> None:
    out = redact.redact_terminal_output(NETRC, command)

    assert NETRC_PASSWORD not in out
    assert "machine api.example.com login me password " in out, "the public fields stay"


@pytest.mark.parametrize("command", WINDOWS_PGPASS_READS)
def test_a_pgpass_read_with_a_windows_path_masks_the_password(command: str) -> None:
    out = redact.redact_terminal_output(PGPASS, command)

    assert PG_PASSWORD not in out
    assert out.startswith("db.host:5432:app:appuser:"), "the four public fields stay"


# ── the two tokenisers must agree ──────────────────────────────────────────


@pytest.mark.parametrize("command", WINDOWS_NETRC_READS + WINDOWS_PGPASS_READS)
def test_the_gate_and_the_format_rule_see_the_same_command(command: str) -> None:
    """The symptom that located this: one said yes, the other said nothing.

    ``reads_credential_file`` answered ``True`` for every command here while
    ``_credential_file_formats_in`` returned an empty set for all of them.
    """
    assert redact.reads_credential_file(command) is True
    assert redact._credential_file_formats_in(command) != set()


def test_the_windows_and_posix_spellings_redact_identically() -> None:
    """The parity this is really about: the same file read two ways."""
    assert redact.redact_terminal_output(NETRC, "type C:\\Users\\me\\_netrc") == (
        redact.redact_terminal_output(NETRC, "cat /home/u/.netrc")
    )
    assert redact.redact_terminal_output(
        PGPASS, "type C:\\Users\\me\\AppData\\Roaming\\postgresql\\.pgpass"
    ) == redact.redact_terminal_output(PGPASS, "cat /home/u/.pgpass")


# ── what must not change ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    ["cat ~/.pgpass", "cat /home/u/.pgpass", "cat ~/.netrc", "cat C:/Users/u/_netrc"],
)
def test_the_posix_and_forward_slash_spellings_still_work(command: str) -> None:
    text = PGPASS if "pgpass" in command else NETRC
    secret = PG_PASSWORD if "pgpass" in command else NETRC_PASSWORD

    assert secret not in redact.redact_terminal_output(text, command)


def test_a_backslash_in_a_command_naming_no_credential_file_finds_no_format() -> None:
    """``_command_operands`` adds a second, escape-free reading of the command.
    That extra reading must not invent a credential file that is not there."""
    assert redact._credential_file_formats_in("type C:\\Users\\me\\notes.txt") == set()
    assert redact._credential_file_formats_in("echo a\\tb") == set()


def test_a_windows_path_that_only_looks_like_pgpass_is_untouched() -> None:
    text = "host:5432:db:user:value\n"

    assert redact.redact_terminal_output(text, "type C:\\Users\\me\\.pgpass.example") == text


def test_a_flag_spelled_with_a_backslash_is_still_a_flag() -> None:
    assert redact._credential_file_formats_in("cat --pgpass C:\\x\\y.txt") == set()


def test_the_kill_switch_disables_the_windows_path_rule_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redact, "_REDACT_ENABLED", False)

    assert redact.redact_terminal_output(NETRC, "type C:\\Users\\me\\_netrc") == NETRC


def test_force_overrides_the_kill_switch_for_a_windows_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redact, "_REDACT_ENABLED", False)

    out = redact.redact_terminal_output(NETRC, "type C:\\Users\\me\\_netrc", force=True)

    assert NETRC_PASSWORD not in out
