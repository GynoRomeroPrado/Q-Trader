"""Trade Executor — High-Performance Agentic Pipeline.

Architecture (5-State Pipeline):
    PERCEPTION  → Read order book + oracle status
    STRATEGY    → Multi-signal analysis + sentiment → TradeDecision
    VALIDATION  → Risk manager checks (drawdown, loss-rate, trailing stop)
    EXECUTION   → Paper wallet or real order manager
    LOGGING     → Audit trail of entire decision chain

Hot-path (tick-by-tick): Direct async function chain for minimal latency.
Cold-path (periodic): LangGraph StateGraph available for high-level orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypedDict

# LangGraph is optional — only used for cold-path orchestration
try:
    from langgraph.graph import StateGraph, START, END
    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False

from config.settings import settings
from core.alert_manager import AlertManager
from core.audit_logger import AuditLogger, audit_action
from core.exchange_client import ExchangeClient
from core.order_manager import OrderManager
from core.paper_wallet import PaperWallet
from core.risk_manager import RiskManager
from core.sentiment_oracle import SentimentOracle
from core.strategy_base import OrderBookStrategy, Signal

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Pipeline State Definitions (LangGraph-Ready)
# ──────────────────────────────────────────────────────────────

class PipelineState(Enum):
    """Execution pipeline states for agentic orchestration."""
    IDLE = "IDLE"
    PERCEPTION = "PERCEPTION"
    STRATEGY = "STRATEGY"
    RISK_VALIDATION = "RISK_VALIDATION"
    EXECUTION = "EXECUTION"
    LOGGING = "LOGGING"
    ERROR = "ERROR"


@dataclass
class PerceptionContext:
    """Output of the PERCEPTION state — raw market data."""
    order_book: dict[str, Any] = field(default_factory=dict)
    best_bid: float = 0.0
    best_ask: float = 0.0
    market_safe: bool = True
    panic_reason: str = ""
    oracle_status: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class TradeDecision:
    """Output of the STRATEGY state — what to do."""
    signal: Signal = Signal.HOLD
    atr_proxy: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    reason: str = ""
    sentiment_score: float | None = None


@dataclass
class ExecutionResult:
    """Output of the EXECUTION state — what happened."""
    success: bool = False
    status: str = "none"
    filled: float = 0.0
    price: float = 0.0
    cost: float = 0.0
    fee: float = 0.0
    via: str = ""  # "paper" | "real" | "aborted"
    reason: str = ""
    pnl: float = 0.0  # realized PnL (only on SELL / close)


class AgentState(TypedDict):
    """Memory for a single run through the execution pipeline."""
    order_book: dict[str, Any]
    perception: PerceptionContext | None
    decision: TradeDecision | None
    validation_passed: bool | None
    execution: ExecutionResult | None


# ──────────────────────────────────────────────────────────────
# Trade Executor (State Machine)
# ──────────────────────────────────────────────────────────────

class TradeExecutor:
    """Runs the HFT micro-structure loop as a 5-state pipeline.

    Each iteration: PERCEPTION → STRATEGY → VALIDATION → EXECUTION → LOGGING.
    All state transitions are audited.
    """

    def __init__(
        self,
        exchange: ExchangeClient,
        strategy: OrderBookStrategy,
        risk_manager: RiskManager,
        db=None,
        ws_manager=None,
        audit_logger: AuditLogger | None = None,
        oracle: SentimentOracle | None = None,
        paper_wallet: PaperWallet | None = None,
    ) -> None:
        self._exchange = exchange
        self._strategy = strategy
        self._risk = risk_manager
        self._db = db
        self._ws = ws_manager
        self._audit = audit_logger
        self._running = False

        self.symbol = settings.trading.symbol
        self.order_manager = OrderManager(exchange, self.symbol)

        # Cerebro 3: Oracle (injected or created)
        self.oracle = oracle or SentimentOracle(polling_interval=300)

        # Paper Trading Mode
        self.paper_mode = settings.trading.paper_trading
        self.paper_wallet = paper_wallet
        if self.paper_mode and not self.paper_wallet:
            logger.warning("Paper mode enabled but no PaperWallet injected!")

        if self.paper_mode:
            logger.info("⚠️  Ejecución operando en modo PAPER TRADING.")
        else:
            logger.warning("🔥 Ejecución operando con FONDOS REALES.")

        # Pipeline state tracking
        self._current_state = PipelineState.IDLE
        self._cached_quote_balance = 0.0
        self._cached_base_balance = 0.0

        # Position tracking for PnL calculation
        self._entry_price: float = 0.0
        self._entry_amount: float = 0.0
        self._entry_fee: float = 0.0  # fee paid on entry leg

        # Performance metrics
        self.tick_count: int = 0
        self._tick_latency_sum: float = 0.0

        # Alert Manager (Telegram)
        self._alert = AlertManager()

        # Build LangGraph Engine (cold-path only — not used in tick loop)
        self.agent_graph = self._build_graph() if _HAS_LANGGRAPH else None

    async def _audit_transition(
        self, from_state: PipelineState, to_state: PipelineState,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log state transition via AuditLogger."""
        self._current_state = to_state
        if self._audit:
            await self._audit.log_state_transition(
                source="TradeExecutor",
                from_state=from_state.value,
                to_state=to_state.value,
                context=context,
            )

    # ──────────────────────────────────────────────────────────
    # LangGraph StateGraph Construction
    # ──────────────────────────────────────────────────────────

    def _build_graph(self):
        """Build and compile the LangGraph StateMachine."""
        graph = StateGraph(AgentState)

        # 1. Add Nodes
        graph.add_node("perceive", self.node_perceive)
        graph.add_node("strategize", self.node_strategize)
        graph.add_node("validate", self.node_validate)
        graph.add_node("execute", self.node_execute)
        graph.add_node("log", self.node_log)

        # 2. Add Conditional Routing (Edges)
        
        # perception -> strategize
        graph.add_edge(START, "perceive")
        graph.add_edge("perceive", "strategize")

        # strategize -> validate (if signal) OR log (if HOLD or PANIC)
        def route_after_strategy(state: AgentState) -> str:
            if not state.get("decision") or state["decision"].signal == Signal.HOLD:
                return "log"
            return "validate"
            
        graph.add_conditional_edges("strategize", route_after_strategy, {"validate": "validate", "log": "log"})

        # validate -> execute OR log
        def route_after_validation(state: AgentState) -> str:
            if state.get("validation_passed"):
                return "execute"
            return "log"
            
        graph.add_conditional_edges("validate", route_after_validation, {"execute": "execute", "log": "log"})

        # execute -> log
        graph.add_edge("execute", "log")
        
        # log -> END
        graph.add_edge("log", END)

        return graph.compile()

    # ──────────────────────────────────────────────────────────
    # LangGraph Node Wrappers
    # ──────────────────────────────────────────────────────────

    async def node_perceive(self, state: AgentState) -> dict:
        perception = await self._state_perceive(state["order_book"])
        return {"perception": perception}
        
    async def node_strategize(self, state: AgentState) -> dict:
        if not state.get("perception"):
            raise ValueError("Perception missing in state")
        decision = await self._state_strategize(state["order_book"], state["perception"])
        return {"decision": decision}

    async def node_validate(self, state: AgentState) -> dict:
        if not state.get("decision"):
            raise ValueError("Decision missing in state")
        passed = await self._state_validate(state["decision"])
        return {"validation_passed": passed}

    async def node_execute(self, state: AgentState) -> dict:
        if not state.get("decision"):
            raise ValueError("Decision missing in state")
        result = await self._state_execute(state["decision"])
        return {"execution": result}

    async def node_log(self, state: AgentState) -> dict:
        decision = state.get("decision")
        result = state.get("execution")
        # Ensure we always clear idle transition cleanly
        if decision:
            # We don't have execution result if we skipped execution (e.g HOLD or Risk reject)
            # Create a fake result dict or None
            if not result:
                result = ExecutionResult(success=False, status="skipped", reason="hold_or_rejected")
            await self._state_log(decision, result)
        else:
            await self._audit_transition(self._current_state, PipelineState.IDLE)
        return {}

    # ──────────────────────────────────────────────────────────
    # Hot-Path Pipeline (tick-by-tick — zero framework overhead)
    # ──────────────────────────────────────────────────────────

    async def _fast_pipeline(self, ob: dict[str, Any]) -> None:
        """Direct async pipeline — bypasses LangGraph for minimal latency.

        This is the hot-path executed on every order book tick.
        LangGraph remains available via self.agent_graph for periodic
        high-level orchestration (rebalancing, risk reviews).
        """
        # State 1: PERCEPTION
        perception = await self._state_perceive(ob)

        # State 2: STRATEGY
        decision = await self._state_strategize(ob, perception)

        if decision.signal == Signal.HOLD:
            self._current_state = PipelineState.IDLE
            return

        # State 3: RISK VALIDATION
        if not await self._state_validate(decision):
            self._current_state = PipelineState.IDLE
            return

        # State 4: EXECUTION
        result = await self._state_execute(decision)

        # State 5: LOGGING
        await self._state_log(decision, result)

    # ──────────────────────────────────────────────────────────
    # State 1: PERCEPTION
    # ──────────────────────────────────────────────────────────

    async def _state_perceive(self, ob: dict[str, Any]) -> PerceptionContext:
        """Read market data and oracle status."""
        await self._audit_transition(self._current_state, PipelineState.PERCEPTION)

        best_bid = ob["bids"][0][0] if ob.get("bids") else 0.0
        best_ask = ob["asks"][0][0] if ob.get("asks") else 0.0

        # OBI + spread for Gemini routing (non-blocking)
        bids = ob.get("bids", [])
        asks = ob.get("asks", [])
        bid_vol = sum(float(b[1]) for b in bids[:5]) if bids else 0.0
        ask_vol = sum(float(a[1]) for a in asks[:5]) if asks else 0.0
        total_vol = bid_vol + ask_vol
        obi = (bid_vol - ask_vol) / total_vol if total_vol > 0 else 0.0
        spread = (best_ask - best_bid) if best_bid > 0 else 0.0
        avg_spread = getattr(self, '_avg_spread', spread)
        spread_ratio = spread / avg_spread if avg_spread > 0 else 0.0
        # EMA of spread for ratio calculation
        self._avg_spread = avg_spread * 0.95 + spread * 0.05 if hasattr(self, '_avg_spread') else spread

        self.oracle.notify_market_conditions(obi=obi, spread_ratio=spread_ratio)

        market_safe = await self.oracle.is_market_safe()
        oracle_status = self.oracle.get_status()

        ctx = PerceptionContext(
            order_book={"bid_levels": len(ob.get("bids", [])), "ask_levels": len(ob.get("asks", []))},
            best_bid=best_bid,
            best_ask=best_ask,
            market_safe=market_safe,
            panic_reason=self.oracle.panic_reason,
            oracle_status=oracle_status,
        )

        return ctx

    # ──────────────────────────────────────────────────────────
    # State 2: STRATEGY
    # ──────────────────────────────────────────────────────────

    async def _state_strategize(
        self, ob: dict[str, Any], perception: PerceptionContext
    ) -> TradeDecision:
        """Evaluate OBI signal + sentiment → TradeDecision."""
        await self._audit_transition(PipelineState.PERCEPTION, PipelineState.STRATEGY)

        # Circuit Breaker check (Layer 1 of decision)
        if not perception.market_safe:
            if self._audit:
                await self._audit.log_action(
                    source="TradeExecutor",
                    action="CIRCUIT_BREAKER_ACTIVE",
                    detail={"reason": perception.panic_reason},
                    level="WARNING",
                )

            # Force-cancel active orders during panic
            if self.order_manager._active_order_id:
                await self.order_manager._safe_cancel(self.order_manager._active_order_id)

            # Alert via Telegram
            await self._alert.circuit_breaker(perception.panic_reason)

            return TradeDecision(
                signal=Signal.HOLD,
                reason=f"Circuit Breaker: {perception.panic_reason}",
                sentiment_score=(
                    perception.oracle_status.get("last_score")
                ),
            )

        # OBI Analysis
        signal, atr_proxy = self._strategy.process_orderbook(ob)

        decision = TradeDecision(
            signal=signal,
            atr_proxy=atr_proxy,
            price=perception.best_bid if signal == Signal.BUY else perception.best_ask,
            reason="OBI threshold crossed" if signal != Signal.HOLD else "Below threshold",
            sentiment_score=perception.oracle_status.get("last_score"),
        )

        if self._audit and signal != Signal.HOLD:
            await self._audit.log_action(
                source="TradeExecutor",
                action="SIGNAL_GENERATED",
                detail={
                    "signal": signal.value,
                    "atr_proxy": f"{atr_proxy:.6f}",
                    "best_bid": perception.best_bid,
                    "best_ask": perception.best_ask,
                },
            )

        return decision

    # ──────────────────────────────────────────────────────────
    # State 3: RISK VALIDATION
    # ──────────────────────────────────────────────────────────

    async def _state_validate(self, decision: TradeDecision) -> bool:
        """Validate against risk constraints."""
        await self._audit_transition(PipelineState.STRATEGY, PipelineState.RISK_VALIDATION)

        if decision.signal == Signal.HOLD:
            return False

        passed = await self._risk.validate(decision.signal, self.symbol)

        # Fire Telegram alert when drawdown kill-switch activates
        if not passed and self._risk.drawdown._is_killed:
            await self._alert.drawdown_limit(self._risk.drawdown._kill_reason)

        # Fire Telegram alert on loss-streak cooldown
        if not passed:
            streak_ok, _streak_reason = self._risk.loss_guard.is_allowed()
            if not streak_ok:
                await self._alert.loss_streak(
                    self._risk.loss_guard._consecutive_losses,
                    self._risk.loss_guard._cooldown_sec,
                )

        if self._audit:
            await self._audit.log_action(
                source="RiskManager",
                action="RISK_CHECK",
                detail={
                    "signal": decision.signal.value,
                    "passed": passed,
                    "open_trades": self._risk.open_trades,
                },
                level="INFO" if passed else "WARNING",
            )

        return passed

    # ──────────────────────────────────────────────────────────
    # State 4: EXECUTION
    # ──────────────────────────────────────────────────────────

    async def _state_execute(self, decision: TradeDecision) -> ExecutionResult:
        """Execute via Paper Wallet or Real Order Manager."""
        await self._audit_transition(
            PipelineState.RISK_VALIDATION, PipelineState.EXECUTION
        )

        await self._update_balances()

        price = decision.price
        balance = (
            self._cached_quote_balance
            if decision.signal == Signal.BUY
            else self._cached_base_balance
        )

        if decision.signal == Signal.SELL and balance <= 0:
            return ExecutionResult(
                success=False,
                status="skipped",
                via="none",
                reason="insufficient_base_balance",
            )

        target_amount = self._risk.calculate_position_size(
            balance=self._cached_quote_balance,
            price=price,
            atr_proxy=decision.atr_proxy,
        )

        if decision.signal == Signal.SELL:
            target_amount = min(target_amount, balance)

        # ── Route: Paper or Real ──
        if self.paper_mode and self.paper_wallet:
            result = await self.paper_wallet.execute_simulated_trade(
                signal=decision.signal,
                symbol=self.symbol,
                price=price,
                amount=target_amount,
            )
            via = "paper"
        else:
            result = await self.order_manager.execute_maker_pegging(
                decision.signal, target_amount
            )
            via = "real"

        if not result or result.get("status") == "failed":
            return ExecutionResult(
                success=False,
                status=result.get("status", "failed") if result else "failed",
                via=via,
                reason=result.get("reason", "execution_failed") if result else "null_result",
            )

        filled = result.get("status") in ["filled", "partial"]
        filled_amount = result.get("filled", 0.0)
        exec_fee = result.get("fee", 0.0)
        realized_pnl = 0.0

        if filled:
            if decision.signal == Signal.BUY:
                # Record entry for later PnL calculation
                self._entry_price = price
                self._entry_amount = filled_amount
                self._entry_fee = exec_fee
                self._risk.record_trade_opened()
            else:
                # SELL closes the position — calculate realized PnL
                sell_amount = min(filled_amount, self._entry_amount)
                realized_pnl = (
                    (price - self._entry_price) * sell_amount
                    - self._entry_fee
                    - exec_fee
                )
                is_win = realized_pnl > 0
                self._risk.record_trade_closed(is_win=is_win)
                # Reset entry state
                self._entry_price = 0.0
                self._entry_amount = 0.0
                self._entry_fee = 0.0

        return ExecutionResult(
            success=filled,
            status=result.get("status", "unknown"),
            filled=filled_amount,
            price=price,
            cost=filled_amount * price,
            fee=exec_fee,
            via=via,
            pnl=realized_pnl,
        )

    # ──────────────────────────────────────────────────────────
    # State 5: LOGGING
    # ──────────────────────────────────────────────────────────

    async def _state_log(
        self, decision: TradeDecision, result: ExecutionResult
    ) -> None:
        """Record trade result to DB, WebSocket, and audit trail."""
        await self._audit_transition(PipelineState.EXECUTION, PipelineState.LOGGING)

        if result.success:
            trade_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "symbol": self.symbol,
                "side": decision.signal.value.lower(),
                "price": result.price,
                "amount": result.filled,
                "order_id": self.order_manager._active_order_id or "",
                "pnl": result.pnl,
                "via": result.via,
            }

            if self._db:
                await self._db.log_trade(trade_data)
            if self._ws:
                await self._ws.broadcast({"type": "trade", "data": trade_data})

            if self._audit:
                await self._audit.log_action(
                    source="TradeExecutor",
                    action="TRADE_COMPLETED",
                    detail={
                        "signal": decision.signal.value,
                        "filled": result.filled,
                        "price": result.price,
                        "via": result.via,
                        "status": result.status,
                    },
                )
        else:
            if self._audit and result.status not in ("none", "skipped"):
                await self._audit.log_action(
                    source="TradeExecutor",
                    action="TRADE_FAILED",
                    detail={
                        "status": result.status,
                        "reason": result.reason,
                        "via": result.via,
                    },
                    level="WARNING",
                )

        # Transition back to IDLE
        await self._audit_transition(PipelineState.LOGGING, PipelineState.IDLE)

    # ──────────────────────────────────────────────────────────
    # Main Loop
    # ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the trading loop — 5-state pipeline."""
        self._running = True
        logger.info(
            f"🚀 Trading engine started (State Machine) | {self.symbol} | "
            f"Strategy: {self._strategy.name} | "
            f"Mode: {'PAPER' if self.paper_mode else 'REAL'}"
        )

        if self._audit:
            await self._audit.log_action(
                source="TradeExecutor",
                action="ENGINE_STARTED",
                detail={
                    "symbol": self.symbol,
                    "strategy": self._strategy.name,
                    "paper_mode": self.paper_mode,
                },
            )

        # Oracle starts FIRST (independent task, never blocked by exchange)
        await self.oracle.start()

        if self._audit:
            await self._audit.log_action(
                source="TradeExecutor",
                action="ORACLE_STATUS_AT_START",
                detail=self.oracle.get_status(),
            )

        await self.order_manager.start_watcher()

        # Configure futures leverage/margin if needed
        await self._exchange._configure_futures(self.symbol)

        await self._update_balances()

        # Initialize drawdown manager with current balance
        self._risk.drawdown.initialize(self._cached_quote_balance)

        # Send Telegram startup alert
        await self._alert.bot_started(
            self.symbol, "PAPER" if self.paper_mode else "REAL"
        )

        while self._running:
            try:
                # Wait for L2 update (WebSocket — zero CPU idle cost)
                ob = await self._exchange._ws_call_with_retry(
                    lambda: self._exchange._exchange.watch_order_book(
                        self.symbol, limit=20
                    ),
                    f"watch_order_book({self.symbol})",
                )

                # ── Hot-Path Pipeline (direct async — zero framework overhead) ──
                _t0 = _time.perf_counter()
                await self._fast_pipeline(ob)
                _elapsed_ms = (_time.perf_counter() - _t0) * 1000
                self.tick_count += 1
                self._tick_latency_sum += _elapsed_ms

                # Throttle (critical for Mini PC CPU/RAM)
                await asyncio.sleep(0.01)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Pipeline error: {e}", exc_info=True)
                if self._audit:
                    await self._audit.log_error(
                        source="TradeExecutor",
                        action="PIPELINE_ERROR",
                        exception=e,
                    )
                self._current_state = PipelineState.ERROR
                await asyncio.sleep(1)

        await self.oracle.stop()
        await self.order_manager.stop_watcher()

        if self._audit:
            await self._audit.log_action(
                source="TradeExecutor",
                action="ENGINE_STOPPED",
                detail={"symbol": self.symbol},
            )

        logger.info("🛑 Trading engine stopped")

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    async def _update_balances(self) -> None:
        """Refresh cached balances from paper wallet or exchange."""
        quote = self.symbol.split("/")[1]
        base = self.symbol.split("/")[0]

        if self.paper_mode and self.paper_wallet:
            bal_quote = await self.paper_wallet.fetch_balance(quote)
            bal_base = await self.paper_wallet.fetch_balance(base)
        else:
            bal_quote = await self._exchange.fetch_balance(quote)
            bal_base = await self._exchange.fetch_balance(base)

        self._cached_quote_balance = bal_quote["free"]
        self._cached_base_balance = bal_base["free"]

    @property
    def avg_tick_ms(self) -> float:
        """Average tick processing latency in milliseconds."""
        if self.tick_count == 0:
            return 0.0
        return round(self._tick_latency_sum / self.tick_count, 3)

    def stop(self) -> None:
        """Signal the engine to stop gracefully."""
        self._running = False
