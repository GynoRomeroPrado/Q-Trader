"""FastAPI server — REST endpoints and WebSocket for real-time dashboard.

Security hardening:
- CORS restricted to dashboard origin
- Status endpoint sanitized (no last_error / stack traces)
- Limit params capped
- Static files resolved from PROJECT_ROOT
- Audit log endpoint for operational visibility
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import psutil

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import settings
from core.audit_logger import AuditLogger
from services.auth import create_token, require_auth
from services.db import Database

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trading Bot API",
    version="3.0.0",
    docs_url=None,      # Disable Swagger UI in production
    redoc_url=None,      # Disable ReDoc in production
    openapi_url=None,    # Disable OpenAPI schema endpoint
)

# Track boot time for uptime metric
_START_TIME = time.time()

# CORS — restrict to localhost + tunnel origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.dashboard.port}",
        f"http://127.0.0.1:{settings.dashboard.port}",
    ],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ------------------------------------------------------------------
# WebSocket Manager
# ------------------------------------------------------------------

class WebSocketManager:
    """Manages connected WebSocket clients for real-time updates."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        logger.info(f"📱 Dashboard client connected ({len(self._clients)} total)")

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._clients:
            self._clients.remove(ws)
        logger.info(f"📱 Dashboard client disconnected ({len(self._clients)} total)")

    async def broadcast(self, data: dict[str, Any]) -> None:
        """Send data to all connected clients."""
        message = json.dumps(data, default=str)
        disconnected = []
        for client in self._clients:
            try:
                await client.send_text(message)
            except Exception:
                disconnected.append(client)
        for client in disconnected:
            if client in self._clients:
                self._clients.remove(client)

    @property
    def client_count(self) -> int:
        return len(self._clients)


ws_manager = WebSocketManager()

# Database, Audit & Executor references (set by run_bot.py at startup)
_db: Database | None = None
_audit: AuditLogger | None = None
_executor = None  # TradeExecutor reference for control endpoints


def set_database(db: Database) -> None:
    global _db
    _db = db


def set_audit_logger(audit: AuditLogger) -> None:
    global _audit
    _audit = audit


def set_trade_executor(executor) -> None:
    global _executor
    _executor = executor


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


def get_audit() -> AuditLogger | None:
    return _audit


# ------------------------------------------------------------------
# Auth Endpoints
# ------------------------------------------------------------------

@app.post("/api/login")
async def login(body: dict):
    """Login with API key, returns JWT token.
    Never reveals whether the key format is wrong vs nonexistent.
    """
    if body.get("api_key") != settings.dashboard.api_key:
        return JSONResponse(status_code=401, content={"error": "Invalid credentials"})
    return {"token": create_token()}

@app.post("/api/demo")
async def login_demo():
    """Demo token — disabled by default. Set ALLOW_DEMO_MODE=true in .env to enable."""
    if not settings.dashboard.allow_demo:
        return JSONResponse(status_code=403, content={"error": "Demo mode is disabled"})
    return {"token": create_token()}

@app.get("/api/config/status")
async def get_config_status():
    """Check if the system requires first-time configuration."""
    import os
    from config.settings import settings
    # Check if keys are actually set and not the default template strings
    has_binance = bool(settings.exchange.api_key and "your_binance" not in settings.exchange.api_key.lower())
    
    # Read directly from env since credentials are now dynamically provided via CredentialProvider
    gem_key = os.getenv("GEMINI_API_KEY", "")
    has_gemini = bool(gem_key and len(gem_key) > 10)
    has_dashboard = bool(settings.dashboard.api_key and len(settings.dashboard.api_key) > 3)
    
    return {
        "configured": has_binance and has_gemini and has_dashboard,
        "has_exchange": has_binance,
        "has_gemini": has_gemini,
        "has_dashboard": has_dashboard,
        "username": os.getenv("TRADING_USERNAME", "Trader")
    }

