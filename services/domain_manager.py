"""DomainManager — runtime lifecycle control for trading domains.

Allows starting/stopping StocksBot from the dashboard REST API
without restarting the process. Uses asyncio.create_task — no threads.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Domain statuses
_STATUS_RUNNING = "running"
_STATUS_STOPPED = "stopped"
_STATUS_ERROR   = "error"


class DomainManager:
    """Singleton that manages asyncio.Task lifecycle per trading domain."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._bots: dict[str, Any] = {}
        self._errors: dict[str, str] = {}

    # ── Public API ─────────────────────────────────────────────

    async def start_domain(self, domain: str) -> tuple[bool, str]:
        """Start a domain bot as a background asyncio.Task.

        Returns:
            (started: bool, message: str)
        """
        if domain in self._tasks and not self._tasks[domain].done():
            return False, "already_running"

        try:
            if domain == "stocks":
                task, bot = await self._create_stocks_task()
            else:
                return False, f"domain '{domain}' not supported via DomainManager"

            self._tasks[domain] = task
            self._bots[domain] = bot
            self._errors.pop(domain, None)
            logger.info(f"🚀 DomainManager: started '{domain}'")
            return True, "started"

        except Exception as e:
            self._errors[domain] = str(e)
            logger.error(f"DomainManager: failed to start '{domain}': {e}")
            return False, str(e)

    async def stop_domain(self, domain: str) -> tuple[bool, str]:
        """Cancel a running domain task.

        Returns:
            (stopped: bool, message: str)
        """
        task = self._tasks.get(domain)
        if task is None or task.done():
            return False, "not_running"

        # Ask bot to stop gracefully first
        bot = self._bots.get(domain)
        if bot is not None and hasattr(bot, "stop"):
            try:
                bot.stop()
            except Exception:
                pass

        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        self._tasks.pop(domain, None)
        self._bots.pop(domain, None)

        # Update stocks_runtime so API endpoints reflect new state
        if domain == "stocks":
            from services.stocks_runtime import set_stocks_bot
            set_stocks_bot(None)

        logger.info(f"⏹ DomainManager: stopped '{domain}'")
        return True, "stopped"

    def get_status(self, domain: str) -> str:
        """Return 'running', 'stopped', or 'error'."""
        if domain in self._errors and domain not in self._tasks:
            return _STATUS_ERROR
        task = self._tasks.get(domain)
        if task is None or task.done():
            return _STATUS_STOPPED
        return _STATUS_RUNNING

    def get_all_status(self) -> dict[str, str]:
        """Return status for all known domains."""
        return {
            "crypto": _STATUS_RUNNING,   # always running (started by run_bot.py)
            "stocks": self.get_status("stocks"),
            "sports": "unavailable",
        }

    # ── Private helpers ────────────────────────────────────────

    async def _create_stocks_task(self) -> tuple[asyncio.Task, Any]:
        """Instantiate StocksBot and wrap run_forever() in a Task."""
        from config.settings import settings
        from core.stocks_exchange_client import create_stocks_client
        from core.stocks_strategy import StocksStrategy
        from core.stocks_bot import StocksBot, StocksRiskConfig
        from services.stocks_runtime import set_stocks_bot
        from services.api_server import _db  # shared DB reference

        if _db is None:
            raise RuntimeError("Database not initialized — start the bot first")

        stocks_client = create_stocks_client(settings.stocks)
        cfg = _db.get_stocks_config()
        strategy = StocksStrategy.from_db_config(cfg)
        watchlist = [s.strip() for s in cfg.get("watchlist", "AAPL,MSFT,TSLA").split(",") if s.strip()]
        risk_config = StocksRiskConfig(
            max_position_qty=float(cfg.get("max_position_qty", 10.0)),
            max_daily_orders=int(cfg.get("max_daily_trades", 50)),
        )

        def trade_logger(trade: dict) -> None:
            _db._log_stock_trade_sync(trade)

        bot = StocksBot(
            client=stocks_client,
            strategy=strategy,
            risk_config=risk_config,
            interval_sec=60.0,
            trade_logger=trade_logger,
            watchlist=watchlist,
        )
        set_stocks_bot(bot)

        task = asyncio.create_task(bot.run_forever(), name=f"domain-stocks")
        return task, bot


# Singleton — imported by api_server.py
domain_manager = DomainManager()
