"""AI Forecast service — deterministic dummy forecasts.

Pure module — no DB, no ML model. Returns coherent synthetic
forecasts based on symbol hash. Replace with Kronos/TimesFM later.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import hashlib


@dataclass
class ForecastPoint:
    symbol: str
    domain: str           # "crypto" | "stocks"
    timeframe: str        # "1m", "5m", "1h", "1d"
    trend_score: float    # [-1.0, 1.0]
    volatility_score: float  # [0.0, 1.0]
    confidence: float     # [0.0, 1.0]
    forecast_horizon: int # candles ahead


def _symbol_hash(symbol: str, salt: str = "") -> float:
    """Deterministic 0..1 float from symbol string."""
    h = hashlib.md5(f"{symbol}{salt}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def get_dummy_forecasts(
    domain: str,
    symbols: list[str],
    timeframe: str = "1h",
) -> list[dict[str, Any]]:
    """Generate deterministic forecasts per symbol.

    Uses symbol hash for reproducible but varied values.
    Future: replace body with Kronos / TimesFM model inference.
    """
    horizon_map = {"1m": 60, "5m": 48, "15m": 24, "1h": 12, "4h": 6, "1d": 5}
    horizon = horizon_map.get(timeframe, 12)

    results: list[ForecastPoint] = []
    for sym in symbols:
        h1 = _symbol_hash(sym, "trend")
        h2 = _symbol_hash(sym, "vol")
        h3 = _symbol_hash(sym, "conf")

        trend = round((h1 * 2.0) - 1.0, 4)     # [-1, 1]
        volatility = round(h2, 4)                # [0, 1]
        confidence = round(0.4 + h3 * 0.55, 4)  # [0.4, 0.95]

        results.append(ForecastPoint(
            symbol=sym,
            domain=domain,
            timeframe=timeframe,
            trend_score=trend,
            volatility_score=volatility,
            confidence=confidence,
            forecast_horizon=horizon,
        ))

    # Sort by abs(trend_score) descending — strongest signals first
    results.sort(key=lambda f: abs(f.trend_score), reverse=True)

    return [asdict(f) for f in results]
