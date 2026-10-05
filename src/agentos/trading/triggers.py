"""Price triggers: conditional orders the trading engine watches and fires by itself.

``docs/triggers.md`` is the contract. This module is the pure part of it: the
condition (``below`` / ``above`` / ``trail``) and its evaluation, relative
prices resolved against the price at creation, the default names, the
labels, the validation of a trigger's terms and the card payload
(``application/vnd.agentos.trigger+json``). It imports nothing from the
service, so the CLI and tests can use it without a wallet, a network or a
ledger; the runner itself lives on
:class:`agentos.trading.service.TradingService`, which owns the swap pipeline
every fire goes through.

Times are epoch seconds (floats) everywhere except the payload, which carries
ISO-8601 UTC strings. USD ``None`` means unknown, never ``0``. The LP card
types (``Chain``, ``Token``, ``Amount``, ``Wallet``) come from :mod:`dca`.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from agentos.trading.chains import ChainSpec
from agentos.trading.dca import (
    amount_json,
    chain_json,
    clean,
    envelope,
    finite,
    iso,
    reason,
    split_reason,
    token_json,
    usd_text,
)
from agentos.trading.pnl import format_amount, to_human

TRIGGER_MIME = "application/vnd.agentos.trigger+json"
TRIGGER_ID_PREFIX = "trg_"

KINDS = ("sell", "buy", "alert")
DIRECTIONS = ("below", "above", "trail")
STATUSES = (
    "awaiting_approval",
    "armed",
    "triggered",
    "paused",
    "done",
    "stopped",
    "rejected",
    "expired",
)
LIVE_STATUSES = frozenset({"awaiting_approval", "armed", "triggered", "paused"})
TERMINAL_STATUSES = frozenset({"done", "stopped", "rejected", "expired"})

# ``pending``: the order is on its way (quoted, approved or submitted);
# ``parked``: it waits for the user. Both become one of the others when the
# order settles.
FIRE_STATUSES = (
    "pending",
    "filled",
    "parked",
    "alerted",
    "skipped",
    "failed",
    "expired",
    "rejected",
)
FIRE_OPEN_STATUSES = frozenset({"pending", "parked"})

#: Consecutive checks the condition must hold on before the trigger fires.
CONFIRM_TICKS = 2
#: Consecutive failed fires that pause a trigger.
BAD_STREAK_LIMIT = 3
#: How long an agent's proposal waits for the user before it expires.
PENDING_TTL = 86_400
#: A ``pending`` fire that never got an order (the process died between the
#: two writes) is written off as failed after this long.
ORPHAN_FIRE_S = 3_600.0
#: Fires a card shows (newest first).
HISTORY_LIMIT = 20
MAX_NAME_LENGTH = 80
#: The shortest ``validFor`` a trigger may be given.
MIN_VALID_FOR_S = 60

#: ``statusReason`` of a manual fire while it is open: where a failed one goes back to.
FIRED_BY_HAND = "fired by hand"
FIRED_BY_HAND_PAUSED = "fired by hand while paused"


def new_trigger_id() -> str:
    return TRIGGER_ID_PREFIX + uuid.uuid4().hex[:8]


# ── prices and labels ──────────────────────────────────────────────────────


def price_text(value: float) -> str:
    """A USD price for a label: ``3800`` → ``"$3,800"``, ``0.00123`` → ``"$0.00123"``.

    :func:`dca.usd_text` from a dollar up; under a dollar four significant
    digits, so a token at a fraction of a cent does not print as ``$0.00``.
    """
    number = float(value)
    if number >= 1 or number <= 0:
        return usd_text(number)
    if abs(number * 100 - round(number * 100)) < 1e-9:
        return f"${number:.2f}"
    places = max(2, 3 - math.floor(math.log10(number)))
    text = f"{number:.{places}f}".rstrip("0")
    return "$" + (text + "0" if text.endswith(".") else text)


def pct_text(value: float) -> str:
    """``10.0`` → ``"10"``, ``7.5`` → ``"7.5"``."""
    return f"{float(value):g}"


def _percent(text: str) -> float | None:
    """``"-10%"`` → ``-10.0``, ``"+15 %"`` → ``15.0``; ``None`` when not a percent."""
    stripped = text.strip()
    if not stripped.endswith("%"):
        return None
    body = stripped[:-1].strip()
    try:
        value = float(body)
    except ValueError:
        raise ValueError(f"not a percent: {text!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"not a percent: {text!r}")
    return value


def parse_price(
    text: str | float | int,
    direction: str,
    current_price: float | None,
    *,
    symbol: str = "the token",
) -> tuple[float, float | None]:
    """A ``below``/``above`` price → ``(price_usd, from_price_usd)``.

    ``"3800"`` (or ``"$3,800"``, or a number) is absolute and ``from_price_usd``
    is ``None``. A percent is relative to ``current_price`` and resolved here,
    once: ``"-10%"`` / ``"10%"`` on ``below`` is 10 % under it, ``"+15%"`` /
    ``"15%"`` on ``above`` 15 % over it. A percent pointing the wrong way
    (``"+10%"`` on ``below``) is refused, as is a percent with no price to
    resolve it against. Every refusal is a ``ValueError`` with the message
    the user sees.
    """
    if direction not in ("below", "above"):
        raise ValueError("a price goes with below or above; trail takes trailPct")
    pct = _percent(text) if isinstance(text, str) else None
    if pct is None:
        raw = str(text).strip().replace(",", "").removeprefix("$").strip()
        try:
            price = float(raw)
        except ValueError:
            raise ValueError(f"price must be a number or a percent, not {text!r}") from None
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be greater than zero")
        return price, None
    signed = str(text).strip().startswith(("+", "-"))
    size = abs(pct)
    if size == 0:
        raise ValueError("a percent price must not be 0 %")
    if direction == "below":
        if signed and pct > 0:
            raise ValueError("below takes a percent under the price now: use -10%")
        if size >= 100:
            raise ValueError("below takes a percent under 100 %")
    elif signed and pct < 0:
        raise ValueError("above takes a percent over the price now: use +15%")
    if current_price is None or not math.isfinite(current_price) or current_price <= 0:
        raise ValueError(f"{symbol} has no price right now: give an absolute price")
    factor = 1 - size / 100 if direction == "below" else 1 + size / 100
    return float(f"{float(current_price) * factor:.12g}"), float(current_price)


def condition_label(direction: str, price_usd: float | None, trail_pct: float | None) -> str:
    """``"under $3,800"`` / ``"over $5,000"`` / ``"10 % below peak"``."""
    if direction == "trail":
        return f"{pct_text(trail_pct or 0)} % below peak"
    word = "under" if direction == "below" else "over"
    return f"{word} {price_text(price_usd)}" if price_usd is not None else word


def default_name(
    kind: str,
    direction: str,
    symbol: str,
    price_usd: float | None,
    trail_pct: float | None,
) -> str:
    """The name a trigger gets without ``--name`` (``docs/triggers.md``, the names table)."""
    sym = symbol or "token"
    if kind == "sell":
        names = {"below": "Stop-loss", "above": "Take-profit", "trail": "Trailing stop"}
        return f"{names.get(direction, 'Sell')} {sym}"
    if direction == "trail":
        return f"Alert {sym} −{pct_text(trail_pct or 0)} % from peak"
    head = "Buy" if kind == "buy" else "Alert"
    word = "under" if direction == "below" else "over"
    if price_usd is None:
        return f"{head} {sym} {word}"
    return f"{head} {sym} {word} {price_text(price_usd)}"


def action_label(
    kind: str,
    token_symbol: str,
    quote_symbol: str,
    *,
    amount_usd: float | None = None,
    amount_pct: float | None = None,
    amount_human: str | None = None,
) -> str:
    """``"sell 50 % of ETH → USDC"`` / ``"buy $50 of ETH with USDC"`` / ``"notify"``."""
    sym = token_symbol or "token"
    quote = quote_symbol or "quote"
    if kind == "alert":
        return "notify"
    if kind == "buy":
        return f"buy {usd_text(amount_usd or 0)} of {sym} with {quote}"
    if amount_pct is not None:
        what = f"{pct_text(amount_pct)} % of {sym}"
    elif amount_human is not None:
        what = f"{amount_human} {sym}"
    else:
        what = f"{usd_text(amount_usd or 0)} of {sym}"
    return f"sell {what} → {quote}"


def fire_note(name: str, price: float | None) -> str:
    """The order note: ``"Stop-loss ETH · fired at $3,790"``."""
    return f"{name} · fired at {price_text(price)}" if price is not None else f"{name} · fired"


def filled_reason(
    kind: str,
    *,
    token_symbol: str,
    token_human: str,
    quote_symbol: str,
    quote_human: str,
    price: float | None,
) -> str:
    """``statusReason`` of a filled fire: ``"sold 0.05 ETH for 189.4 USDC at $3,788"``."""
    verb = "sold" if kind == "sell" else "bought"
    text = f"{verb} {token_human} {token_symbol or 'tokens'} for {quote_human} {quote_symbol}"
    return f"{text.rstrip()} at {price_text(price)}" if price is not None else text.rstrip()


def alerted_reason(price: float | None) -> str:
    return f"alerted at {price_text(price)}" if price is not None else "alerted"


#: How a fire's reason is stored: ``"<code>: <detail>"`` (as a DCA run's).
fire_reason = reason


def insufficient_reason(symbol: str, *, nothing: bool = False) -> str:
    """``statusReason`` of a fire skipped for balance: the trigger pauses at once.

    ``"paused: nothing to sell"`` when a sell finds none of the token at all,
    ``"paused: insufficient USDC"`` when what is there does not cover the size.
    """
    if nothing:
        return "paused: nothing to sell"
    return f"paused: insufficient {symbol or 'balance'}"


def failed_pause_reason(code: str | None) -> str:
    return f"paused after {BAD_STREAK_LIMIT} failed fires: {code or 'failed'}"


def expired_order_reason(kind: str) -> str:
    return f"the {kind} waited for approval and expired"


def rejected_order_reason(kind: str) -> str:
    return f"{kind} rejected by you"


def not_reached_reason(valid_until: float) -> str:
    """``"not reached by 2026-10-11"`` (the UTC date of ``validUntil``)."""
    return f"not reached by {str(iso(valid_until))[:10]}"


# ── the condition ──────────────────────────────────────────────────────────


def stop_price(peak: float | None, trail_pct: float | None) -> float | None:
    """A trail's line: ``peak × (1 − trail_pct / 100)``; ``None`` without a peak."""
    if peak is None or trail_pct is None:
        return None
    return float(peak) * (1 - float(trail_pct) / 100)


