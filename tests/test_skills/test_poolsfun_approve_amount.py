"""Issue #2719: ``pools_write.py approve`` read ``--amount 0`` as "no amount".

``_amount`` returns ``0`` for ``--amount 0``, and ``amount_raw if amount_raw
else 2**256 - 1`` is a truthiness test, so the standard ERC20 revoke
(``approve(spender, 0)``) was rewritten into an unlimited allowance and
announced as ``new allowance: unlimited``. An operator reducing their exposure
got the maximum of it.

The second half is the replay command. ``PLAN_HASH`` covers the amount, and
the printed "to execute" line dropped ``--amount``, so pasting it back
recomputed the unlimited default, hashed differently, and ``--confirm``
refused the broadcast.

Everything here is offline: the RPC client is a stub, the signer is a
synthetic key built from a fixed byte, and nothing touches the network.
"""

from __future__ import annotations

import importlib.util
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "agentos"
    / "skills"
    / "bundled"
    / "poolsdotfun-token-launcher"
    / "scripts"
)

_UNLIMITED = 2**256 - 1

#: Built rather than pasted, so no key-shaped literal is committed. ``0x11…11``
#: is a valid secp256k1 scalar and obviously synthetic.
_SIGNER_KEY = "0x" + "11" * 32


@pytest.fixture
def pools_write(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load the script the way its shebang does, with a signer in the env."""
    monkeypatch.syspath_prepend(str(_SCRIPTS))
    monkeypatch.setenv("POOLSFUN_PRIVATE_KEY", _SIGNER_KEY)
    spec = importlib.util.spec_from_file_location("pools_write", _SCRIPTS / "pools_write.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "pools_write", module)
    spec.loader.exec_module(module)
    return module


class _StubClient:
    """Reports an existing allowance, so a revoke has something to revoke."""

    def __init__(self, allowance: int = 5 * 10**18) -> None:
        self.allowance = allowance

    def read(self, *_args: Any, **_kwargs: Any) -> int:
        return self.allowance


def _run(
    pools_write: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> dict[str, Any]:
    """Run one ``approve`` invocation and report what the operator can act on.

    ``_send`` is stubbed rather than the RPC layer, so a confirmed replay runs
    the whole command up to the point of broadcast and the calldata it would
    have put on the chain is captured exactly.
    """
    from poolsfun.fmt import parse_args

    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(pools_write, "_send", lambda *a, **k: sent.append(a))
    pools_write.cmd_approve(_StubClient(), parse_args(argv))

    lines = capsys.readouterr().out.splitlines()
    return {
        "allowance": next(x for x in lines if "new allowance" in x).split(":", 1)[1].strip(),
        "digest": next(x for x in lines if "PLAN_HASH" in x).split()[-1],
        "replay": next((x.strip() for x in lines if "python3 pools_write.py" in x), None),
        # ``_send(client, signer, to, data, args)`` -- the encoded call.
        "calldata": sent[0][3] if sent else None,
        "sends": len(sent),
    }


def _replay(
    pools_write: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    printed: str,
) -> dict[str, Any]:
    """Re-run the printed command the way a shell that was handed it would.

    ``shlex.split`` rather than ``str.split``: the line is meant to be pasted
    into a shell, so a quoted value has to survive that reading -- which is
    the reason the flags are emitted through ``shlex.quote``.
    """
    tokens = shlex.split(printed)
    assert tokens[:2] == ["python3", "pools_write.py"]
    return _run(pools_write, monkeypatch, capsys, tokens[2:])


def _calldata(pools_write: Any, amount: int) -> str:
    """The raw ``approve(spender, amount)`` calldata, for an exact comparison.

    Asserting on the encoded bytes rather than on a rendered allowance string
    is the point: the rendering passes through ``fmt_units``, and a defect in
    the amount that reaches the chain can hide behind a correct-looking label.
    """
    return pools_write.encode_function_data(
        pools_write.ERC20_ABI, "approve", [pools_write.PARTY_FACTORY, amount]
    )


# --- the revoke ------------------------------------------------------------


def test_amount_zero_plans_a_revoke_not_an_unlimited_allowance(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``approve(spender, 0)`` is how ERC20 revokes. It must stay a revoke."""
    plan = _run(pools_write, monkeypatch, capsys, ["approve", "--paired", "weth", "--amount", "0"])

    assert plan["allowance"] == "0"
    assert "unlimited" not in plan["allowance"]


def test_amount_zero_sends_zero_to_the_contract(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole round trip, on the case that made this a security issue.

    Plan with ``--amount 0``, paste the printed command back, and assert the
    calldata that reaches the chain encodes ``0`` — not ``2**256 - 1``. This
    is the assertion the bug defeats twice: once by rewriting the amount, and
    once by dropping ``--amount`` so the re-run refuses with a hash mismatch.
    """
    plan = _run(pools_write, monkeypatch, capsys, ["approve", "--paired", "weth", "--amount", "0"])

    confirmed = _replay(pools_write, monkeypatch, capsys, plan["replay"])

    assert confirmed["sends"] == 1
    assert confirmed["calldata"] == _calldata(pools_write, 0)
    assert confirmed["calldata"] != _calldata(pools_write, _UNLIMITED)


def test_amount_zero_keeps_the_flag_in_the_replay_command(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``0`` is the value the old code could not tell from "absent"."""
    plan = _run(pools_write, monkeypatch, capsys, ["approve", "--paired", "weth", "--amount", "0"])

    assert "--amount 0" in plan["replay"]


# --- the plan hash round trip ----------------------------------------------


@pytest.mark.parametrize("amount", ["0", "1", "10.5", "0.000000000000000001"])
def test_the_printed_command_reproduces_the_plan_hash(
    pools_write: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    amount: str,
) -> None:
    """Re-running the printed line must produce the same PLAN_HASH.

    Includes one-wei and a fractional amount: the digest covers the parsed
    integer, so a flag that round-trips as text but re-parses differently
    would show up here.

    The ``0`` case is the one that passes against the broken code, and it is
    worth keeping for exactly that reason: with the truthiness test, ``0``
    became unlimited, the replay dropped ``--amount``, and the re-run computed
    unlimited again — self-consistent, same hash, wrong allowance. A hash
    round trip alone cannot see this bug, which is why
    :func:`test_amount_zero_sends_zero_to_the_contract` asserts on the
    calldata instead.
    """
    plan = _run(
        pools_write, monkeypatch, capsys, ["approve", "--paired", "weth", "--amount", amount]
    )

    replayed = _replay(pools_write, monkeypatch, capsys, plan["replay"])

    assert replayed["digest"] == plan["digest"]
    assert replayed["sends"] == 1


def test_the_replay_carries_the_signer_the_digest_was_built_from(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--signer-env`` feeds ``owner``, which feeds the digest.

    A different signer is a different plan, so dropping the flag produces a
    hash the re-run cannot match even when ``--amount`` survives.
    """
    monkeypatch.setenv("OTHER_SIGNER", "0x" + "22" * 32)
    plan = _run(
        pools_write,
        monkeypatch,
        capsys,
        ["approve", "--paired", "weth", "--amount", "7", "--signer-env", "OTHER_SIGNER"],
    )

    assert "--signer-env OTHER_SIGNER" in plan["replay"]

    replayed = _replay(pools_write, monkeypatch, capsys, plan["replay"])
    assert replayed["digest"] == plan["digest"]


def test_the_replay_carries_a_custom_rpc(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--rpc`` does not feed the digest, but it decides which node executes.

    A command labelled "to execute" that silently moves to the default node is
    not the run the operator planned.
    """
    plan = _run(
        pools_write,
        monkeypatch,
        capsys,
        ["approve", "--paired", "weth", "--amount", "3", "--rpc", "https://rpc.invalid/path"],
    )

    assert "--rpc https://rpc.invalid/path" in plan["replay"]


def test_the_replay_command_is_accepted_by_confirm(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Positive control through the operator's own two-step protocol.

    The negative assertions above would all pass against a plan that never
    reaches ``_confirmed`` at all; this one proves the printed line really is
    accepted and really does broadcast.
    """
    plan = _run(pools_write, monkeypatch, capsys, ["approve", "--paired", "weth", "--amount", "42"])

    confirmed = _replay(pools_write, monkeypatch, capsys, plan["replay"])

    assert confirmed["sends"] == 1
    assert confirmed["calldata"] == _calldata(pools_write, 42 * 10**18)


# --- the omitted flag, unchanged -------------------------------------------


def test_omitting_amount_still_means_unlimited(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented default. Passes either way by design — it is the
    behaviour the fix must not disturb while making ``0`` distinct from it."""
    plan = _run(pools_write, monkeypatch, capsys, ["approve", "--paired", "weth"])

    assert plan["allowance"] == "unlimited"


def test_omitting_amount_adds_no_amount_flag_to_the_replay(
    pools_write: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent flag must stay absent, or the replay would pin a value the
    operator never chose and the default could never be re-planned."""
    plan = _run(pools_write, monkeypatch, capsys, ["approve", "--paired", "weth"])

    assert "--amount" not in plan["replay"]

    confirmed = _replay(pools_write, monkeypatch, capsys, plan["replay"])
    assert confirmed["calldata"] == _calldata(pools_write, _UNLIMITED)


# --- rejected inputs, unchanged --------------------------------------------


def test_a_bare_amount_flag_still_demands_a_value(pools_write: Any) -> None:
    """``--amount`` with nothing after it parses as ``True``.

    ``True is not None``, so the presence test reaches ``_amount``, which
    rejects it — the flag must not be read as an unlimited allowance by
    another route. Passes either way by design; it guards the new check.
    """
    from poolsfun.fmt import parse_args

    with pytest.raises(ValueError, match="needs a value"):
        pools_write.cmd_approve(
            _StubClient(), parse_args(["approve", "--paired", "weth", "--amount"])
        )


def test_a_negative_amount_is_still_refused(pools_write: Any) -> None:
    """Passes either way by design: ``_amount`` is untouched by this change."""
    from poolsfun.fmt import parse_args

    with pytest.raises(ValueError, match="cannot be negative"):
        pools_write.cmd_approve(
            _StubClient(), parse_args(["approve", "--paired", "weth", "--amount", "-1"])
        )