@app.post("/api/config")
async def save_configuration(body: dict):
    """Save user configuration only after strict external API verification."""
    import dotenv
    from fastapi import HTTPException
    import aiohttp
    import ccxt.async_support as ccxt_async
    
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    
    username = body.get("username", "").strip()
    dash_key = body.get("dashboard_api_key", "").strip()
    binance_key = body.get("binance_api_key", "").strip()
    binance_sec = body.get("binance_secret", "").strip()
    gemini_key = body.get("gemini_api_key", "").strip()
    
    # 1. Validate Binance Credentials
    if binance_key and binance_sec:
        try:
            exchange = ccxt_async.binance({
                "apiKey": binance_key,
                "secret": binance_sec,
                "enableRateLimit": True,
            })
            await exchange.fetch_balance()
            await exchange.close()
        except ccxt_async.AuthenticationError:
            await exchange.close()
            raise HTTPException(status_code=400, detail="Llaves de Binance (Spot) Inválidas o sin Permisos.")
        except Exception as e:
            await exchange.close()
            raise HTTPException(status_code=400, detail=f"Error al validar Binance: {type(e).__name__}")
    elif binance_key or binance_sec:
        raise HTTPException(status_code=400, detail="Debes proveer ambas llaves de Binance (Key y Secret).")
        
    # 2. Validate Gemini Settings
    if gemini_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
            payload = {"contents": [{"parts": [{"text": "Reply ok"}]}], "generationConfig": {"maxOutputTokens": 5}}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    if resp.status == 400 and "API key not valid" in await resp.text():
                        raise HTTPException(status_code=400, detail="Llave de Google Gemini (AI) Rechazada.")
                    elif resp.status in (401, 403):
                        raise HTTPException(status_code=400, detail="Llave de Google Gemini Inválida.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error al testear Servicio Gemini: {e}")

    # 3. Validation Passed -> Save payload
    if username:
        dotenv.set_key(env_file, "TRADING_USERNAME", username)
    if dash_key:
        dotenv.set_key(env_file, "API_KEY", dash_key)
    if binance_key and binance_sec:
        dotenv.set_key(env_file, "EXCHANGE_API_KEY", binance_key)
        dotenv.set_key(env_file, "EXCHANGE_SECRET", binance_sec)
    if gemini_key:
        dotenv.set_key(env_file, "GEMINI_API_KEY", gemini_key)
        
    return {"status": "success", "message": "Credenciales validadas exitosamente. Reiniciando HFT Engine..."}

# ------------------------------------------------------------------
# REST Endpoints — all require auth
# ------------------------------------------------------------------

# Global reference to oracle (set during crypto startup)
_oracle_ref = None

def set_oracle(oracle) -> None:
    global _oracle_ref
    _oracle_ref = oracle

def get_oracle():
    return _oracle_ref


@app.get("/api/oracle")
async def get_oracle_status(auth: dict = Depends(require_auth)):
    """Sentiment Oracle status — visible across all domains."""
    oracle = get_oracle()
    if oracle is None:
        return {
            "enabled": False,
            "market_panic": False,
            "last_score": None,
            "last_reason": "",
            "last_update": None,
            "last_error": "",
            "network_ok": False,
        }
    status = oracle.get_status()
    return {
        "enabled": True,
        "market_panic": status.get("market_panic", False),
        "last_score": status.get("last_score"),
        "last_reason": status.get("panic_reason", ""),
        "last_update": status.get("last_score") is not None,
        "last_error": status.get("last_error", ""),
        "network_ok": status.get("network_ok", False),
        "analysis_depth": status.get("analysis_depth", "normal"),
        "model_used_last": status.get("model_used_last", "flash"),
        "pro_cooldown_remaining": int(status.get("pro_cooldown_remaining", 0)),
    }

@app.get("/api/status")
async def get_status(auth: dict = Depends(require_auth)):
    """Bot status — sanitized, no error details exposed."""
    db = get_db()
    status = await db.get_status()
    status["ws_clients"] = ws_manager.client_count
    status["paper_mode"] = settings.trading.paper_trading
    status["market_type"] = settings.exchange.market_type
    status["domain"] = "crypto"  # default; overridden by DomainManager if available

    # Market hours for stocks domain
    try:
        from core.market_hours import get_market_status
        status["market"] = get_market_status()
    except Exception:
        status["market"] = {"is_open": False, "timezone": "America/New_York", "next_event": "unknown"}

    return status


@app.get("/api/pnl")
async def get_pnl(auth: dict = Depends(require_auth)):
    db = get_db()
    return await db.get_pnl_summary()


@app.get("/api/trades")
async def get_trades(limit: int = 50, auth: dict = Depends(require_auth)):
    db = get_db()
    return await db.get_trades(min(limit, 500))


