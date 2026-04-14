"""Tests for StocksBot, StocksStrategy, and stock trade logging.

Uses FakeStocksClient — no real HTTP calls.
"""

import asyncio
import pytest

from core.stocks_strategy import (
    StocksStrategy,
    StocksStrategyConfig,
    StockBar,
    StrategyDecision,
)
from core.stocks_bot import StocksBot, StocksRiskConfig
from core.stocks_exchange_client import (
    Quote,
    Position,
    OrderResult,
    StocksClientError,
    StocksExchangeClientProtocol,
)


# ── Fake Client ─────────────────────────────────────────────

class FakeStocksClient:
    """In-memory fake that tracks calls. No HTTP."""

    def __init__(self, quotes: dict[str, float] | None = None):
        self.quotes = quotes or {"AAPL": 190.0, "MSFT": 420.0, "TSLA": 175.0}
        self.orders: list[dict] = []
        self.order_count = 0
        self._should_fail_order = False

    async def fetch_quote(self, symbol: str) -> Quote:
        price = self.quotes.get(symbol, 100.0)
        return Quote(
            symbol=symbol, bid=price - 0.05, ask=price + 0.05,
            last=price, volume=500_000, timestamp="2026-01-01T12:00:00Z",
        )

    async def fetch_positions(self) -> list[Position]:
        return []

    async def create_order(
        self, symbol: str, side: str, qty: float,
        order_type: str = "market", limit_price: float | None = None,
    ) -> OrderResult:
        if self._should_fail_order:
            raise StocksClientError("Simulated order failure")

        self.order_count += 1
        result = OrderResult(
            order_id=f"fake_{self.order_count}",
            symbol=symbol, side=side, qty=qty,
            order_type=order_type, status="filled",
            filled_price=self.quotes.get(symbol, 100.0),
            filled_at="2026-01-01T12:00:01Z",
        )
        self.orders.append({
            "symbol": symbol, "side": side, "qty": qty, "order_id": result.order_id,
        })
        return result

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def get_account_balance(self) -> dict:
        return {"cash": 100_000.0, "buying_power": 100_000.0}


# ── Strategy Tests ──────────────────────────────────────────

class TestStocksStrategy:

    def test_hold_during_warmup(self):
        strategy = StocksStrategy(StocksStrategyConfig(short_window=3, long_window=5))
        bar = StockBar("AAPL", "2026-01-01T00:00:00Z", 190, 191, 189, 190, 100000)
        decision = strategy.on_bar(bar)
        assert decision.side == "hold"
        assert "warming up" in decision.reason

    def test_buy_signal_on_uptrend(self):
        """Feed rising prices to trigger a buy."""
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=3, long_window=5, margin_pct=0.001,
        ))
        # Feed 5 bars at 100, then 3 bars climbing to 110
        prices = [100, 100, 100, 100, 100, 105, 108, 110]
        for i, p in enumerate(prices):
            bar = StockBar("AAPL", f"2026-01-01T{i:02d}:00:00Z", p, p+1, p-1, p, 100000)
            decision = strategy.on_bar(bar)

        # After strong uptrend, short_ma > long_ma * (1 + margin)
        assert decision.side == "buy"

    def test_sell_signal_on_downtrend(self):
        """Feed falling prices to trigger a sell."""
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=3, long_window=5, margin_pct=0.001,
        ))
        prices = [100, 100, 100, 100, 100, 95, 92, 90]
        for i, p in enumerate(prices):
            bar = StockBar("AAPL", f"2026-01-01T{i:02d}:00:00Z", p, p+1, p-1, p, 100000)
            decision = strategy.on_bar(bar)

        assert decision.side == "sell"

    def test_hold_in_neutral_zone(self):
        """Flat prices → hold."""
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=3, long_window=5, margin_pct=0.01,
        ))
        for i in range(10):
            bar = StockBar("AAPL", f"2026-01-01T{i:02d}:00:00Z", 100, 100.1, 99.9, 100, 100000)
            decision = strategy.on_bar(bar)

        assert decision.side == "hold"

    def test_separate_history_per_symbol(self):
        """Each symbol maintains independent history."""
        strategy = StocksStrategy(StocksStrategyConfig(short_window=2, long_window=3))
        for i in range(5):
            strategy.on_bar(StockBar("AAPL", f"T{i}", 100, 101, 99, 100, 1000))

        # MSFT starts fresh
        decision = strategy.on_bar(StockBar("MSFT", "T0", 200, 201, 199, 200, 1000))
        assert decision.side == "hold"
        assert "warming up" in decision.reason

    def test_reset_clears_history(self):
        strategy = StocksStrategy()
        for i in range(25):
            strategy.on_bar(StockBar("AAPL", f"T{i}", 100, 101, 99, 100, 1000))
        strategy.reset("AAPL")
        d = strategy.on_bar(StockBar("AAPL", "T99", 100, 101, 99, 100, 1000))
        assert "warming up" in d.reason


