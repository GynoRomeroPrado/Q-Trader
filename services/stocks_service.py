"""Stocks domain service — paper mode fallback + live Alpaca.

Returns performance data from Alpaca positions when provider=alpaca,
with automatic fallback to stub data on any broker error.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, asdict
from typing import Any

from core.stocks_exchange_client import (
    create_stocks_client,
    StocksExchangeClientProtocol,
    StocksClientError,
)
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class StockTradeSummary:
    symbol: str
    total_trades: int
    total_pnl: float
    win_rate: float
    max_drawdown: float
    last_price: float | None = None
    day_change_pct: float | None = None


# ── Client singleton (created from config) ──────────────────

_stocks_client: StocksExchangeClientProtocol = create_stocks_client(settings.stocks)


def get_stocks_client() -> StocksExchangeClientProtocol:
    """Access the stocks client singleton."""
    return _stocks_client


# ── Stub data (paper mode / fallback) ──────────────────────

_STUB_STOCKS: list[StockTradeSummary] = [
    StockTradeSummary(
        symbol="AAPL",
        total_trades=12,
        total_pnl=84.50,
        win_rate=0.6667,
        max_drawdown=0.038,
        last_price=189.72,
        day_change_pct=1.23,
    ),
    StockTradeSummary(
        symbol="MSFT",
        total_trades=8,
        total_pnl=42.10,
        win_rate=0.625,
        max_drawdown=0.021,
        last_price=422.15,
        day_change_pct=-0.45,
    ),
    StockTradeSummary(
        symbol="TSLA",
        total_trades=15,
        total_pnl=-18.30,
        win_rate=0.4,
        max_drawdown=0.092,
        last_price=175.60,
        day_change_pct=2.87,
    ),
]


def _stub_data() -> list[dict[str, Any]]:
    """Return stub performance data."""
    return [asdict(s) for s in _STUB_STOCKS]


async def _fetch_alpaca_performance() -> list[dict[str, Any]]:
    """Fetch live positions from Alpaca and map to StockTradeSummary format."""
    client = get_stocks_client()
    positions = await client.fetch_positions()

    if not positions:
        logger.info("Alpaca: no open positions, returning stubs")
        return _stub_data()

    results: list[dict[str, Any]] = []
    for p in positions:
        results.append(asdict(StockTradeSummary(
            symbol=p.symbol,
            total_trades=0,  # Would need trade history DB
            total_pnl=round(p.unrealized_pnl, 2),
            win_rate=1.0 if p.unrealized_pnl > 0 else 0.0,
            max_drawdown=0.0,
            last_price=p.current_price,
            day_change_pct=round(
                ((p.current_price - p.avg_entry_price) / p.avg_entry_price) * 100, 2
            ) if p.avg_entry_price > 0 else 0.0,
        )))

    return results


def get_stocks_performance_summary() -> list[dict[str, Any]]:
    """Return per-symbol performance summaries.

    If provider is 'alpaca', tries to fetch live data.
    On any error, falls back to stub data.
    """
    if settings.stocks.provider != "alpaca":
        return _stub_data()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Called from within an async context (FastAPI) — can't use asyncio.run
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _fetch_alpaca_performance())
                return future.result(timeout=15)
        else:
            return asyncio.run(_fetch_alpaca_performance())
    except (StocksClientError, Exception) as e:
        logger.warning("Alpaca fetch failed, using fallback stubs: %s", e)
        return _stub_data()


def get_stocks_status() -> dict[str, Any]:
    """Aggregated overview across all stock symbols."""
    summaries_raw = get_stocks_performance_summary()

    total_trades = sum(s.get("total_trades", 0) for s in summaries_raw)
    total_pnl = sum(s.get("total_pnl", 0) for s in summaries_raw)

    if total_trades > 0:
        wins = sum(
            s.get("total_trades", 0) * s.get("win_rate", 0)
            for s in summaries_raw
        )
        win_rate = wins / total_trades
    else:
        win_rate = 0.0

    max_dd = max((s.get("max_drawdown", 0) for s in summaries_raw), default=0.0)

    return {
        "state": settings.stocks.provider,
        "symbols_tracked": len(summaries_raw),
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_dd, 4),
    }