@app.get("/api/balance")
async def get_balance(auth: dict = Depends(require_auth)):
    db = get_db()
    equity = await db.get_equity_curve(limit=1)
    return equity[0] if equity else {"total": 0, "timestamp": "N/A"}


@app.get("/api/equity")
async def get_equity(limit: int = 100, auth: dict = Depends(require_auth)):
    db = get_db()
    return await db.get_equity_curve(min(limit, 1000))


@app.get("/api/performance")
async def get_performance(auth: dict = Depends(require_auth)):
    """Aggregated performance metrics: win rate, max drawdown, totals."""
    db = get_db()
    return await db.get_performance_summary()


# ------------------------------------------------------------------
# Stocks Domain Endpoints
# ------------------------------------------------------------------

@app.get("/api/stocks/performance")
async def get_stocks_performance(auth: dict = Depends(require_auth)):
    """Per-symbol performance summaries for the Stocks domain."""
    from services.stocks_service import get_stocks_performance_summary
    return get_stocks_performance_summary()


@app.get("/api/stocks/status")
async def get_stocks_status(auth: dict = Depends(require_auth)):
    """Aggregated status across all tracked stock symbols + Alpaca balance."""
    from services.stocks_service import get_stocks_status
    from services.domain_manager import domain_manager
    result = get_stocks_status()

    # Domain runtime status
    result["domain_status"] = domain_manager.get_status("stocks")

    # Alpaca account balance (non-blocking — returns null on failure)
    result["alpaca_balance"] = None
    try:
        from services.stocks_runtime import get_stocks_bot
        bot = get_stocks_bot()
        if bot is not None and hasattr(bot, "_client"):
            acct = await bot._client.get_account()
            result["alpaca_balance"] = {
                "cash": float(acct.get("cash", 0)),
                "equity": float(acct.get("equity", 0)),
                "buying_power": float(acct.get("buying_power", 0)),
                "mode": "paper" if settings.trading.paper_trading else "live",
            }
    except Exception:
        pass  # Alpaca unavailable — dashboard shows skeleton

    return result


@app.get("/api/stocks/trades")
async def get_stocks_trades(
    limit: int = Query(default=50, le=500),
    auth: dict = Depends(require_auth),
):
    """Recent stock trades from the StocksBot trading loop."""
    db = get_db()
    return await db.get_stock_trades(limit)


@app.get("/api/stocks/pnl")
async def get_stocks_pnl(auth: dict = Depends(require_auth)):
    """PnL summary for the Stocks domain (total, win_rate, today)."""
    db = get_db()
    return await db.get_stocks_pnl_summary()


# ------------------------------------------------------------------
# Domain Lifecycle Endpoints (start/stop domains from dashboard)
# ------------------------------------------------------------------

from services.domain_manager import domain_manager
from pydantic import BaseModel as _BaseModel


class _DomainBody(_BaseModel):
    domain: str


@app.post("/api/domain/start")
async def domain_start(body: _DomainBody, auth: dict = Depends(require_auth)):
    """Start a trading domain as a background task (no process restart needed)."""
    d = body.domain.lower().strip()
    if d not in ("stocks",):
        return JSONResponse(status_code=400, content={"status": "error", "domain": d,
                            "message": f"Domain '{d}' cannot be started via API"})
    started, msg = await domain_manager.start_domain(d)
    status = "started" if started else ("already_running" if msg == "already_running" else "error")
    return {"status": status, "domain": d, "message": msg}


@app.post("/api/domain/stop")
async def domain_stop(body: _DomainBody, auth: dict = Depends(require_auth)):
    """Stop a running trading domain gracefully."""
    d = body.domain.lower().strip()
    stopped, msg = await domain_manager.stop_domain(d)
    status = "stopped" if stopped else ("not_running" if msg == "not_running" else "error")
    return {"status": status, "domain": d, "message": msg}


@app.get("/api/domain/status")
async def domain_status(auth: dict = Depends(require_auth)):
    """Return running/stopped status for all domains."""
    return domain_manager.get_all_status()


# ------------------------------------------------------------------
# Stocks Bot Control Endpoints
# ------------------------------------------------------------------

from fastapi.responses import JSONResponse