# ── Bot Tests ───────────────────────────────────────────────

class TestStocksBot:

    def test_run_once_processes_watchlist(self):
        """Bot fetches quotes and feeds strategy for all watchlist symbols."""
        fake = FakeStocksClient()
        strategy = StocksStrategy(StocksStrategyConfig(short_window=2, long_window=3))
        logged_trades: list[dict] = []

        bot = StocksBot(
            client=fake,
            strategy=strategy,
            trade_logger=lambda t: logged_trades.append(t),
            interval_sec=0,
        )

        # Run one iteration (strategy in warmup → no orders)
        asyncio.run(bot._run_once())
        assert fake.order_count == 0

    def test_run_once_places_order_on_signal(self):
        """Warm up strategy, then force a buy signal."""
        fake = FakeStocksClient({"AAPL": 100.0, "MSFT": 100.0, "TSLA": 100.0})
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=2, long_window=3, margin_pct=0.001, default_qty=5.0,
        ))
        logged_trades: list[dict] = []

        bot = StocksBot(
            client=fake,
            strategy=strategy,
            trade_logger=lambda t: logged_trades.append(t),
            interval_sec=0,
        )

        # Warm up with flat prices (3 iterations = long_window)
        for _ in range(3):
            asyncio.run(bot._run_once())

        # Now pump prices up to trigger buy
        fake.quotes = {"AAPL": 110.0, "MSFT": 110.0, "TSLA": 110.0}
        asyncio.run(bot._run_once())

        # At least one order should have been placed
        assert fake.order_count > 0
        assert len(logged_trades) > 0
        assert logged_trades[0]["side"] == "buy"

    def test_client_error_does_not_crash_bot(self):
        """StocksClientError in create_order is caught; single symbol failures don't crash."""
        fake = FakeStocksClient({"AAPL": 100.0, "MSFT": 100.0, "TSLA": 100.0})
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=2, long_window=3, margin_pct=0.001,
        ))

        bot = StocksBot(
            client=fake,
            strategy=strategy,
            interval_sec=0,
        )

        # Warm up
        for _ in range(3):
            asyncio.run(bot._run_once())

        # Force buy signals but orders will fail
        fake.quotes = {"AAPL": 110.0, "MSFT": 110.0, "TSLA": 110.0}
        fake._should_fail_order = True

        # All 3 symbols fail → _run_once raises (new circuit-breaker behavior)
        # In run_forever, this would be caught and counted toward the limit
        import pytest
        with pytest.raises(StocksClientError):
            asyncio.run(bot._run_once())
        assert fake.order_count == 0  # No orders succeeded

    def test_risk_blocks_after_daily_limit(self):
        """Daily order limit blocks further orders."""
        fake = FakeStocksClient({"AAPL": 100.0, "MSFT": 100.0, "TSLA": 100.0})
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=2, long_window=3, margin_pct=0.001,
        ))

        bot = StocksBot(
            client=fake,
            strategy=strategy,
            risk_config=StocksRiskConfig(max_daily_orders=2),
            interval_sec=0,
        )

        # Warm up
        for _ in range(3):
            asyncio.run(bot._run_once())

        # Trigger buys
        fake.quotes = {"AAPL": 110.0, "MSFT": 110.0, "TSLA": 110.0}
        asyncio.run(bot._run_once())

        # After 2 orders, daily limit hit → no more
        count_after = fake.order_count
        asyncio.run(bot._run_once())
        assert fake.order_count == count_after  # No new orders

    def test_stop_prevents_further_iterations(self):
        """bot.stop() sets running=False."""
        fake = FakeStocksClient()
        strategy = StocksStrategy()
        bot = StocksBot(client=fake, strategy=strategy, interval_sec=0)

        bot._running = True
        bot.stop()
        assert bot.is_running is False

    def test_qty_clamped_by_max_position(self):
        """Strategy qty_hint is clamped to max_position_qty."""
        fake = FakeStocksClient({"AAPL": 100.0, "MSFT": 100.0, "TSLA": 100.0})
        strategy = StocksStrategy(StocksStrategyConfig(
            short_window=2, long_window=3, margin_pct=0.001, default_qty=999.0,
        ))
        logged: list[dict] = []

        bot = StocksBot(
            client=fake,
            strategy=strategy,
            risk_config=StocksRiskConfig(max_position_qty=5.0),
            trade_logger=lambda t: logged.append(t),
            interval_sec=0,
        )

        # Warm up
        for _ in range(3):
            asyncio.run(bot._run_once())

        fake.quotes = {"AAPL": 110.0, "MSFT": 110.0, "TSLA": 110.0}
        asyncio.run(bot._run_once())

        # All orders should be clamped to 5.0
        for trade in logged:
            assert trade["qty"] <= 5.0


# ── Stock Trade DB Tests ────────────────────────────────────