def line_price(
    direction: str, price_usd: float | None, trail_pct: float | None, peak: float | None
) -> float | None:
    """The price the trigger fires at: the threshold, or a trail's stop price."""
    if direction == "trail":
        return stop_price(peak, trail_pct)
    return finite(price_usd)


def evaluate(
    direction: str,
    price: float | None,
    price_usd: float | None,
    trail_pct: float | None,
    peak: float | None,
) -> bool | None:
    """Whether the condition holds at ``price``; ``None`` when the price is unknown.

    ``peak`` is the trail's highest price *including* this one (the caller
    raises it first). A trail with no peak yet does not hold.
    """
    if price is None or not math.isfinite(price):
        return None
    if direction == "below":
        return price_usd is not None and price <= float(price_usd)
    if direction == "above":
        return price_usd is not None and price >= float(price_usd)
    line = stop_price(peak, trail_pct)
    return line is not None and price <= line


def distance_pct(
    direction: str,
    price: float | None,
    price_usd: float | None,
    trail_pct: float | None,
    peak: float | None,
) -> float | None:
    """Signed % move from ``price`` the condition needs: −2.1 (must fall), +4.0 (must rise).

    ``0`` when the condition already holds; ``None`` when either side is unknown.
    """
    if price is None or price <= 0:
        return None
    line = line_price(direction, price_usd, trail_pct, peak)
    if line is None:
        return None
    if evaluate(direction, price, price_usd, trail_pct, peak):
        return 0.0
    return (line - float(price)) / float(price) * 100.0


