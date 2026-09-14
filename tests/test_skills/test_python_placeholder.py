"""``{python}`` in a skill body resolves to the interpreter AgentOS runs on.

A skill script needs the Python that carries AgentOS's dependencies and is new
enough for the script's syntax. A bare ``python`` in the instruction text is
whatever the user's PATH says — a Homebrew 3.9 that happens to have ``httpx``
got as far as ``isinstance(x, int | float)`` before dying — so the body names
the interpreter through a placeholder and the tool layer fills it in.
"""

from __future__ import annotations

import sys

import pytest

from agentos.skills.resources import (
    SKILL_PYTHON_PLACEHOLDER,
    expand_skill_placeholders,
    skill_python,
)


def test_skill_python_is_the_running_interpreter() -> None:
    assert skill_python() == sys.executable


def test_skill_python_quotes_paths_with_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\AgentOS\python.exe")
    assert skill_python() == r'"C:\Program Files\AgentOS\python.exe"'


def test_expand_resolves_python_and_base_dir_together() -> None:
    body = "Run `{python} {baseDir}/scripts/run.py` and read {baseDir}/references/x.md"
    out = expand_skill_placeholders(body, "/skills/deck", python="/venv/bin/python")
    assert out == (
        "Run `/venv/bin/python /skills/deck/scripts/run.py` and read /skills/deck/references/x.md"
    )


def test_expand_resolves_python_even_without_base_dir() -> None:
    # A workspace skill with no directory still gets a usable interpreter.
    out = expand_skill_placeholders(f"{SKILL_PYTHON_PLACEHOLDER} -c 'print(1)'", "", python="/p")
    assert out == "/p -c 'print(1)'"


def test_expand_defaults_to_the_running_interpreter() -> None:
    assert expand_skill_placeholders("{python}", "/x") == skill_python()