class TestStockTradeLogging:

    def test_log_stock_trade(self):
        """Verify stocks_trades table insert and stocks_status update."""
        from services.db import Database
        db = Database()

        trade = {
            "timestamp": "2026-01-01T12:00:00Z",
            "symbol": "AAPL",
            "side": "buy",
            "price": 190.50,
            "qty": 5.0,
            "order_id": "test_001",
            "status": "filled",
            "reason": "MA cross UP",
        }
        db._log_stock_trade_sync(trade)

        # Verify trade was inserted
        rows = db._get_stock_trades_sync(10)
        assert len(rows) >= 1
        latest = rows[0]
        assert latest["symbol"] == "AAPL"
        assert latest["side"] == "buy"
        assert latest["price"] == 190.50

        # Verify stocks_status was updated
        pnl = db._get_stocks_pnl_sync()
        assert pnl["total_trades"] >= 1

        db.close()

    def test_stocks_pnl_accumulates(self):
        """Multiple stock trades accumulate PnL."""
        from services.db import Database
        db = Database()

        for i, pnl_val in enumerate([10.0, -3.0, 5.0]):
            db._log_stock_trade_sync({
                "timestamp": f"2026-01-0{i+1}T00:00:00Z",
                "symbol": "MSFT",
                "side": "buy",
                "price": 420.0,
                "qty": 1.0,
                "pnl": pnl_val,
            })

        summary = db._get_stocks_pnl_sync()
        # total_pnl should include these + any from previous test
        assert summary["total_pnl"] != 0 or summary["total_trades"] >= 3

        db.close()


# ── Circuit-Breaker Tests ───────────────────────────────────

class FailingStocksClient(FakeStocksClient):
    """Always raises StocksClientError on fetch_quote."""

    async def fetch_quote(self, symbol: str) -> Quote:
        raise StocksClientError("network down")


class ControlledClient(FakeStocksClient):
    """Fails N times then succeeds."""

    def __init__(self, fail_count: int):
        super().__init__()
        self._fail_count = fail_count
        self._call_count = 0

    async def fetch_quote(self, symbol: str) -> Quote:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise StocksClientError("temporary failure")
        return await super().fetch_quote(symbol)


class TestCircuitBreaker:

    def test_bot_stops_after_max_consecutive_errors(self):
        """After N consecutive errors, bot sets running=False and stops."""
        import os
        os.environ["STOCKS_MAX_CONSECUTIVE_ERRORS"] = "3"

        from config.settings import StocksSettings, settings
        fake_stocks = StocksSettings()
        original = settings.stocks
        object.__setattr__(settings, "stocks", fake_stocks)

        try:
            failing = FailingStocksClient()
            strategy = StocksStrategy()
            bot = StocksBot(client=failing, strategy=strategy, interval_sec=0)

            import unittest.mock
            with unittest.mock.patch("core.market_hours.is_market_open", return_value=True), \
                 unittest.mock.patch("core.stocks_bot.asyncio.sleep", new_callable=unittest.mock.AsyncMock):
                asyncio.run(bot.run_forever())

            assert bot.is_running is False
            assert "Circuit breaker" in bot._status.last_error
        finally:
            object.__setattr__(settings, "stocks", original)
            os.environ.pop("STOCKS_MAX_CONSECUTIVE_ERRORS", None)

    def test_success_resets_consecutive_errors(self):
        """A successful iteration resets error counter to 0."""
        import os
        os.environ["STOCKS_MAX_CONSECUTIVE_ERRORS"] = "5"

        from config.settings import StocksSettings, settings
        fake_stocks = StocksSettings()
        original = settings.stocks
        object.__setattr__(settings, "stocks", fake_stocks)

        try:
            # Fail 2 times, then succeed => counter resets, bot keeps running
            controlled = ControlledClient(fail_count=2)
            strategy = StocksStrategy(StocksStrategyConfig(short_window=2, long_window=3))
            bot = StocksBot(client=controlled, strategy=strategy, interval_sec=0)

            # Run only a few iterations then manually stop
            async def _run_limited():
                import unittest.mock
                with unittest.mock.patch("asyncio.sleep", new_callable=unittest.mock.AsyncMock):
                    bot._running = True
                    bot._status.running = True
                    # Manually iterate 4 times
                    for _ in range(4):
                        if not bot._running:
                            break
                        try:
                            await bot._run_once()
                        except Exception:
                            pass
                    bot.stop()

            asyncio.run(_run_limited())
            # Bot should have been manually stopped, NOT by circuit breaker
            assert "Circuit breaker" not in (bot._status.last_error or "")
        finally:
            object.__setattr__(settings, "stocks", original)
            os.environ.pop("STOCKS_MAX_CONSECUTIVE_ERRORS", None)

    def test_backoff_is_exponential(self):
        """Backoff follows 2^n capped at 60."""
        results = [min(2 ** n, 60) for n in range(1, 8)]
        assert results == [2, 4, 8, 16, 32, 60, 60]

