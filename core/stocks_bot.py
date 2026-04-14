"""Stocks Trading Bot — Async loop for paper/live stock trading.

Processes a watchlist of symbols on a fixed interval,
feeds bars to StocksStrategy, and executes via StocksExchangeClientProtocol.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from core.stocks_exchange_client import (
    StocksExchangeClientProtocol,
    StocksClientError,
)
from core.stocks_strategy import (
    StocksStrategy,
    StockBar,
    StrategyDecision,
)

logger = logging.getLogger(__name__)


# ── Risk Config (simple) ───────────────────────────────────

@dataclass
class StocksRiskConfig:
    """Minimal risk guard for stocks trading."""
    max_position_qty: float = 10.0      # Max shares per order
    max_open_positions: int = 5         # Max concurrent positions
    max_daily_orders: int = 50          # Circuit breaker


# ── Bot Status ──────────────────────────────────────────────

@dataclass
class StocksBotStatus:
    """Snapshot of the bot's operational state."""
    running: bool = False
    paused: bool = False
    last_cycle_ts: str | None = None    # ISO-8601
    last_error: str | None = None
    total_cycles: int = 0
    daily_orders: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Bot ─────────────────────────────────────────────────────

class StocksBot:
    """Async trading bot for the Stocks domain.

    Loop:
        1. Fetch latest quote per symbol → build StockBar
        2. Feed bar to StocksStrategy → StrategyDecision
        3. Risk check (position size / daily limits)
        4. If buy/sell → create_order via stocks client
        5. Log trade to DB callback
        6. Sleep interval_sec
    """

    WATCHLIST: list[str] = ["AAPL", "MSFT", "TSLA"]

    def __init__(
        self,
        client: StocksExchangeClientProtocol,
        strategy: StocksStrategy,
        risk_config: StocksRiskConfig | None = None,
        interval_sec: float = 60.0,
        trade_logger: Any = None,  # callable(trade_dict) -> None
        watchlist: list[str] | None = None,
    ) -> None:
        self._client = client
        self._strategy = strategy
        self._risk = risk_config or StocksRiskConfig()
        self._interval = interval_sec
        self._trade_logger = trade_logger
        self._watchlist = watchlist or list(self.WATCHLIST)
        self._running = False
        self._paused = False
        self._daily_orders = 0
        self._status = StocksBotStatus()

    @property
    def watchlist(self) -> list[str]:
        return self._watchlist

    @watchlist.setter
    def watchlist(self, symbols: list[str]) -> None:
        self._watchlist = symbols

    @property
    def is_running(self) -> bool:
        return self._running

    # ── Control Methods ─────────────────────────────────────

    def get_status(self) -> StocksBotStatus:
        """Return current bot status snapshot."""
        self._status.running = self._running
        self._status.paused = self._paused
        self._status.daily_orders = self._daily_orders
        return self._status

    def pause(self) -> None:
        """Pause trading — loop continues but no new orders."""
        self._paused = True
        self._status.paused = True
        logger.info("⏸️  StocksBot PAUSED — no new orders until resume")

    def resume(self) -> None:
        """Resume trading after pause."""
        self._paused = False
        self._status.paused = False
        logger.info("▶️  StocksBot RESUMED")

    async def panic_stop(self) -> None:
        """Emergency stop: cancel open orders, close positions (stub), stop loop."""
        logger.warning("🚨 StocksBot PANIC STOP triggered")
        self._paused = True

        # Attempt to close open positions (best-effort)
        try:
            positions = await self._client.fetch_positions()
            for pos in positions:
                if pos.qty > 0:
                    side = "sell" if pos.side == "long" else "buy"
                    logger.info("🚨 Panic closing %s x%.1f", pos.symbol, pos.qty)
                    await self._client.create_order(
                        symbol=pos.symbol,
                        side=side,
                        qty=pos.qty,
                        order_type="market",
                    )
        except (StocksClientError, Exception) as e:
            logger.error("Panic close error: %s", e)
            self._status.last_error = f"panic close failed: {e}"

        self._running = False
        self._status.running = False
        logger.warning("🛑 StocksBot stopped via PANIC")

    def stop(self) -> None:
        """Signal the bot to stop after current iteration."""
        self._running = False
        self._status.running = False
        logger.info("🛑 StocksBot stop requested")

    # ── Main Loop ───────────────────────────────────────────

    async def run_forever(self) -> None:
        """Main trading loop — runs until stop() is called or circuit-breaker trips."""
        from config.settings import settings as _settings
        from core.market_hours import is_market_open, get_market_status

        self._running = True
        self._status.running = True
        max_errors = _settings.stocks.max_consecutive_errors
        consecutive_errors = 0
        _logged_market_closed = False

        logger.info(
            "StocksBot started | watchlist=%s | interval=%ds | max_errors=%d",
            self._watchlist, self._interval, max_errors,
        )

        while self._running:
            # ── Market hours guard ──
            if not is_market_open():
                if not _logged_market_closed:
                    status = get_market_status()
                    logger.info(
                        "Market closed — %s | %s",
                        status["next_event"], status["current_time_et"],
                    )
                    _logged_market_closed = True
                await asyncio.sleep(self._interval)
                continue
            _logged_market_closed = False

            try:
                await self._run_once()
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                logger.error(
                    "StocksBot iteration error (%d/%d): %s",
                    consecutive_errors, max_errors, e, exc_info=True,
                )
                self._status.last_error = str(e)[:200]

                if consecutive_errors >= max_errors:
                    msg = (
                        f"Circuit breaker: {consecutive_errors} consecutive errors, "
                        f"bot stopped"
                    )
                    logger.critical(msg)
                    self._status.last_error = msg
                    self._running = False
                    self._status.running = False
                    break

                backoff = min(2 ** consecutive_errors, 60)
                logger.warning("Backoff %ds before next iteration", backoff)
                await asyncio.sleep(backoff)
                continue

            # Update cycle timestamp
            self._status.last_cycle_ts = datetime.now(timezone.utc).isoformat()
            self._status.total_cycles += 1

            if self._running:
                await asyncio.sleep(self._interval)

        logger.info("StocksBot loop ended")

    async def _run_once(self) -> None:
        """Single iteration: scan all watchlist symbols.

        Raises if ALL symbols fail (indicating systemic issue like network outage).
        """
        errors = 0
        for symbol in self._watchlist:
            try:
                await self._process_symbol(symbol)
            except StocksClientError as e:
                logger.warning("StocksBot client error for %s: %s", symbol, e)
                self._status.last_error = f"{symbol}: {e}"
                errors += 1
            except Exception as e:
                logger.error("StocksBot unexpected error for %s: %s", symbol, e)
                self._status.last_error = f"{symbol}: {e}"
                errors += 1

        # If every symbol failed, propagate to run_forever for circuit-breaker
        if errors > 0 and errors == len(self._watchlist):
            raise StocksClientError(
                f"All {errors} symbols failed — possible network outage"
            )

    async def _process_symbol(self, symbol: str) -> None:
        """Fetch quote → strategy → risk → order → log."""
        # 1. PERCEPTION: Get latest quote
        quote = await self._client.fetch_quote(symbol)
        now = datetime.now(timezone.utc).isoformat()

        bar = StockBar(
            symbol=symbol,
            timestamp=quote.timestamp or now,
            open=quote.last,    # Simplified: use last as OHLC
            high=quote.ask,
            low=quote.bid,
            close=quote.last,
            volume=quote.volume,
        )

        # 2. STRATEGY: Evaluate
        decision = self._strategy.on_bar(bar)

        if decision.side == "hold":
            return

        # 3. PAUSED: Skip execution if paused
        if self._paused:
            logger.info("⏸️  Paused — skipping %s %s", decision.side, symbol)
            return

        # 4. RISK: Validate
        if not self._risk_check(decision):
            logger.info(
                "⚠️ Risk blocked %s %s (qty=%.1f): %s",
                decision.side, symbol, decision.qty_hint, decision.reason,
            )
            return

        # 5. EXECUTION: Place order
        qty = min(decision.qty_hint, self._risk.max_position_qty)
        logger.info(
            "📡 Executing %s %s x%.1f | reason: %s",
            decision.side.upper(), symbol, qty, decision.reason,
        )

        result = await self._client.create_order(
            symbol=symbol,
            side=decision.side,
            qty=qty,
            order_type="market",
        )

        self._daily_orders += 1

        # 6. LOGGING: Record trade
        trade_record = {
            "timestamp": now,
            "symbol": symbol,
            "side": decision.side,
            "price": result.filled_price or quote.last,
            "qty": qty,
            "order_id": result.order_id,
            "status": result.status,
            "reason": decision.reason,
        }

        logger.info(
            "✅ Order %s | %s %s x%.1f @ $%.2f | status=%s",
            result.order_id, decision.side.upper(), symbol, qty,
            trade_record["price"], result.status,
        )

        if self._trade_logger:
            try:
                self._trade_logger(trade_record)
            except Exception as e:
                logger.error("Trade logging error: %s", e)

    # ── Risk Check ──────────────────────────────────────────

    def _risk_check(self, decision: StrategyDecision) -> bool:
        """Simple risk gate: max qty, max daily orders."""
        if self._daily_orders >= self._risk.max_daily_orders:
            logger.warning("Daily order limit reached (%d)", self._risk.max_daily_orders)
            return False

        if decision.qty_hint > self._risk.max_position_qty:
            # Will be clamped in _process_symbol, but log it
            logger.info("Qty clamped from %.1f to %.1f", decision.qty_hint, self._risk.max_position_qty)

        return True