# ── validation ─────────────────────────────────────────────────────────────


def _positive(value: Any) -> bool:
    number = finite(value)
    return number is not None and number > 0


def validate_terms(
    *,
    kind: str,
    direction: str,
    price: str | float | None,
    trail_pct: float | None,
    amount_usd: float | None,
    amount_pct: float | None,
    amount: str | None,
    valid_for_seconds: int | None,
) -> str | None:
    """The first thing wrong with a trigger's terms, or ``None``.

    ``price`` is what the caller gave (``"3800"``, ``"-10%"``, a number): its
    shape is checked here; resolving a percent needs the current price and
    happens in :func:`parse_price`.
    """
    if kind not in KINDS:
        return "kind must be sell, buy or alert"
    if direction not in DIRECTIONS:
        return "direction must be below, above or trail"
    if kind == "buy" and direction == "trail":
        return "a buy cannot trail: use below or above"
    if direction == "trail":
        if price is not None:
            return "price does not go with trail: give trailPct"
        if trail_pct is None:
            return "trailPct is required for trail"
        if finite(trail_pct) is None or not 0 < float(trail_pct) < 100:
            return "trailPct must be above 0 and under 100"
    else:
        if trail_pct is not None:
            return f"trailPct only goes with trail, not {direction}"
        if price is None or (isinstance(price, str) and not price.strip()):
            return f"price is required for {direction}"
        try:
            parse_price(price, direction, 1.0)
        except ValueError as exc:
            return str(exc)
    sizes = [s for s in (amount_usd, amount_pct, amount) if s is not None]
    if kind == "alert":
        if sizes:
            return "an alert takes no size"
    elif kind == "buy":
        if amount_pct is not None or amount is not None:
            return "a buy is sized with amountUsd only"
        if amount_usd is None:
            return "a buy needs amountUsd"
    elif not sizes:
        return "a sell needs one size: amountPct, amount or amountUsd"
    elif len(sizes) > 1:
        return "a sell takes one size: amountPct, amount or amountUsd"
    if amount_pct is not None and (finite(amount_pct) is None or not 0 < amount_pct <= 100):
        return "amountPct must be above 0 and at most 100"
    if amount_usd is not None and not _positive(amount_usd):
        return "amountUsd must be greater than zero"
    if amount is not None:
        try:
            value = Decimal(str(amount).strip())
        except InvalidOperation:
            return "amount must be a number"
        if not value.is_finite() or value <= 0:
            return "amount must be greater than zero"
    if valid_for_seconds is not None and int(valid_for_seconds) < MIN_VALID_FOR_S:
        return f"validForSeconds must be at least {MIN_VALID_FOR_S}"
    return None


