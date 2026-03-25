"""Trading Bot — Main Entry Point.

Starts the trading engine and FastAPI dashboard in a single asyncio event loop.

Windows (NSSM):  See README.md → "Registrar como Servicio Windows"
Linux (systemd): See deploy/tradingbot.service
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import uvicorn

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from core.exchange_client import ExchangeClient
from core.risk_manager import RiskManager
from core.trade_executor import TradeExecutor
from services.api_server import app, set_database, ws_manager
from services.db import Database
from strategies.ema_crossover import EMACrossover


def setup_logging() -> None:
    """Configure logging to both console and file."""
    log_path = settings.log_file  # Already a resolved Path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_path), encoding="utf-8"),
        ],
    )


async def balance_snapshot_loop(exchange: ExchangeClient, db: Database) -> None:
    """Periodically snapshot balance for equity curve."""
    while True:
        try:
            balance = await exchange.fetch_balance("USDT")
            await db.save_balance_snapshot("USDT", balance["free"], balance["total"])
            await ws_manager.broadcast({
                "type": "balance",
                "data": balance,
            })
        except Exception as e:
            logging.error(f"Balance snapshot error: {e}")
        await asyncio.sleep(60)  # Every minute


async def heartbeat_loop(db: Database) -> None:
    """Periodic heartbeat for bot status monitoring."""
    while True:
        await db.heartbeat()
        await asyncio.sleep(30)


async def main() -> None:
    setup_logging()
    logger = logging.getLogger("run_bot")

    logger.info("=" * 60)
    logger.info("⚡ Trading Bot Starting")
    logger.info(f"   Symbol:    {settings.trading.symbol}")
    logger.info(f"   Timeframe: {settings.trading.timeframe}")
    logger.info(f"   Sandbox:   {settings.exchange.sandbox}")
    logger.info(f"   Dashboard: http://localhost:{settings.dashboard.port}")
    logger.info("=" * 60)

    # Validate settings
    settings.validate()

    # Initialize components
    db = Database()
    set_database(db)
    await db.update_status("running")

    exchange = ExchangeClient()
    strategy = EMACrossover(fast=9, slow=21)
    risk_manager = RiskManager(exchange)

    executor = TradeExecutor(
        exchange=exchange,
        strategy=strategy,
        risk_manager=risk_manager,
        db=db,
        ws_manager=ws_manager,
    )

    # Create uvicorn server config (programmatic)
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=settings.dashboard.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    try:
        # Run everything concurrently on the SAME event loop
        await asyncio.gather(
            server.serve(),                         # FastAPI + WebSocket
            executor.start(),                       # Trading engine
            balance_snapshot_loop(exchange, db),     # Equity snapshots
            heartbeat_loop(db),                      # Status heartbeat
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        await db.update_status("error", str(e))
    finally:
        executor.stop()
        await exchange.close()
        db.close()
        logger.info("🛑 Trading Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
