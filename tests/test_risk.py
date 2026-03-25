"""Tests for Risk Manager."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.risk_manager import RiskManager
from core.strategy_base import Signal


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    exchange.fetch_balance = AsyncMock(return_value={
        "free": 1000.0,
        "used": 0.0,
        "total": 1000.0,
    })
    return exchange


@pytest.fixture
def risk_manager(mock_exchange):
    rm = RiskManager(mock_exchange)
    # Reset cooldown
    rm._last_trade_time = 0.0
    return rm


class TestRiskManager:
    """Test suite for risk management rules."""

    def test_position_size_calculation(self, risk_manager):
        """Position size = balance * max_pct / price."""
        size = risk_manager.calculate_position_size(
            balance=1000.0, price=50000.0
        )
        # Default max_position_pct = 0.02
        # 1000 * 0.02 / 50000 = 0.0004
        assert abs(size - 0.0004) < 1e-8

    def test_stop_loss_buy(self, risk_manager):
        """Stop-loss for BUY should be below entry."""
        sl = risk_manager.calculate_stop_loss(50000.0, side="buy")
        # Default stop_loss_pct = 0.03
        # 50000 * (1 - 0.03) = 48500
        assert abs(sl - 48500.0) < 1e-2

    def test_stop_loss_sell(self, risk_manager):
        """Stop-loss for SELL should be above entry."""
        sl = risk_manager.calculate_stop_loss(50000.0, side="sell")
        # 50000 * (1 + 0.03) = 51500
        assert abs(sl - 51500.0) < 1e-2

    def test_validate_passes_normal(self, risk_manager):
        """Validate should pass with no constraints hit."""
        result = asyncio.run(risk_manager.validate(Signal.BUY, "BTC/USDT"))
        assert result is True

    def test_cooldown_rejects(self, risk_manager):
        """Validate should reject during cooldown."""
        risk_manager._last_trade_time = time.time()  # Just traded
        result = asyncio.run(risk_manager.validate(Signal.BUY, "BTC/USDT"))
        assert result is False

    def test_max_trades_rejects(self, risk_manager):
        """Validate should reject when max open trades reached."""
        risk_manager._open_trades = 999
        result = asyncio.run(risk_manager.validate(Signal.BUY, "BTC/USDT"))
        assert result is False

    def test_low_balance_rejects(self, risk_manager, mock_exchange):
        """Validate should reject BUY when balance too low."""
        mock_exchange.fetch_balance = AsyncMock(return_value={
            "free": 1.0, "used": 0.0, "total": 1.0,
        })
        result = asyncio.run(risk_manager.validate(Signal.BUY, "BTC/USDT"))
        assert result is False

    def test_trade_tracking(self, risk_manager):
        """Open trade counter should increment/decrement."""
        assert risk_manager.open_trades == 0
        risk_manager.record_trade_opened()
        assert risk_manager.open_trades == 1
        risk_manager.record_trade_closed()
        assert risk_manager.open_trades == 0
        risk_manager.record_trade_closed()  # Should not go below 0
        assert risk_manager.open_trades == 0