@app.get("/api/stocks/bot/status")
async def stocks_bot_status(auth: dict = Depends(require_auth)):
    """Current operational state of the Stocks trading bot."""
    from services.stocks_runtime import get_stocks_bot
    bot = get_stocks_bot()
    if bot is None:
        return {"running": False, "paused": False, "last_cycle_ts": None,
                "last_error": None, "total_cycles": 0, "daily_orders": 0,
                "state": "offline"}
    status = bot.get_status().to_dict()
    # Add human-readable state
    if not status["running"]:
        status["state"] = "stopped"
    elif status["paused"]:
        status["state"] = "paused"
    else:
        status["state"] = "running"
    return status


@app.post("/api/stocks/bot/pause")
async def stocks_bot_pause(auth: dict = Depends(require_auth)):
    """Pause the Stocks bot — loop continues but no new orders."""
    from services.stocks_runtime import get_stocks_bot
    bot = get_stocks_bot()
    if bot is None:
        return JSONResponse(status_code=503, content={"detail": "StocksBot not initialized"})
    bot.pause()
    return {"ok": True, "state": "paused"}


@app.post("/api/stocks/bot/resume")
async def stocks_bot_resume(auth: dict = Depends(require_auth)):
    """Resume the Stocks bot after a pause."""
    from services.stocks_runtime import get_stocks_bot
    bot = get_stocks_bot()
    if bot is None:
        return JSONResponse(status_code=503, content={"detail": "StocksBot not initialized"})
    bot.resume()
    return {"ok": True, "state": "running"}


@app.post("/api/stocks/bot/panic")
async def stocks_bot_panic(auth: dict = Depends(require_auth)):
    """Emergency stop — close positions and halt the Stocks bot."""
    from services.stocks_runtime import get_stocks_bot
    bot = get_stocks_bot()
    if bot is None:
        return JSONResponse(status_code=503, content={"detail": "StocksBot not initialized"})
    await bot.panic_stop()
    return {"ok": True, "state": "stopped"}


# ------------------------------------------------------------------
# Stocks Config Endpoints
# ------------------------------------------------------------------

@app.get("/api/stocks/config")
async def get_stocks_config_endpoint(auth: dict = Depends(require_auth)):
    """Current stocks strategy + risk configuration."""
    db = get_db()
    return db.get_stocks_config()


@app.put("/api/stocks/config")
async def put_stocks_config_endpoint(request: Request, auth: dict = Depends(require_auth)):
    """Update stocks strategy + risk config. Validates before saving."""
    db = get_db()
    cfg = await request.json()

    # ── Validation ──
    errors = []

    watchlist_raw = cfg.get("watchlist", "")
    if not watchlist_raw or not watchlist_raw.strip():
        errors.append("watchlist no puede estar vacía")

    fast = cfg.get("ma_fast_window")
    slow = cfg.get("ma_slow_window")
    if fast is not None and slow is not None:
        try:
            fast, slow = int(fast), int(slow)
            if fast < 1:
                errors.append("ma_fast_window debe ser >= 1")
            if slow < 2:
                errors.append("ma_slow_window debe ser >= 2")
            if fast >= slow:
                errors.append("ma_fast_window debe ser menor que ma_slow_window")
        except (ValueError, TypeError):
            errors.append("ma_fast_window y ma_slow_window deben ser enteros")

    margin = cfg.get("signal_margin")
    if margin is not None:
        try:
            if float(margin) <= 0:
                errors.append("signal_margin debe ser > 0")
        except (ValueError, TypeError):
            errors.append("signal_margin debe ser numérico")

    max_qty = cfg.get("max_position_qty")
    if max_qty is not None:
        try:
            if float(max_qty) <= 0:
                errors.append("max_position_qty debe ser > 0")
        except (ValueError, TypeError):
            errors.append("max_position_qty debe ser numérico")

    max_daily = cfg.get("max_daily_trades")
    if max_daily is not None:
        try:
            if int(max_daily) < 1:
                errors.append("max_daily_trades debe ser >= 1")
        except (ValueError, TypeError):
            errors.append("max_daily_trades debe ser entero")

    if errors:
        return JSONResponse(status_code=422, content={"detail": errors})

    # ── Save ──
    db.upsert_stocks_config(cfg)

    # ── Hot-reload running bot (if active) ──
    from services.stocks_runtime import get_stocks_bot
    bot = get_stocks_bot()
    if bot is not None:
        bot._strategy.update_config(cfg)
        new_watchlist = [s.strip() for s in cfg.get("watchlist", "").split(",") if s.strip()]
        if new_watchlist:
            bot.watchlist = new_watchlist
        bot._risk.max_position_qty = float(cfg.get("max_position_qty", bot._risk.max_position_qty))
        bot._risk.max_daily_orders = int(cfg.get("max_daily_trades", bot._risk.max_daily_orders))

    return {"ok": True, "config": db.get_stocks_config()}


