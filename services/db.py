"""Database layer — DuckDB for analytics, SQLite for trade logs.

All paths resolved dynamically from config.settings (pathlib-based).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import duckdb

from config.settings import settings

logger = logging.getLogger(__name__)


class Database:
    """Dual-database manager: DuckDB (OLAP) + SQLite (OLTP)."""

    def __init__(self) -> None:
        # Resolve absolute paths from settings (pathlib)
        duck_path = settings.database.duckdb_path
        sqlite_path = settings.database.sqlite_path

        # Ensure parent directories exist
        duck_path.parent.mkdir(parents=True, exist_ok=True)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"📂 DuckDB:  {duck_path}")
        logger.info(f"📂 SQLite:  {sqlite_path}")

        # DuckDB — analytics (columnar, fast aggregations)
        self._duck = duckdb.connect(str(duck_path))

        # SQLite — trade logs (transactional, lightweight)
        self._sqlite = sqlite3.connect(
            str(sqlite_path), check_same_thread=False
        )
        self._sqlite.row_factory = sqlite3.Row

        # WAL mode for better concurrent read performance
        self._sqlite.execute("PRAGMA journal_mode=WAL")
        self._sqlite.execute("PRAGMA busy_timeout=5000")

        self._init_tables()

    def _init_tables(self) -> None:
        # DuckDB tables
        self._duck.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_cache (
                symbol VARCHAR,
                timeframe VARCHAR,
                timestamp TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                PRIMARY KEY (symbol, timeframe, timestamp)
            )
        """)
        self._duck.execute("""
            CREATE TABLE IF NOT EXISTS balance_snapshots (
                id INTEGER,
                asset VARCHAR,
                free DOUBLE,
                total DOUBLE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._duck.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_balance START 1
        """)

        # SQLite tables
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                order_id TEXT,
                pnl REAL DEFAULT 0.0
            )
        """)
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS bot_status (
                id INTEGER PRIMARY KEY DEFAULT 1,
                state TEXT DEFAULT 'stopped',
                started_at TEXT,
                last_heartbeat TEXT,
                last_error TEXT,
                total_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0.0
            )
        """)
        self._sqlite.execute("""
            INSERT OR IGNORE INTO bot_status (id) VALUES (1)
        """)
        self._sqlite.commit()
        logger.info("✅ Database tables initialized")

    # ------------------------------------------------------------------
    # Trade Logging (SQLite)
    # ------------------------------------------------------------------

    async def log_trade(self, trade: dict[str, Any]) -> None:
        """Log a completed trade."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._log_trade_sync, trade)

    def _log_trade_sync(self, trade: dict[str, Any]) -> None:
        self._sqlite.execute(
            """INSERT INTO trades (timestamp, symbol, side, price, amount, order_id, pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["timestamp"],
                trade["symbol"],
                trade["side"],
                trade["price"],
                trade["amount"],
                trade.get("order_id", ""),
                trade.get("pnl", 0.0),
            ),
        )
        self._sqlite.execute(
            "UPDATE bot_status SET total_trades = total_trades + 1 WHERE id = 1"
        )
        self._sqlite.commit()

    async def get_trades(self, limit: int = 50) -> list[dict]:
        limit = min(limit, 500)  # Cap to prevent abuse
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_trades_sync, limit)

    def _get_trades_sync(self, limit: int) -> list[dict]:
        rows = self._sqlite.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # PnL Queries (SQLite + DuckDB)
    # ------------------------------------------------------------------

    async def get_pnl_summary(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_pnl_sync)

    def _get_pnl_sync(self) -> dict:
        row = self._sqlite.execute(
            "SELECT total_trades, total_pnl FROM bot_status WHERE id = 1"
        ).fetchone()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_row = self._sqlite.execute(
            """SELECT COALESCE(SUM(pnl), 0) as today_pnl,
                      COUNT(*) as today_trades
               FROM trades WHERE timestamp >= ?""",
            (today,),
        ).fetchone()
        return {
            "total_trades": row["total_trades"],
            "total_pnl": row["total_pnl"],
            "today_pnl": today_row["today_pnl"],
            "today_trades": today_row["today_trades"],
        }

    # ------------------------------------------------------------------
    # Balance Snapshots (DuckDB)
    # ------------------------------------------------------------------

    async def save_balance_snapshot(self, asset: str, free: float,
                                    total: float) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._save_balance_sync, asset, free, total
        )

    def _save_balance_sync(self, asset: str, free: float, total: float) -> None:
        self._duck.execute(
            """INSERT INTO balance_snapshots (id, asset, free, total, timestamp)
               VALUES (nextval('seq_balance'), ?, ?, ?, CURRENT_TIMESTAMP)""",
            (asset, free, total),
        )

    async def get_equity_curve(self, limit: int = 100) -> list[dict]:
        limit = min(limit, 1000)  # Cap
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_equity_sync, limit)

    def _get_equity_sync(self, limit: int) -> list[dict]:
        result = self._duck.execute(
            """SELECT timestamp, total FROM balance_snapshots
               ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"timestamp": str(r[0]), "total": r[1]}
            for r in reversed(result)
        ]

    # ------------------------------------------------------------------
    # Bot Status (SQLite) — sanitized output
    # ------------------------------------------------------------------

    async def update_status(self, state: str, error: str | None = None) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._update_status_sync, state, error)

    def _update_status_sync(self, state: str, error: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if state == "running":
            self._sqlite.execute(
                """UPDATE bot_status SET state=?, started_at=?,
                   last_heartbeat=? WHERE id=1""",
                (state, now, now),
            )
        else:
            self._sqlite.execute(
                """UPDATE bot_status SET state=?, last_heartbeat=?,
                   last_error=? WHERE id=1""",
                (state, now, error),
            )
        self._sqlite.commit()

    async def heartbeat(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._heartbeat_sync)

    def _heartbeat_sync(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._sqlite.execute(
            "UPDATE bot_status SET last_heartbeat=? WHERE id=1", (now,)
        )
        self._sqlite.commit()

    async def get_status(self) -> dict:
        """Return bot status, sanitized for API exposure."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_status_sync)

    def _get_status_sync(self) -> dict:
        row = self._sqlite.execute(
            "SELECT state, started_at, last_heartbeat, total_trades, total_pnl "
            "FROM bot_status WHERE id=1"
        ).fetchone()
        return dict(row) if row else {}

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._duck.close()
        self._sqlite.close()
        logger.info("Databases closed")
