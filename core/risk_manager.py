"""Risk Manager — Position Sizing, Drawdown Control, and Trade Guards.

Components:
    RiskManager      — main validator (position sizing, cooldown, max trades)
    DrawdownManager  — peak tracking, daily/total drawdown kill-switch
    LossStreakGuard  — consecutive loss cooldown
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config.settings import settings
from core.exchange_client import ExchangeClient
from core.strategy_base import Signal

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Drawdown Manager
# ──────────────────────────────────────────────────────────────

class DrawdownManager:
    """Tracks peak balance and enforces drawdown kill-switches.

    Two independent limits:
        - Daily drawdown: resets every UTC midnight
        - Max drawdown from peak: lifetime (or until manual reset)
    """

    def __init__(
        self,
        max_daily_loss_pct: float = 0.02,
        max_drawdown_pct: float = 0.05,
    ) -> None:
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct

        self._peak_balance: float = 0.0
        self._daily_start_balance: float = 0.0
        self._last_reset_date: str = ""
        self._is_killed: bool = False
        self._kill_reason: str = ""

    def initialize(self, balance: float) -> None:
        """Set initial balances on first call or bot restart."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._peak_balance == 0.0:
            self._peak_balance = balance
        if self._daily_start_balance == 0.0 or self._last_reset_date != today:
            self._daily_start_balance = balance
            self._last_reset_date = today
            self._is_killed = False
            self._kill_reason = ""
            logger.info(
                f"📊 Drawdown Manager reset — Peak: {self._peak_balance:.2f}, "
                f"Daily Start: {self._daily_start_balance:.2f}"
            )

    def update(self, current_balance: float) -> None:
        """Update peak balance if new high. Called after every balance refresh."""
        if current_balance > self._peak_balance:
            self._peak_balance = current_balance

        # Check for daily reset at UTC midnight
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._daily_start_balance = current_balance
            self._last_reset_date = today
            # Un-kill if daily reset and we haven't hit max drawdown
            if self._kill_reason.startswith("Daily"):
                self._is_killed = False
                self._kill_reason = ""
                logger.info("🔄 Daily drawdown limit reset at UTC midnight")

    def check(self, current_balance: float) -> tuple[bool, str]:
        """Check if trading is allowed based on drawdown limits.

        Returns:
            (allowed, reason)
        """
        if self._is_killed:
            return False, self._kill_reason

        self.update(current_balance)

        # Daily drawdown check
        if self._daily_start_balance > 0:
            daily_loss = (self._daily_start_balance - current_balance) / self._daily_start_balance
            if daily_loss >= self.max_daily_loss_pct:
                self._is_killed = True
                self._kill_reason = (
                    f"Daily drawdown limit: {daily_loss:.2%} >= {self.max_daily_loss_pct:.2%}"
                )
                logger.error(f"🛑 KILL SWITCH: {self._kill_reason}")
                return False, self._kill_reason

        # Max drawdown from peak
        if self._peak_balance > 0:
            drawdown = (self._peak_balance - current_balance) / self._peak_balance
            if drawdown >= self.max_drawdown_pct:
                self._is_killed = True
                self._kill_reason = (
                    f"Max drawdown from peak: {drawdown:.2%} >= {self.max_drawdown_pct:.2%} "
                    f"(Peak: {self._peak_balance:.2f}, Current: {current_balance:.2f})"
                )
                logger.error(f"🛑 KILL SWITCH: {self._kill_reason}")
                return False, self._kill_reason

        return True, "OK"

    def reset(self, balance: float) -> None:
        """Manual reset — use after reviewing and accepting losses."""
        self._peak_balance = balance
        self._daily_start_balance = balance
        self._last_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._is_killed = False
        self._kill_reason = ""
        logger.info(f"🔄 Drawdown Manager manually reset at {balance:.2f}")

    def get_status(self) -> dict:
        return {
            "peak_balance": self._peak_balance,
            "daily_start": self._daily_start_balance,
            "is_killed": self._is_killed,
            "kill_reason": self._kill_reason,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
        }


# ──────────────────────────────────────────────────────────────
# Loss Streak Guard
# ──────────────────────────────────────────────────────────────

class LossStreakGuard:
    """Blocks trading after N consecutive losses until cooldown expires."""

    def __init__(
        self,
        max_consecutive: int = 3,
        cooldown_sec: int = 900,
    ) -> None:
        self.max_consecutive = max_consecutive
        self.cooldown_sec = cooldown_sec
        self._consecutive_losses: int = 0
        self._cooldown_until: float = 0.0

    def record_win(self) -> None:
        """Reset streak on a winning trade."""
        self._consecutive_losses = 0

    def record_loss(self) -> None:
        """Increment loss counter and activate cooldown if limit reached."""
        self._consecutive_losses += 1
        if self._consecutive_losses >= self.max_consecutive:
            self._cooldown_until = time.time() + self.cooldown_sec
            logger.warning(
                f"🧊 Loss streak guard: {self._consecutive_losses} consecutive losses. "
                f"Cooldown for {self.cooldown_sec}s"
            )

    def is_allowed(self) -> tuple[bool, str]:
        """Check if trading is allowed."""
        if self._consecutive_losses >= self.max_consecutive:
            remaining = self._cooldown_until - time.time()
            if remaining > 0:
                return False, (
                    f"Loss streak cooldown: {self._consecutive_losses} losses, "
                    f"{remaining:.0f}s remaining"
                )
            # Cooldown expired — reset
            self._consecutive_losses = 0
        return True, "OK"

    def get_status(self) -> dict:
        remaining = max(0, self._cooldown_until - time.time())
        return {
            "consecutive_losses": self._consecutive_losses,
            "max_consecutive": self.max_consecutive,
            "cooldown_remaining_sec": round(remaining, 1),
            "is_blocked": remaining > 0 and self._consecutive_losses >= self.max_consecutive,
        }


