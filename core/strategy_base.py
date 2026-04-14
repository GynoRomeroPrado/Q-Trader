"""Microstructure Strategy — Multi-Signal Composite Engine.

Signals:
    1. OBI  (Order Book Imbalance)   — bid/ask volume asymmetry
    2. BPG  (Book Pressure Gradient) — OBI rate of change across ticks
    3. MP   (Micro-Price)            — volume-weighted mid-price trend

All numeric hot-paths are JIT-compiled via Numba when available.
Threshold adapts dynamically to market volatility (ATR proxy).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from enum import Enum
from typing import Any, Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Numba JIT — optional, graceful fallback
# ──────────────────────────────────────────────────────────────

_NUMBA_AVAILABLE = False

try:
    from numba import njit

    @njit(cache=True)
    def _fast_obi(bid_vols: np.ndarray, ask_vols: np.ndarray) -> float:
        """Order Book Imbalance — compiled to machine code."""
        bid_sum = 0.0
        for i in range(bid_vols.shape[0]):
            bid_sum += bid_vols[i]
        ask_sum = 0.0
        for i in range(ask_vols.shape[0]):
            ask_sum += ask_vols[i]
        total = bid_sum + ask_sum
        if total == 0.0:
            return 0.0
        return (bid_sum - ask_sum) / total

    @njit(cache=True)
    def _fast_micro_price(
        best_bid: float, best_ask: float,
        bid_vol_top: float, ask_vol_top: float,
    ) -> float:
        """Volume-weighted mid-price (micro-price)."""
        total = bid_vol_top + ask_vol_top
        if total == 0.0:
            return (best_bid + best_ask) / 2.0
        return (best_bid * ask_vol_top + best_ask * bid_vol_top) / total

    @njit(cache=True)
    def _fast_atr_proxy(best_bid: float, best_ask: float) -> float:
        """Spread as ATR proxy."""
        if best_bid == 0.0:
            return 0.0
        return (best_ask - best_bid) / best_bid

    # Warm up the JIT (first call triggers compilation)
    _dummy_arr = np.array([1.0, 2.0], dtype=np.float64)
    _fast_obi(_dummy_arr, _dummy_arr)
    _fast_micro_price(100.0, 101.0, 1.0, 1.0)
    _fast_atr_proxy(100.0, 101.0)

    _NUMBA_AVAILABLE = True
    logger.info("⚡ Numba JIT compiled successfully — OBI running at native speed")

except Exception as e:
    logger.warning(f"⚠️  Numba not available ({e}), using pure-Python fallback")

    def _fast_obi(bid_vols, ask_vols) -> float:  # type: ignore[misc]
        bid_sum = float(np.sum(bid_vols))
        ask_sum = float(np.sum(ask_vols))
        total = bid_sum + ask_sum
        if total == 0.0:
            return 0.0
        return (bid_sum - ask_sum) / total

    def _fast_micro_price(best_bid, best_ask, bid_vol_top, ask_vol_top) -> float:  # type: ignore[misc]
        total = bid_vol_top + ask_vol_top
        if total == 0.0:
            return (best_bid + best_ask) / 2.0
        return (best_bid * ask_vol_top + best_ask * bid_vol_top) / total

    def _fast_atr_proxy(best_bid, best_ask) -> float:  # type: ignore[misc]
        if best_bid == 0.0:
            return 0.0
        return (best_ask - best_bid) / best_bid


# ──────────────────────────────────────────────────────────────
# Signal Enum
# ──────────────────────────────────────────────────────────────

class Signal(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


# ──────────────────────────────────────────────────────────────
# Composite Strategy
# ──────────────────────────────────────────────────────────────

class OrderBookStrategy:
    """Multi-signal microstructure strategy with adaptive threshold.

    Computes a composite score from:
        - OBI (Order Book Imbalance)       weight: 0.50
        - BPG (Book Pressure Gradient)     weight: 0.25
        - MP  (Micro-Price trend)          weight: 0.25

    Threshold adapts inversely to volatility:
        high vol → higher threshold → fewer trades (safety)
        low  vol → lower threshold  → more opportunities
    """

    def __init__(
        self,
        depth: int = 10,
        imbalance_threshold: float = 0.65,
        history_size: int = 20,
        # Signal weights
        w_obi: float = 0.50,
        w_bpg: float = 0.25,
        w_mp: float = 0.25,
        # Adaptive threshold params
        adaptive_threshold: bool = True,
        vol_baseline_bps: float = 10.0,   # 10 bps = 0.001 = typical crypto spread
        min_threshold: float = 0.40,
        max_threshold: float = 0.85,
    ) -> None:
        self.depth = depth
        self.thresh = imbalance_threshold
        self.history_size = history_size

        # Weights (must sum to 1.0)
        self.w_obi = w_obi
        self.w_bpg = w_bpg
        self.w_mp = w_mp

        # Adaptive threshold
        self.adaptive = adaptive_threshold
        self.vol_baseline = vol_baseline_bps / 10000.0
        self.min_thresh = min_threshold
        self.max_thresh = max_threshold

        # Rolling history for gradient / trend signals
        self._obi_history: deque[float] = deque(maxlen=history_size)
        self._mp_history: deque[float] = deque(maxlen=history_size)
        self._atr_history: deque[float] = deque(maxlen=history_size)

        # Pre-allocated numpy arrays (avoid per-tick allocation)
        self._bid_vols = np.zeros(depth, dtype=np.float64)
        self._ask_vols = np.zeros(depth, dtype=np.float64)

        # Stats
        self.ticks_processed: int = 0
        self._last_signal_time: float = 0.0

    @property
    def name(self) -> str:
        mode = "Numba" if _NUMBA_AVAILABLE else "Python"
        return (
            f"MultiSignal-OBI(D={self.depth}, T={self.thresh:.2f}, "
            f"W=[{self.w_obi}/{self.w_bpg}/{self.w_mp}], {mode})"
        )

    def _compute_adaptive_threshold(self) -> float:
        """Scale threshold inversely to volatility."""
        if not self.adaptive or len(self._atr_history) < 3:
            return self.thresh

        avg_atr = sum(self._atr_history) / len(self._atr_history)
        safe_atr = max(avg_atr, 0.000001)

        # vol_scaler > 1 when market is calmer than baseline → lower threshold
        # vol_scaler < 1 when market is volatile → higher threshold
        vol_scaler = self.vol_baseline / safe_atr
        adapted = self.thresh * (1.0 / max(vol_scaler, 0.3))

        return max(self.min_thresh, min(adapted, self.max_thresh))

    def _compute_bpg(self) -> float:
        """Book Pressure Gradient — rate of change of OBI over last N ticks."""
        if len(self._obi_history) < 3:
            return 0.0
        recent = list(self._obi_history)
        # Weighted slope: more recent ticks matter more
        n = min(5, len(recent))
        tail = recent[-n:]
        if n < 2:
            return 0.0
        gradient = tail[-1] - tail[0]
        return max(-1.0, min(1.0, gradient * 2.0))  # Scale & clamp

    def _compute_mp_trend(self) -> float:
        """Micro-Price trend — direction of fair price movement."""
        if len(self._mp_history) < 3:
            return 0.0
        recent = list(self._mp_history)
        n = min(5, len(recent))
        tail = recent[-n:]
        if tail[0] == 0.0:
            return 0.0
        pct_change = (tail[-1] - tail[0]) / tail[0]
        # Scale: 0.01% move → score ±0.5
        return max(-1.0, min(1.0, pct_change * 5000.0))

    def process_orderbook(self, ob: Dict[str, Any]) -> Tuple[Signal, float]:
        """Full multi-signal analysis — O(depth) per tick.

        Returns:
            (Signal, atr_proxy)
        """
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if len(bids) < self.depth or len(asks) < self.depth:
            return Signal.HOLD, 0.0

        self.ticks_processed += 1

        # ── Extract data into pre-allocated arrays ──
        actual_depth = min(self.depth, len(bids), len(asks))
        for i in range(actual_depth):
            self._bid_vols[i] = float(bids[i][1])
            self._ask_vols[i] = float(asks[i][1])

        bid_slice = self._bid_vols[:actual_depth]
        ask_slice = self._ask_vols[:actual_depth]

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        bid_vol_top = float(bids[0][1])
        ask_vol_top = float(asks[0][1])

        # ── Signal 1: OBI (Numba-accelerated) ──
        obi = _fast_obi(bid_slice, ask_slice)

        # ── Signal 2: ATR Proxy ──
        atr_proxy = _fast_atr_proxy(best_bid, best_ask)

        # ── Signal 3: Micro-Price ──
        micro_price = _fast_micro_price(best_bid, best_ask, bid_vol_top, ask_vol_top)

        # ── Update rolling history ──
        self._obi_history.append(obi)
        self._mp_history.append(micro_price)
        self._atr_history.append(atr_proxy)

        # ── Signal 2b: Book Pressure Gradient (OBI derivative) ──
        bpg = self._compute_bpg()

        # ── Signal 3b: Micro-Price trend ──
        mp_trend = self._compute_mp_trend()

        # ── Composite Score [-1.0, +1.0] ──
        composite = (
            self.w_obi * obi
            + self.w_bpg * bpg
            + self.w_mp * mp_trend
        )

        # ── Adaptive threshold ──
        threshold = self._compute_adaptive_threshold()

        # ── Signal generation ──
        if composite > threshold:
            return Signal.BUY, atr_proxy
        elif composite < -threshold:
            return Signal.SELL, atr_proxy

        return Signal.HOLD, atr_proxy

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return internal state for dashboard / debugging."""
        return {
            "ticks_processed": self.ticks_processed,
            "numba_active": _NUMBA_AVAILABLE,
            "current_obi": self._obi_history[-1] if self._obi_history else 0.0,
            "current_bpg": self._compute_bpg(),
            "current_mp_trend": self._compute_mp_trend(),
            "avg_atr_proxy": (
                sum(self._atr_history) / len(self._atr_history)
                if self._atr_history else 0.0
            ),
            "adaptive_threshold": self._compute_adaptive_threshold(),
            "history_depth": len(self._obi_history),
        }