# ------------------------------------------------------------------
# AI Forecast / Alpha Radar Endpoint
# ------------------------------------------------------------------

from pydantic import BaseModel
from typing import Literal


class ForecastRequestModel(BaseModel):
    domain: Literal["crypto", "stocks"]
    symbols: list[str]
    timeframe: str = "1h"


@app.post("/api/ai/forecast")
async def ai_forecast(request: ForecastRequestModel, auth: dict = Depends(require_auth)):
    """AI-powered forecasts for Alpha Radar (dummy data, future: Kronos/TimesFM)."""
    from services.ai_service import get_dummy_forecasts
    return get_dummy_forecasts(request.domain, request.symbols, request.timeframe)


# ------------------------------------------------------------------
# Audit Log Endpoint
# ------------------------------------------------------------------

@app.get("/api/logs")
async def get_logs(
    limit: int = Query(default=100, le=1000),
    level: str | None = Query(default=None),
    source: str | None = Query(default=None),
    auth: dict = Depends(require_auth),
):
    """Query audit logs with optional filters."""
    db = get_db()
    return await db.get_action_logs(limit=limit, level=level, source=source)

@app.post("/api/audit/ui")
async def log_ui_interaction(body: dict):
    """Log tracking sequences for every button click to map UI to Stitch specifications."""
    try:
        from pathlib import Path
        import os
        from datetime import datetime, timezone
        
        log_dir = Path("data")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "ui_interactions.log"
        
        timestamp = datetime.now(timezone.utc).isoformat()
        component_id = body.get("component_id", "Unknown")
        action = body.get("action", "click")
        details = body.get("details", "")
        
        log_entry = f"[{timestamp}] [UI_INTERACTION] Component: {component_id} | Action: {action} | Details: {details}\n"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
            
        return {"status": "logged"}
    except Exception as e:
        logger.error(f"Failed to log UI interaction: {e}")
        return {"status": "error"}



# ------------------------------------------------------------------
# Paper Trading Endpoints
# ------------------------------------------------------------------

@app.get("/api/paper/balance")
async def get_paper_balance(auth: dict = Depends(require_auth)):
    """Get current paper wallet balances."""
    db = get_db()
    rows = db._sqlite.execute(
        "SELECT asset, free, used, total FROM paper_balances"
    ).fetchall()
    return {r["asset"]: {"free": r["free"], "used": r["used"], "total": r["total"]} for r in rows}


