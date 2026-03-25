"""Asynchronous Execution Engine for Maker-Only High-Frequency Trading.

Provee "pegging" (persecución de precios) usando ccxt.pro watch_orders()
y órdenes límite Post-Only para garantizar Maker Rebates.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.exchange_client import ExchangeClient
from core.strategy_base import Signal

logger = logging.getLogger(__name__)


class OrderManager:
    """Gestor de órdenes asíncrono basado en eventos WS (State Machine)."""

    def __init__(self, exchange: ExchangeClient, symbol: str) -> None:
        self.exchange = exchange
        self.symbol = symbol
        
        # Parámetros de Pegging
        self.max_chases = 3
        self.chase_delay_sec = 2.0
        
        # Estado de la orden activa
        self._active_order_id: str | None = None
        self._order_status: str | None = None
        self._order_filled: float = 0.0
        
        self._watcher_task: asyncio.Task | None = None
        self._is_running = False

    async def start_watcher(self) -> None:
        """Inicia la corrutina que escucha eventos de órdenes en tiempo real."""
        self._is_running = True
        self._watcher_task = asyncio.create_task(self._watch_orders_loop())

    async def stop_watcher(self) -> None:
        """Detiene el listener gracefully."""
        self._is_running = False
        if self._watcher_task:
            self._watcher_task.cancel()
            try:
                await self._watcher_task
            except asyncio.CancelledError:
                pass

    async def _watch_orders_loop(self) -> None:
        """State machine reactiva a WS watch_orders."""
        logger.info(f"👁️ OrderManager Watcher iniciado para {self.symbol}")
        while self._is_running:
            try:
                # CCXT bloquea aquí hasta recibir un evento de trade/orden
                orders = await self.exchange._ws_call_with_retry(
                    lambda: self.exchange._exchange.watch_orders(self.symbol),
                    f"watch_orders({self.symbol})"
                )
                
                for order in orders:
                    if order["id"] == self._active_order_id:
                        self._order_status = order["status"]
                        self._order_filled = order.get("filled", 0.0)
                        logger.debug(
                            f"🔔 WS Update -> {self._active_order_id}: "
                            f"{self._order_status.upper()} "
                            f"(Llenado: {self._order_filled})"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error en watch_orders: {e}")
                await asyncio.sleep(1)

    async def _safe_cancel(self, order_id: str) -> bool:
        """Cancela orden ignorando errores si ya fue llenada/cancelada en el milisegundo anterior."""
        try:
            await self.exchange._rest_call_with_retry(
                lambda: self.exchange._exchange.cancel_order(order_id, self.symbol),
                f"cancel_order({order_id})"
            )
            return True
        except Exception as e:
            err = str(e).lower()
            if "unknown order" in err or "filled" in err or "canceled" in err:
                logger.debug(f"Cancelación ignorada (orden ya llenada/cancelada): {order_id}")
                return False
            logger.warning(f"Error al cancelar {order_id}: {e}")
            return False

    async def execute_maker_pegging(self, signal: Signal, total_amount: float) -> dict[str, Any] | None:
        """Ejecuta orden Maker Post-Only y persigue el precio (pegging)."""
        chases = 0
        rem_amount = total_amount
        cum_filled = 0.0

        # Binance rechaza Post-Only si cruza el spread
        params = {"postOnly": True}
        
        while chases <= self.max_chases and rem_amount > 0:
            try:
                # 1. Monitorear el L2 fresco para cada intento de "chase"
                ob = await self.exchange._exchange.watch_order_book(self.symbol, limit=5)
                best_bid = ob["bids"][0][0]
                best_ask = ob["asks"][0][0]
                
                # Para ser Maker exclusivo: BUY va al Best Bid, SELL al Best Ask
                target_price = best_bid if signal == Signal.BUY else best_ask
                
                logger.info(
                    f"🎯 [Pegging {chases}/{self.max_chases}] "
                    f"Maker {signal.value} de {rem_amount} @ {target_price}"
                )

                # 2. Inyectar Orden Post-Only
                order = await self.exchange._rest_call_with_retry(
                    lambda: self.exchange._exchange.create_order(
                        self.symbol, "limit", signal.value.lower(), rem_amount, target_price, params
                    ),
                    f"create_post_only({target_price})"
                )
                self._active_order_id = order["id"]
                self._order_status = order["status"]
                
            except Exception as e:
                if "post only" in str(e).lower() or "immediately match" in str(e).lower():
                    logger.warning(f"⚠️ Post-Only rechazado @ {target_price} (Mercado muy rápido). Re-chase...")
                    chases += 1
                    await asyncio.sleep(0.1)
                    continue
                else:
                    logger.error(f"❌ Fallo crítico inyectando Maker Order: {e}")
                    return None

            # 3. Monitoreo pasivo (timeout o fill)
            start_time = asyncio.get_event_loop().time()
            local_filled = False
            
            while asyncio.get_event_loop().time() - start_time < self.chase_delay_sec:
                if self._order_status == "closed":
                    logger.info(f"✅ Orden llenada completamente: {self._active_order_id}")
                    cum_filled += rem_amount
                    rem_amount = 0
                    local_filled = True
                    break
                
                # Check OB drift (cancelar si el precio se aleja > 2 ticks)
                drift_ob = await self.exchange._exchange.watch_order_book(self.symbol, limit=5)
                curr_bid = drift_ob["bids"][0][0]
                curr_ask = drift_ob["asks"][0][0]
                curr_target = curr_bid if signal == Signal.BUY else curr_ask
                
                # Distancia (simplificada en precio crudo)
                if abs(curr_target - target_price) / target_price > 0.0005:  # ~0.05% de drift
                    logger.info("📉 Mercado drafteó. Cancelando para re-posicionar...")
                    break  # Rompe el loop de delay, gatilla cancelación
                    
                await asyncio.sleep(0.05)  # Yield al event loop

            if rem_amount == 0:
                break  # Éxito total

            # 4. Timeout o Drift -> Cancelar y evaluar re-chase
            if self._order_status in ["open", "partial_fill"]:
                logger.info(f"⏱️ Cancelando vieja orden {self._active_order_id} para perseguir...")
                await self._safe_cancel(self._active_order_id)
                await asyncio.sleep(0.2)  # Dar tiempo al WS de reportar el filled_amount real
                
                filled_amount = self._order_filled
                rem_amount = rem_amount - filled_amount
                cum_filled += filled_amount
                chases += 1

        # Resumen final
        if rem_amount > 0:
            logger.warning(
                f"🛑 Pegging abortado tras {self.max_chases} persecuciones. "
                f"Llenado parcial: {cum_filled}/{total_amount}"
            )

        return {
            "status": "filled" if rem_amount == 0 else "partial" if cum_filled > 0 else "failed",
            "filled": cum_filled,
            "chases": chases
        }
