"""EMA Crossover Strategy — Example strategy using `ta` library.

Signals:
- BUY:  EMA(9) crosses ABOVE EMA(21)
- SELL: EMA(9) crosses BELOW EMA(21)
- HOLD: No crossover detected
"""

from __future__ import annotations

import logging

import pandas as pd
from ta.trend import EMAIndicator

from core.strategy_base import Signal, Strategy

logger = logging.getLogger(__name__)


class EMACrossover(Strategy):
    """EMA crossover strategy with configurable periods."""

    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        self._fast = fast
        self._slow = slow

    @property
    def name(self) -> str:
        return f"EMA({self._fast}/{self._slow}) Crossover"

    async def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self._slow + 2:
            logger.debug("Not enough candles for EMA calculation")
            return Signal.HOLD

        # Calculate EMAs using `ta` library (Python 3.14 compatible)
        ema_fast_series = EMAIndicator(
            close=df["close"], window=self._fast
        ).ema_indicator()
        ema_slow_series = EMAIndicator(
            close=df["close"], window=self._slow
        ).ema_indicator()

        df[f"ema_{self._fast}"] = ema_fast_series
        df[f"ema_{self._slow}"] = ema_slow_series

        # Get last two rows for crossover detection
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        ema_fast_prev = prev[f"ema_{self._fast}"]
        ema_slow_prev = prev[f"ema_{self._slow}"]
        ema_fast_curr = curr[f"ema_{self._fast}"]
        ema_slow_curr = curr[f"ema_{self._slow}"]

        # Check for NaN (not enough data for EMA)
        if pd.isna(ema_fast_prev) or pd.isna(ema_slow_prev):
            return Signal.HOLD
        if pd.isna(ema_fast_curr) or pd.isna(ema_slow_curr):
            return Signal.HOLD

        # Detect crossover
        if ema_fast_prev <= ema_slow_prev and ema_fast_curr > ema_slow_curr:
            logger.info(
                f"📈 BUY signal: EMA({self._fast})={ema_fast_curr:.2f} "
                f"crossed above EMA({self._slow})={ema_slow_curr:.2f}"
            )
            return Signal.BUY

        if ema_fast_prev >= ema_slow_prev and ema_fast_curr < ema_slow_curr:
            logger.info(
                f"📉 SELL signal: EMA({self._fast})={ema_fast_curr:.2f} "
                f"crossed below EMA({self._slow})={ema_slow_curr:.2f}"
            )
            return Signal.SELL

        return Signal.HOLD
