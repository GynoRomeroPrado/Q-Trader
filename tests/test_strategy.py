"""Tests for EMA Crossover Strategy."""

import asyncio
import pytest
import pandas as pd

from core.strategy_base import Signal
from strategies.ema_crossover import EMACrossover


def _make_ohlcv(prices: list[float]) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame from close prices."""
    n = len(prices)
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=n, freq="5min"),
        "open": prices,
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.99 for p in prices],
        "close": prices,
        "volume": [1000.0] * n,
    })


@pytest.fixture
def strategy():
    return EMACrossover(fast=3, slow=5)


class TestEMACrossover:
    """Test suite for EMA Crossover strategy signals."""

    def test_hold_insufficient_data(self, strategy):
        """HOLD when not enough candles for EMA calculation."""
        df = _make_ohlcv([100, 101, 102])
        signal = asyncio.run(strategy.generate_signal(df))
        assert signal == Signal.HOLD

    def test_buy_signal_on_upward_crossover(self, strategy):
        """BUY when fast EMA crosses above slow EMA.

        We need diff[-2] <= 0 and diff[-1] > 0.
        Downtrend that transitions into upturn at the LAST candle.
        """
        prices = [
            100, 98, 96, 94, 92, 90, 88, 86, 84, 82, 80,
            79, 78, 77,   # deep downtrend so EMAs converge with fast < slow
            90,           # sharp jump: fast EMA reacts, slow lags → crossover
        ]
        df = _make_ohlcv(prices)
        signal = asyncio.run(strategy.generate_signal(df))
        assert signal == Signal.BUY

    def test_sell_signal_on_downward_crossover(self, strategy):
        """SELL when fast EMA crosses below slow EMA.

        We need diff[-2] >= 0 and diff[-1] < 0.
        Uptrend that drops sharply at the LAST candle.
        """
        prices = [
            80, 82, 84, 86, 88, 90, 92, 94, 96, 98, 100,
            101, 102, 103,  # uptrend so EMAs converge with fast >= slow
            88,             # sharp drop: fast EMA drops, slow lags → crossover
        ]
        df = _make_ohlcv(prices)
        signal = asyncio.run(strategy.generate_signal(df))
        assert signal == Signal.SELL

    def test_hold_in_flat_market(self, strategy):
        """HOLD in a flat/sideways market."""
        prices = [100.0] * 20
        df = _make_ohlcv(prices)
        signal = asyncio.run(strategy.generate_signal(df))
        assert signal == Signal.HOLD

    def test_strategy_name(self, strategy):
        assert strategy.name == "EMA(3/5) Crossover"