# ── payload ────────────────────────────────────────────────────────────────


def estimated_usd(
    row: Mapping[str, Any],
    *,
    token_decimals: int,
    token_price: float | None,
    balance_raw: int | None,
) -> float | None:
    """What a fire would move at ``token_price``: a buy's spend, a sell's size in USD."""
    kind = str(row["kind"])
    if kind == "alert":
        return None
    if row.get("amount_usd") is not None:
        return finite(row["amount_usd"])
    if token_price is None:
        return None
    if row.get("amount_raw") is not None:
        human = float(to_human(int(row["amount_raw"]), token_decimals))
        return finite(human * float(token_price))
    if row.get("amount_pct") is not None and balance_raw is not None:
        human = float(to_human(int(balance_raw), token_decimals))
        return finite(human * float(row["amount_pct"]) / 100.0 * float(token_price))
    return None


def fire_payload(
    row: Mapping[str, Any],
    order_row: Mapping[str, Any] | None,
    *,
    chain: ChainSpec | None = None,
) -> dict[str, Any]:
    """One ``Fire`` of the payload from its row and (when it placed one) its order."""
    code, detail = split_reason(row.get("reason"))
    tx_hash = order_row.get("tx_hash") if order_row else None
    return {
        "n": int(row["n"]),
        "at": iso(float(row["at"])),
        "manual": bool(row.get("manual")),
        "status": str(row["status"]),
        "reasonCode": code,
        "reason": detail,
        "priceUsd": finite(row.get("price_usd")),
        "orderId": row.get("order_id"),
        "txHash": tx_hash,
        "explorerUrl": chain.tx_url(str(tx_hash)) if chain is not None and tx_hash else None,
    }


def result_payload(
    kind: str,
    order_row: Mapping[str, Any],
    *,
    chain: ChainSpec,
    token: Mapping[str, Any],
    quote: Mapping[str, Any],
    quote_price: float | None,
    gas_usd: float | None,
) -> dict[str, Any]:
    """The ``result`` of a trigger an order finished: what went in, what came out, at what."""
    token_dec = int(token.get("decimals", 18))
    quote_dec = int(quote.get("decimals", 18))
    spent = int(order_row.get("spent_in_raw") or order_row.get("amount_raw") or 0)
    received_text = order_row.get("received_out_raw")
    received = int(received_text) if received_text is not None else None
    token_raw = spent if kind == "sell" else (received or 0)
    token_human = float(to_human(token_raw, token_dec))
    value = finite(order_row.get("value_usd"))
    price = value / token_human if value is not None and token_human > 0 else None
    if kind == "sell":
        amount_in = amount_json(spent, token_dec, price)
        amount_out = amount_json(received, quote_dec, quote_price) if received is not None else None
    else:
        amount_in = amount_json(spent, quote_dec, quote_price)
        amount_out = amount_json(received, token_dec, price) if received is not None else None
    tx_hash = order_row.get("tx_hash")
    return {
        "orderId": order_row["order_id"],
        "txHash": tx_hash,
        "explorerUrl": chain.tx_url(str(tx_hash)) if tx_hash else None,
        "amountIn": amount_in,
        "amountOut": amount_out,
        "priceUsd": finite(price),
        "gasUsd": finite(gas_usd),
    }


