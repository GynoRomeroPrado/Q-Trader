"""Risk manager — position sizing, stop-loss, and trade guards."""

from __future__ import annotations

import asyncio
import logging
import time

from config.settings import settings
from core.exchange_client import ExchangeClient
from core.strategy_base import Signal

logger = logging.getLogger(__name__)


class RiskManager:
    """Validates trade signals against risk constraints."""

    def __init__(self, exchange: ExchangeClient) -> None:
        self._exchange = exchange
        self._open_trades: int = 0
        self._last_trade_time: float = 0.0

    async def validate(self, signal: Signal, symbol: str | None = None) -> bool:
        """Return True if the signal passes all risk checks."""
        sym = symbol or settings.trading.symbol

        # 1. Check cooldown
        elapsed = time.time() - self._last_trade_time
        if elapsed < settings.trading.cooldown_seconds:
            return False

        # 2. Check max open trades
        if self._open_trades >= settings.trading.max_open_trades:
            return False

        # 3. Check minimum balance for BUY
        if signal == Signal.BUY:
            quote = sym.split("/")[1]  # e.g. "USDT"
            balance = await self._exchange.fetch_balance(quote)
            if balance["free"] < 10.0:
                logger.warning(f"🚫 Insufficient {quote} balance: {balance['free']}")
                return False

        return True

    def calculate_position_size(self, balance: float, price: float, atr_proxy: float = 0.001) -> float:
        """Position Sizing dinámico (Fixed Fractional Inverso a Volatilidad)."""
        base_risk_pct = settings.trading.max_position_pct
        
        # Evitar división por cero si el spread es anormalmente estrecho
        safe_atr = max(atr_proxy, 0.00001)
        
        # Escalar numérico: Baseline crypto ATR_proxy típico es ~0.001 (10 bps)
        vol_scaler = 0.001 / safe_atr 
        
        adjusted_risk = base_risk_pct * vol_scaler
        
        # Hard caps de seguridad para el Mini PC (0.5% min, 5% max)
        adjusted_risk = min(max(adjusted_risk, 0.005), 0.05)
        
        usd_size = balance * adjusted_risk
        
        logger.info(
            f"📐 Position Size: {usd_size/price:.8f} "
            f"(${usd_size:.2f} @ {adjusted_risk*100:.2f}% risk | ATR Proxy: {safe_atr:.5f})"
        )
        return usd_size / price

    def calculate_stop_loss(self, entry_price: float, side: str = "buy") -> float:
        """Calculate stop-loss price."""
        if side == "buy":
            sl = entry_price * (1 - settings.trading.stop_loss_pct)
        else:
            sl = entry_price * (1 + settings.trading.stop_loss_pct)
        logger.info(f"🛑 Stop-loss set at {sl:.2f}")
        return sl

    def record_trade_opened(self) -> None:
        self._open_trades += 1
        self._last_trade_time = time.time()

    def record_trade_closed(self) -> None:
        self._open_trades = max(0, self._open_trades - 1)

    @property
    def open_trades(self) -> int:
        return self._open_trades
