"""Paper Wallet — SQLite-persistent virtual execution environment.

Simulates exchange balance and trade execution for forward-testing
without risking real capital. All state survives bot restarts.

Architecture:
    - SQLite tables: paper_balances (per-asset), paper_trades (history)
    - pathlib paths for Windows ↔ Linux portability
    - Interface mirrors ExchangeClient.fetch_balance() for transparent swap
    - Maker Fee: 0.00% (configurable)
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.strategy_base import Signal

logger = logging.getLogger(__name__)


class PaperWallet:
    """SQLite-backed virtual wallet for paper trading.

    Persists balances and trade history across restarts.
    Drop-in replacement for ExchangeClient balance/execution methods.
    """

    _BALANCES_DDL = """
        CREATE TABLE IF NOT EXISTS paper_balances (
            asset TEXT PRIMARY KEY,
            free REAL NOT NULL DEFAULT 0.0,
            used REAL NOT NULL DEFAULT 0.0,
            total REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT
        )
    """
    _TRADES_DDL = """
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            signal TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            amount REAL NOT NULL,
            cost REAL NOT NULL,
            fee REAL NOT NULL DEFAULT 0.0,
            quote_balance_after REAL,
            base_balance_after REAL,
            metadata TEXT DEFAULT '{}'
        )
    """

    def __init__(
        self,
        db_path: Path,
        initial_quote: float = 1000.0,
        quote_asset: str = "USDT",
        maker_fee: float | None = None,
        slippage_bps: float | None = None,
    ) -> None:
        from config.settings import settings as _s
        self._db_path = db_path
        self._initial_quote = initial_quote
        self._quote_asset = quote_asset
        self._maker_fee = maker_fee if maker_fee is not None else _s.risk.fee_taker
        self._slippage_bps = slippage_bps if slippage_bps is not None else _s.risk.paper_slippage_bps

        # Ensure parent directory exists (pathlib cross-platform)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Open SQLite (thread-safe for executor)
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

        self._init_tables()

        current = self._get_balance_sync(self._quote_asset)
        logger.info(
            f"🏦 Paper Wallet Inicializado → {current['free']:.2f} {quote_asset} "
            f"(Fee Maker: {self._maker_fee * 100:.4f}% | "
            f"Slippage: ~{self._slippage_bps:.1f}bps) | DB: {self._db_path}"
        )

    def _init_tables(self) -> None:
        """Create tables and seed initial balance if needed."""
        self._conn.execute(self._BALANCES_DDL)
        self._conn.execute(self._TRADES_DDL)
        self._conn.commit()

        # Seed initial quote balance if this is a fresh database
        row = self._conn.execute(
            "SELECT free FROM paper_balances WHERE asset = ?",
            (self._quote_asset,),
        ).fetchone()

        if row is None:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """INSERT INTO paper_balances (asset, free, used, total, updated_at)
                   VALUES (?, ?, 0.0, ?, ?)""",
                (self._quote_asset, self._initial_quote, self._initial_quote, now),
            )
            self._conn.commit()
            logger.info(
                f"🆕 Paper Wallet: Seed balance creado → "
                f"{self._initial_quote:.2f} {self._quote_asset}"
            )

    # ──────────────────────────────────────────────────────────
    # Balance Interface (mirrors ExchangeClient)
    # ──────────────────────────────────────────────────────────

    def _get_balance_sync(self, asset: str) -> dict[str, float]:
        """Synchronous balance read."""
        row = self._conn.execute(
            "SELECT free, used, total FROM paper_balances WHERE asset = ?",
            (asset,),
        ).fetchone()

        if row is None:
            return {"free": 0.0, "used": 0.0, "total": 0.0}

        return {"free": row["free"], "used": row["used"], "total": row["total"]}

    def _set_balance_sync(self, asset: str, free: float) -> None:
        """Synchronous balance update (upsert)."""
        now = datetime.now(timezone.utc).isoformat()
        total = free  # Paper wallet: no 'used' concept
        self._conn.execute(
            """INSERT INTO paper_balances (asset, free, used, total, updated_at)
               VALUES (?, ?, 0.0, ?, ?)
               ON CONFLICT(asset) DO UPDATE SET
                   free = excluded.free,
                   total = excluded.total,
                   updated_at = excluded.updated_at""",
            (asset, free, total, now),
        )

    async def fetch_balance(self, asset: str) -> dict[str, float]:
        """Async balance query — compatible with ExchangeClient interface."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_balance_sync, asset)

    async def get_all_balances(self) -> dict[str, dict[str, float]]:
        """Return all asset balances."""
        def _query() -> dict[str, dict[str, float]]:
            rows = self._conn.execute(
                "SELECT asset, free, used, total FROM paper_balances"
            ).fetchall()
            return {
                r["asset"]: {"free": r["free"], "used": r["used"], "total": r["total"]}
                for r in rows
            }
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _query)

    # ──────────────────────────────────────────────────────────
    # Slippage Simulation
    # ──────────────────────────────────────────────────────────

    def _simulate_slippage(self, price: float, signal: Signal) -> float:
        """Simulate realistic execution price with adversarial slippage.

        Uses a half-normal distribution (absolute value of gaussian) centered
        at ~slippage_bps basis points. Direction is always adverse:
            - BUY  → price goes UP   (buyer pays more)
            - SELL → price goes DOWN  (seller receives less)
        """
        slip_pct = abs(random.gauss(0, self._slippage_bps / 10000))
        if signal == Signal.BUY:
            return price * (1 + slip_pct)
        return price * (1 - slip_pct)

    # ──────────────────────────────────────────────────────────
    # Trade Execution
    # ──────────────────────────────────────────────────────────

    def _execute_trade_sync(
        self, signal: Signal, symbol: str, price: float, amount: float
    ) -> dict[str, Any]:
        """Synchronous simulated trade execution."""
        quote_asset = symbol.split("/")[1]  # e.g., "USDT"
        base_asset = symbol.split("/")[0]   # e.g., "BTC"

        # Simulate realistic slippage FIRST (adversarial direction)
        exec_price = self._simulate_slippage(price, signal)

        # Cost and fee based on realistic execution price
        cost = amount * exec_price
        fee = cost * self._maker_fee
        price = exec_price  # Use slipped price for logging and balance updates

        quote_bal = self._get_balance_sync(quote_asset)
        base_bal = self._get_balance_sync(base_asset)

        if signal == Signal.BUY:
            total_cost = cost + fee
            if quote_bal["free"] < total_cost:
                return {
                    "status": "failed",
                    "reason": "insufficient_quote",
                    "required": total_cost,
                    "available": quote_bal["free"],
                    "filled": 0.0,
                    "chases": 0,
                }

            new_quote = quote_bal["free"] - total_cost
            new_base = base_bal["free"] + amount
            self._set_balance_sync(quote_asset, new_quote)
            self._set_balance_sync(base_asset, new_base)

        elif signal == Signal.SELL:
            if base_bal["free"] < amount:
                return {
                    "status": "failed",
                    "reason": "insufficient_base",
                    "required": amount,
                    "available": base_bal["free"],
                    "filled": 0.0,
                    "chases": 0,
                }

            new_base = base_bal["free"] - amount
            new_quote = quote_bal["free"] + (cost - fee)
            self._set_balance_sync(base_asset, new_base)
            self._set_balance_sync(quote_asset, new_quote)
        else:
            return {"status": "failed", "reason": "invalid_signal", "filled": 0.0, "chases": 0}

        # Record trade in history
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO paper_trades
               (timestamp, signal, symbol, price, amount, cost, fee,
                quote_balance_after, base_balance_after)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now,
                signal.value,
                symbol,
                price,
                amount,
                cost,
                fee,
                self._get_balance_sync(quote_asset)["free"],
                self._get_balance_sync(base_asset)["free"],
            ),
        )
        self._conn.commit()

        logger.info(
            f"📄 [PAPER FILL] {signal.value} {amount:.8f} {base_asset} "
            f"@ {price:.2f} | Cost: {cost:.2f} {quote_asset} | "
            f"Q_Bal: {self._get_balance_sync(quote_asset)['free']:.2f} | "
            f"B_Bal: {self._get_balance_sync(base_asset)['free']:.8f}"
        )

        return {
            "status": "filled",
            "filled": amount,
            "price": price,
            "cost": cost,
            "fee": fee,
            "chases": 0,
        }

    async def execute_simulated_trade(
        self, signal: Signal, symbol: str, price: float, amount: float
    ) -> dict[str, Any]:
        """Async simulated trade — primary interface for TradeExecutor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._execute_trade_sync, signal, symbol, price, amount
        )

    # Legacy compatibility alias
    async def execute_virtual_pegging(
        self, signal: Signal, amount: float, best_bid: float, best_ask: float
    ) -> dict[str, Any]:
        """Legacy interface — delegates to execute_simulated_trade."""
        price = best_bid if signal == Signal.BUY else best_ask
        # Infer symbol from standard pair (override in production)
        return await self.execute_simulated_trade(signal, "BTC/USDT", price, amount)

    # ──────────────────────────────────────────────────────────
    # Trade History Queries
    # ──────────────────────────────────────────────────────────

    async def get_trade_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent paper trades."""
        def _query() -> list[dict[str, Any]]:
            rows = self._conn.execute(
                "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?",
                (min(limit, 500),),
            ).fetchall()
            return [dict(r) for r in rows]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _query)

    async def get_pnl_summary(self) -> dict[str, Any]:
        """Calculate paper trading PnL summary."""
        def _calc() -> dict[str, Any]:
            row = self._conn.execute(
                """SELECT
                    COUNT(*) as total_trades,
                    COALESCE(SUM(CASE WHEN signal='BUY' THEN 1 ELSE 0 END), 0) as buys,
                    COALESCE(SUM(CASE WHEN signal='SELL' THEN 1 ELSE 0 END), 0) as sells,
                    COALESCE(SUM(fee), 0) as total_fees,
                    MIN(timestamp) as first_trade,
                    MAX(timestamp) as last_trade
                FROM paper_trades"""
            ).fetchone()

            quote_bal = self._get_balance_sync(self._quote_asset)

            return {
                "total_trades": row["total_trades"],
                "buys": row["buys"],
                "sells": row["sells"],
                "total_fees": row["total_fees"],
                "first_trade": row["first_trade"],
                "last_trade": row["last_trade"],
                "current_quote_balance": quote_bal["free"],
                "initial_balance": self._initial_quote,
                "unrealized_pnl": quote_bal["free"] - self._initial_quote,
            }

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _calc)

    # ──────────────────────────────────────────────────────────
    # Reset (for testing)
    # ──────────────────────────────────────────────────────────

    async def reset(self) -> None:
        """Wipe all paper data and re-seed initial balance."""
        def _reset() -> None:
            self._conn.execute("DELETE FROM paper_trades")
            self._conn.execute("DELETE FROM paper_balances")
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                """INSERT INTO paper_balances (asset, free, used, total, updated_at)
                   VALUES (?, ?, 0.0, ?, ?)""",
                (self._quote_asset, self._initial_quote, self._initial_quote, now),
            )
            self._conn.commit()
            logger.info("🔄 Paper Wallet reseteado a estado inicial")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _reset)

    # ──────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the paper wallet database."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        logger.info("🏦 Paper Wallet DB cerrado")
