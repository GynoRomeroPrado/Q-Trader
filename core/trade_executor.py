"""Trade executor — HFT Maker-only Microstructure execution."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import settings
from core.exchange_client import ExchangeClient
from core.order_manager import OrderManager
from core.risk_manager import RiskManager
from core.sentiment_oracle import SentimentOracle
from core.strategy_base import OrderBookStrategy, Signal

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Runs the HFT micro-structure order book loop."""

    def __init__(
        self,
        exchange: ExchangeClient,
        strategy: OrderBookStrategy,
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
        self.symbol = settings.trading.symbol
        self.order_manager = OrderManager(exchange, self.symbol)
        
        # Cerebro 3: Oráculo de Sentimiento (Circuit Breaker)
        api_key = getattr(settings.trading, "cryptopanic_key", "") 
        self.oracle = SentimentOracle(api_key=api_key, polling_interval=60)
        
        self._cached_quote_balance = 0.0
        self._cached_base_balance = 0.0

    async def start(self) -> None:
        """Start the trading loop."""
        self._running = True
        logger.info(
            f"🚀 Trading engine started (HFT Maker-Only): {self.symbol} | "
            f"Strategy: {self._strategy.name}"
        )

        await self.order_manager.start_watcher()
        await self.oracle.start()
        await self._update_balances()

        while self._running:
            try:
                # 1. Espera pasiva de actualizaciones del L2 (Zero CPU idle cost)
                ob = await self._exchange._ws_call_with_retry(
                    lambda: self._exchange._exchange.watch_order_book(self.symbol, limit=20),
                    f"watch_order_book({self.symbol})"
                )
                
                # 2. Análisis ultra-rápido determinístico O(1)
                signal, atr_proxy = self._strategy.process_orderbook(ob)
                
                if signal != Signal.HOLD:
                    # 1. EVALUACIÓN PÁNICO MACRO (Cerebro 3: Circuit Breaker)
                    if not await self.oracle.is_market_safe():
                        logger.warning(
                            f"🛑 SEÑAL OBI DESCARTADA: Circuit Breaker activo. "
                            f"Motivo: {self.oracle.panic_reason}"
                        )
                        # Cancelación forzosa si el Oráculo detecta pánico estando adentro
                        if self.order_manager._active_order_id:
                            await self.order_manager._safe_cancel(self.order_manager._active_order_id)
                        await asyncio.sleep(0.01)
                        continue

                    # 2. VALIDACIÓN DE RIESGO DE CARTERA
                    if await self._risk.validate(signal, self.symbol):
                        best_bid = ob["bids"][0][0]
                        best_ask = ob["asks"][0][0]
                        
                        await self._execute_maker_trade(signal, best_bid, best_ask, atr_proxy)

                # 3. THROTTLE DE SEGURIDAD (Crítico para CPU/RAM de Mini PC)
                # Ceder 10ms explícitos al event loop permite I/O a la red de FastAPI
                await asyncio.sleep(0.01)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Trading loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

        await self.oracle.stop()
        await self.order_manager.stop_watcher()
        logger.info("🛑 Trading engine stopped")

    async def _update_balances(self) -> None:
        quote = self.symbol.split("/")[1]
        base = self.symbol.split("/")[0]
        bal_quote = await self._exchange.fetch_balance(quote)
        bal_base = await self._exchange.fetch_balance(base)
        self._cached_quote_balance = bal_quote["free"]
        self._cached_base_balance = bal_base["free"]

    async def _execute_maker_trade(
        self, signal: Signal, best_bid: float, best_ask: float, atr_proxy: float
    ) -> None:
        """Delegates Pegging execution to OrderManager without blocking the Order Book entirely."""
        await self._update_balances()
        
        price = best_bid if signal == Signal.BUY else best_ask
        balance = self._cached_quote_balance if signal == Signal.BUY else self._cached_base_balance
        
        if signal == Signal.SELL and balance <= 0:
            logger.debug("Omitiendo SELL: Sin balance base suficiente")
            return

        target_amount = self._risk.calculate_position_size(
            balance=self._cached_quote_balance,  # Usar USD total para scaling
            price=price,
            atr_proxy=atr_proxy
        )
        
        if signal == Signal.SELL:
            target_amount = min(target_amount, balance)

        # Delegamos ejecución. Bloquea iteración actual pero es intencional:
        # no queremos acumular múltiples pegs si ya estamos cazando el spread.
        result = await self.order_manager.execute_maker_pegging(signal, target_amount)
        
        if not result or result["status"] == "failed":
            logger.info("❌ Ejecución Pegging abortada/rechazada")
            return

        filled = result["status"] in ["filled", "partial"]
        filled_amount = result["filled"]
        
        if filled:
            if signal == Signal.BUY:
                self._risk.record_trade_opened()
            else:
                self._risk.record_trade_closed()

            trade_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": self.symbol,
                "side": signal.value.lower(),
                "price": price,  # Aproximación visual
                "amount": filled_amount,
                "order_id": self.order_manager._active_order_id or "",
                "pnl": 0.0,
            }

            if self._db:
                await self._db.log_trade(trade_data)
            if self._ws:
                await self._ws.broadcast({"type": "trade", "data": trade_data})

    def stop(self) -> None:
        self._running = False