@app.get("/api/paper/trades")
async def get_paper_trades(
    limit: int = Query(default=50, le=500),
    auth: dict = Depends(require_auth),
):
    """Get recent paper trades."""
    db = get_db()
    rows = db._sqlite.execute(
        "SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Trading Control Endpoints
# ------------------------------------------------------------------

@app.post("/api/control/pause")
async def control_pause(auth: dict = Depends(require_auth)):
    """Pause the trading engine."""
    if _executor is None:
        return JSONResponse(status_code=503, content={"error": "Executor not available"})
    _executor._running = False
    audit = get_audit()
    if audit:
        await audit.log_action(
            source="Dashboard", action="TRADING_PAUSED",
            detail={"by": "user"}, level="WARNING",
        )
    await ws_manager.broadcast({"type": "control", "data": {"action": "paused"}})
    return {"status": "paused"}


@app.post("/api/control/resume")
async def control_resume(auth: dict = Depends(require_auth)):
    """Resume the trading engine."""
    if _executor is None:
        return JSONResponse(status_code=503, content={"error": "Executor not available"})
    _executor._running = True
    audit = get_audit()
    if audit:
        await audit.log_action(
            source="Dashboard", action="TRADING_RESUMED",
            detail={"by": "user"},
        )
    await ws_manager.broadcast({"type": "control", "data": {"action": "resumed"}})
    return {"status": "running"}


@app.post("/api/control/panic")
async def control_panic(auth: dict = Depends(require_auth)):
    """Emergency stop — pause trading and cancel all active orders."""
    if _executor is None:
        return JSONResponse(status_code=503, content={"error": "Executor not available"})
    _executor._running = False
    # Cancel active order if any
    if hasattr(_executor, 'order_manager') and _executor.order_manager._active_order_id:
        await _executor.order_manager._safe_cancel(
            _executor.order_manager._active_order_id
        )
    audit = get_audit()
    if audit:
        await audit.log_action(
            source="Dashboard", action="PANIC_STOP",
            detail={"by": "user"}, level="CRITICAL",
        )
    await ws_manager.broadcast({"type": "control", "data": {"action": "panic"}})
    return {"status": "panic_stopped"}


@app.get("/api/oracle")
async def get_oracle_status(auth: dict = Depends(require_auth)):
    """Get current sentiment oracle status."""
    if _executor is None or not hasattr(_executor, 'oracle'):
        return {"market_panic": False, "panic_reason": "", "last_score": None}
    return _executor.oracle.get_status()


# ------------------------------------------------------------------
# System Metrics & Risk Status
# ------------------------------------------------------------------

@app.get("/api/metrics")
async def get_metrics(auth: dict = Depends(require_auth)):
    """System health metrics for monitoring."""
    proc = psutil.Process()
    mem = proc.memory_info()

    metrics: dict[str, Any] = {
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "memory_rss_mb": round(mem.rss / 1024 / 1024, 1),
        "memory_vms_mb": round(mem.vms / 1024 / 1024, 1),
        "cpu_percent": proc.cpu_percent(interval=0),
        "system_cpu_percent": psutil.cpu_percent(interval=0),
        "ws_clients": ws_manager.client_count,
    }

    # DB sizes
    try:
        sqlite_path = str(settings.database.sqlite_path)
        if os.path.exists(sqlite_path):
            metrics["sqlite_size_mb"] = round(os.path.getsize(sqlite_path) / 1024 / 1024, 2)
        duck_path = str(settings.database.duckdb_path)
        if os.path.exists(duck_path):
            metrics["duckdb_size_mb"] = round(os.path.getsize(duck_path) / 1024 / 1024, 2)
    except Exception:
        pass

    # Strategy diagnostics
    if _executor and hasattr(_executor, '_strategy'):
        try:
            metrics["strategy"] = _executor._strategy.get_diagnostics()
        except Exception:
            pass

    # Executor tick count
    if _executor and hasattr(_executor, 'tick_count'):
        metrics["ticks_processed"] = _executor.tick_count
    if _executor and hasattr(_executor, 'avg_tick_ms'):
        metrics["avg_tick_latency_ms"] = _executor.avg_tick_ms

    return metrics


@app.get("/api/risk")
async def get_risk_status(auth: dict = Depends(require_auth)):
    """Full risk manager status including drawdown and loss streaks."""
    if _executor is None or not hasattr(_executor, '_risk'):
        return {"error": "Risk manager not available"}
    return _executor._risk.get_full_status()


# ------------------------------------------------------------------
# Configuration Management Endpoints
# ------------------------------------------------------------------

def _key_preview(key: str) -> str | None:
    """Return first 4 chars + '...' or None if empty."""
    if not key or len(key) < 4:
        return None
    return key[:4] + "..."


def _read_env_file() -> dict[str, str]:
    """Read .env into a dict preserving all lines."""
    env_path = settings.project_root / ".env"
    result: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                k, _, v = stripped.partition("=")
                result[k.strip()] = v.strip().strip("'\"")
    return result


def _write_env_updates(updates: dict[str, str]) -> None:
    """Safely update .env on disk (atomic write via temp + rename)."""
    import tempfile
    import shutil

    env_path = settings.project_root / ".env"
    lines: list[str] = []
    updated_keys: set[str] = set()

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in updates:
                    lines.append(f"{key}={updates[key]}")
                    updated_keys.add(key)
                    continue
            lines.append(line)

    # Append any new keys not already in file
    for key, value in updates.items():
        if key not in updated_keys:
            lines.append(f"{key}={value}")

    # Atomic write
    fd, tmp_path = tempfile.mkstemp(
        dir=str(settings.project_root), suffix=".env.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        shutil.move(tmp_path, str(env_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _reload_settings_from_env() -> None:
    """Reload environment and rebuild settings singleton."""
    from dotenv import load_dotenv
    load_dotenv(settings.project_root / ".env", override=True)


@app.get("/api/config/status")
async def get_config_status(auth: dict = Depends(require_auth)):
    """Return config status with masked key previews."""
    return {
        "gemini": {
            "configured": bool(settings.gemini.api_key),
            "preview": _key_preview(settings.gemini.api_key),
            "model_standard": settings.gemini.standard_model,
            "model_pro": settings.gemini.pro_model,
        },
        "alpaca": {
            "configured": bool(settings.stocks.alpaca_api_key),
            "preview": _key_preview(settings.stocks.alpaca_api_key),
            "provider": settings.stocks.provider,
        },
        "sentiment_enabled": settings.sentiment.enabled,
        "thresholds": {
            "obi": settings.gemini.pro_obi_threshold,
            "spread_multiplier": settings.gemini.pro_spread_multiplier,
            "pro_cooldown_seconds": settings.gemini.pro_cooldown_seconds,
        },
    }


@app.post("/api/config/update")
async def update_config(request: Request, auth: dict = Depends(require_auth)):
    """Update .env and reload settings in memory."""
    body = await request.json()
    env_updates: dict[str, str] = {}
    errors: list[str] = []

    # --- API Keys ---
    if "gemini_api_key" in body and body["gemini_api_key"]:
        env_updates["GEMINI_API_KEY"] = body["gemini_api_key"]
    if "alpaca_api_key" in body and body["alpaca_api_key"]:
        env_updates["ALPACA_API_KEY"] = body["alpaca_api_key"]
    if "alpaca_api_secret" in body and body["alpaca_api_secret"]:
        env_updates["ALPACA_API_SECRET"] = body["alpaca_api_secret"]

    # --- Provider ---
    if "alpaca_provider" in body:
        if body["alpaca_provider"] in ("paper", "alpaca"):
            env_updates["STOCKS_PROVIDER"] = body["alpaca_provider"]
        else:
            errors.append("alpaca_provider must be 'paper' or 'alpaca'")

    # --- Sentiment toggle ---
    if "sentiment_enabled" in body:
        env_updates["SENTIMENT_ENABLED"] = str(body["sentiment_enabled"]).lower()

    # --- Thresholds with validation ---
    if "gemini_pro_obi_threshold" in body:
        val = float(body["gemini_pro_obi_threshold"])
        if 0.5 <= val <= 1.0:
            env_updates["GEMINI_PRO_OBI_THRESHOLD"] = str(val)
        else:
            errors.append("gemini_pro_obi_threshold must be between 0.5 and 1.0")

    if "gemini_pro_spread_multiplier" in body:
        val = float(body["gemini_pro_spread_multiplier"])
        if 1.0 <= val <= 5.0:
            env_updates["GEMINI_PRO_SPREAD_MULTIPLIER"] = str(val)
        else:
            errors.append("gemini_pro_spread_multiplier must be between 1.0 and 5.0")

    if "gemini_pro_cooldown_seconds" in body:
        val = int(body["gemini_pro_cooldown_seconds"])
        if 60 <= val <= 3600:
            env_updates["GEMINI_PRO_COOLDOWN_SECONDS"] = str(val)
        else:
            errors.append("gemini_pro_cooldown_seconds must be between 60 and 3600")

    if errors:
        return JSONResponse(status_code=422, content={"errors": errors})

    if not env_updates:
        return JSONResponse(status_code=400, content={"error": "No valid fields to update"})

    try:
        _write_env_updates(env_updates)
        _reload_settings_from_env()
    except Exception as e:
        logger.error("Config write failed: %s", e)
        return JSONResponse(status_code=500, content={"error": f"Failed to write .env: {e}"})

    # Return updated status
    return await get_config_status(auth)


# ------------------------------------------------------------------
# WebSocket Endpoint
# ------------------------------------------------------------------

@app.websocket("/ws/live")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)


# ------------------------------------------------------------------
# Static Files (Dashboard) — resolved from project root
# ------------------------------------------------------------------

_dashboard_dir = settings.project_root / "dashboard"
app.mount("/", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")

