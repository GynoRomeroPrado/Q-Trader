"""Trade executor — main async trading loop."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import pandas as pd

from config.settings import settings
from core.exchange_client import ExchangeClient
from core.risk_manager import RiskManager
from core.strategy_base import Signal, Strategy

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Runs the main trading loop: data → signal → validation → execution."""

    def __init__(
        self,
        exchange: ExchangeClient,
        strategy: Strategy,
        risk_manager: RiskManager,
        db=None,
        ws_manager=None,
    ) -> None:
        self._exchange = exchange
        self._strategy = strategy
        self._risk = risk_manager
        self._db = db
        self._ws = ws_manager
        self._running = False

    async def start(self) -> None:
        """Start the trading loop."""
        self._running = True
        symbol = settings.trading.symbol
        timeframe = settings.trading.timeframe
        logger.info(
            f"🚀 Trading engine started: {symbol} | {timeframe} | "
            f"Strategy: {self._strategy.name}"
        )

        # Initial historical candles for indicators
        df = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=100)
        logger.info(f"📊 Loaded {len(df)} historical candles")

        while self._running:
            try:
                # Stream new candles via WebSocket
                new_candles = await self._exchange.watch_ohlcv(symbol, timeframe)

                # Update DataFrame with new data
                new_df = pd.DataFrame(
                    new_candles,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], unit="ms")

                # Merge: keep last 200 candles
                df = pd.concat([df, new_df]).drop_duplicates(
                    subset=["timestamp"], keep="last"
                ).tail(200).reset_index(drop=True)

                # Generate signal
                signal = await self._strategy.generate_signal(df.copy())

                if signal == Signal.HOLD:
                    continue

                # Validate against risk rules
                if not await self._risk.validate(signal, symbol):
                    logger.info(f"⛔ Signal {signal.value} rejected by risk manager")
                    continue

                # Execute trade
                await self._execute_trade(signal, symbol, df)

            except asyncio.CancelledError:
                logger.info("Trading loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Trading loop error: {e}", exc_info=True)
                await asyncio.sleep(5)  # Brief pause before retry

        logger.info("🛑 Trading engine stopped")

    async def _execute_trade(
        self, signal: Signal, symbol: str, df: pd.DataFrame
    ) -> None:
        """Execute a single trade based on the signal."""
        quote = symbol.split("/")[1]  # e.g. "USDT"
        base = symbol.split("/")[0]   # e.g. "BTC"

        side = "buy" if signal == Signal.BUY else "sell"
        current_price = float(df.iloc[-1]["close"])

        if side == "buy":
            balance = await self._exchange.fetch_balance(quote)
            amount = self._risk.calculate_position_size(
                balance["free"], current_price
            )
        else:
            balance = await self._exchange.fetch_balance(base)
            amount = balance["free"]
            if amount <= 0:
                logger.warning(f"No {base} to sell")
                return

        # Place order
        order = await self._exchange.create_market_order(symbol, side, amount)

        # Record in risk manager
        if side == "buy":
            self._risk.record_trade_opened()
        else:
            self._risk.record_trade_closed()

        # Log trade
        trade_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "price": order.get("average", current_price),
            "amount": amount,
            "order_id": order.get("id", ""),
            "pnl": 0.0,  # PnL calculated on close
        }

        if self._db:
            await self._db.log_trade(trade_data)

        if self._ws:
            await self._ws.broadcast({
                "type": "trade",
                "data": trade_data,
            })

        logger.info(f"✅ Trade executed: {trade_data}")

    def stop(self) -> None:
        """Signal the trading loop to stop."""
        self._running = False
