"""``wallet.*`` and ``trading.*`` RPC — the engine side of the Trading page.

Thin handlers: validate params, call :class:`agentos.trading.service.TradingService`,
map its coded errors to :class:`RpcHandlerError`. The service is a lazy
module singleton built from ``ctx.config`` on first use; its background
sync/expiry loop starts the first time a handler runs on a live event loop.
Control-plane only.
"""

from __future__ import annotations

import functools
import inspect
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from agentos.gateway.agent_surface import agent_binding
from agentos.gateway.rpc import RpcContext, RpcHandlerError, get_dispatcher
from agentos.trading import get_trading_service
from agentos.trading.chains import ChainSpec, resolve_chain
from agentos.trading.providers import PROVIDER_IDS
from agentos.trading.service import (
    DEFAULT_CHART_RANGE,
    TradingError,
    TradingService,
    _err,
)

_d = get_dispatcher()

_VALID_EXPORT_FORMATS = {"keystore", "privateKey"}
_VALID_INITIATORS = {"manual", "agent"}
NOTE_MAX_CHARS = 240
# Unicode bidi controls (LRM/RLM/ALM, the embedding/override/isolate pairs):
# in a note they can make "send to A" read as "send to B" in the approval card.
_BIDI_CONTROLS = "\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069"
_NOTE_STRIP = re.compile(f"[\\x00-\\x1f\\x7f-\\x9f{_BIDI_CONTROLS}]")
_NOTE_SPACES = re.compile(r"\s+")


def _require_operator(ctx: RpcContext, action: str) -> None:
    """Refuse an agent's connection: this is the user's action, not the agent's.

    The binding is computed at admission by ``gateway.agent_surface``; a
    client cannot talk its way out of it with a parameter.
    """
    binding = agent_binding(ctx)
    if binding is not None:
        raise RpcHandlerError(
            "trading.operator_required",
            f"{action} is the user's action; an agent cannot do it. "
            "Ask the user to do it in the app.",
            details=binding.to_dict(),
        )


