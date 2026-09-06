"""Regression tests for issue #971.

The copy-trade backtest floored ``wallet_pct`` with ``wallet_pct or 0.0001``.
``wallet_pct`` is always a float, so ``or`` only ever fired on an exact ``0.0``
-- a wallet at genuinely 0% ROI -- and 0.0001 then became the divisor in
``copy_7d = realized_profit * (copy_pct / wallet_pct)``, amplifying a
break-even wallet into a six-figure loss shown straight to the user. The
``if wallet_pct else 0.0`` guard already in the code handles that case
correctly once the floor is gone.

``score.py`` is a flat script that does its work at import time, so it is run
here the way the sibling robinhood-rwa test runs its script: loaded through
``importlib`` with ``sys.argv`` and ``subprocess.run`` patched, stdout
captured. Nothing spawns a process and nothing touches the network, which also
keeps the test honest on Windows.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "src" / "agentos" / "skills" / "bundled" / "gmgn-wallet-score" / "scripts" / "score.py"
)


def _stats(*, realized_profit: float, bought_cost: float, roi: float) -> dict[str, Any]:
    return {
        "buy": 1,
        "sell": 1,
        "realized_profit": realized_profit,
        "bought_cost": bought_cost,
        "realized_profit_pnl": roi,
        "pnl_stat": {
            "token_num": 1,
            "winrate": 0.5,
            "avg_holding_period": 3600,
            "pnl_gt_5x_num": 0,
            "pnl_2x_5x_num": 0,
            "pnl_0x_2x_num": 1,
            "pnl_nd5_0x_num": 0,
            "pnl_lt_nd5_num": 0,
        },
        "common": {"created_token_count": 0},
    }


class _FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _run_score(monkeypatch: pytest.MonkeyPatch, stats: dict[str, Any]) -> str:
    """Execute score.py with a stubbed ``gmgn-cli`` and return its stdout."""
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> _FakeCompleted:
        seen.append(cmd)
        assert cmd[0] == "gmgn-cli", cmd
        argv = cmd[1:]
        if argv[:2] == ["portfolio", "stats"]:
            return _FakeCompleted(json.dumps(stats))
        if argv[:2] == ["portfolio", "activity"]:
            return _FakeCompleted(json.dumps({"activities": [], "next": None}))
        raise AssertionError(f"unexpected gmgn-cli invocation: {argv}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "0xWalletAddress", "bsc", "en"])

    spec = importlib.util.spec_from_file_location("gmgn_score_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        spec.loader.exec_module(module)

    assert seen, "score.py never called gmgn-cli"
    return buf.getvalue()


def _copy_estimate(stdout: str) -> str:
    """Pull the formatted copy-trade estimate out of the backtest line.

    Returned as printed rather than parsed: the buggy value renders as
    ``$567.0K``, so a string comparison shows exactly what the user would have
    seen instead of blowing up in a float conversion.
    """
    for line in stdout.splitlines():
        if "copy estimate" in line:
            return line.split("copy estimate")[1].split("(")[0].strip()
    raise AssertionError(f"no backtest line in output:\n{stdout}")


def test_zero_roi_dev_wallet_reports_a_zero_copy_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bought_cost == 0 and roi == 0.0: the divisor guard must fire, not a floor."""
    stdout = _run_score(monkeypatch, _stats(realized_profit=800.0, bought_cost=0.0, roi=0.0))

    assert "Wallet per-trade return  +0.0%" in stdout
    assert _copy_estimate(stdout) == "$0.00"


def test_zero_roi_with_a_loss_also_reports_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = _run_score(monkeypatch, _stats(realized_profit=-500.0, bought_cost=0.0, roi=0.0))

    assert _copy_estimate(stdout) == "$0.00"


@pytest.mark.parametrize(
    ("realized_profit", "bought_cost", "roi"),
    [
        pytest.param(800.0, 1000.0, 0.8, id="ordinary-profitable-wallet"),
        pytest.param(-200.0, 1000.0, -0.2, id="losing-wallet"),
        pytest.param(800.0, 0.0, 0.5, id="dev-wallet-nonzero-roi"),
    ],
)
def test_nonzero_returns_are_unaffected(
    monkeypatch: pytest.MonkeyPatch,
    realized_profit: float,
    bought_cost: float,
    roi: float,
) -> None:
    """Removing the floor must not move any wallet whose return is not 0.0."""
    stdout = _run_score(
        monkeypatch,
        _stats(realized_profit=realized_profit, bought_cost=bought_cost, roi=roi),
    )

    assert _copy_estimate(stdout) != "$0.00"
