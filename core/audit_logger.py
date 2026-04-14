"""Fail-Safe Audit Logger — Structured action tracking for Q-Trader.

Provides persistent, queryable audit trail of every bot decision,
trade execution, error, and state transition. Writes to SQLite
asynchronously with file fallback if DB fails.

Architecture:
    - AuditEvent dataclass → structured log entry
    - @audit_action decorator → auto-instrument any async function
    - Dual sink: SQLite (primary) + file (fallback)
    - Non-blocking: all I/O via run_in_executor
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import sqlite3
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Structured Event
# ──────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """Immutable record of a single auditable action."""

    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    level: str = "INFO"           # INFO | WARNING | ERROR | CRITICAL
    source: str = "unknown"       # Module/class originating the event
    action: str = "GENERIC"       # SIGNAL_GENERATED, ORDER_PLACED, CIRCUIT_BREAKER, etc.
    detail: str = "{}"            # JSON-serializable context payload
    error_trace: str = ""         # Full traceback string if applicable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ──────────────────────────────────────────────────────────────
# Audit Logger Core
# ──────────────────────────────────────────────────────────────

class AuditLogger:
    """Centralized, fail-safe audit logging engine.

    Primary sink: SQLite `action_logs` table (queryable).
    Fallback sink: `data/audit_fallback.log` (append-only text).
    All writes are non-blocking via asyncio.run_in_executor.
    """

    _TABLE_DDL = """
        CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'INFO',
            source TEXT NOT NULL DEFAULT 'unknown',
            action TEXT NOT NULL,
            detail TEXT DEFAULT '{}',
            error_trace TEXT DEFAULT ''
        )
    """
    _INDEX_DDL = """
        CREATE INDEX IF NOT EXISTS idx_logs_ts ON action_logs(timestamp);
        CREATE INDEX IF NOT EXISTS idx_logs_level ON action_logs(level);
        CREATE INDEX IF NOT EXISTS idx_logs_source ON action_logs(source);
    """
    _INSERT_SQL = """
        INSERT INTO action_logs (timestamp, level, source, action, detail, error_trace)
        VALUES (?, ?, ?, ?, ?, ?)
    """

    def __init__(self, db_path: Path, fallback_path: Path | None = None) -> None:
        self._db_path = db_path
        self._fallback_path = fallback_path or db_path.parent / "audit_fallback.log"

        # Ensure directories exist
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize SQLite connection (thread-safe for executor)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

        logger.info(
            f"📋 AuditLogger inicializado → DB: {self._db_path} | "
            f"Fallback: {self._fallback_path}"
        )

    def _init_db(self) -> None:
        """Create or open the audit database and ensure schema exists."""
        try:
            self._conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute(self._TABLE_DDL)
            # Execute index creation statements individually
            for stmt in self._INDEX_DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    self._conn.execute(stmt)
            self._conn.commit()
        except Exception as e:
            logger.error(f"AuditLogger: SQLite init failed, using fallback only: {e}")
            self._conn = None

    # ──────────────────────────────────────────────────────────
    # Write Methods
    # ──────────────────────────────────────────────────────────

    def _write_sync(self, event: AuditEvent) -> None:
        """Synchronous write — called inside executor thread."""
        # Console echo (always)
        log_fn = getattr(logger, event.level.lower(), logger.info)
        log_fn(
            f"[AUDIT] {event.action} | {event.source} | "
            f"{event.detail[:200]}"
        )

        # Primary: SQLite
        if self._conn is not None:
            try:
                self._conn.execute(
                    self._INSERT_SQL,
                    (
                        event.timestamp,
                        event.level,
                        event.source,
                        event.action,
                        event.detail,
                        event.error_trace,
                    ),
                )
                self._conn.commit()
                return  # Success — skip fallback
            except Exception as e:
                logger.warning(f"AuditLogger: SQLite write failed, falling back: {e}")

        # Fallback: File append
        try:
            with open(self._fallback_path, "a", encoding="utf-8") as f:
                f.write(event.to_json() + "\n")
        except Exception as e:
            logger.error(f"AuditLogger: BOTH sinks failed! Event lost: {e}")

    async def log(self, event: AuditEvent) -> None:
        """Non-blocking async write."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_sync, event)

    # ──────────────────────────────────────────────────────────
    # Convenience Methods
    # ──────────────────────────────────────────────────────────

    async def log_action(
        self,
        source: str,
        action: str,
        detail: dict[str, Any] | None = None,
        level: str = "INFO",
    ) -> None:
        """Quick structured log entry."""
        event = AuditEvent(
            level=level,
            source=source,
            action=action,
            detail=json.dumps(detail or {}, default=str, ensure_ascii=False),
        )
        await self.log(event)

    async def log_error(
        self,
        source: str,
        action: str,
        exception: Exception,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Log an error with full traceback."""
        event = AuditEvent(
            level="ERROR",
            source=source,
            action=action,
            detail=json.dumps(
                {**(detail or {}), "error_type": type(exception).__name__, "error_msg": str(exception)},
                default=str,
                ensure_ascii=False,
            ),
            error_trace=traceback.format_exc(),
        )
        await self.log(event)

    async def log_state_transition(
        self,
        source: str,
        from_state: str,
        to_state: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log a state machine transition (LangGraph-ready)."""
        await self.log_action(
            source=source,
            action="STATE_TRANSITION",
            detail={
                "from": from_state,
                "to": to_state,
                **(context or {}),
            },
        )

    # ──────────────────────────────────────────────────────────
    # Query Methods
    # ──────────────────────────────────────────────────────────

    def _query_sync(
        self,
        limit: int = 100,
        level: str | None = None,
        source: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Synchronous query — called inside executor."""
        if self._conn is None:
            return []

        query = "SELECT * FROM action_logs WHERE 1=1"
        params: list[Any] = []

        if level:
            query += " AND level = ?"
            params.append(level)
        if source:
            query += " AND source = ?"
            params.append(source)
        if action:
            query += " AND action LIKE ?"
            params.append(f"%{action}%")

        query += " ORDER BY id DESC LIMIT ?"
        params.append(min(limit, 1000))

        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    async def get_logs(
        self,
        limit: int = 100,
        level: str | None = None,
        source: str | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """Async query with filters."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._query_sync, limit, level, source, action
        )

    # ──────────────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the audit database connection."""
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        logger.info("📋 AuditLogger cerrado")


# ──────────────────────────────────────────────────────────────
# Decorator for automatic action auditing
# ──────────────────────────────────────────────────────────────

def audit_action(
    action_name: str,
    source: str = "auto",
    level: str = "INFO",
):
    """Decorator to auto-log async function calls with args and result/error.

    Usage:
        @audit_action("ORDER_PLACED", source="OrderManager")
        async def place_order(self, symbol, amount):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # Resolve audit_logger from self._audit or first AuditLogger arg
            audit: AuditLogger | None = None
            if args and hasattr(args[0], "_audit"):
                audit = getattr(args[0], "_audit")
            elif args and hasattr(args[0], "audit_logger"):
                audit = getattr(args[0], "audit_logger")

            resolved_source = source
            if resolved_source == "auto" and args:
                resolved_source = type(args[0]).__name__

            try:
                result = await fn(*args, **kwargs)
                if audit:
                    await audit.log_action(
                        source=resolved_source,
                        action=f"{action_name}_OK",
                        detail={
                            "args": str(kwargs)[:300],
                            "result_preview": str(result)[:200] if result else "None",
                        },
                        level=level,
                    )
                return result
            except Exception as exc:
                if audit:
                    await audit.log_error(
                        source=resolved_source,
                        action=f"{action_name}_FAILED",
                        exception=exc,
                        detail={"args": str(kwargs)[:300]},
                    )
                raise

        return wrapper

    return decorator
