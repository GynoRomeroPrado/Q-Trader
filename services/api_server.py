"""FastAPI server — REST endpoints and WebSocket for real-time dashboard.

Security hardening:
- CORS restricted to dashboard origin
- Status endpoint sanitized (no last_error / stack traces)
- Limit params capped
- Static files resolved from PROJECT_ROOT
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config.settings import settings
from services.auth import create_token, require_auth
from services.db import Database

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Trading Bot API",
    version="2.0.0",
    docs_url=None,      # Disable Swagger UI in production
    redoc_url=None,      # Disable ReDoc in production
    openapi_url=None,    # Disable OpenAPI schema endpoint
)

# CORS — restrict to localhost + tunnel origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{settings.dashboard.port}",
        f"http://127.0.0.1:{settings.dashboard.port}",
    ],
    allow_methods=["GET", "POST"],
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

# Database reference (set by run_bot.py at startup)
_db: Database | None = None


def set_database(db: Database) -> None:
    global _db
    _db = db


def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Database not initialized")
    return _db


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


# ------------------------------------------------------------------
# REST Endpoints — all require auth
# ------------------------------------------------------------------

@app.get("/api/status")
async def get_status(auth: dict = Depends(require_auth)):
    """Bot status — sanitized, no error details exposed."""
    db = get_db()
    status = await db.get_status()
    status["ws_clients"] = ws_manager.client_count
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
