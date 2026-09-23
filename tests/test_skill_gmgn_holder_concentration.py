"""``gmgn-holder-analysis``: Top10/Top20 concentration counts only real holders.

The report splits the top-100 into ``normal`` / ``burn`` / ``dex`` and says what
the last two mean: the burn line calls that balance "permanently locked,
non-circulating", and the footer prints ``DEX … excluded from eval``. Every
aggregate in the script sums over ``normal`` — except the two that produce the
headline number.

``top10``/``top20`` summed over the raw ``holders`` list. On any tradable token
the AMM pool holds the largest balance and the burn address is usually next, so
the first slots of that list are exactly the supply the report has just declared
uncountable. A token whose real holders are spread thin therefore printed a red
"highly concentrated" verdict over a pool that cannot sell.

Nothing errors: the number is rendered, given a risk light, and turned into the
dump-risk summary line the agent reads back to the user.

These tests run the script the way the CLI does — module level, real argv — and
stub only ``subprocess.run``, which is the ``gmgn-cli`` network boundary.
"""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "gmgn-holder-analysis"
    / "scripts"
    / "analyze.py"
)

_ADDR_NORMAL = 0
_ADDR_BURN = 1
_ADDR_DEX = 2


def _holder(address: str, share: float, *, addr_type: int = _ADDR_NORMAL) -> dict:
    """One top-100 row. ``share`` is a fraction — the report multiplies by 100."""
    return {
        "address": address,
        "addr_type": addr_type,
        "amount_percentage": share,
        "balance": share * 1000.0,
        "usd_value": share * 1000.0,
        "maker_token_tags": [],
        "tags": [],
        "buy_tx_count_cur": 3,
        "sell_tx_count_cur": 0,
        "native_balance": 0,
        "native_transfer": {},
        "realized_profit": 0,
        "profit": 0,
    }


def _report(monkeypatch: pytest.MonkeyPatch, capsys, holders: list[dict]) -> str:
    def fake_run(args, *_a, **_kw):
        listed = [] if "--tag" in args else holders
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"list": listed}), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["analyze.py", "SoLtoken1111", "sol", "en"])
    runpy.run_path(str(SCRIPT), run_name="__main__")
    return capsys.readouterr().out


def _spread_token() -> list[dict]:
    """A pool-dominated token whose real holders are not concentrated at all.

    Pool 70%, burn 10%, twelve wallets at 1% each. Real concentration is 10% at
    Top10 and 12% at Top20; the raw top-100 slices give 88% and 92%.
    """
    return [
        _holder("PooL", 0.70, addr_type=_ADDR_DEX),
        _holder("BurN", 0.10, addr_type=_ADDR_BURN),
        *[_holder(f"w{i:02d}", 0.01) for i in range(12)],
    ]


def test_top10_and_top20_exclude_the_dex_pool_and_the_burn_address(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    out = _report(monkeypatch, capsys, _spread_token())

    assert "Top10 10.0%" in out, "the ten largest *holders* hold 10%, not 88%"
    assert "Top20 12.0%" in out, "the twenty largest *holders* hold 12%, not 92%"
    assert "Top10 88.0%" not in out
    assert "Top20 92.0%" not in out


def test_a_pool_dominated_token_is_not_flagged_as_a_dump_risk(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    out = _report(monkeypatch, capsys, _spread_token())

    assert "highly concentrated" not in out, (
        "the verdict was driven by supply the same report calls non-circulating"
    )
    assert "Top10 10.0% \U0001f7e2" in out, "10% across the top ten holders is a green light"


def test_guard_the_pool_and_burn_shares_are_still_reported(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Excluding them from concentration must not hide them from the reader."""
    out = _report(monkeypatch, capsys, _spread_token())

    assert "Burn addr   10.00%" in out
    assert "DEX 70.0% excluded from eval" in out


def test_positive_control_a_genuinely_concentrated_token_still_flags_red(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Without this the assertions above could pass on a report that never flags.

    Deliberately a token with no burn row and no pool row, so ``holders`` and
    ``normal`` are the same list and this reads identically before and after the
    change — it proves the concentration path is live, nothing more.
    """
    holders = [
        _holder("whale", 0.60),
        *[_holder(f"w{i:02d}", 0.01) for i in range(10)],
    ]

    out = _report(monkeypatch, capsys, holders)

    assert "Top10 69.0% \U0001f534" in out, "one wallet on 60% is a real concentration risk"
    assert "Largest wallet holds 60.0%" in out, "the dump-risk summary still fires"
