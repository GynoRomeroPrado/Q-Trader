"""Tests for Multi-Signal Order Book Strategy.

Covers:
    - OBI signal with confluence (gradient + micro-price)
    - Adaptive threshold scaling
    - Hold on insufficient data / balanced market
    - Gradient momentum check
    - Strategy name / metadata
"""

import pytest

from core.strategy_base import Signal, OrderBookStrategy


@pytest.fixture
def strategy():
    return OrderBookStrategy(
        depth=3,
        imbalance_threshold=0.6,
        gradient_min=0.02,
        adaptive_vol_baseline=0.001,
    )


def _make_ob(bid_base, ask_base, bid_vols, ask_vols, spread_bps=1):
    """Helper: build a realistic order book with configurable spread.

    spread_bps = 1 means 0.01% spread (1 basis point) — realistic for BTC/USDT.
    Prices are spaced by 0.01 increments.
    """
    spread = bid_base * (spread_bps / 10000)
    ask_start = bid_base + spread
    bids = [[bid_base - i * 0.01, v] for i, v in enumerate(bid_vols)]
    asks = [[ask_start + i * 0.01, v] for i, v in enumerate(ask_vols)]
    return {"bids": bids, "asks": asks}


class TestOrderBookStrategy:
    """Test suite for multi-signal OBI strategy."""

    def test_hold_insufficient_data(self, strategy):
        """HOLD when not enough order book levels."""
        ob = {
            "bids": [[50000.0, 1.0], [49999.0, 1.0]],
            "asks": [[50001.0, 1.0], [50002.0, 1.0]]
        }
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.HOLD
        assert atr == 0.0

    def test_hold_in_balanced_market(self, strategy):
        """HOLD when orders are relatively balanced."""
        ob = _make_ob(50000.0, 50001.0, [10.0, 10.0, 10.0], [12.0, 10.0, 10.0])
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.HOLD

    def test_buy_signal_requires_confluence(self, strategy):
        """BUY only when OBI, gradient, AND micro-price all agree.

        We prime the gradient by sending a neutral tick first,
        then the bullish tick creates a positive gradient.
        Using realistic BTC/USDT prices with ~1bp spread.
        """
        neutral_ob = _make_ob(50000.0, 50005.0, [10.0, 10.0, 10.0], [10.0, 10.0, 10.0])
        strategy.process_orderbook(neutral_ob)  # prime gradient (OBI≈0)

        # Heavy bid-side imbalance
        bullish_ob = _make_ob(50000.0, 50005.0, [50.0, 40.0, 30.0], [2.0, 3.0, 2.0])
        # OBI = (120 - 7) / 127 = 0.89 → well above threshold
        # gradient ≈ 0.89 - 0.0 = 0.89 >> 0.02
        # micro_bias: bid_vol(top)=50 >> ask_vol(top)=2 → micro_price near ask → bullish
        signal, atr = strategy.process_orderbook(bullish_ob)
        assert signal == Signal.BUY
        assert atr > 0  # should have a valid ATR proxy

    def test_sell_signal_requires_confluence(self, strategy):
        """SELL only with full bearish confluence."""
        neutral_ob = _make_ob(50000.0, 50005.0, [10.0, 10.0, 10.0], [10.0, 10.0, 10.0])
        strategy.process_orderbook(neutral_ob)  # prime gradient (OBI≈0)

        # Heavy ask-side imbalance
        bearish_ob = _make_ob(50000.0, 50005.0, [2.0, 3.0, 2.0], [50.0, 40.0, 30.0])
        # OBI = (7 - 120) / 127 = -0.89 → well below -threshold
        # gradient = -0.89 - 0.0 = -0.89 << -0.02
        # micro_bias: ask_vol(top)=50 >> bid_vol(top)=2 → micro_price near bid → bearish
        signal, atr = strategy.process_orderbook(bearish_ob)
        assert signal == Signal.SELL

    def test_no_signal_without_gradient(self, strategy):
        """HOLD if OBI is strong but gradient is flat (same book twice)."""
        bullish_ob = _make_ob(50000.0, 50005.0, [50.0, 40.0, 30.0], [2.0, 3.0, 2.0])
        strategy.process_orderbook(bullish_ob)  # First call → BUY (gradient from 0)

        # Second call with identical book: gradient = 0.89 - 0.89 = 0 → no momentum
        signal, atr = strategy.process_orderbook(bullish_ob)
        assert signal == Signal.HOLD

    def test_strategy_name(self, strategy):
        assert "OBI-MultiSignal" in strategy.name
        assert "Depth=3" in strategy.name

    def test_get_status(self, strategy):
        status = strategy.get_status()
        assert "prev_obi" in status
        assert "prev_micro_price" in status
        assert "base_threshold" in status

    def test_hold_on_empty_book(self, strategy):
        """HOLD when book is empty."""
        ob = {"bids": [], "asks": []}
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.HOLD

    def test_hold_zero_volume(self, strategy):
        """HOLD when all volumes are zero."""
        ob = _make_ob(50000.0, 50005.0, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
        signal, atr = strategy.process_orderbook(ob)
        assert signal == Signal.HOLD
