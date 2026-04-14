"""Alert Manager — Push notifications via Telegram.

Sends real-time alerts for critical trading events.
Fully optional: does nothing if TELEGRAM_ENABLED=False or credentials missing.
Non-blocking: all sends are fire-and-forget via asyncio tasks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from config.settings import settings

logger = logging.getLogger(__name__)


class AlertManager:
    """Async Telegram alert sender for critical trading events.

    Usage:
        alert = AlertManager()
        await alert.send("🚨 Circuit Breaker activated!")
        await alert.trade_completed(signal="BUY", amount=0.001, price=87000)
    """

    # Throttle: max 1 message per event type per N seconds
    _THROTTLE_SEC = 30

    def __init__(self) -> None:
        self._enabled = settings.telegram.enabled
        self._token = settings.telegram.bot_token
        self._chat_id = settings.telegram.chat_id
        self._last_sent: dict[str, float] = {}

        if self._enabled and self._token and self._chat_id:
            logger.info("📱 AlertManager enabled (Telegram)")
        elif self._enabled:
            logger.warning(
                "📱 AlertManager enabled but missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
            )
            self._enabled = False
        else:
            logger.info("📱 AlertManager disabled (set TELEGRAM_ENABLED=true to enable)")

    def _is_throttled(self, event_key: str) -> bool:
        """Check if this event type was sent recently."""
        import time
        now = time.time()
        last = self._last_sent.get(event_key, 0)
        if now - last < self._THROTTLE_SEC:
            return True
        self._last_sent[event_key] = now
        return False

    async def send(self, message: str, event_key: str = "generic") -> None:
        """Send a Telegram message (non-blocking, fire-and-forget).

        Args:
            message: Text to send (supports Telegram Markdown).
            event_key: Throttle key — same key won't fire twice within THROTTLE_SEC.
        """
        if not self._enabled:
            return

        if self._is_throttled(event_key):
            return

        try:
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = {
                "chat_id": self._chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.warning(f"Telegram API error {resp.status}: {body[:100]}")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    # ──────────────────────────────────────────────────────────
    # Pre-built event alerts
    # ──────────────────────────────────────────────────────────

    async def circuit_breaker(self, reason: str) -> None:
        await self.send(
            f"🚨 *CIRCUIT BREAKER ACTIVATED*\n\n"
            f"Reason: `{reason[:200]}`\n"
            f"Action: All trading paused",
            event_key="circuit_breaker",
        )

    async def drawdown_limit(self, reason: str) -> None:
        await self.send(
            f"📉 *DRAWDOWN KILL-SWITCH*\n\n"
            f"`{reason[:200]}`\n"
            f"Action: Trading halted until manual reset",
            event_key="drawdown",
        )

    async def trade_completed(
        self, signal: str, amount: float, price: float, via: str = "paper"
    ) -> None:
        emoji = "🟢" if signal == "BUY" else "🔴"
        await self.send(
            f"{emoji} *Trade Executed ({via.upper()})*\n\n"
            f"Signal: `{signal}`\n"
            f"Amount: `{amount:.8f}`\n"
            f"Price: `{price:.2f}`",
            event_key=f"trade_{signal}",
        )

    async def bot_started(self, symbol: str, mode: str) -> None:
        await self.send(
            f"⚡ *Q-Trader Started*\n\n"
            f"Symbol: `{symbol}`\n"
            f"Mode: `{mode}`",
            event_key="startup",
        )

    async def bot_error(self, error: str) -> None:
        await self.send(
            f"❌ *Bot Error*\n\n"
            f"`{error[:300]}`",
            event_key="error",
        )

    async def loss_streak(self, count: int, cooldown_sec: int) -> None:
        await self.send(
            f"🧊 *Loss Streak Alert*\n\n"
            f"Consecutive losses: `{count}`\n"
            f"Cooldown: `{cooldown_sec}s`",
            event_key="loss_streak",
        )

    async def network_issue(self, detail: str) -> None:
        await self.send(
            f"🌐 *Network Issue*\n\n"
            f"`{detail[:200]}`",
            event_key="network",
        )
