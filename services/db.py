"""Database layer — DuckDB for analytics, SQLite for trade logs + audit + paper.

All paths resolved dynamically from config.settings (pathlib-based).
"""

from __future__ import annotations

import asyncio
import logging
import os
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

        # SQLite tables — Core
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

        # SQLite tables — Audit Logger
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'INFO',
                source TEXT NOT NULL DEFAULT 'unknown',
                action TEXT NOT NULL,
                detail TEXT DEFAULT '{}',
                error_trace TEXT DEFAULT ''
            )
        """)
        self._sqlite.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_ts ON action_logs(timestamp)
        """)
        self._sqlite.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_level ON action_logs(level)
        """)
        self._sqlite.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_source ON action_logs(source)
        """)

        # SQLite tables — Paper Wallet
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS paper_balances (
                asset TEXT PRIMARY KEY,
                free REAL NOT NULL DEFAULT 0.0,
                used REAL NOT NULL DEFAULT 0.0,
                total REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT
            )
        """)
        self._sqlite.execute("""
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
        """)

        # SQLite tables — Stocks Domain
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS stocks_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                qty REAL NOT NULL,
                order_id TEXT,
                status TEXT DEFAULT 'filled',
                pnl REAL DEFAULT 0.0,
                reason TEXT DEFAULT ''
            )
        """)
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS stocks_status (
                id INTEGER PRIMARY KEY DEFAULT 1,
                total_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0.0
            )
        """)
        self._sqlite.execute("""
            INSERT OR IGNORE INTO stocks_status (id) VALUES (1)
        """)

        # SQLite table — Stocks Config (single row)
        self._sqlite.execute("""
            CREATE TABLE IF NOT EXISTS stocks_config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                watchlist TEXT DEFAULT 'AAPL,MSFT,TSLA',
                ma_fast_window INTEGER DEFAULT 5,
                ma_slow_window INTEGER DEFAULT 20,
                signal_margin REAL DEFAULT 0.002,
                default_qty REAL DEFAULT 1.0,
                max_position_qty REAL DEFAULT 10.0,
                max_daily_trades INTEGER DEFAULT 50,
                updated_at TEXT DEFAULT ''
            )
        """)
        self._sqlite.execute("""
            INSERT OR IGNORE INTO stocks_config (id) VALUES (1)
        """)

        self._sqlite.commit()
        logger.info("✅ Database tables initialized (trades, audit, paper, stocks)")

    # ------------------------------------------------------------------
    # Trade Logging (SQLite)
    # ------------------------------------------------------------------

    async def log_trade(self, trade: dict[str, Any]) -> None:
        """Log a completed trade."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._log_trade_sync, trade)

    def _log_trade_sync(self, trade: dict[str, Any]) -> None:
        pnl = trade.get("pnl", 0.0)
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
                pnl,
            ),
        )
        self._sqlite.execute(
            """UPDATE bot_status
               SET total_trades = total_trades + 1,
                   total_pnl    = total_pnl + ?
               WHERE id = 1""",
            (pnl,),
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
    # Stock Trade Logging (SQLite — separate from crypto)
    # ------------------------------------------------------------------

    async def log_stock_trade(self, trade: dict[str, Any]) -> None:
        """Log a completed stock trade."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._log_stock_trade_sync, trade)

    def _log_stock_trade_sync(self, trade: dict[str, Any]) -> None:
        pnl = trade.get("pnl", 0.0)
        self._sqlite.execute(
            """INSERT INTO stocks_trades
               (timestamp, symbol, side, price, qty, order_id, status, pnl, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade["timestamp"],
                trade["symbol"],
                trade["side"],
                trade["price"],
                trade.get("qty", 0),
                trade.get("order_id", ""),
                trade.get("status", "filled"),
                pnl,
                trade.get("reason", ""),
            ),
        )
        self._sqlite.execute(
            """UPDATE stocks_status
               SET total_trades = total_trades + 1,
                   total_pnl    = total_pnl + ?
               WHERE id = 1""",
            (pnl,),
        )
        self._sqlite.commit()

    async def get_stock_trades(self, limit: int = 50) -> list[dict]:
        limit = min(limit, 500)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_stock_trades_sync, limit)

    def _get_stock_trades_sync(self, limit: int) -> list[dict]:
        rows = self._sqlite.execute(
            "SELECT * FROM stocks_trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    async def get_stocks_pnl_summary(self) -> dict:
        """Aggregate PnL for stocks domain."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_stocks_pnl_sync)

    def _get_stocks_pnl_sync(self) -> dict:
        row = self._sqlite.execute(
            "SELECT total_trades, total_pnl FROM stocks_status WHERE id = 1"
        ).fetchone()
        total_trades = row["total_trades"] if row else 0
        total_pnl = row["total_pnl"] if row else 0.0

        # Win rate from stocks_trades
        wins_row = self._sqlite.execute(
            "SELECT COUNT(*) as wins FROM stocks_trades WHERE pnl > 0"
        ).fetchone()
        wins = wins_row["wins"] if wins_row else 0
        win_rate = (wins / total_trades) if total_trades > 0 else 0.0

        # Today's stats
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_row = self._sqlite.execute(
            """SELECT COALESCE(SUM(pnl), 0) as today_pnl,
                      COUNT(*) as today_trades
               FROM stocks_trades WHERE timestamp >= ?""",
            (today,),
        ).fetchone()

        return {
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 4),
            "today_trades": today_row["today_trades"] if today_row else 0,
            "today_pnl": round(today_row["today_pnl"], 2) if today_row else 0.0,
        }

    # ------------------------------------------------------------------
    # Stock Config (SQLite — single row)
    # ------------------------------------------------------------------

    _STOCKS_CONFIG_DEFAULTS = {
        "watchlist": "AAPL,MSFT,TSLA",
        "ma_fast_window": 5,
        "ma_slow_window": 20,
        "signal_margin": 0.002,
        "default_qty": 1.0,
        "max_position_qty": 10.0,
        "max_daily_trades": 50,
    }

    def get_stocks_config(self) -> dict:
        """Read stocks config (sync). Returns defaults if empty."""
        row = self._sqlite.execute(
            "SELECT * FROM stocks_config WHERE id = 1"
        ).fetchone()
        if row is None:
            return dict(self._STOCKS_CONFIG_DEFAULTS)
        d = dict(row)
        d.pop("id", None)
        return d

    def upsert_stocks_config(self, cfg: dict) -> None:
        """Insert-or-update stocks config (sync)."""
        now = datetime.now(timezone.utc).isoformat()
        self._sqlite.execute("""
            INSERT INTO stocks_config (id, watchlist, ma_fast_window, ma_slow_window,
                                       signal_margin, default_qty, max_position_qty,
                                       max_daily_trades, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                watchlist = excluded.watchlist,
                ma_fast_window = excluded.ma_fast_window,
                ma_slow_window = excluded.ma_slow_window,
                signal_margin = excluded.signal_margin,
                default_qty = excluded.default_qty,
                max_position_qty = excluded.max_position_qty,
                max_daily_trades = excluded.max_daily_trades,
                updated_at = excluded.updated_at
        """, (
            cfg.get("watchlist", self._STOCKS_CONFIG_DEFAULTS["watchlist"]),
            int(cfg.get("ma_fast_window", self._STOCKS_CONFIG_DEFAULTS["ma_fast_window"])),
            int(cfg.get("ma_slow_window", self._STOCKS_CONFIG_DEFAULTS["ma_slow_window"])),
            float(cfg.get("signal_margin", self._STOCKS_CONFIG_DEFAULTS["signal_margin"])),
            float(cfg.get("default_qty", self._STOCKS_CONFIG_DEFAULTS["default_qty"])),
            float(cfg.get("max_position_qty", self._STOCKS_CONFIG_DEFAULTS["max_position_qty"])),
            int(cfg.get("max_daily_trades", self._STOCKS_CONFIG_DEFAULTS["max_daily_trades"])),
            now,
        ))
        self._sqlite.commit()

    # ------------------------------------------------------------------
    # Daily PnL Aggregates (for backfill / reporting)
    # ------------------------------------------------------------------

    def get_crypto_daily_pnl(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """Aggregate crypto trades by day → list of {date, total_pnl, total_trades, win_rate}."""
        query = """
            SELECT
                SUBSTR(timestamp, 1, 10) AS date,
                SUM(pnl)                 AS total_pnl,
                COUNT(*)                 AS total_trades,
                ROUND(CAST(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS REAL)
                      / MAX(COUNT(*), 1), 4) AS win_rate
            FROM trades
            WHERE 1=1
        """
        params: list = []
        if start_date:
            query += " AND SUBSTR(timestamp, 1, 10) >= ?"
            params.append(start_date)
        if end_date:
            query += " AND SUBSTR(timestamp, 1, 10) <= ?"
            params.append(end_date)
        query += " GROUP BY SUBSTR(timestamp, 1, 10) ORDER BY date ASC"

        rows = self._sqlite.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_stocks_daily_pnl(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """Aggregate stock trades by day → list of {date, total_pnl, total_trades, win_rate}."""
        query = """
            SELECT
                SUBSTR(timestamp, 1, 10) AS date,
                SUM(pnl)                 AS total_pnl,
                COUNT(*)                 AS total_trades,
                ROUND(CAST(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS REAL)
                      / MAX(COUNT(*), 1), 4) AS win_rate
            FROM stocks_trades
            WHERE 1=1
        """
        params: list = []
        if start_date:
            query += " AND SUBSTR(timestamp, 1, 10) >= ?"
            params.append(start_date)
        if end_date:
            query += " AND SUBSTR(timestamp, 1, 10) <= ?"
            params.append(end_date)
        query += " GROUP BY SUBSTR(timestamp, 1, 10) ORDER BY date ASC"

        rows = self._sqlite.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Audit Log Queries (SQLite)
    # ------------------------------------------------------------------

    async def get_action_logs(
        self,
        limit: int = 100,
        level: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """Query action logs with optional filters."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._get_action_logs_sync, limit, level, source
        )

    def _get_action_logs_sync(
        self, limit: int, level: str | None, source: str | None
    ) -> list[dict]:
        query = "SELECT * FROM action_logs WHERE 1=1"
        params: list[Any] = []

        if level:
            query += " AND level = ?"
            params.append(level)
        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(min(limit, 1000))

        rows = self._sqlite.execute(query, params).fetchall()
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
    # Performance Metrics (SQLite → metrics module)
    # ------------------------------------------------------------------

    async def get_performance_summary(self) -> dict:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_performance_sync)

    def _get_performance_sync(self) -> dict:
        from services.metrics import compute_win_rate, compute_max_drawdown, build_equity_series

        # bot_status totals
        status = self._sqlite.execute(
            "SELECT total_trades, total_pnl FROM bot_status WHERE id = 1"
        ).fetchone()

        # All trades (chronological) for win rate + equity curve
        rows = self._sqlite.execute(
            "SELECT pnl FROM trades ORDER BY id ASC"
        ).fetchall()
        trades = [{"pnl": r["pnl"]} for r in rows]

        win_rate = compute_win_rate(trades)

        # Build equity series from initial balance (paper default 1000)
        initial = float(os.getenv("PAPER_TRADING_INITIAL_BALANCE", "1000"))
        equity = build_equity_series(initial, trades)
        max_dd = compute_max_drawdown(equity)

        return {
            "total_trades": status["total_trades"],
            "total_pnl": round(status["total_pnl"], 4),
            "win_rate": round(win_rate, 4),
            "max_drawdown": round(max_dd, 4),
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
    # Datalake Export (Hot/Cold Storage)
    # ------------------------------------------------------------------

    async def export_historical_datalake(self, export_dir: str = "data/export") -> str:
        """Export historical data to Parquet for Cold Storage (Hosting/S3).
        Truncates the local SQLite and DuckDB tables afterwards to save disk on Ubuntu MiniPC.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._export_sync, export_dir)

    def _export_sync(self, export_dir: str) -> str:
        import os
        from pathlib import Path
        Path(export_dir).mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_path = f"{export_dir}/trades_{timestamp}.parquet"
        
        # Pull data from SQLite to DuckDB and write to Parquet
        # First, connect SQLite to DuckDB
        try:
            self._duck.execute("INSTALL sqlite;")
            self._duck.execute("LOAD sqlite;")
            sqlite_db_path = str(settings.database.sqlite_path)
            # Create a table view of SQLite inside DuckDB, write to Parquet
            query = f"COPY (SELECT * FROM sqlite_scan('{sqlite_db_path}', 'trades')) TO '{export_path}' (FORMAT PARQUET);"
            self._duck.execute(query)
            
            # Now prune local SQLite (keep only last 100)
            self._sqlite.execute(
                "DELETE FROM trades WHERE id NOT IN (SELECT id FROM trades ORDER BY timestamp DESC LIMIT 100)"
            )
            self._sqlite.commit()
            
            logger.info(f"Datalake export complete: {export_path}")
            return export_path
        except Exception as e:
            logger.error(f"Error exporting datalake: {e}")
            return ""

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._duck.close()
        self._sqlite.close()
        logger.info("Databases closed")
