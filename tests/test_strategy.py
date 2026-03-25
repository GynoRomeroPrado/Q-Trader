"""Tests for Order Book Imbalance Strategy."""

import asyncio
import pytest

from core.strategy_base import Signal, OrderBookStrategy


@pytest.fixture
def strategy():
    return OrderBookStrategy(depth=3, imbalance_threshold=0.6)


class TestOrderBookStrategy:
    """Test suite for OBI strategy signals."""

    def test_hold_insufficient_data(self, strategy):
        """HOLD when not enough order book levels."""
        ob = {
            "bids": [[100.0, 1.0], [99.0, 1.0]],
            "asks": [[101.0, 1.0], [102.0, 1.0]]
        }
        # Required depth is 3, have 2
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.HOLD
        assert atr == 0.0

    def test_buy_signal_strong_bid_pressure(self, strategy):
        """BUY when bid volume overwhelms ask volume."""
        ob = {
            "bids": [[100.0, 10.0], [99.0, 20.0], [98.0, 20.0]], # 50.0 vol
            "asks": [[101.0, 1.0], [102.0, 2.0], [103.0, 2.0]]   # 5.0 vol
        }
        # Imbalance = (50 - 5) / 55 = 45 / 55 = 0.81 > 0.6
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.BUY
        assert abs(atr - ((101.0 - 100.0) / 100.0)) < 1e-8

    def test_sell_signal_strong_ask_pressure(self, strategy):
        """SELL when ask volume overwhelms bid volume."""
        ob = {
            "bids": [[100.0, 2.0], [99.0, 1.0], [98.0, 2.0]],    # 5.0 vol
            "asks": [[101.0, 15.0], [102.0, 20.0], [103.0, 15.0]]# 50.0 vol
        }
        # Imbalance = (5 - 50) / 55 = -45 / 55 = -0.81 < -0.6
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.SELL

    def test_hold_in_balanced_market(self, strategy):
        """HOLD when orders are relatively balanced."""
        ob = {
            "bids": [[100.0, 10.0], [99.0, 10.0], [98.0, 10.0]], # 30.0
            "asks": [[101.0, 12.0], [102.0, 10.0], [103.0, 10.0]]# 32.0
        }
        # Imbalance = (30 - 32) / 62 = -2 / 62 = -0.03
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.HOLD

    def test_strategy_name(self, strategy):
        assert strategy.name == "OBI-HFT(Depth=3, Thresh=0.6)"
