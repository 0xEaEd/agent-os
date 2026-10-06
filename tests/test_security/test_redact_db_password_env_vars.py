"""Issue #3608: the standard database password env vars survived an env dump.

`env` is the case the assignment pass exists for -- `is_env_dump_command`
fires and turns it on precisely so a dumped environment does not hand its
secrets to the model. It masked `DB_PASSWORD` and passed `PGPASSWORD`,
`MYSQL_PWD` and `REDISCLI_AUTH` through verbatim.

Names are matched on segment boundaries, and these three do not produce the
segment the vocabulary holds:

    _name_segments("PGPASSWORD")     -> ['pgpassword']        one all-caps run
    _name_segments("MYSQL_PWD")      -> ['mysql', 'pwd']      'pwd' is not strong
    _name_segments("REDISCLI_AUTH")  -> ['rediscli', 'auth']  nor is 'auth'

`_NAME_SPLIT_RE` splits on separators and on the two camel-case boundaries;
`PGPASSWORD` has neither, so `password` is never seen inside it. The project
already treats this exact credential as sensitive from the other direction --
`.pgpass` has had a format rule since #2620/#2721.
"""

from __future__ import annotations

import pytest

from agentos.redact import (
    _is_credential_name,
    _name_segments,
    redact_sensitive_text,
    redact_terminal_output,
)

SECRET = "Sup3rS3cret-3608xyz"

LEAKED_NAMES = ["PGPASSWORD", "MYSQL_PWD", "REDISCLI_AUTH", "MONGODB_PASSWORD"]


# ── the issue's reproduction ───────────────────────────────────────────────


@pytest.mark.parametrize("name", LEAKED_NAMES)
def test_an_env_dump_masks_the_value(name: str) -> None:
    out = redact_terminal_output(f"PATH=/usr/bin\n{name}={SECRET}\n", "env")

    assert SECRET not in out
    assert out.startswith("PATH=/usr/bin"), "an ordinary variable is untouched"


@pytest.mark.parametrize("name", LEAKED_NAMES)
def test_the_name_is_recognised_as_a_credential(name: str) -> None:
    assert _is_credential_name(name) is True


def test_the_whole_dump_is_covered() -> None:
    env = "\n".join(
        [
            "PATH=/usr/bin",
            f"PGPASSWORD={SECRET}",
            f"MYSQL_PWD={SECRET}",
            f"REDISCLI_AUTH={SECRET}",
            f"DB_PASSWORD={SECRET}",
        ]
    )

    out = redact_terminal_output(env, "env")

    assert SECRET not in out
    assert out.count("***") == 4


@pytest.mark.parametrize("spelling", ["PGPASSWORD", "pgpassword", "PgPassword", "  PGPASSWORD  "])
def test_the_name_matches_however_it_is_cased(spelling: str) -> None:
    assert _is_credential_name(spelling) is True


def test_why_the_segment_rules_miss_it() -> None:
    """Pins the diagnosis, so a later change to the splitter shows up here."""
    assert _name_segments("PGPASSWORD") == ["pgpassword"]
    assert _name_segments("MYSQL_PWD") == ["mysql", "pwd"]
    assert _name_segments("REDISCLI_AUTH") == ["rediscli", "auth"]


# ── what must not change ───────────────────────────────────────────────────


def test_a_path_variable_naming_the_same_secret_is_not_masked() -> None:
    """``PGPASSFILE`` holds a path, not a password. Masking it would lose the
    only useful thing the line said, and the file itself has its own rule."""
    out = redact_terminal_output("PGPASSFILE=/home/u/.pgpass\n", "env")

    assert out.strip() == "PGPASSFILE=/home/u/.pgpass"
    assert _is_credential_name("PGPASSFILE") is False


@pytest.mark.parametrize("name", ["PATH", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "HOME"])
def test_an_ordinary_variable_is_untouched(name: str) -> None:
    line = f"{name}=something-fairly-long-here\n"

    assert redact_terminal_output(line, "env") == line


def test_the_names_that_already_worked_still_do() -> None:
    env = f"DB_PASSWORD={SECRET}\nAPI_KEY={SECRET}aaaa\napiSecret={SECRET}\n"

    out = redact_terminal_output(env, "env")

    assert SECRET not in out


def test_a_reference_rather_than_a_literal_is_left_alone() -> None:
    line = "PGPASSWORD=$PGPASSWORD\n"

    assert redact_terminal_output(line, "env") == line


def test_ordinary_command_output_is_still_not_name_scanned() -> None:
    """The assignment pass stays gated: this change adds a name, not a surface."""
    line = f"PGPASSWORD={SECRET}"

    assert SECRET in redact_terminal_output(line, "ls -la")


def test_it_also_covers_the_default_text_surface() -> None:
    assert SECRET not in (redact_sensitive_text(f"PGPASSWORD={SECRET}") or "")