def _operator_only(fn: Callable[[dict | None, RpcContext], Awaitable[dict[str, Any]]]) -> Any:
    @functools.wraps(fn)
    async def wrapper(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
        _require_operator(ctx, fn.__name__.strip("_").replace("_", "."))
        return await fn(params, ctx)

    return wrapper


def _initiator(ctx: RpcContext, p: dict[str, Any]) -> tuple[str, str | None]:
    """Who is asking, decided server-side.

    An agent-bound connection is an agent whatever it declares, and its
    orders are filed under the chat the binding names. Only an unbound
    connection may call itself ``manual``.
    """
    declared = _str(p, "initiator") or "manual"
    if declared not in _VALID_INITIATORS:
        raise ValueError("params.initiator must be 'manual' or 'agent'")
    session_key = _str(p, "sessionKey")
    binding = agent_binding(ctx)
    if binding is not None:
        return "agent", binding.session_key or session_key
    return declared, session_key


async def _broadcast(event: str, payload: dict[str, Any]) -> None:
    """Fan an engine event out to every authenticated WebSocket connection."""
    from agentos.gateway.websocket import get_registry

    try:
        await get_registry().broadcast(event, payload)
    except Exception:  # pragma: no cover - best effort
        pass


def _service(ctx: RpcContext) -> TradingService:
    service = get_trading_service(ctx.config, broadcast=_broadcast)
    service.ensure_started()
    return service


def _params(params: dict | None) -> dict[str, Any]:
    return params if isinstance(params, dict) else {}


def _str(params: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = params.get(key)
    if value is None or value == "":
        if required:
            raise ValueError(f"params.{key} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"params.{key} must be a string")
    return value.strip()


def _note(params: dict[str, Any], key: str = "note") -> str | None:
    """A free-text note as it will be shown in history and the approval card.

    C0/C1 control characters and Unicode bidi controls are dropped, runs of
    whitespace collapse to one space, and the text is cut at
    :data:`NOTE_MAX_CHARS`. The note is the one field an agent writes that a
    person reads before approving, so it must render as it was typed.
    """
    raw = _str(params, key)
    if raw is None:
        return None
    text = unicodedata.normalize("NFC", raw)
    # Whitespace first (a newline or tab becomes a space, not nothing), then
    # the controls that are not whitespace, then collapse what that left.
    text = _NOTE_SPACES.sub(" ", text)
    text = _NOTE_STRIP.sub("", text)
    text = _NOTE_SPACES.sub(" ", text).strip()
    if not text:
        return None
    return text[:NOTE_MAX_CHARS].rstrip()


def _without_paths(ctx: RpcContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Drop filesystem paths (``vaultPath`` and any other ``*Path``) for an agent.

    Where the keystores live is the operator's business; an agent only needs
    to know whether the vault is set up and unlocked.
    """
    if agent_binding(ctx) is None:
        return payload
    return {k: v for k, v in payload.items() if not (isinstance(k, str) and k.endswith("Path"))}


def _client_order_id(service: TradingService, method: str, p: dict[str, Any]) -> dict[str, Any]:
    """``clientOrderId`` → ``client_order_id=`` for the service call.

    An idempotency key must never be dropped on the floor: a retry that lost
    its key is a second trade. So when the engine's ``method`` does not take
    one, a caller that sent one is refused instead of served without it.
    """
    value = _str(p, "clientOrderId")
    if value is None:
        return {}
    fn = getattr(type(service), method, None)
    try:
        accepted = fn is not None and "client_order_id" in inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins without a signature
        accepted = False
    if not accepted:
        raise RpcHandlerError(
            "trading.invalid", f"clientOrderId is not supported by this engine's {method}"
        )
    return {"client_order_id": value}


def _number(params: dict[str, Any], key: str) -> float | None:
    value = params.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"params.{key} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"params.{key} must be a number") from exc


def _raw_amount(params: dict[str, Any], key: str) -> int | None:
    """A token amount in base units: an integer or an int-like string, never a float."""
    value = params.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"params.{key} must be an integer string")
    if isinstance(value, str):
        value = value.strip()
        if not value.isdigit():
            raise ValueError(f"params.{key} must be an integer string")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"params.{key} must be an integer string") from exc


def _int(params: dict[str, Any], key: str, default: int) -> int:
    value = params.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ValueError(f"params.{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"params.{key} must be an integer") from exc


def _chain(params: dict[str, Any], *, required: bool = True) -> ChainSpec | None:
    value = params.get("chainId", params.get("chain"))
    if value is None or value == "":
        if required:
            raise ValueError("params.chainId is required")
        return None
    try:
        return resolve_chain(value)
    except ValueError as exc:
        raise RpcHandlerError("trading.unsupported_chain", str(exc)) from exc


def _raise(exc: Exception) -> RpcHandlerError:
    if isinstance(exc, RpcHandlerError):
        return exc
    error: TradingError = _err(exc)
    return RpcHandlerError(error.code, str(error), details=error.details)


# ── wallet.* ───────────────────────────────────────────────────────────────


@_d.method("wallet.status")
async def _wallet_status(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    service = _service(ctx)
    service.ensure_unlocked()
    return _without_paths(ctx, service.vault.status())


@_d.method("wallet.setup")
@_operator_only
async def _wallet_setup(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    password = _str(p, "password", required=True) or ""
    mode = _str(p, "unlockMode") or "auto"
    if mode not in ("auto", "manual"):
        raise ValueError("params.unlockMode must be 'auto' or 'manual'")
    service = _service(ctx)
    try:
        service.vault.setup(password, mode)  # type: ignore[arg-type]
    except Exception as exc:
        raise _raise(exc) from exc
    await service._emit("trading.changed", {"reason": "wallet"})
    return {"initialized": True, **service.vault.status()}


@_d.method("wallet.unlock")
@_operator_only
async def _wallet_unlock(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    password = _str(p, "password", required=True) or ""
    service = _service(ctx)
    try:
        service.vault.unlock(password)
    except Exception as exc:
        raise _raise(exc) from exc
    await service._emit("trading.changed", {"reason": "wallet"})
    return {"unlocked": True}


@_d.method("wallet.lock")
@_operator_only
async def _wallet_lock(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    service = _service(ctx)
    service.vault.lock()
    await service._emit("trading.changed", {"reason": "wallet"})
    return {"unlocked": False}


@_d.method("wallet.setUnlockMode")
@_operator_only
async def _wallet_set_unlock_mode(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    mode = _str(p, "mode", required=True) or ""
    password = _str(p, "password", required=True) or ""
    if mode not in ("auto", "manual"):
        raise ValueError("params.mode must be 'auto' or 'manual'")
    service = _service(ctx)
    try:
        service.vault.set_unlock_mode(mode, password)  # type: ignore[arg-type]
    except Exception as exc:
        raise _raise(exc) from exc
    await service._emit("trading.changed", {"reason": "wallet"})
    return {"unlockMode": mode}


@_d.method("wallet.changePassword")
@_operator_only
async def _wallet_change_password(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    password = _str(p, "password", required=True) or ""
    new_password = _str(p, "newPassword", required=True) or ""
    service = _service(ctx)
    try:
        service.vault.change_password(password, new_password)
    except Exception as exc:
        raise _raise(exc) from exc
    return {"changed": True}


@_d.method("wallet.list")
async def _wallet_list(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    service = _service(ctx)
    service.ensure_unlocked()
    try:
        wallets = service.wallet_dicts()
        primary = service.vault.primary_address() if service.vault.initialized else None
    except Exception as exc:
        raise _raise(exc) from exc
    return {"wallets": wallets, "primary": primary}


@_d.method("wallet.create")
@_operator_only
async def _wallet_create(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    label = _str(p, "label") or ""
    service = _service(ctx)
    try:
        return {"wallet": await service.create_wallet(label)}
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("wallet.import")
@_operator_only
async def _wallet_import(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    label = _str(p, "label") or ""
    private_key = _str(p, "privateKey")
    keystore_json = _str(p, "keystoreJson")
    keystore_password = _str(p, "keystorePassword")
    if not private_key and not keystore_json:
        raise ValueError("params.privateKey or params.keystoreJson is required")
    service = _service(ctx)
    try:
        wallet = await service.import_wallet(
            label,
            private_key=private_key,
            keystore_json=keystore_json,
            keystore_password=keystore_password,
        )
    except Exception as exc:
        raise _raise(exc) from exc
    return {"wallet": wallet}


@_d.method("wallet.export")
@_operator_only
async def _wallet_export(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    address = _str(p, "address", required=True) or ""
    password = _str(p, "password", required=True) or ""
    fmt = _str(p, "format") or "keystore"
    if fmt not in _VALID_EXPORT_FORMATS:
        raise ValueError("params.format must be 'keystore' or 'privateKey'")
    service = _service(ctx)
    try:
        secret = service.vault.export(address, password, fmt)  # type: ignore[arg-type]
    except Exception as exc:
        raise _raise(exc) from exc
    return {"keystoreJson": secret} if fmt == "keystore" else {"privateKey": secret}


@_d.method("wallet.rename")
@_operator_only
async def _wallet_rename(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    address = _str(p, "address", required=True) or ""
    label = _str(p, "label", required=True) or ""
    service = _service(ctx)
    try:
        record = service.vault.rename(address, label)
        service._mirror_wallet(record)
    except Exception as exc:
        raise _raise(exc) from exc
    await service._emit("trading.changed", {"reason": "wallet", "wallet": record.address})
    primary = service.vault.primary_address()
    return {"wallet": record.to_dict(primary=(record.address == primary))}


@_d.method("wallet.remove")
@_operator_only
async def _wallet_remove(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    address = _str(p, "address", required=True) or ""
    password = _str(p, "password", required=True) or ""
    service = _service(ctx)
    try:
        await service.remove_wallet(address, password)
    except Exception as exc:
        raise _raise(exc) from exc
    return {"removed": True}


@_d.method("wallet.setPrimary")
@_operator_only
async def _wallet_set_primary(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    address = _str(p, "address", required=True) or ""
    service = _service(ctx)
    try:
        record = service.vault.set_primary(address)
        for item in service.vault.list():
            service._mirror_wallet(item)
    except Exception as exc:
        raise _raise(exc) from exc
    await service._emit("trading.changed", {"reason": "wallet", "wallet": record.address})
    return {"wallet": record.to_dict(primary=True)}


@_d.method("wallet.balances")
async def _wallet_balances(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Balances from the ledger. ``refresh: true`` forces a chain read first (throttled).

    ``chains`` says how fresh each wallet/chain is: ``ok``, ``partial`` (some
    token reads failed, their rows are last-good) or ``failed`` (the node was
    unreachable). ``updatedAt`` is the newest balance row returned.
    """
    p = _params(params)
    address = _str(p, "address")
    chain = _chain(p, required=False)
    chain_id = chain.chain_id if chain else None
    service = _service(ctx)
    service.ensure_unlocked()
    include_hidden = bool(p.get("includeHidden"))
    try:
        rows = await service.balances(
            address, chain_id, refresh=bool(p.get("refresh")), include_hidden=include_hidden
        )
        reads = service.chain_reads(address, chain_id)
        hidden = service.hidden_balance_count(address, chain_id)
    except Exception as exc:
        raise _raise(exc) from exc
    newest = max((int(r.get("updatedAt") or 0) for r in rows), default=0)
    return {"balances": rows, "hiddenCount": hidden, "chains": reads, "updatedAt": newest or None}


# ── trading.* ──────────────────────────────────────────────────────────────


@_d.method("trading.status")
async def _trading_status(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    service = _service(ctx)
    service.ensure_unlocked()
    return _without_paths(ctx, await service.status(check_rpc=bool(p.get("checkRpc"))))


@_d.method("trading.probe")
async def _trading_probe(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Is the provider reachable? With ``apiKey`` it also tests a key that is not
    in config, which turns the gateway into a key oracle — so that form is the
    operator's only."""
    p = _params(params)
    api_key = _str(p, "apiKey")
    if api_key is not None:
        _require_operator(ctx, "trading.probe(apiKey)")
    service = _service(ctx)
    try:
        return await service.probe(api_key, provider_id=_str(p, "provider"))
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.setProvider")
async def _trading_set_provider(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Persist ``trading.provider`` through the same path as ``config.set``.

    Convenience for the CLI; the desktop may equally call
    ``config.patch {trading: {provider}}``. Both take effect on the next
    trading call without a gateway restart.
    """
    p = _params(params)
    provider = (_str(p, "provider", required=True) or "").lower()
    if provider not in PROVIDER_IDS:
        raise ValueError(
            "params.provider must be one of: " + ", ".join(repr(p) for p in PROVIDER_IDS)
        )
    result = await get_dispatcher().dispatch(
        "trading.setProvider",
        "config.set",
        {"path": "trading.provider", "value": provider},
        ctx,
    )
    if not result.ok:
        message = result.error.message if result.error else "config write failed"
        code = result.error.code if result.error else "trading.error"
        raise RpcHandlerError(str(code), str(message))
    service = _service(ctx)
    await service._emit("trading.changed", {"reason": "config", "provider": provider})
    payload = result.payload if isinstance(result.payload, dict) else {}
    return {"provider": provider, "restartRequired": bool(payload.get("restartRequired"))}


@_d.method("trading.tokens.search")
async def _trading_tokens_search(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    query = _str(p, "query", required=True) or ""
    service = _service(ctx)
    try:
        return {"tokens": await service.search_tokens(chain, query)}
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.tokens.resolve")
async def _trading_tokens_resolve(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    value = _str(p, "address") or _str(p, "token", required=True) or ""
    service = _service(ctx)
    try:
        meta = await service.resolve_token(chain, value)
    except Exception as exc:
        raise _raise(exc) from exc
    return {"token": meta.to_dict()}


@_d.method("trading.tokens.hide")
@_operator_only
async def _trading_tokens_hide(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Hide (``hidden: true``, the default) or show a token. The user's call, and final."""
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    address = _str(p, "address", required=True) or ""
    hidden = p.get("hidden", True)
    if not isinstance(hidden, bool):
        raise RpcHandlerError("trading.invalid", "hidden must be a boolean")
    service = _service(ctx)
    try:
        token = await service.set_token_hidden(chain, address, hidden)
    except Exception as exc:
        raise _raise(exc) from exc
    return {"token": token}


@_d.method("trading.quote")
async def _trading_quote(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    initiator, _session = _initiator(ctx, p)
    service = _service(ctx)
    service.ensure_unlocked()
    try:
        return await service.quote(
            chain=chain,
            wallet=_str(p, "wallet"),
            token_in=_str(p, "tokenIn", required=True) or "",
            token_out=_str(p, "tokenOut", required=True) or "",
            amount_in=_str(p, "amountIn"),
            amount_usd=_number(p, "amountUsd"),
            slippage_pct=_number(p, "slippagePct"),
            initiator=initiator,  # type: ignore[arg-type]
        )
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.swap")
async def _trading_swap(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    initiator, session_key = _initiator(ctx, p)
    wallets = p.get("wallets", p.get("wallet"))
    if wallets is not None and not isinstance(wallets, str | list):
        raise ValueError("params.wallets must be an address list, 'all' or omitted")
    amount_in = p.get("amountIn")
    if amount_in is not None and not isinstance(amount_in, str | int | float):
        raise ValueError("params.amountIn must be a decimal string")
    service = _service(ctx)
    try:
        orders = await service.swap(
            chain=chain,
            wallets=wallets,
            token_in=_str(p, "tokenIn", required=True) or "",
            token_out=_str(p, "tokenOut", required=True) or "",
            amount_in=str(amount_in) if amount_in is not None else None,
            amount_pct=_number(p, "amountPct"),
            amount_usd=_number(p, "amountUsd"),
            slippage_pct=_number(p, "slippagePct"),
            initiator=initiator,  # type: ignore[arg-type]
            session_key=session_key,
            note=_note(p),
            wait=bool(p.get("wait")),
            expected_out_raw=_raw_amount(p, "expectedOutRaw"),
            min_out_raw=_raw_amount(p, "minOutRaw"),
            quote_id=_str(p, "quoteId"),
            **_client_order_id(service, "swap", p),
        )
    except Exception as exc:
        raise _raise(exc) from exc
    return {"orders": orders}


def _recipients(p: dict[str, Any]) -> list[dict[str, Any]]:
    """Recipients from either shape: ``recipients: [{to, amount|amountUsd}]``
    or the one-address ``to`` + ``amount``/``amountUsd`` form."""
    listed = p.get("recipients")
    if listed is not None:
        if not isinstance(listed, list) or not listed:
            raise ValueError("params.recipients must be a non-empty list")
        out: list[dict[str, Any]] = []
        for item in listed:
            if not isinstance(item, dict):
                raise ValueError("params.recipients entries must be objects")
            entry: dict[str, Any] = {"to": _str(item, "to", required=True)}
            amount = item.get("amount")
            if amount is not None and amount != "":
                if not isinstance(amount, str | int | float) or isinstance(amount, bool):
                    raise ValueError("recipient amount must be a decimal string")
                entry["amount"] = str(amount)
            usd = _number(item, "amountUsd")
            if usd is not None:
                entry["amountUsd"] = usd
            out.append(entry)
        return out
    to = _str(p, "to", required=True) or ""
    entry = {"to": to}
    amount = p.get("amount")
    if amount is not None and amount != "":
        if not isinstance(amount, str | int | float) or isinstance(amount, bool):
            raise ValueError("params.amount must be a decimal string")
        entry["amount"] = str(amount)
    usd = _number(p, "amountUsd")
    if usd is not None:
        entry["amountUsd"] = usd
    return [entry]


@_d.method("trading.send")
async def _trading_send(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Send one token to one or many addresses. An agent's send always parks for approval."""
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    initiator, session_key = _initiator(ctx, p)
    recipients = _recipients(p)
    service = _service(ctx)
    try:
        orders = await service.send(
            chain=chain,
            wallet=_str(p, "wallet"),
            token=_str(p, "token", required=True) or "",
            recipients=recipients,
            initiator=initiator,  # type: ignore[arg-type]
            session_key=session_key,
            note=_note(p),
            wait=bool(p.get("wait")),
            **_client_order_id(service, "send", p),
        )
    except Exception as exc:
        raise _raise(exc) from exc
    return {"orders": orders, "batchId": orders[0].get("batchId") if orders else None}


@_d.method("trading.orders.batch")
async def _trading_orders_batch(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    batch_id = _str(p, "batchId", required=True) or ""
    service = _service(ctx)
    try:
        return {"orders": service.batch(batch_id), "batchId": batch_id}
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.allowances.list")
async def _trading_allowances_list(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """What a wallet has approved others to spend, per chain (all chains when none given)."""
    p = _params(params)
    chain = _chain(p, required=False)
    service = _service(ctx)
    service.ensure_unlocked()
    full = bool(p.get("full"))
    wait = bool(p.get("wait"))
    wallet = _str(p, "wallet")
    try:
        if chain is not None:
            return await service.allowances(chain, wallet, full=full, wait=wait)
        results = [
            await service.allowances(c, wallet, full=full, wait=wait) for c in service.chains()
        ]
    except Exception as exc:
        raise _raise(exc) from exc
    rows = [a for r in results for a in r["allowances"]]
    return {
        "wallet": results[0]["wallet"] if results else wallet,
        "chainId": None,
        "allowances": rows,
        "count": len(rows),
        "unlimitedCount": sum(int(r["unlimitedCount"]) for r in results),
        "scanning": any(bool(r["scanning"]) for r in results),
        "chains": [
            {
                "chainId": r["chainId"],
                "count": r["count"],
                "scanning": r["scanning"],
                "scannedTo": r["scannedTo"],
                "scanFrom": r["scanFrom"],
                "head": r["head"],
            }
            for r in results
        ],
    }


@_d.method("trading.allowances.revoke")
async def _trading_allowances_revoke(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Set an allowance to zero. From an agent this parks an order; from the user it runs."""
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    initiator, session_key = _initiator(ctx, p)
    service = _service(ctx)
    try:
        order = await service.revoke(
            chain=chain,
            wallet=_str(p, "wallet"),
            token=_str(p, "token", required=True) or "",
            spender=_str(p, "spender", required=True) or "",
            initiator=initiator,  # type: ignore[arg-type]
            session_key=session_key,
            note=_note(p),
            wait=bool(p.get("wait")),
        )
    except Exception as exc:
        raise _raise(exc) from exc
    return {"order": order}


@_d.method("trading.decode")
async def _trading_decode(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Explain a transaction hash, or raw calldata (with an optional ``to``)."""
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    tx_hash = _str(p, "txHash")
    data = _str(p, "data")
    if not tx_hash and data is None:
        raise ValueError("params.txHash or params.data is required")
    service = _service(ctx)
    try:
        return await service.decode(chain, tx_hash=tx_hash, data=data, to=_str(p, "to"))
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.network")
async def _trading_network(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Head block, block age, gas and RPC latency per chain (cached for a few seconds)."""
    p = _params(params)
    service = _service(ctx)
    try:
        return await service.network(fresh=bool(p.get("fresh")))
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.unwrap")
@_operator_only
async def _trading_unwrap(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Unwrap WETH held by a wallet. It moves funds, so it is the user's action."""
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    service = _service(ctx)
    try:
        return await service.unwrap(chain, _str(p, "wallet"), _str(p, "amount"))
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.orders.list")
async def _trading_orders_list(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    service = _service(ctx)
    try:
        return service.list_orders(
            status=_str(p, "status"),
            wallet=_str(p, "wallet"),
            limit=_int(p, "limit", 50),
            kind=_str(p, "kind"),
        )
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.orders.get")
async def _trading_orders_get(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    order_id = _str(p, "orderId", required=True) or ""
    service = _service(ctx)
    try:
        return {"order": service.get_order(order_id)}
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.orders.wait")
async def _trading_orders_wait(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    order_id = _str(p, "orderId", required=True) or ""
    timeout = _number(p, "timeoutSeconds")
    service = _service(ctx)
    try:
        order = await service.wait_order(order_id, timeout if timeout is not None else 60.0)
    except Exception as exc:
        raise _raise(exc) from exc
    return {"order": order}


@_d.method("trading.orders.approve")
@_operator_only
async def _trading_orders_approve(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    order_id = _str(p, "orderId", required=True) or ""
    service = _service(ctx)
    try:
        return {"order": await service.approve(order_id, wait=bool(p.get("wait")))}
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.orders.reject")
@_operator_only
async def _trading_orders_reject(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    order_id = _str(p, "orderId", required=True) or ""
    service = _service(ctx)
    try:
        return {"order": await service.reject(order_id, _str(p, "reason"))}
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.history")
async def _trading_history(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    chain = _chain(p, required=False)
    before = _number(p, "before")
    service = _service(ctx)
    try:
        return service.history(
            wallet=_str(p, "wallet"),
            chain_id=chain.chain_id if chain else None,
            kind=_str(p, "kind"),
            limit=_int(p, "limit", 100),
            before=before / 1000.0 if before is not None and before > 10**11 else before,
            include_hidden=bool(p.get("includeHidden")),
        )
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.portfolio")
async def _trading_portfolio(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    service = _service(ctx)
    service.ensure_unlocked()
    try:
        return await service.portfolio(
            _str(p, "wallet"), include_hidden=bool(p.get("includeHidden"))
        )
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.chart")
async def _trading_chart(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    chain = _chain(p)
    assert chain is not None
    token = _str(p, "token", required=True) or ""
    range_key = _str(p, "range") or DEFAULT_CHART_RANGE
    service = _service(ctx)
    try:
        return await service.chart(chain, token, range_key)
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.sync")
async def _trading_sync(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    """Re-read the chain into the ledger. ``full`` drops and rebuilds the ledger
    (every chain, from the wallet's first block), so an agent may not ask for it."""
    p = _params(params)
    full = bool(p.get("full"))
    if full:
        _require_operator(ctx, "trading.sync(full)")
    service = _service(ctx)
    service.ensure_unlocked()
    try:
        service.request_sync(wallet=_str(p, "wallet"), full=full)
    except Exception as exc:
        raise _raise(exc) from exc
    return {"started": True}


@_d.method("trading.limits")
async def _trading_limits(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    service = _service(ctx)
    try:
        return service.limits(_str(p, "wallet"))
    except Exception as exc:
        raise _raise(exc) from exc


@_d.method("trading.lot.setCost")
@_operator_only
async def _trading_lot_set_cost(params: dict | None, ctx: RpcContext) -> dict[str, Any]:
    p = _params(params)
    entry_id = _int(p, "entryId", 0)
    cost = _number(p, "costUsdPerToken")
    if entry_id <= 0:
        raise ValueError("params.entryId is required")
    if cost is None or cost < 0:
        raise ValueError("params.costUsdPerToken must be a non-negative number")
    service = _service(ctx)
    try:
        entry = service.set_lot_cost(entry_id, cost)
    except Exception as exc:
        raise _raise(exc) from exc
    await service._emit("trading.changed", {"reason": "sync"})
    return {"entry": entry}