def trigger_json(
    row: Mapping[str, Any],
    *,
    chain: ChainSpec,
    wallet: dict[str, Any],
    token: Mapping[str, Any],
    quote: Mapping[str, Any],
    token_price: float | None,
    quote_price: float | None,
    fires: list[dict[str, Any]],
    result: dict[str, Any] | None,
    balance_raw: int | None,
    approval_threshold_usd: float,
    daily_cap_usd: float,
    default_slippage_pct: float | None,
) -> dict[str, Any]:
    """The ``Trigger`` object. ``fires`` are ready ``Fire`` dicts, newest first.

    ``balance_raw`` is the wallet's token (sell) or quote (buy) balance now;
    ignored for an alert and once the trigger is over: the wallet now says
    nothing about what the trigger did, so a terminal card has no balance and
    its ``estimatedUsd`` is what its order actually moved (``result``).
    """
    kind = str(row["kind"])
    status = str(row["status"])
    live = status in LIVE_STATUSES
    if not live:
        balance_raw = None
    direction = str(row["direction"])
    trail = finite(row.get("trail_pct"))
    threshold = finite(row.get("price_usd"))
    peak = finite(row.get("peak_price_usd"))
    token_dec = int(token.get("decimals", 18))
    quote_dec = int(quote.get("decimals", 18))
    now_price = finite(token_price)
    if now_price is None:
        now_price = finite(row.get("last_price_usd"))
    estimate = estimated_usd(
        row, token_decimals=token_dec, token_price=now_price, balance_raw=balance_raw
    )
    if not live and result is not None:
        estimate = finite((result.get("amountIn") or {}).get("usd"))
    amount: dict[str, Any] | None = None
    amount_human: str | None = None
    if row.get("amount_raw") is not None:
        amount = amount_json(int(row["amount_raw"]), token_dec, now_price)
        amount_human = amount["human"]
    balance: dict[str, Any] | None = None
    if kind != "alert" and balance_raw is not None:
        if kind == "sell":
            balance = amount_json(int(balance_raw), token_dec, now_price)
        else:
            balance = amount_json(int(balance_raw), quote_dec, quote_price)
    slippage = row.get("slippage_pct")
    return {
        "id": row["trigger_id"],
        "name": row["name"],
        "kind": kind,
        "status": status,
        "statusReason": row.get("status_reason"),
        "chain": chain_json(chain),
        "wallet": wallet,
        "token": token_json(token, token_price),
        "quote": token_json(quote, quote_price),
        "condition": {
            "direction": direction,
            "priceUsd": threshold,
            "trailPct": trail,
            "fromPriceUsd": finite(row.get("from_price_usd")),
            "peakPriceUsd": peak if direction == "trail" else None,
            "stopPriceUsd": stop_price(peak, trail) if direction == "trail" else None,
            "confirmTicks": int(row.get("confirm_ticks") or CONFIRM_TICKS),
            "hits": int(row.get("hits") or 0),
            "label": condition_label(direction, threshold, trail),
        },
        "action": {
            "kind": kind,
            "amountUsd": finite(row.get("amount_usd")),
            "amountPct": finite(row.get("amount_pct")),
            "amount": amount,
            "estimatedUsd": estimate,
            "slippagePct": finite(slippage if slippage is not None else default_slippage_pct)
            if kind != "alert"
            else None,
            "needsApproval": estimate is not None and estimate > float(approval_threshold_usd),
            "approvalThresholdUsd": float(approval_threshold_usd),
            "dailyCapUsd": float(daily_cap_usd),
            "label": action_label(
                kind,
                str(token.get("symbol") or ""),
                str(quote.get("symbol") or ""),
                amount_usd=finite(row.get("amount_usd")),
                amount_pct=finite(row.get("amount_pct")),
                amount_human=amount_human,
            ),
        },
        "market": {
            "priceUsd": now_price,
            "armedPriceUsd": finite(row.get("armed_price_usd")),
            "distancePct": distance_pct(direction, now_price, threshold, trail, peak)
            if live
            else None,
            "checkedAt": iso(row.get("last_checked_at")),
            "balance": balance,
        },
        "fires": fires[:HISTORY_LIMIT],
        "result": result,
        "validUntil": iso(row.get("valid_until")),
        "initiator": row["initiator"],
        "sessionKey": row.get("session_key"),
        "createdAt": iso(float(row["created_at"])),
        "updatedAt": iso(float(row["updated_at"])),
        "approvedAt": iso(row.get("approved_at")),
        "armedAt": iso(row.get("armed_at")),
        "triggeredAt": iso(row.get("triggered_at")),
        "expiresAt": iso(row.get("expires_at")),
    }


