"""Regression tests for issue #971.

The copy-trade backtest floored ``wallet_pct`` with ``wallet_pct or 0.0001``.
``wallet_pct`` is always a float, so ``or`` only ever fired on an exact ``0.0``
-- a wallet at genuinely 0% ROI -- and 0.0001 then became the divisor in
``copy_7d = realized_profit * (copy_pct / wallet_pct)``, amplifying a
break-even wallet into a six-figure loss shown straight to the user. The
``if wallet_pct else 0.0`` guard already in the code handles that case
correctly once the floor is gone.

Everything here is offline: ``gmgn-cli`` is replaced by a stub on ``PATH``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT / "src" / "agentos" / "skills" / "bundled" / "gmgn-wallet-score" / "scripts" / "score.py"
)

STUB = '''#!/usr/bin/env python3
"""Stand-in for gmgn-cli: answers from a fixture file, never touches network."""
import json, os, sys

fixture = json.loads(open(os.environ["GMGN_FIXTURE"], encoding="utf-8").read())
argv = sys.argv[1:]
if argv[:2] == ["portfolio", "stats"]:
    print(json.dumps(fixture["stats"]))
elif argv[:2] == ["portfolio", "activity"]:
    print(json.dumps({"activities": [], "next": None}))
else:
    print("unexpected gmgn-cli invocation: " + " ".join(argv), file=sys.stderr)
    sys.exit(1)
'''


def _run_score(tmp_path: Path, stats: dict) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "gmgn-cli"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"stats": stats}), encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["GMGN_FIXTURE"] = str(fixture)

    return subprocess.run(
        [sys.executable, str(SCRIPT), "0xWalletAddress", "bsc", "en"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _stats(*, realized_profit: float, bought_cost: float, roi: float) -> dict:
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


def _copy_estimate(stdout: str) -> str:
    match = re.search(r"copy estimate (\S+)", stdout)
    assert match is not None, f"no backtest line in output:\n{stdout}"
    return match.group(1)


def test_zero_roi_dev_wallet_reports_a_zero_copy_estimate(tmp_path: Path) -> None:
    """bought_cost == 0 and roi == 0.0: the divisor guard must fire, not a floor."""
    result = _run_score(tmp_path, _stats(realized_profit=800.0, bought_cost=0.0, roi=0.0))

    assert result.returncode == 0, result.stderr
    assert "Wallet per-trade return  +0.0%" in result.stdout
    assert _copy_estimate(result.stdout) == "$0.00"


def test_zero_roi_with_a_loss_also_reports_zero(tmp_path: Path) -> None:
    result = _run_score(tmp_path, _stats(realized_profit=-500.0, bought_cost=0.0, roi=0.0))

    assert result.returncode == 0, result.stderr
    assert _copy_estimate(result.stdout) == "$0.00"


@pytest.mark.parametrize(
    ("realized_profit", "bought_cost", "roi"),
    [
        pytest.param(800.0, 1000.0, 0.8, id="ordinary-profitable-wallet"),
        pytest.param(-200.0, 1000.0, -0.2, id="losing-wallet"),
        pytest.param(800.0, 0.0, 0.5, id="dev-wallet-nonzero-roi"),
    ],
)
def test_nonzero_returns_are_unaffected(
    tmp_path: Path, realized_profit: float, bought_cost: float, roi: float
) -> None:
    """Removing the floor must not move any wallet whose return is not 0.0."""
    result = _run_score(
        tmp_path, _stats(realized_profit=realized_profit, bought_cost=bought_cost, roi=roi)
    )

    assert result.returncode == 0, result.stderr
    assert _copy_estimate(result.stdout) != "$0.00"