# ──────────────────────────────────────────────────────────────
# Risk Manager (Main)
# ──────────────────────────────────────────────────────────────

class RiskManager:
    """Validates trade signals against risk constraints.

    Orchestrates:
        - Position sizing (Kelly-inspired, inverse to volatility)
        - Trade cooldown
        - Max open trade limits
        - Balance checks
        - DrawdownManager (daily/total kill-switch)
        - LossStreakGuard (consecutive loss cooldown)
        - Trailing stop-loss calculation
    """

    def __init__(self, exchange: ExchangeClient) -> None:
        self._exchange = exchange
        self._open_trades: int = 0
        self._last_trade_time: float = 0.0

        # Sub-managers from settings
        self.drawdown = DrawdownManager(
            max_daily_loss_pct=settings.risk.max_daily_loss_pct,
            max_drawdown_pct=settings.risk.max_drawdown_pct,
        )
        self.loss_guard = LossStreakGuard(
            max_consecutive=settings.risk.max_consecutive_losses,
            cooldown_sec=settings.risk.loss_streak_cooldown_sec,
        )

        # Trailing stop state
        self._trailing_high: float = 0.0
        self._trailing_stop_pct = settings.risk.trailing_stop_pct

    async def validate(self, signal: Signal, symbol: str | None = None) -> bool:
        """Return True if the signal passes all risk checks."""
        sym = symbol or settings.trading.symbol

        # 1. Drawdown kill-switch check
        quote = sym.split("/")[1]
        balance = await self._exchange.fetch_balance(quote)
        allowed, reason = self.drawdown.check(balance["free"])
        if not allowed:
            logger.warning(f"🚫 Drawdown block: {reason}")
            return False

        # 2. Loss streak cooldown
        allowed, reason = self.loss_guard.is_allowed()
        if not allowed:
            logger.warning(f"🚫 Loss streak block: {reason}")
            return False

        # 3. Check cooldown
        elapsed = time.time() - self._last_trade_time
        if elapsed < settings.trading.cooldown_seconds:
            return False

        # 4. Check max open trades
        if self._open_trades >= settings.trading.max_open_trades:
            return False

        # 5. Check minimum balance for BUY
        if signal == Signal.BUY:
            if balance["free"] < 10.0:
                logger.warning(f"🚫 Insufficient {quote} balance: {balance['free']}")
                return False

        return True

    def calculate_position_size(
        self, balance: float, price: float, atr_proxy: float = 0.001
    ) -> float:
        """Position Sizing — Fixed Fractional Inverse to Volatility."""
        base_risk_pct = settings.trading.max_position_pct

        safe_atr = max(atr_proxy, 0.00001)

        # Baseline crypto ATR_proxy typical ~0.001 (10 bps)
        vol_scaler = 0.001 / safe_atr

        adjusted_risk = base_risk_pct * vol_scaler

        # Hard caps (0.5% min, 5% max)
        adjusted_risk = min(max(adjusted_risk, 0.005), 0.05)

        usd_size = balance * adjusted_risk

        logger.info(
            f"📐 Position Size: {usd_size / price:.8f} "
            f"(${usd_size:.2f} @ {adjusted_risk * 100:.2f}% risk | ATR: {safe_atr:.5f})"
        )
        return usd_size / price

    def calculate_stop_loss(self, entry_price: float, side: str = "buy") -> float:
        """Calculate fixed stop-loss price."""
        if side == "buy":
            sl = entry_price * (1 - settings.trading.stop_loss_pct)
        else:
            sl = entry_price * (1 + settings.trading.stop_loss_pct)
        logger.info(f"🛑 Stop-loss set at {sl:.2f}")
        return sl

    def calculate_trailing_stop(self, current_price: float, side: str = "buy") -> float:
        """Update and return trailing stop-loss price.

        Tracks the highest price seen since entry and places the stop
        at trailing_stop_pct below that peak.
        """
        if current_price > self._trailing_high:
            self._trailing_high = current_price

        if side == "buy":
            return self._trailing_high * (1 - self._trailing_stop_pct)
        else:
            # For short positions, trailing stop above lowest price
            return self._trailing_high * (1 + self._trailing_stop_pct)

    def reset_trailing_stop(self) -> None:
        """Reset trailing state for new position."""
        self._trailing_high = 0.0

    def record_trade_opened(self) -> None:
        self._open_trades += 1
        self._last_trade_time = time.time()
        self.reset_trailing_stop()

    def record_trade_closed(self, is_win: bool = True) -> None:
        self._open_trades = max(0, self._open_trades - 1)
        if is_win:
            self.loss_guard.record_win()
        else:
            self.loss_guard.record_loss()

    @property
    def open_trades(self) -> int:
        return self._open_trades

    def get_full_status(self) -> dict:
        """Complete risk status for dashboard."""
        return {
            "open_trades": self._open_trades,
            "drawdown": self.drawdown.get_status(),
            "loss_streak": self.loss_guard.get_status(),
            "trailing_high": self._trailing_high,
            "trailing_stop_pct": self._trailing_stop_pct,
        }
