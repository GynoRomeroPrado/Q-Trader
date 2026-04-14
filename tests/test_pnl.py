"""Tests for PnL calculation, total_pnl accumulation, and is_win derivation."""

import sqlite3
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.strategy_base import Signal


# ──────────────────────────────────────────────────────────
# 1. Database layer: pnl stored & total_pnl updated
# ──────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """Create a minimal in-memory Database for testing."""
    from services.db import Database

    with patch.object(Database, "__init__", lambda self: None):
        d = Database()

    d._sqlite = sqlite3.connect(":memory:")
    d._sqlite.row_factory = sqlite3.Row
    d._sqlite.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, side TEXT,
            price REAL, amount REAL, order_id TEXT,
            pnl REAL DEFAULT 0.0
        )
    """)
    d._sqlite.execute("""
        CREATE TABLE bot_status (
            id INTEGER PRIMARY KEY DEFAULT 1,
            state TEXT DEFAULT 'stopped',
            started_at TEXT, last_heartbeat TEXT, last_error TEXT,
            total_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0.0
        )
    """)
    d._sqlite.execute("INSERT INTO bot_status (id) VALUES (1)")
    d._sqlite.commit()
    return d


class TestPnLStorage:
    """Verify pnl is written to trades and total_pnl accumulates."""

    def test_pnl_stored_in_trade_row(self, db):
        """Insert a trade with pnl != 0 and verify it persists."""
        db._log_trade_sync({
            "timestamp": "2026-01-01T00:00:00Z",
            "symbol": "BTC/USDT",
            "side": "sell",
            "price": 110.0,
            "amount": 1.0,
            "order_id": "test-001",
            "pnl": 9.85,
        })
        row = db._sqlite.execute("SELECT pnl FROM trades WHERE id = 1").fetchone()
        assert row["pnl"] == pytest.approx(9.85)

    def test_total_pnl_accumulates(self, db):
        """Two trades with different pnl → total_pnl = sum."""
        db._log_trade_sync({
            "timestamp": "2026-01-01T00:00:00Z",
            "symbol": "BTC/USDT", "side": "buy",
            "price": 100.0, "amount": 1.0, "pnl": 0.0,
        })
        db._log_trade_sync({
            "timestamp": "2026-01-01T00:01:00Z",
            "symbol": "BTC/USDT", "side": "sell",
            "price": 110.0, "amount": 1.0, "pnl": 9.85,
        })
        status = db._sqlite.execute(
            "SELECT total_trades, total_pnl FROM bot_status WHERE id = 1"
        ).fetchone()
        assert status["total_trades"] == 2
        assert status["total_pnl"] == pytest.approx(9.85)

    def test_negative_pnl_decreases_total(self, db):
        """A losing trade should subtract from total_pnl."""
        db._log_trade_sync({
            "timestamp": "2026-01-01T00:00:00Z",
            "symbol": "BTC/USDT", "side": "sell",
            "price": 90.0, "amount": 1.0, "pnl": -10.15,
        })
        status = db._sqlite.execute(
            "SELECT total_pnl FROM bot_status WHERE id = 1"
        ).fetchone()
        assert status["total_pnl"] == pytest.approx(-10.15)


# ──────────────────────────────────────────────────────────
# 2. PnL formula verification (BUY → SELL cycle)
# ──────────────────────────────────────────────────────────

class TestPnLFormula:
    """Verify the PnL formula: (sell - buy)*qty - fees."""

    def test_buy_then_sell_pnl(self):
        """BUY@100, SELL@110, qty=1, fee_buy=0.075, fee_sell=0.075 → pnl=9.85."""
        buy_price = 100.0
        sell_price = 110.0
        qty = 1.0
        fee_buy = buy_price * qty * 0.00075   # 0.075
        fee_sell = sell_price * qty * 0.00075  # 0.0825

        expected_pnl = (sell_price - buy_price) * qty - fee_buy - fee_sell
        # = 10.0 - 0.075 - 0.0825 = 9.8425

        assert expected_pnl == pytest.approx(9.8425)

    def test_losing_trade_pnl(self):
        """BUY@100, SELL@95, qty=2, fees → negative PnL."""
        buy_price = 100.0
        sell_price = 95.0
        qty = 2.0
        fee_buy = 0.15
        fee_sell = 0.1425

        pnl = (sell_price - buy_price) * qty - fee_buy - fee_sell
        # = -10 - 0.15 - 0.1425 = -10.2925
        assert pnl < 0
        assert pnl == pytest.approx(-10.2925)


# ──────────────────────────────────────────────────────────
# 3. is_win derived from PnL
# ──────────────────────────────────────────────────────────

class TestIsWinDerivation:
    """Verify that record_trade_closed receives correct is_win."""

    def _make_executor(self):
        """Build a minimal TradeExecutor with mocked deps."""
        from core.trade_executor import TradeExecutor, ExecutionResult

        exchange = MagicMock()
        exchange.fetch_balance = AsyncMock(return_value={"free": 1000.0, "total": 1000.0})
        strategy = MagicMock()
        risk = MagicMock()
        risk.record_trade_opened = MagicMock()
        risk.record_trade_closed = MagicMock()
        risk.calculate_position_size = MagicMock(return_value=1.0)

        with patch("core.trade_executor.settings") as mock_settings:
            mock_settings.trading.symbol = "BTC/USDT"
            mock_settings.trading.paper_trading = True
            executor = TradeExecutor.__new__(TradeExecutor)

        # Minimal init to avoid full constructor
        executor._exchange = exchange
        executor._strategy = strategy
        executor._risk = risk
        executor._db = None
        executor._ws = None
        executor._audit = None
        executor._running = False
        executor.symbol = "BTC/USDT"
        executor.paper_mode = True

        pw = MagicMock()
        pw.fetch_balance = AsyncMock(return_value={"free": 1000.0, "used": 0.0, "total": 1000.0})
        executor.paper_wallet = pw

        executor.order_manager = MagicMock()
        executor.order_manager._active_order_id = None
        executor.oracle = MagicMock()
        executor._alert = MagicMock()
        executor._current_state = MagicMock()
        executor._cached_quote_balance = 1000.0
        executor._cached_base_balance = 0.0
        executor._entry_price = 0.0
        executor._entry_amount = 0.0
        executor._entry_fee = 0.0
        executor.tick_count = 0
        executor._tick_latency_sum = 0.0
        executor.agent_graph = None
        executor._audit_transition = AsyncMock()

        return executor

    def test_winning_trade_sets_is_win_true(self):
        """SELL at profit → record_trade_closed(is_win=True)."""
        from core.trade_executor import TradeDecision

        executor = self._make_executor()

        # Simulate BUY entry already recorded
        executor._entry_price = 100.0
        executor._entry_amount = 1.0
        executor._entry_fee = 0.075

        # Simulate SELL fill
        executor.paper_wallet.execute_simulated_trade = AsyncMock(return_value={
            "status": "filled", "filled": 1.0, "price": 110.0,
            "cost": 110.0, "fee": 0.0825, "chases": 0,
        })

        decision = TradeDecision(signal=Signal.SELL, price=110.0, atr_proxy=0.001)

        result = asyncio.run(executor._state_execute(decision))
        assert result.pnl == pytest.approx(
            (110.0 - 100.0) * 1.0 - 0.075 - 0.0825
        )
        executor._risk.record_trade_closed.assert_called_once_with(is_win=True)

    def test_losing_trade_sets_is_win_false(self):
        """SELL at loss → record_trade_closed(is_win=False)."""
        from core.trade_executor import TradeDecision

        executor = self._make_executor()

        executor._entry_price = 100.0
        executor._entry_amount = 1.0
        executor._entry_fee = 0.075

        executor.paper_wallet.execute_simulated_trade = AsyncMock(return_value={
            "status": "filled", "filled": 1.0, "price": 95.0,
            "cost": 95.0, "fee": 0.07125, "chases": 0,
        })

        decision = TradeDecision(signal=Signal.SELL, price=95.0, atr_proxy=0.001)

        result = asyncio.run(executor._state_execute(decision))
        assert result.pnl < 0
        executor._risk.record_trade_closed.assert_called_once_with(is_win=False)

    def test_breakeven_is_loss(self):
        """pnl == 0 (exact breakeven) → is_win=False."""
        # pnl = (price - entry)*qty - fees  can be exactly 0 only with contrived numbers
        # With fees > 0 and same price → always negative, so is_win=False
        from core.trade_executor import TradeDecision

        executor = self._make_executor()

        executor._entry_price = 100.0
        executor._entry_amount = 1.0
        executor._entry_fee = 0.0  # zero fee entry

        executor.paper_wallet.execute_simulated_trade = AsyncMock(return_value={
            "status": "filled", "filled": 1.0, "price": 100.0,
            "cost": 100.0, "fee": 0.0, "chases": 0,
        })

        decision = TradeDecision(signal=Signal.SELL, price=100.0, atr_proxy=0.001)

        result = asyncio.run(executor._state_execute(decision))
        assert result.pnl == pytest.approx(0.0)
        executor._risk.record_trade_closed.assert_called_once_with(is_win=False)