def trigger_warnings(
    row: Mapping[str, Any],
    *,
    token_symbol: str,
    wallet_label: str | None,
    token_price: float | None,
    estimated: float | None,
    balance_raw: int | None,
    approval_threshold_usd: float,
) -> list[str]:
    """The one-line warnings a card shows (``docs/triggers.md``, *Warnings at creation*)."""
    status = str(row["status"])
    if status not in ("awaiting_approval", "armed", "paused"):
        return []
    out: list[str] = []
    kind = str(row["kind"])
    direction = str(row["direction"])
    sym = token_symbol or "the token"
    threshold = finite(row.get("price_usd"))
    # Once a check has counted, the card's own "fires after 1 more check" says it better.
    fresh = status != "paused" and int(row.get("hits") or 0) == 0
    if fresh and token_price is not None and threshold is not None:
        if direction == "below" and token_price <= threshold:
            out.append(
                f"{sym} is already at {price_text(token_price)}, under {price_text(threshold)}: "
                "this fires after the next two checks"
            )
        elif direction == "above" and token_price >= threshold:
            out.append(
                f"{sym} is already at {price_text(token_price)}, over {price_text(threshold)}: "
                "this fires after the next two checks"
            )
    if kind != "alert" and estimated is not None and estimated > float(approval_threshold_usd):
        out.append(
            f"a {kind} of ≈{usd_text(estimated)} is above the "
            f"{usd_text(approval_threshold_usd)} approval threshold and will wait for you "
            "when it fires"
        )
    if kind == "sell" and balance_raw is not None and int(balance_raw) == 0:
        out.append(f"{wallet_label or 'the wallet'} holds no {sym}")
    if token_price is None:
        out.append(f"price unknown: the trigger waits until {sym} has a price")
    return out


def trigger_payload(
    trigger: dict[str, Any],
    *,
    fetched_at: float,
    warnings: Iterable[str] = (),
    fire: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``kind = "trigger"``: one card; ``fire`` only in the answer to a fire-now."""
    payload = envelope(
        "trigger",
        fetched_at=fetched_at,
        warnings=warnings,
        request={"kind": "get", "params": {"triggerId": trigger["id"]}},
    )
    payload["trigger"] = trigger
    if fire is not None:
        payload["fire"] = fire
    cleaned: dict[str, Any] = clean(payload)
    return cleaned


def triggers_payload(
    triggers: list[dict[str, Any]],
    *,
    fetched_at: float,
    all: bool = False,
    wallet: str | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """``kind = "triggers"``: live ones first, then newest first, with totals."""
    ordered = sorted(triggers, key=lambda t: str(t.get("createdAt") or ""), reverse=True)
    ordered.sort(key=lambda t: t["status"] not in LIVE_STATUSES)
    params: dict[str, Any] = {"all": bool(all)}
    if wallet:
        params["wallet"] = wallet
    payload = envelope(
        "triggers",
        fetched_at=fetched_at,
        warnings=warnings,
        request={"kind": "list", "params": params},
    )
    payload["triggers"] = ordered
    payload["totals"] = {
        "count": len(ordered),
        "armed": sum(1 for t in ordered if t["status"] == "armed"),
        "awaiting": sum(1 for t in ordered if t["status"] == "awaiting_approval"),
        "triggered": sum(1 for t in ordered if t["status"] == "triggered"),
    }
    cleaned: dict[str, Any] = clean(payload)
    return cleaned


def human(raw: int, decimals: int) -> str:
    """A token amount for a reason line: at most six places, no trailing zeros."""
    return format_amount(int(raw), int(decimals), max_places=6)
