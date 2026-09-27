"""Regression tests for nano-banana-pro ``generate_image.py`` retry backoff (#2637).

The sleep between attempts used ``2 ** n``, where ``n`` is the per-model
attempt counter and resets to 1 on every fallback model, so the backoff dropped
from 4s back to 2s exactly when the schedule moved off the struggling primary.
It now uses the global 1-based ``attempt_idx``.

These tests drive the real ``main()`` entry point with every attempt failing,
so the schedule and the backoff arithmetic are the script's own, not a copy.
The script is imported by file path because the skill directory contains a
hyphen and runs as a standalone subprocess script.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_SCRIPT = REPO_ROOT / "src/agentos/skills/bundled/nano-banana-pro/scripts/generate_image.py"


def _load_script(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def image_script() -> ModuleType:
    return _load_script(IMAGE_SCRIPT, "_agentos_test_nano_banana_backoff")


def _run_all_failing(
    image_script: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *extra_argv: str,
) -> tuple[int, list[str], list[float]]:
    """Run ``main()`` with every attempt failing; return (exit, models, sleeps)."""
    models: list[str] = []
    sleeps: list[float] = []

    def _always_fail(**kwargs: object) -> bytes:
        models.append(str(kwargs["model"]))
        # The same exception type the real _try_one_attempt raises; main()
        # catches it, records it, and moves on to the next scheduled attempt.
        raise RuntimeError("no image (finish_reason=test)")

    monkeypatch.setattr(image_script, "_try_one_attempt", _always_fail)
    # The script does ``import time`` and calls ``time.sleep``; swap the
    # module's own ``time`` reference so nothing really sleeps.
    monkeypatch.setattr(image_script, "time", SimpleNamespace(sleep=sleeps.append))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_image.py",
            "-p",
            "x",
            "-f",
            str(tmp_path / "out.png"),
            "--api-key",
            "k",
            *extra_argv,
        ],
    )

    exit_code = image_script.main()
    return exit_code, models, sleeps


def test_backoff_does_not_reset_when_switching_to_fallback_model(
    image_script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--max-retries 1 plus two fallbacks is a 4-attempt schedule:

    primary #1, primary #2, a #1, b #1. main() sleeps after every failed attempt
    except the last, so there are 3 sleeps: 2**1, 2**2, 2**3. With the old
    per-model exponent the third sleep (before ``b``, after ``a`` #1) was
    2**1 = 2, i.e. ``[2, 4, 2]``.
    """
    exit_code, models, sleeps = _run_all_failing(
        image_script,
        monkeypatch,
        tmp_path,
        "--max-retries",
        "1",
        "--fallback-model",
        "a",
        "--fallback-model",
        "b",
        "--retry-backoff-cap",
        "60",
    )

    assert exit_code == 1
    assert models == [image_script.DEFAULT_MODEL, image_script.DEFAULT_MODEL, "a", "b"]
    assert sleeps == [2, 4, 8]
    assert not (tmp_path / "out.png").exists()


def test_backoff_respects_cap_across_fallback_models(
    image_script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--retry-backoff-cap still bounds the (now monotonic) global backoff.

    5 attempts (primary x2, a, b, c) give 4 sleeps: 2, 4, then 8 and 16 capped
    at 5. The old per-model exponent gave ``[2, 4, 2, 2]``.
    """
    exit_code, models, sleeps = _run_all_failing(
        image_script,
        monkeypatch,
        tmp_path,
        "--max-retries",
        "1",
        "--fallback-model",
        "a",
        "--fallback-model",
        "b",
        "--fallback-model",
        "c",
        "--retry-backoff-cap",
        "5",
    )

    assert exit_code == 1
    assert models == [image_script.DEFAULT_MODEL, image_script.DEFAULT_MODEL, "a", "b", "c"]
    assert sleeps == [2, 4, 5, 5]
