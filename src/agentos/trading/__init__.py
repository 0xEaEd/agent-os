"""Wallets, Uniswap swaps, ledger and PnL for the desktop Trading page.

Lazy public surface: importing this package pulls nothing heavy. The
service (and with it eth-account, httpx clients and SQLite) is created on
first use through :func:`get_trading_service`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from agentos.trading.service import TradingService

__all__ = ["get_trading_service", "reset_trading_service"]


def get_trading_service(config: Any | None = None, *, broadcast: Any = None) -> TradingService:
    from agentos.trading.service import get_trading_service as _get

    return _get(config, broadcast=broadcast)


def reset_trading_service() -> None:
    from agentos.trading.service import reset_trading_service as _reset

    _reset()
