"""Abstract base class for trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd


class Signal(Enum):
    """Trading signal types."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy(ABC):
    """Base class for all trading strategies.

    To create a new strategy:
    1. Subclass this class
    2. Implement generate_signal(df) -> Signal
    3. Register it in run_bot.py
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    @abstractmethod
    async def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Analyze OHLCV DataFrame and return a trading signal.

        Args:
            df: DataFrame with columns [timestamp, open, high, low, close, volume]

        Returns:
            Signal.BUY, Signal.SELL, or Signal.HOLD
        """
        ...
