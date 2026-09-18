import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "gmgn-wallet-score"
    / "scripts"
    / "score.py"
)


def test_score_rejects_non_numeric_latency():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "0x123", "solana", "en", "not_a_number"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "Error: invalid numeric argument" in res.stderr


def test_score_rejects_negative_slippage():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "0x123", "solana", "en", "3.0", "-0.1"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "Error: invalid numeric argument" in res.stderr
    assert "slippage_pct must be non-negative" in res.stderr


def test_score_rejects_invalid_sample_count():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "0x123", "solana", "en", "3.0", "0.05", "0.2", "0"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 2
    assert "Error: invalid numeric argument" in res.stderr
    assert "sample must be positive" in res.stderr
