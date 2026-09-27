"""``agentos trade`` — quotes, swaps, sends, allowances, orders, history and PnL.

A thin client over the gateway's ``trading.*`` RPCs. Every order goes through
the engine's guardrails, and the *gateway* decides who is asking: a shell
spawned by an agent turn carries ``AGENTOS_AGENT_TOKEN`` (minted by the shell
tool, presented at the handshake by ``gateway_rpc``), and any connection opened
while an agent shell is running counts as the agent's (see
``agentos.gateway.agent_surface``). An agent-bound connection is the agent
whatever it declares: its swaps obey the per-order approval threshold and the
per-wallet daily cap, and its sends and revokes always park for the user's
approval. ``--as-agent`` (or ``AGENTOS_SESSION_KEY``/``AGENTOS_AGENT`` in the
environment) only lets a person opt *into* the agent rules; it cannot opt an
agent out of them.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import typer
from rich.table import Table

from agentos.cli.gateway_rpc import run_gateway_sync
from agentos.cli.output import emit_error, print_json
from agentos.cli.ui import ACCENT, ACCENT_HEADER, console, markup_escape
from agentos.cli.wallet_cmd import (
    NATIVE_ADDRESS,
    amount_text,
    chain_id_from_arg,
    chain_label,
    is_address,
    money,
    percent,
    short_address,
    token_symbol,
)

app = typer.Typer(
    help=(
        "Swap and send tokens, review and revoke allowances, decode transactions, "
        "check the network, and track orders, history and PnL."
    )
)

AGENT_ENV_MARKERS = ("AGENTOS_SESSION_KEY", "AGENTOS_AGENT")
PROVIDERS: dict[str, str] = {"aggregator": "AgentOS Aggregator", "uniswap": "Uniswap"}
# Statuses that still move on their own after ``trading.swap`` returns.
_PENDING_STATUSES = frozenset({"awaiting_approval", "approved", "submitted", "quoted"})


class TokenResolutionError(Exception):
    """A symbol did not resolve to exactly one verified token."""

    def __init__(self, message: str, *, code: str = "TOKEN_UNRESOLVED") -> None:
        super().__init__(message)
        self.code = code


def provider_id_from_arg(value: str) -> str:
    """Normalise ``aggregator``/``uniswap`` (case-insensitive; ``agg`` accepted)."""

    key = value.strip().lower()
    if key in ("agg", "404", "agentos"):
        key = "aggregator"
    if key not in PROVIDERS:
        choices = ", ".join(PROVIDERS)
        raise typer.BadParameter(f"unknown provider {value!r}; expected one of: {choices}")
    return key


def provider_label(value: Any) -> str:
    key = str(value or "").strip().lower()
    return PROVIDERS.get(key, key or "—")


def initiator_for(as_agent: bool, environ: dict[str, str] | None = None) -> str:
    """``agent`` when forced or when running inside an agent turn, else ``manual``."""

    env = os.environ if environ is None else environ
    if as_agent:
        return "agent"
    if any(env.get(name, "").strip() for name in AGENT_ENV_MARKERS):
        return "agent"
    return "manual"


async def resolve_token(client: Any, chain_id: int, value: str) -> str:
    """Turn ``ETH``, an address, or a symbol into a token address.

    Symbols must match exactly one *verified* token on the chain; anything
    else raises so the caller never swaps into a lookalike by accident. An
    address is passed through untouched — the engine resolves its metadata.
    """

    text = value.strip()
    if not text:
        raise TokenResolutionError("token cannot be empty")
    if text.upper() == "ETH":
        return NATIVE_ADDRESS
    if is_address(text):
        return text
    result = await client.call("trading.tokens.search", {"chainId": chain_id, "query": text})
    tokens = result.get("tokens", []) if isinstance(result, dict) else []
    matches = [
        t
        for t in tokens
        if isinstance(t, dict) and str(t.get("symbol") or "").upper() == text.upper()
    ]
    if not matches:
        raise TokenResolutionError(
            f"no token with symbol {text!r} on {chain_label(chain_id)}; pass its address",
            code="TOKEN_NOT_FOUND",
        )
    verified = [t for t in matches if t.get("verified")]
    if len(verified) == 1:
        return str(verified[0]["address"])
    if not verified:
        listed = ", ".join(str(t.get("address")) for t in matches[:5])
        raise TokenResolutionError(
            f"{text!r} matched only unverified tokens on {chain_label(chain_id)} ({listed}); "
            "pass the address explicitly if you mean one of them",
            code="TOKEN_UNVERIFIED",
        )
    listed = ", ".join(str(t.get("address")) for t in verified[:5])
    raise TokenResolutionError(
        f"{text!r} is ambiguous on {chain_label(chain_id)} ({listed}); pass the address",
        code="TOKEN_AMBIGUOUS",
    )


def _exit_token_error(exc: TokenResolutionError, *, json_output: bool) -> None:
    emit_error(str(exc), json_output=json_output, code=exc.code)
    raise typer.Exit(2)


def _bad_argument(message: str, *, json_output: bool) -> None:
    """An argument error an agent can parse: JSON on stderr, exit 2.

    ``typer.BadParameter`` prints a usage panel, which is right for a person
    and useless for ``--json`` callers reading stderr for ``{"error": …}``.
    """
    emit_error(message, json_output=json_output, code="INVALID_ARGUMENT")
    raise typer.Exit(2)


def _dict(value: Any) -> dict[str, Any]:
    """Narrow an RPC payload (or one of its fields) to a dict for rendering."""

    return value if isinstance(value, dict) else {}


def _order_rows(result: Any) -> list[dict[str, Any]]:
    orders = result.get("orders", []) if isinstance(result, dict) else []
    return [o for o in orders if isinstance(o, dict)]


def _order_legs(order: dict[str, Any]) -> str:
    """Legs in one phrase: "10 USDC → WETH", "10 USDC → 0x2222…2222", or the spender of a revoke."""
    kind = str(order.get("kind") or "swap")
    amount = f"{amount_text(order.get('amountIn'))} {token_symbol(order.get('tokenIn'))}"
    if kind == "send":
        return f"{amount} → {short_address(order.get('recipient'))}"
    if kind == "revoke":
        who = order.get("recipientLabel") or short_address(order.get("recipient"))
        return f"revoke {token_symbol(order.get('tokenIn'))} for {who}"
    return f"{amount} → {token_symbol(order.get('tokenOut'))}"


def _order_table(orders: list[dict[str, Any]], title: str = "Orders") -> Table:
    table = Table(title=title, show_header=True, header_style=ACCENT_HEADER)
    table.add_column("Order")
    table.add_column("Chain")
    table.add_column("Wallet")
    table.add_column("Legs")
    table.add_column("Value", justify="right")
    table.add_column("Status")
    table.add_column("Tx / reason")
    for order in orders:
        tail = order.get("txHash") or order.get("reason") or ""
        table.add_row(
            str(order.get("orderId") or ""),
            chain_label(order.get("chainId")),
            short_address(order.get("wallet")),
            markup_escape(_order_legs(order)),
            money(order.get("valueUsd")),
            str(order.get("status") or ""),
            markup_escape(str(tail)),
        )
    return table


def _print_order(order: dict[str, Any]) -> None:
    table = Table(title=f"Order {order.get('orderId') or ''}", show_header=False)
    table.add_column("Field", style=ACCENT)
    table.add_column("Value")
    kind = str(order.get("kind") or "swap")
    recipient = order.get("recipient")
    if recipient and order.get("recipientLabel"):
        recipient = f"{recipient} ({order['recipientLabel']})"
    for field, value in (
        ("kind", kind if kind != "swap" else None),
        ("batch", order.get("batchId")),
        ("status", order.get("status")),
        ("reason", order.get("reason")),
        ("chain", chain_label(order.get("chainId"))),
        ("wallet", order.get("wallet")),
        ("to" if kind == "send" else "spender", recipient),
        (
            "allowance" if kind == "revoke" else "in",
            f"{amount_text(order.get('amountIn'))} {token_symbol(order.get('tokenIn'))}",
        ),
        (
            "out",
            f"{amount_text(order.get('expectedOut'))} {token_symbol(order.get('tokenOut'))}"
            if kind == "swap"
            else None,
        ),
        ("min out", order.get("minOut")),
        ("value", money(order.get("valueUsd"))),
        ("price impact", percent(order.get("priceImpactPct"))),
        ("gas", money(order.get("gasUsd"))),
        ("initiator", order.get("initiator")),
        ("tx", order.get("txHash")),
        ("explorer", order.get("explorerUrl")),
        ("expires", order.get("expiresAt")),
        ("note", order.get("note")),
    ):
        if value in (None, "", "—"):
            continue
        table.add_row(field, markup_escape(str(value)))
    console.print(table)


# ── commands ────────────────────────────────────────────────────────────────


@app.command("status")
def trade_status(
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show trading readiness: API key, chains, limits, vault state."""

    async def _run(client):
        return await client.call("trading.status", {})

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    result = _dict(result)
    limits = _dict(result.get("limits"))
    table = Table(title="Trading", show_header=False)
    table.add_column("Field", style=ACCENT)
    table.add_column("Value")
    table.add_row("enabled", "yes" if result.get("enabled") else "no")
    table.add_row("provider", provider_label(result.get("provider")))
    table.add_row("Uniswap API key", "configured" if result.get("apiKeyConfigured") else "missing")
    table.add_row("vault", "unlocked" if result.get("unlocked") else "locked")
    table.add_row("unlock mode", str(result.get("unlockMode") or "—"))
    table.add_row("approval threshold", money(limits.get("approvalThresholdUsd")))
    table.add_row("daily cap / wallet", money(limits.get("dailyCapUsd")))
    table.add_row("approval TTL", f"{limits.get('approvalTtlSeconds', '—')} s")
    table.add_row("syncing", "yes" if result.get("syncing") else "no")
    if result.get("ledgerRepair"):
        table.add_row("ledger", f"[bold red]{markup_escape(str(result['ledgerRepair']))}[/]")
    console.print(table)
    providers = result.get("providers", [])
    if isinstance(providers, list) and providers:
        pt = Table(title="Swap providers", show_header=True, header_style=ACCENT_HEADER)
        pt.add_column("Provider")
        pt.add_column("Active")
        pt.add_column("API key")
        pt.add_column("Reachable")
        for entry in providers:
            if not isinstance(entry, dict):
                continue
            healthy = entry.get("healthy")
            reachable = "—" if healthy is None else ("yes" if healthy else "no")
            if entry.get("needsKey"):
                key_state = "configured" if entry.get("keyConfigured") else "missing"
            else:
                key_state = "not needed"
            pt.add_row(
                str(entry.get("label") or entry.get("id") or ""),
                "★" if entry.get("id") == result.get("provider") else "",
                key_state,
                reachable,
            )
        console.print(pt)
    chains = result.get("chains", [])
    if isinstance(chains, list) and chains:
        ct = Table(title="Chains", show_header=True, header_style=ACCENT_HEADER)
        ct.add_column("Chain")
        ct.add_column("Id")
        ct.add_column("RPC")
        ct.add_column("Healthy")
        for chain in chains:
            if not isinstance(chain, dict):
                continue
            healthy = chain.get("healthy")
            ct.add_row(
                str(chain.get("name") or chain.get("key") or ""),
                str(chain.get("chainId") or ""),
                markup_escape(str(chain.get("rpcUrl") or "")),
                "—" if healthy is None else ("yes" if healthy else "no"),
            )
        console.print(ct)


