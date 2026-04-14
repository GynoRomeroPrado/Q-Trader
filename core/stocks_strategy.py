"""Stocks Strategy — Simple moving average crossover for paper trading.

Pure in-memory strategy that processes StockBar datapoints.
No broker calls — only signal generation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal


# ── Types ───────────────────────────────────────────────────

@dataclass
class StockBar:
    """Single OHLCV bar for a stock symbol."""
    symbol: str
    timestamp: str   # ISO-8601
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class StrategyDecision:
    """Output of strategy evaluation for one symbol."""
    symbol: str
    side: Literal["buy", "sell", "hold"]
    qty_hint: float     # Suggested quantity (may be overridden by risk)
    reason: str


@dataclass
class StocksStrategyConfig:
    """Configuration for the moving average crossover strategy."""
    short_window: int = 5       # Fast MA period
    long_window: int = 20       # Slow MA period
    margin_pct: float = 0.002   # 0.2% margin above/below long MA to trigger
    default_qty: float = 1.0    # Default shares per order


# ── Strategy ────────────────────────────────────────────────

class StocksStrategy:
    """Moving average crossover strategy.

    - price > long_ma * (1 + margin)  → BUY
    - price < long_ma * (1 - margin)  → SELL
    - otherwise                       → HOLD

    Maintains separate price history per symbol.
    """

    def __init__(self, config: StocksStrategyConfig | None = None) -> None:
        self._config = config or StocksStrategyConfig()
        # Per-symbol FIFO of closing prices
        self._history: dict[str, deque[float]] = {}

    @classmethod
    def from_db_config(cls, cfg: dict) -> "StocksStrategy":
        """Build a StocksStrategy from a DB config dict."""
        return cls(StocksStrategyConfig(
            short_window=int(cfg.get("ma_fast_window", 5)),
            long_window=int(cfg.get("ma_slow_window", 20)),
            margin_pct=float(cfg.get("signal_margin", 0.002)),
            default_qty=float(cfg.get("default_qty", 1.0)),
        ))

    def update_config(self, cfg: dict) -> None:
        """Hot-reload config without losing price history."""
        self._config = StocksStrategyConfig(
            short_window=int(cfg.get("ma_fast_window", self._config.short_window)),
            long_window=int(cfg.get("ma_slow_window", self._config.long_window)),
            margin_pct=float(cfg.get("signal_margin", self._config.margin_pct)),
            default_qty=float(cfg.get("default_qty", self._config.default_qty)),
        )

    @property
    def config(self) -> StocksStrategyConfig:
        return self._config

    def _get_history(self, symbol: str) -> deque[float]:
        """Get or create price history deque for a symbol."""
        if symbol not in self._history:
            max_len = max(self._config.short_window, self._config.long_window) + 5
            self._history[symbol] = deque(maxlen=max_len)
        return self._history[symbol]

    def on_bar(self, bar: StockBar) -> StrategyDecision:
        """Process a new bar and return a trading decision.

        Args:
            bar: Latest OHLCV bar for a symbol.

        Returns:
            StrategyDecision with side = buy/sell/hold.
        """
        history = self._get_history(bar.symbol)
        history.append(bar.close)

        cfg = self._config

        # Not enough data for long MA yet
        if len(history) < cfg.long_window:
            return StrategyDecision(
                symbol=bar.symbol,
                side="hold",
                qty_hint=0.0,
                reason=f"warming up ({len(history)}/{cfg.long_window} bars)",
            )

        # Calculate moving averages
        prices = list(history)
        short_ma = sum(prices[-cfg.short_window:]) / cfg.short_window
        long_ma = sum(prices[-cfg.long_window:]) / cfg.long_window

        upper = long_ma * (1 + cfg.margin_pct)
        lower = long_ma * (1 - cfg.margin_pct)

        if short_ma > upper:
            return StrategyDecision(
                symbol=bar.symbol,
                side="buy",
                qty_hint=cfg.default_qty,
                reason=f"MA cross UP: short={short_ma:.2f} > upper={upper:.2f}",
            )
        elif short_ma < lower:
            return StrategyDecision(
                symbol=bar.symbol,
                side="sell",
                qty_hint=cfg.default_qty,
                reason=f"MA cross DOWN: short={short_ma:.2f} < lower={lower:.2f}",
            )
        else:
            return StrategyDecision(
                symbol=bar.symbol,
                side="hold",
                qty_hint=0.0,
                reason=f"neutral zone: short={short_ma:.2f}, long={long_ma:.2f}",
            )

    def reset(self, symbol: str | None = None) -> None:
        """Clear price history for one or all symbols."""
        if symbol:
            self._history.pop(symbol, None)
        else:
            self._history.clear()
