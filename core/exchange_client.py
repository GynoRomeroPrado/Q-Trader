"""Async exchange client with WebSocket auto-reconnection.

Wraps ccxt.pro with exponential backoff on connection failures.
The event loop is never blocked — all reconnection happens cooperatively.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import ccxt.pro as ccxtpro
import pandas as pd

from config.settings import settings

logger = logging.getLogger(__name__)

# Reconnection constants
_BASE_DELAY = 1.0      # Initial backoff in seconds
_MAX_DELAY = 60.0       # Cap backoff at 60s
_MAX_RETRIES = 50       # Then give up (≈30 min of total retrying)
_JITTER_RANGE = 0.5     # ±0.5s random jitter


class ExchangeClient:
    """Unified async interface to a crypto exchange via ccxt.pro.

    All WebSocket methods (`watch_*`) include automatic reconnection
    with exponential backoff + jitter. REST methods (`fetch_*`) retry
    up to 3 times with linear backoff.
    """

    def __init__(self) -> None:
        self._exchange: ccxtpro.Exchange | None = None
        self._build_exchange()

    def _build_exchange(self) -> None:
        """Create (or recreate) the ccxt.pro exchange instance.

        Supports market types: spot, future, swap (perpetual).
        For futures: sets leverage and margin mode after init.
        """
        exchange_class = getattr(ccxtpro, settings.exchange.id)
        market_type = settings.exchange.market_type  # spot | future | swap

        self._exchange = exchange_class({
            "apiKey": settings.exchange.api_key,
            "secret": settings.exchange.secret,
            "enableRateLimit": True,
            "options": {"defaultType": market_type},
        })

        if settings.exchange.sandbox:
            self._exchange.set_sandbox_mode(True)
            logger.info("🧪 Exchange running in SANDBOX mode")

        self._is_futures = market_type in ("future", "swap")
        self._reconnect_count = 0

        if self._is_futures:
            logger.info(
                f"📊 Futures mode: {market_type} | "
                f"Leverage: {settings.trading.leverage}x | "
                f"Margin: {settings.trading.margin_mode}"
            )

    async def _configure_futures(self, symbol: str) -> None:
        """Set leverage and margin mode for futures trading.

        Called once before first trade. Safe to call multiple times
        (exchanges silently accept if already configured).
        """
        if not self._is_futures:
            return

        try:
            # Set margin mode (isolated/cross)
            await self._exchange.set_margin_mode(
                settings.trading.margin_mode,
                symbol,
            )
            logger.info(f"✅ Margin mode set: {settings.trading.margin_mode}")
        except Exception as e:
            # Some exchanges don't support this or it's already set
            logger.debug(f"Margin mode set skipped: {e}")

        try:
            # Set leverage
            await self._exchange.set_leverage(
                settings.trading.leverage,
                symbol,
            )
            logger.info(f"✅ Leverage set: {settings.trading.leverage}x")
        except Exception as e:
            logger.debug(f"Leverage set skipped: {e}")

    # ------------------------------------------------------------------
    # Resilient WebSocket wrapper
    # ------------------------------------------------------------------

    async def _ws_call_with_retry(self, coro_factory, label: str):
        """Execute a WebSocket coroutine with exponential backoff.

        Args:
            coro_factory: Callable that returns a new coroutine each retry.
            label: Human-readable label for logging.
        """
        retries = 0
        while retries < _MAX_RETRIES:
            try:
                return await coro_factory()
            except (
                ccxtpro.NetworkError,
                ccxtpro.ExchangeNotAvailable,
                ccxtpro.RequestTimeout,
                ConnectionError,
                OSError,
            ) as e:
                retries += 1
                delay = min(_BASE_DELAY * (2 ** retries), _MAX_DELAY)
                jitter = random.uniform(-_JITTER_RANGE, _JITTER_RANGE)
                wait = max(0.1, delay + jitter)

                logger.warning(
                    f"🔌 {label} connection lost (attempt {retries}/{_MAX_RETRIES}): "
                    f"{type(e).__name__}: {e} — retrying in {wait:.1f}s"
                )

                # Rebuild exchange instance on repeated failures
                if retries % 5 == 0:
                    logger.info("♻️  Rebuilding exchange instance...")
                    await self._safe_close()
                    self._build_exchange()

                await asyncio.sleep(wait)

            except asyncio.CancelledError:
                raise  # Let cancellation propagate cleanly

            except Exception as e:
                # Non-network errors (auth, invalid symbol, etc.) — do NOT retry
                logger.error(f"❌ {label} fatal error: {type(e).__name__}: {e}")
                raise

        raise RuntimeError(
            f"{label}: max retries ({_MAX_RETRIES}) exceeded, giving up"
        )

    # ------------------------------------------------------------------
    # Resilient REST wrapper
    # ------------------------------------------------------------------

    async def _rest_call_with_retry(self, coro_factory, label: str,
                                     max_retries: int = 3):
        """Execute a REST call with linear backoff (3 attempts)."""
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_factory()
            except (
                ccxtpro.NetworkError,
                ccxtpro.ExchangeNotAvailable,
                ccxtpro.RequestTimeout,
            ) as e:
                if attempt == max_retries:
                    raise
                wait = attempt * 2
                logger.warning(
                    f"🔁 {label} REST retry {attempt}/{max_retries}: "
                    f"{type(e).__name__} — waiting {wait}s"
                )
                await asyncio.sleep(wait)

    # ------------------------------------------------------------------
    # Market Data (WebSocket)
    # ------------------------------------------------------------------

    async def watch_ohlcv(self, symbol: str | None = None,
                          timeframe: str | None = None) -> list[list]:
        """Stream OHLCV candles via WebSocket with auto-reconnection."""
        sym = symbol or settings.trading.symbol
        tf = timeframe or settings.trading.timeframe
        return await self._ws_call_with_retry(
            lambda: self._exchange.watch_ohlcv(sym, tf),
            f"watch_ohlcv({sym}/{tf})",
        )

    # ------------------------------------------------------------------
    # Market Data (REST — fallback)
    # ------------------------------------------------------------------

    async def fetch_ohlcv(self, symbol: str | None = None,
                          timeframe: str | None = None,
                          limit: int = 100) -> pd.DataFrame:
        """Fetch historical OHLCV via REST with retry."""
        sym = symbol or settings.trading.symbol
        tf = timeframe or settings.trading.timeframe
        data = await self._rest_call_with_retry(
            lambda: self._exchange.fetch_ohlcv(sym, tf, limit=limit),
            f"fetch_ohlcv({sym})",
        )
        df = pd.DataFrame(
            data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    async def fetch_ticker(self, symbol: str | None = None) -> dict:
        sym = symbol or settings.trading.symbol
        return await self._rest_call_with_retry(
            lambda: self._exchange.fetch_ticker(sym),
            f"fetch_ticker({sym})",
        )

    # ------------------------------------------------------------------
    # Account (REST)
    # ------------------------------------------------------------------

    async def fetch_balance(self, asset: str = "USDT") -> dict[str, float]:
        """Return {'free': x, 'used': y, 'total': z} for given asset."""
        balance = await self._rest_call_with_retry(
            lambda: self._exchange.fetch_balance(),
            "fetch_balance",
        )
        return {
            "free": float(balance.get(asset, {}).get("free", 0)),
            "used": float(balance.get(asset, {}).get("used", 0)),
            "total": float(balance.get(asset, {}).get("total", 0)),
        }

    # ------------------------------------------------------------------
    # Orders (REST)
    # ------------------------------------------------------------------

    async def create_market_order(self, symbol: str, side: str,
                                  amount: float) -> dict[str, Any]:
        """Place a market order. side = 'buy' | 'sell'."""
        logger.info(f"📤 Market {side.upper()} {amount} {symbol}")
        order = await self._rest_call_with_retry(
            lambda: self._exchange.create_market_order(symbol, side, amount),
            f"create_market_order({symbol})",
        )
        logger.info(f"✅ Order filled: {order['id']} @ {order.get('average', 'N/A')}")
        return order

    async def create_limit_order(self, symbol: str, side: str,
                                 amount: float, price: float) -> dict[str, Any]:
        """Place a limit order."""
        logger.info(f"📤 Limit {side.upper()} {amount} {symbol} @ {price}")
        order = await self._rest_call_with_retry(
            lambda: self._exchange.create_limit_order(symbol, side, amount, price),
            f"create_limit_order({symbol})",
        )
        logger.info(f"📝 Order placed: {order['id']}")
        return order

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _safe_close(self) -> None:
        """Close exchange connection, ignoring errors."""
        try:
            if self._exchange:
                await self._exchange.close()
        except Exception:
            pass

    async def close(self) -> None:
        await self._safe_close()
        logger.info("Exchange connection closed")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