@app.command("probe")
def trade_probe(
    provider: str | None = typer.Option(
        None, "--provider", help="aggregator or uniswap (default: the configured provider)"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", help="Test this Uniswap key instead of the configured one"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Check that the swap provider is reachable (and, for Uniswap, that the key works)."""

    params: dict[str, Any] = {}
    if provider:
        params["provider"] = provider_id_from_arg(provider)
    if api_key:
        params["apiKey"] = api_key

    async def _run(client):
        return await client.call("trading.probe", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        if isinstance(result, dict) and not result.get("ok"):
            raise typer.Exit(1)
        return
    result = _dict(result)
    name = provider_label(result.get("provider") or params.get("provider"))
    if result.get("ok"):
        latency = result.get("latencyMs")
        suffix = f" ({latency} ms)" if latency is not None else ""
        console.print(f"[green]{name} OK[/]{suffix}")
        return
    console.print(f"[red]{name} failed:[/] {markup_escape(str(result.get('error')))}")
    raise typer.Exit(1)


@app.command("provider")
def trade_provider(
    name: str | None = typer.Argument(
        None, help="aggregator or uniswap (omit to show the current one)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show or switch the swap provider (the aggregator needs no key; Uniswap needs one)."""

    if name is None:

        async def _show(client):
            return await client.call("trading.status", {})

        result = _dict(run_gateway_sync(_show, json_output=json_output))
        current = result.get("provider")
        if json_output:
            print_json({"provider": current, "providers": result.get("providers", [])})
            return
        console.print(f"Swap provider: [{ACCENT}]{provider_label(current)}[/]")
        return

    provider = provider_id_from_arg(name)

    async def _set(client):
        return await client.call("config.set", {"path": "trading.provider", "value": provider})

    result = _dict(run_gateway_sync(_set, json_output=json_output))
    if json_output:
        print_json({"provider": provider, **result})
        return
    console.print(f"Swap provider is now [{ACCENT}]{provider_label(provider)}[/].")
    if provider == "uniswap":
        console.print(
            "Uniswap needs an API key in `trading.uniswap_api_key` (or UNISWAP_API_KEY). "
            "Run `agentos trade probe --provider uniswap` to check it from here."
        )
    elif result.get("restartRequired"):
        console.print("Restart the gateway to apply.")


@app.command("tokens")
def trade_tokens(
    query: str = typer.Argument(..., help="Symbol, name, or address"),
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Search tokens on a chain (verified Robinhood Stock Tokens are flagged)."""

    chain_id = chain_id_from_arg(chain)

    async def _run(client):
        return await client.call("trading.tokens.search", {"chainId": chain_id, "query": query})

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    rows = result.get("tokens", []) if isinstance(result, dict) else []
    table = Table(
        title=f"Tokens on {chain_label(chain_id)}", show_header=True, header_style=ACCENT_HEADER
    )
    table.add_column("Symbol")
    table.add_column("Name")
    table.add_column("Address")
    table.add_column("Price", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("Verified")
    for row in rows:
        if not isinstance(row, dict):
            continue
        table.add_row(
            markup_escape(str(row.get("symbol") or "")),
            markup_escape(str(row.get("name") or "")),
            str(row.get("address") or ""),
            money(row.get("priceUsd")),
            money(row.get("liquidityUsd")),
            "✓" if row.get("verified") else "",
        )
    console.print(table)


def _set_hidden(chain: str, address: str, hidden: bool, json_output: bool) -> None:
    chain_id = chain_id_from_arg(chain)

    async def _run(client):
        return await client.call(
            "trading.tokens.hide", {"chainId": chain_id, "address": address, "hidden": hidden}
        )

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    token = _dict(_dict(result).get("token"))
    symbol = markup_escape(str(token.get("symbol") or token.get("address") or address))
    verb = "Hidden" if token.get("hidden") else "Shown"
    console.print(f"{verb}: {symbol} on {chain_label(chain_id)}")


@app.command("hide")
def trade_hide(
    address: str = typer.Argument(..., help="Token address"),
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Hide a token from balances, portfolio and history (the ledger keeps it)."""

    _set_hidden(chain, address, True, json_output)


@app.command("unhide")
def trade_unhide(
    address: str = typer.Argument(..., help="Token address"),
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show a token again; the engine will not auto-hide it after this."""

    _set_hidden(chain, address, False, json_output)


@app.command("quote")
def trade_quote(
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    token_in: str = typer.Option(..., "--in", help="Token to sell: symbol, address, or ETH"),
    token_out: str = typer.Option(..., "--out", help="Token to buy: symbol, address, or ETH"),
    amount: str | None = typer.Option(None, "--amount", help="Amount of --in to sell, human units"),
    usd: float | None = typer.Option(
        None, "--usd", help="Sell this many US dollars' worth of --in"
    ),
    wallet: str | None = typer.Option(None, "--wallet", help="Wallet address (default primary)"),
    slippage: float | None = typer.Option(None, "--slippage", help="Slippage %, default auto"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Get a swap quote without executing anything."""

    if (amount is None) == (usd is None):
        _bad_argument("Use exactly one of --amount or --usd", json_output=json_output)
    if usd is not None and usd <= 0:
        _bad_argument("--usd must be above 0", json_output=json_output)
    chain_id = chain_id_from_arg(chain)
    # The gateway decides who is asking from the connection itself; this is
    # the fallback declaration so a quote inside an agent turn carries the
    # agent's guard verdict (threshold, cap) instead of a manual "allow".
    initiator = initiator_for(False)

    async def _run(client):
        params: dict[str, Any] = {
            "chainId": chain_id,
            "tokenIn": await resolve_token(client, chain_id, token_in),
            "tokenOut": await resolve_token(client, chain_id, token_out),
            "initiator": initiator,
        }
        if amount is not None:
            params["amountIn"] = amount
        else:
            params["amountUsd"] = usd
        if wallet:
            params["wallet"] = wallet
        if slippage is not None:
            params["slippagePct"] = slippage
        return await client.call("trading.quote", params)

    try:
        result = run_gateway_sync(_run, json_output=json_output)
    except TokenResolutionError as exc:
        _exit_token_error(exc, json_output=json_output)
        return
    if json_output:
        print_json(result)
        return
    result = _dict(result)
    guard = _dict(result.get("guard"))
    table = Table(title="Quote", show_header=False)
    table.add_column("Field", style=ACCENT)
    table.add_column("Value")
    table.add_row(
        "sell", f"{amount_text(result.get('amountIn'))} {token_symbol(result.get('tokenIn'))}"
    )
    table.add_row(
        "receive", f"{amount_text(result.get('amountOut'))} {token_symbol(result.get('tokenOut'))}"
    )
    table.add_row("minimum", amount_text(result.get("minOut")))
    table.add_row("rate", amount_text(result.get("rate")))
    table.add_row("value", money(result.get("valueUsd")))
    table.add_row("price impact", percent(result.get("priceImpactPct")))
    table.add_row("gas", money(result.get("gasUsd")))
    table.add_row("slippage", percent(result.get("slippagePct")))
    table.add_row("routing", str(result.get("routing") or "—"))
    table.add_row("expires", str(result.get("expiresAt") or "—"))
    if guard:
        table.add_row(
            "guardrail",
            f"{guard.get('decision')} (spent today {money(guard.get('spentTodayUsd'))} "
            f"of {money(guard.get('dailyCapUsd'))}; threshold {money(guard.get('thresholdUsd'))})",
        )
    console.print(table)


@app.command("swap")
def trade_swap(
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    token_in: str = typer.Option(..., "--in", help="Token to sell: symbol, address, or ETH"),
    token_out: str = typer.Option(..., "--out", help="Token to buy: symbol, address, or ETH"),
    amount: str | None = typer.Option(None, "--amount", help="Amount of --in, human units"),
    pct: float | None = typer.Option(
        None, "--pct", help="Percent of the --in balance (above 0, up to 100; fractions allowed)"
    ),
    usd: float | None = typer.Option(
        None, "--usd", help="Sell this many US dollars' worth of --in"
    ),
    wallets: list[str] | None = typer.Option(
        None, "--wallet", help="Wallet address (repeatable; default primary)"
    ),
    all_wallets: bool = typer.Option(False, "--all-wallets", help="Swap from every wallet"),
    slippage: float | None = typer.Option(None, "--slippage", help="Slippage %, default auto"),
    note: str | None = typer.Option(None, "--note", help="Why this swap (kept in history)"),
    client_id: str | None = typer.Option(
        None,
        "--client-id",
        help=(
            "Idempotency key; re-running with the same id returns the same order "
            "instead of trading twice"
        ),
    ),
    wait: bool = typer.Option(False, "--wait", help="Block until each order settles"),
    wait_seconds: int = typer.Option(
        300, "--wait-seconds", help="How long --wait blocks per order", min=1, max=900
    ),
    as_agent: bool = typer.Option(
        False, "--as-agent", help="Apply the agent guardrails (threshold, daily cap)"
    ),
    expected_out_raw: str | None = typer.Option(
        None,
        "--expected-out-raw",
        hidden=True,
        help="The quote's amountOutRaw you confirmed; refused if the price moved past it",
    ),
    min_out_raw: str | None = typer.Option(
        None, "--min-out-raw", hidden=True, help="The quote's minOutRaw you confirmed"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Swap tokens from one, several, or all wallets."""

    for name, raw in (("--expected-out-raw", expected_out_raw), ("--min-out-raw", min_out_raw)):
        if raw is not None and not raw.strip().isdigit():
            _bad_argument(f"{name} must be a whole number in base units", json_output=json_output)
    if sum(v is not None for v in (amount, pct, usd)) != 1:
        _bad_argument("Use exactly one of --amount, --pct or --usd", json_output=json_output)
    if pct is not None and not 0 < pct <= 100:
        _bad_argument("--pct must be above 0 and at most 100", json_output=json_output)
    if usd is not None and usd <= 0:
        _bad_argument("--usd must be above 0", json_output=json_output)
    if all_wallets and wallets:
        _bad_argument("Use either --wallet or --all-wallets, not both", json_output=json_output)
    chain_id = chain_id_from_arg(chain)
    initiator = initiator_for(as_agent)
    session_key = os.environ.get("AGENTOS_SESSION_KEY", "").strip()

    async def _run(client):
        params: dict[str, Any] = {
            "chainId": chain_id,
            "tokenIn": await resolve_token(client, chain_id, token_in),
            "tokenOut": await resolve_token(client, chain_id, token_out),
            "initiator": initiator,
        }
        if amount is not None:
            params["amountIn"] = amount
        elif pct is not None:
            params["amountPct"] = pct
        else:
            params["amountUsd"] = usd
        if all_wallets:
            params["wallets"] = "all"
        elif wallets:
            params["wallets"] = list(wallets)
        if slippage is not None:
            params["slippagePct"] = slippage
        if note:
            params["note"] = note
        if client_id:
            params["clientOrderId"] = client_id.strip()
        if expected_out_raw is not None:
            params["expectedOutRaw"] = expected_out_raw.strip()
        if min_out_raw is not None:
            params["minOutRaw"] = min_out_raw.strip()
        if session_key:
            params["sessionKey"] = session_key
        result = await client.call("trading.swap", params)
        if not wait:
            return result
        settled: list[dict[str, Any]] = []
        for order in _order_rows(result):
            order_id = order.get("orderId")
            if order_id and order.get("status") in _PENDING_STATUSES:
                waited = await client.call(
                    "trading.orders.wait", {"orderId": order_id, "timeoutSeconds": wait_seconds}
                )
                waited_order = waited.get("order") if isinstance(waited, dict) else None
                settled.append(waited_order if isinstance(waited_order, dict) else order)
            else:
                settled.append(order)
        return {"orders": settled}

    try:
        result = run_gateway_sync(_run, json_output=json_output)
    except TokenResolutionError as exc:
        _exit_token_error(exc, json_output=json_output)
        return
    if json_output:
        print_json(result)
        return
    orders = _order_rows(result)
    console.print(_order_table(orders, title=f"Swap ({initiator})"))
    for order in orders:
        if order.get("status") == "awaiting_approval":
            console.print(
                f"Order [{ACCENT}]{order.get('orderId')}[/] is waiting for approval in the app "
                f"(or: agentos trade approve {order.get('orderId')})."
            )


def _parse_recipient(text: str) -> tuple[str, str | None]:
    """``0xabc`` or ``0xabc=10`` → (address, amount or None)."""
    raw = text.strip()
    if "=" in raw:
        address, amount = raw.split("=", 1)
    elif "," in raw:
        address, amount = raw.split(",", 1)
    elif len(raw.split()) == 2:
        address, amount = raw.split()
    else:
        address, amount = raw, ""
    address = address.strip()
    amount = amount.strip()
    if not is_address(address):
        raise ValueError(f"not an address: {address!r}")
    if amount and not amount.replace(".", "", 1).isdigit():
        raise ValueError(f"not an amount: {amount!r} for {address}")
    return address, amount or None


def _recipients_from(
    to: list[str] | None,
    path: str | None,
    amount: str | None,
    usd: float | None,
    *,
    json_output: bool,
) -> list[dict[str, Any]]:
    """Recipients from ``--to`` and/or a file, each sized by its own amount or the shared one."""
    entries: list[str] = list(to or [])
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    text = line.split("#", 1)[0].strip()
                    if text:
                        entries.append(text)
        except OSError as exc:
            _bad_argument(f"cannot read {path}: {exc}", json_output=json_output)
    if not entries:
        _bad_argument("Give at least one --to, or --file", json_output=json_output)
    recipients: list[dict[str, Any]] = []
    for entry in entries:
        try:
            address, own = _parse_recipient(entry)
        except ValueError as exc:
            _bad_argument(str(exc), json_output=json_output)
            return []
        item: dict[str, Any] = {"to": address}
        if own is not None:
            if usd is not None:
                _bad_argument(
                    f"{address} carries its own amount; --usd cannot also size it",
                    json_output=json_output,
                )
            item["amount"] = own
        elif amount is not None:
            item["amount"] = amount
        elif usd is not None:
            item["amountUsd"] = usd
        else:
            _bad_argument(
                f"{address} has no amount: add =<amount> to it, or pass --amount / --usd",
                json_output=json_output,
            )
        recipients.append(item)
    return recipients


@app.command("send")
def trade_send(
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    token: str = typer.Option(..., "--token", help="Token to send: symbol, address, or ETH"),
    to: list[str] | None = typer.Option(
        None,
        "--to",
        help="Recipient address, optionally with its own amount as ADDR=AMOUNT (repeatable)",
    ),
    file: str | None = typer.Option(
        None, "--file", help="Recipients file: one 'ADDR' or 'ADDR,AMOUNT' per line; # comments"
    ),
    amount: str | None = typer.Option(
        None, "--amount", help="Amount for every recipient without its own, human units"
    ),
    usd: float | None = typer.Option(
        None, "--usd", help="US dollars' worth of --token for every recipient without its own"
    ),
    wallet: str | None = typer.Option(None, "--wallet", help="Wallet address (default primary)"),
    note: str | None = typer.Option(None, "--note", help="Why (kept in history)"),
    client_id: str | None = typer.Option(
        None,
        "--client-id",
        help=(
            "Idempotency key; re-running with the same id returns the same order "
            "instead of trading twice"
        ),
    ),
    wait: bool = typer.Option(False, "--wait", help="Block until every leg settles"),
    wait_seconds: int = typer.Option(
        300, "--wait-seconds", help="How long --wait blocks per leg", min=1, max=900
    ),
    as_agent: bool = typer.Option(
        False, "--as-agent", help="Apply the agent rules (every send waits for approval)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Send a token to one or many addresses. Several --to make one multisend, decided once."""

    if amount is not None and usd is not None:
        _bad_argument("Use one of --amount or --usd, not both", json_output=json_output)
    if usd is not None and usd <= 0:
        _bad_argument("--usd must be above 0", json_output=json_output)
    chain_id = chain_id_from_arg(chain)
    recipients = _recipients_from(to, file, amount, usd, json_output=json_output)
    initiator = initiator_for(as_agent)
    session_key = os.environ.get("AGENTOS_SESSION_KEY", "").strip()

    async def _run(client):
        params: dict[str, Any] = {
            "chainId": chain_id,
            "token": await resolve_token(client, chain_id, token),
            "recipients": recipients,
            "initiator": initiator,
        }
        if wallet:
            params["wallet"] = wallet
        if note:
            params["note"] = note
        if client_id:
            params["clientOrderId"] = client_id.strip()
        if session_key:
            params["sessionKey"] = session_key
        result = await client.call("trading.send", params)
        if not wait:
            return result
        settled: list[dict[str, Any]] = []
        for order in _order_rows(result):
            order_id = order.get("orderId")
            if order_id and order.get("status") in _PENDING_STATUSES:
                waited = await client.call(
                    "trading.orders.wait", {"orderId": order_id, "timeoutSeconds": wait_seconds}
                )
                waited_order = waited.get("order") if isinstance(waited, dict) else None
                settled.append(waited_order if isinstance(waited_order, dict) else order)
            else:
                settled.append(order)
        return {"orders": settled, "batchId": result.get("batchId")}

    try:
        result = run_gateway_sync(_run, json_output=json_output)
    except TokenResolutionError as exc:
        _exit_token_error(exc, json_output=json_output)
        return
    if json_output:
        print_json(result)
        return
    orders = _order_rows(result)
    title = f"Send ({initiator})" if len(orders) == 1 else f"Multisend ({initiator})"
    console.print(_order_table(orders, title=title))
    waiting = [o for o in orders if o.get("status") == "awaiting_approval"]
    if waiting:
        first = waiting[0]
        console.print(
            f"Waiting for approval in the app (or: agentos trade approve {first.get('orderId')}"
            + (" — one approval covers the whole batch)." if len(waiting) > 1 else ").")
        )


@app.command("allowances")
def trade_allowances(
    chain: str | None = typer.Option(None, "--chain", help="base or robinhood (default: both)"),
    wallet: str | None = typer.Option(None, "--wallet", help="Wallet address (default primary)"),
    full: bool = typer.Option(False, "--full", help="Rescan the chain from the wallet's start"),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Keep polling until the engine's log scan has caught up with the chain",
    ),
    wait_seconds: int = typer.Option(
        600, "--wait-seconds", help="How long --wait polls at most", min=1, max=3600
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """What this wallet has approved others to spend, with live amounts and exposure."""

    params: dict[str, Any] = {}
    if chain:
        params["chainId"] = chain_id_from_arg(chain)
    if wallet:
        params["wallet"] = wallet
    if full:
        params["full"] = True

    async def _run(client):
        # The engine scans in the background and answers at once with what it
        # has; short polls keep every RPC well inside its timeout, however
        # long a first pass over a 0.1 s chain takes.
        result = await client.call("trading.allowances.list", params)
        if not wait:
            return result
        deadline = time.monotonic() + wait_seconds
        again = {k: v for k, v in params.items() if k != "full"}
        while isinstance(result, dict) and result.get("scanning") and time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            result = await client.call("trading.allowances.list", again)
        return result

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    result = _dict(result)
    rows = [a for a in result.get("allowances", []) if isinstance(a, dict)]
    table = Table(
        title=f"Allowances for {short_address(result.get('wallet'))}",
        show_header=True,
        header_style=ACCENT_HEADER,
    )
    table.add_column("Chain")
    table.add_column("Token")
    table.add_column("Spender")
    table.add_column("Allowance", justify="right")
    table.add_column("Held", justify="right")
    table.add_column("At stake", justify="right")
    table.add_column("Granted in")
    for row in rows:
        spender = row.get("spenderLabel") or short_address(row.get("spender"))
        allowance = row.get("allowance")
        if row.get("readFailed"):
            allowance = "?"
        table.add_row(
            chain_label(row.get("chainId")),
            markup_escape(token_symbol(row.get("token"))),
            markup_escape(str(spender)),
            f"[red]{allowance}[/red]" if row.get("unlimited") else amount_text(allowance),
            amount_text(row.get("balance")),
            money(row.get("exposureUsd")),
            short_address(row.get("lastTxHash")) if row.get("lastTxHash") else "",
        )
    console.print(table)
    if not rows:
        console.print("No live allowances.")
    if result.get("scanning"):
        console.print(
            "[dim]The engine is still scanning older blocks; run again in a moment "
            "for the complete list.[/dim]"
        )
    unlimited = int(result.get("unlimitedCount") or 0)
    if unlimited:
        console.print(
            f"[yellow]{unlimited} unlimited allowance(s).[/yellow] Revoke with: "
            "agentos trade revoke --chain <chain> --token <addr> --spender <addr>"
        )


@app.command("revoke")
def trade_revoke(
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    token: str = typer.Option(..., "--token", help="Token: symbol or address"),
    spender: str = typer.Option(..., "--spender", help="Spender address to cut off"),
    wallet: str | None = typer.Option(None, "--wallet", help="Wallet address (default primary)"),
    note: str | None = typer.Option(None, "--note", help="Why (kept in history)"),
    wait: bool = typer.Option(False, "--wait", help="Block until the revoke settles"),
    wait_seconds: int = typer.Option(
        300, "--wait-seconds", help="How long --wait blocks", min=1, max=900
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Set an ERC-20 allowance to zero. From an agent turn this queues for the user's approval."""

    if not is_address(spender):
        _bad_argument(f"--spender must be an address, got {spender!r}", json_output=json_output)
    chain_id = chain_id_from_arg(chain)
    session_key = os.environ.get("AGENTOS_SESSION_KEY", "").strip()

    async def _run(client):
        params: dict[str, Any] = {
            "chainId": chain_id,
            "token": await resolve_token(client, chain_id, token),
            "spender": spender,
            "initiator": initiator_for(False),
        }
        if wallet:
            params["wallet"] = wallet
        if note:
            params["note"] = note
        if session_key:
            params["sessionKey"] = session_key
        result = await client.call("trading.allowances.revoke", params)
        order = result.get("order") if isinstance(result, dict) else None
        if wait and isinstance(order, dict) and order.get("status") in _PENDING_STATUSES:
            waited = await client.call(
                "trading.orders.wait",
                {"orderId": order.get("orderId"), "timeoutSeconds": wait_seconds},
            )
            return waited if isinstance(waited, dict) else result
        return result

    try:
        result = run_gateway_sync(_run, json_output=json_output)
    except TokenResolutionError as exc:
        _exit_token_error(exc, json_output=json_output)
        return
    if json_output:
        print_json(result)
        return
    order = result.get("order") if isinstance(result, dict) else None
    _print_order(order if isinstance(order, dict) else {})
    if isinstance(order, dict) and order.get("status") == "awaiting_approval":
        console.print(
            f"Waiting for approval in the app (or: agentos trade approve {order.get('orderId')})."
        )


@app.command("decode")
def trade_decode(
    tx_hash: str | None = typer.Argument(None, help="Transaction hash to explain"),
    chain: str = typer.Option(..., "--chain", help="base or robinhood"),
    data: str | None = typer.Option(None, "--data", help="Raw calldata instead of a hash"),
    to: str | None = typer.Option(None, "--to", help="Target contract for --data (optional)"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Explain a transaction (what it called, what moved) or raw calldata."""

    if (tx_hash is None) == (data is None):
        _bad_argument("Give a transaction hash, or --data", json_output=json_output)
    chain_id = chain_id_from_arg(chain)
    params: dict[str, Any] = {"chainId": chain_id}
    if tx_hash:
        params["txHash"] = tx_hash
    else:
        params["data"] = data
        if to:
            params["to"] = to

    async def _run(client):
        return await client.call("trading.decode", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    result = _dict(result)
    call = _dict(result.get("call"))
    console.print(f"[{ACCENT}]{markup_escape(str(result.get('description') or ''))}[/]")
    table = Table(show_header=False)
    table.add_column("Field", style=ACCENT)
    table.add_column("Value")
    tx = _dict(result.get("tx"))
    decoded = _dict(result.get("decoded"))
    to_label = result.get("toLabel") or token_symbol(result.get("toToken")) or ""
    target = str(result.get("to") or "")
    for field, value in (
        ("function", call.get("function") or f"{call.get('selector')} (unknown)"),
        ("to", f"{target} ({to_label})" if to_label else target),
        ("status", tx.get("status")),
        ("from", tx.get("from")),
        ("block", tx.get("blockNumber")),
        ("value", f"{tx.get('valueWei')} wei" if tx.get("valueWei") not in (None, "0") else None),
        ("gas used", tx.get("gasUsed")),
        (
            decoded.get("function") or "",
            (
                f"{amount_text(decoded.get('amount'))} {token_symbol(decoded.get('token'))} "
                f"→ {decoded.get('counterparty')}"
                + (f" ({decoded['counterpartyLabel']})" if decoded.get("counterpartyLabel") else "")
            )
            if decoded
            else None,
        ),
        ("explorer", result.get("explorerUrl")),
    ):
        if value in (None, "", "—"):
            continue
        table.add_row(field, markup_escape(str(value)))
    console.print(table)
    transfers = [t for t in result.get("transfers", []) if isinstance(t, dict)]
    if transfers:
        moved = Table(title="Transfers", show_header=True, header_style=ACCENT_HEADER)
        moved.add_column("Token")
        moved.add_column("Amount", justify="right")
        moved.add_column("From")
        moved.add_column("To")
        for t in transfers:
            moved.add_row(
                markup_escape(token_symbol(t.get("token"))),
                amount_text(t.get("amount")),
                short_address(t.get("from")),
                short_address(t.get("to")),
            )
        console.print(moved)
    approvals = [a for a in result.get("approvals", []) if isinstance(a, dict)]
    if approvals:
        granted = Table(title="Approvals", show_header=True, header_style=ACCENT_HEADER)
        granted.add_column("Token")
        granted.add_column("Owner")
        granted.add_column("Spender")
        granted.add_column("Amount", justify="right")
        for a in approvals:
            granted.add_row(
                markup_escape(token_symbol(a.get("token"))),
                short_address(a.get("owner")),
                markup_escape(str(a.get("spenderLabel") or short_address(a.get("spender")))),
                f"[red]{a.get('amount')}[/red]"
                if a.get("unlimited")
                else amount_text(a.get("amount")),
            )
        console.print(granted)
    wallets = result.get("wallets") or []
    if wallets:
        console.print(f"Involves your wallet(s): {', '.join(short_address(w) for w in wallets)}")


@app.command("network")
def trade_network(
    fresh: bool = typer.Option(False, "--fresh", help="Skip the engine's short cache"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Head block, block age, gas and RPC latency for every chain."""

    params: dict[str, Any] = {"fresh": True} if fresh else {}

    async def _run(client):
        return await client.call("trading.network", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    rows = [r for r in _dict(result).get("chains", []) if isinstance(r, dict)]
    table = Table(title="Network", show_header=True, header_style=ACCENT_HEADER)
    table.add_column("Chain")
    table.add_column("RPC")
    table.add_column("Head", justify="right")
    table.add_column("Age", justify="right")
    table.add_column("Base fee", justify="right")
    table.add_column("Tip", justify="right")
    table.add_column("Latency", justify="right")
    table.add_column("Healthy")
    for row in rows:
        age = row.get("blockAgeS")
        base_fee = row.get("baseFeeGwei")
        tip = row.get("priorityFeeGwei")
        latency = row.get("latencyMs")
        healthy = row.get("healthy")
        table.add_row(
            str(row.get("name") or row.get("key") or ""),
            markup_escape(str(row.get("rpcUrl") or "")),
            str(row.get("blockNumber") or "—"),
            f"{age} s" if age is not None else "—",
            f"{base_fee:.4f} gwei" if isinstance(base_fee, int | float) else "—",
            f"{tip:.4f} gwei" if isinstance(tip, int | float) else "—",
            f"{latency} ms" if latency is not None else "—",
            "[green]yes[/green]"
            if healthy
            else f"[red]no[/red] {markup_escape(str(row.get('error') or ''))}".strip(),
        )
    console.print(table)


@app.command("orders")
def trade_orders(
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    wallet: str | None = typer.Option(None, "--wallet", help="Filter by wallet address"),
    kind: str | None = typer.Option(None, "--kind", help="swap, send or revoke"),
    limit: int = typer.Option(50, "--limit", help="Max rows", min=1, max=500),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """List recent orders (pending approvals first)."""

    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    if wallet:
        params["wallet"] = wallet
    if kind:
        if kind not in ("swap", "send", "revoke"):
            _bad_argument("--kind must be swap, send or revoke", json_output=json_output)
        params["kind"] = kind

    async def _run(client):
        return await client.call("trading.orders.list", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    orders = _order_rows(result)
    console.print(_order_table(orders))
    pending = result.get("pendingApprovals") if isinstance(result, dict) else None
    if pending:
        console.print(f"{pending} order(s) awaiting approval.")


@app.command("order")
def trade_order(
    order_id: str = typer.Argument(..., help="Order id"),
    wait: bool = typer.Option(False, "--wait", help="Block until the order settles"),
    wait_seconds: int = typer.Option(
        300, "--wait-seconds", help="How long --wait blocks", min=1, max=900
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show one order, optionally waiting for it to settle."""

    async def _run(client):
        if wait:
            return await client.call(
                "trading.orders.wait", {"orderId": order_id, "timeoutSeconds": wait_seconds}
            )
        return await client.call("trading.orders.get", {"orderId": order_id})

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    order = result.get("order") if isinstance(result, dict) else None
    _print_order(order if isinstance(order, dict) else {})


@app.command("approve")
def trade_approve(
    order_id: str = typer.Argument(..., help="Order id awaiting approval"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Approve a parked agent order.

    Agent swaps park above the approval threshold or the price-impact ceiling,
    or when the engine cannot price them; agent sends and revokes always park.
    """

    async def _run(client):
        return await client.call("trading.orders.approve", {"orderId": order_id})

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    order = result.get("order") if isinstance(result, dict) else None
    _print_order(order if isinstance(order, dict) else {})


@app.command("reject")
def trade_reject(
    order_id: str = typer.Argument(..., help="Order id awaiting approval"),
    reason: str | None = typer.Option(None, "--reason", help="Why (returned to the agent)"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Reject a queued order."""

    params: dict[str, Any] = {"orderId": order_id}
    if reason:
        params["reason"] = reason

    async def _run(client):
        return await client.call("trading.orders.reject", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    order = result.get("order") if isinstance(result, dict) else None
    _print_order(order if isinstance(order, dict) else {})


@app.command("history")
def trade_history(
    wallet: str | None = typer.Option(None, "--wallet", help="Filter by wallet address"),
    chain: str | None = typer.Option(None, "--chain", help="base or robinhood"),
    kind: str | None = typer.Option(None, "--kind", help="swap, deposit, withdraw, gas, approval"),
    limit: int = typer.Option(100, "--limit", help="Max rows", min=1, max=1000),
    hidden: bool = typer.Option(False, "--hidden", help="Include entries of hidden (junk) tokens"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show the ledger: swaps, deposits, withdrawals, approvals."""

    params: dict[str, Any] = {"limit": limit}
    if wallet:
        params["wallet"] = wallet
    if chain:
        params["chainId"] = chain_id_from_arg(chain)
    if kind:
        params["kind"] = kind
    if hidden:
        params["includeHidden"] = True

    async def _run(client):
        return await client.call("trading.history", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    rows = result.get("entries", []) if isinstance(result, dict) else []
    table = Table(title="History", show_header=True, header_style=ACCENT_HEADER)
    table.add_column("When")
    table.add_column("Kind")
    table.add_column("Chain")
    table.add_column("Wallet")
    table.add_column("In")
    table.add_column("Out")
    table.add_column("Value", justify="right")
    table.add_column("Gas", justify="right")
    table.add_column("By")
    table.add_column("Tx")
    for row in rows:
        if not isinstance(row, dict):
            continue
        token_in = row.get("tokenIn")
        token_out = row.get("tokenOut")
        table.add_row(
            str(row.get("ts") or ""),
            str(row.get("kind") or ""),
            chain_label(row.get("chainId")),
            short_address(row.get("wallet")),
            markup_escape(
                f"{amount_text(row.get('amountIn'))} {token_symbol(token_in)}" if token_in else "—"
            ),
            markup_escape(
                f"{amount_text(row.get('amountOut'))} {token_symbol(token_out)}"
                if token_out
                else "—"
            ),
            money(row.get("valueUsd")),
            money(row.get("gasUsd")),
            str(row.get("initiator") or ""),
            short_address(row.get("txHash")) if row.get("txHash") else "",
        )
    console.print(table)


@app.command("portfolio")
def trade_portfolio(
    wallet: str | None = typer.Option(None, "--wallet", help="One wallet (default: all)"),
    hidden: bool = typer.Option(False, "--hidden", help="Include hidden (junk) tokens"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Holdings with cost basis, realized and unrealized PnL."""

    params: dict[str, Any] = {}
    if wallet:
        params["wallet"] = wallet
    if hidden:
        params["includeHidden"] = True

    async def _run(client):
        return await client.call("trading.portfolio", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    result = _dict(result)
    totals = _dict(result.get("totals"))
    summary = Table(title="Portfolio", show_header=False)
    summary.add_column("Field", style=ACCENT)
    summary.add_column("Value", justify="right")
    summary.add_row("value", money(totals.get("valueUsd")))
    summary.add_row("cost", money(totals.get("costUsd")))
    summary.add_row("unrealized", money(totals.get("unrealizedUsd")))
    summary.add_row("realized", money(totals.get("realizedUsd")))
    summary.add_row("gas", money(totals.get("gasUsd")))
    summary.add_row(
        "24h",
        f"{money(totals.get('change24hUsd'))} ({percent(totals.get('change24hPct'))})",
    )
    console.print(summary)
    holdings = result.get("holdings", [])
    table = Table(title="Holdings", show_header=True, header_style=ACCENT_HEADER)
    table.add_column("Chain")
    table.add_column("Token")
    table.add_column("Amount", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("Avg cost", justify="right")
    table.add_column("Unrealized", justify="right")
    table.add_column("Realized", justify="right")
    table.add_column("Alloc", justify="right")
    for row in holdings if isinstance(holdings, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = markup_escape(token_symbol(row.get("token")))
        table.add_row(
            chain_label(row.get("chainId")),
            f"[dim]{symbol} (hidden)[/dim]" if row.get("hidden") else symbol,
            amount_text(row.get("amount")),
            money(row.get("priceUsd")),
            money(row.get("valueUsd")),
            money(row.get("avgCostUsd")),
            f"{money(row.get('unrealizedUsd'))} ({percent(row.get('unrealizedPct'))})",
            money(row.get("realizedUsd")),
            percent(row.get("allocationPct")),
        )
    console.print(table)
    hidden_count = int(result.get("hiddenCount") or 0)
    if hidden_count and not hidden:
        noun = "token" if hidden_count == 1 else "tokens"
        console.print(
            f"[dim]{hidden_count} junk {noun} hidden and not counted; add --hidden to list "
            "them, or `agentos trade unhide` to keep one.[/dim]"
        )
    if result.get("syncing"):
        console.print("Ledger sync in progress; numbers may still move.")


@app.command("sync")
def trade_sync(
    wallet: str | None = typer.Option(None, "--wallet", help="One wallet (default: all)"),
    full: bool = typer.Option(False, "--full", help="Rebuild lots and history from the chain"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Re-read the chain into the ledger."""

    params: dict[str, Any] = {"full": full}
    if wallet:
        params["wallet"] = wallet

    async def _run(client):
        return await client.call("trading.sync", params)

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    console.print("Sync started." + (" Full rebuild." if full else ""))


@app.command("limits")
def trade_limits(
    wallet: str | None = typer.Argument(None, help="Wallet address (default: the primary wallet)"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Show the agent guardrails and today's spend for a wallet."""

    async def _run(client):
        target = wallet
        if not target:
            status = await client.call("wallet.status", {})
            target = (status or {}).get("primary") if isinstance(status, dict) else None
            if not target:
                _bad_argument("No primary wallet; pass a wallet address", json_output=json_output)
        return await client.call("trading.limits", {"wallet": target})

    result = run_gateway_sync(_run, json_output=json_output)
    if json_output:
        print_json(result)
        return
    result = result if isinstance(result, dict) else {}
    shown = str(wallet or result.get("wallet") or "")
    table = Table(title=f"Limits for {short_address(shown)}", show_header=False)
    table.add_column("Field", style=ACCENT)
    table.add_column("Value", justify="right")
    table.add_row("daily cap", money(result.get("dailyCapUsd")))
    table.add_row("spent today", money(result.get("spentTodayUsd")))
    table.add_row("approval threshold", money(result.get("approvalThresholdUsd")))
    table.add_row("approval TTL", f"{result.get('approvalTtlSeconds', '—')} s")
    console.print(table)
