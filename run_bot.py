"""Trading Bot — Main Entry Point.

Starts the trading engine and FastAPI dashboard in a single asyncio event loop.
Initializes AuditLogger, SentimentOracle, and PaperWallet with full dependency injection.

Windows (NSSM):  See README.md → "Registrar como Servicio Windows"
Linux (systemd): See deploy/tradingbot.service
"""

from __future__ import annotations

import asyncio
import argparse
import logging
import sys
from pathlib import Path

import uvicorn

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings, credential_provider
from core.audit_logger import AuditLogger
from core.exchange_client import ExchangeClient
from core.paper_wallet import PaperWallet
from core.risk_manager import RiskManager
from core.sentiment_oracle import SentimentOracle, GeminiProvider, ClaudeProvider
from core.trade_executor import TradeExecutor
from services.api_server import app, set_database, set_audit_logger, set_trade_executor, set_oracle, ws_manager
from services.db import Database
from core.strategy_base import OrderBookStrategy


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


async def balance_snapshot_loop(
    exchange: ExchangeClient,
    db: Database,
    paper_wallet: PaperWallet | None = None,
    paper_mode: bool = False,
) -> None:
    """Periodically snapshot balance for equity curve."""
    while True:
        try:
            if paper_mode and paper_wallet:
                balance = await paper_wallet.fetch_balance("USDT")
            else:
                balance = await exchange.fetch_balance("USDT")
            await db.save_balance_snapshot("USDT", balance["free"], balance.get("total", balance["free"]))
            await ws_manager.broadcast({
                "type": "balance",
                "data": balance,
            })
        except Exception as e:
            logging.error(f"Balance snapshot error: {e}")
        await asyncio.sleep(60)  # Every minute


async def heartbeat_loop(db: Database) -> None:
    """Periodic heartbeat for bot status monitoring.

    Also sends systemd watchdog pings (Linux production only).
    If WatchdogSec=60s in the .service file, this must fire
    at least every 30s (< WatchdogSec / 2).
    """
    # Initialize systemd notifier (Linux only, no-op on Windows)
    _sd_notifier = None
    try:
        import sdnotify
        _sd_notifier = sdnotify.SystemdNotifier()
        _sd_notifier.notify("READY=1")
        logging.getLogger("run_bot").info("🐧 systemd notifier: READY sent")
    except ImportError:
        pass  # Windows or sdnotify not installed — fine

    _tick = 0

    while True:
        await db.heartbeat()

        # Send watchdog ping to systemd
        if _sd_notifier:
            try:
                _sd_notifier.notify("WATCHDOG=1")
            except Exception:
                pass

        # Supabase telemetry sync (every ~60s = 3 ticks × 20s)
        _tick += 1
        if _tick % 3 == 0:
            asyncio.create_task(_supabase_sync_tick(db))

        await asyncio.sleep(20)  # Every 20s (< WatchdogSec / 2)


async def _supabase_sync_tick(db: Database) -> None:
    """Fire-and-forget Supabase telemetry push."""
    try:
        from services.supabase_sync import push_bot_status, push_daily_pnl

        # Stocks bot status
        from services.stocks_runtime import get_stocks_bot
        bot = get_stocks_bot()
        if bot is not None:
            status = bot.get_status().to_dict()
            await push_bot_status("stocks", status)

            # Daily PnL snapshot
            pnl = db._get_stocks_pnl_sync()
            await push_daily_pnl("stocks", pnl)

        # Crypto status (if available via bot_status table)
        try:
            row = db._sqlite.execute(
                "SELECT total_trades, total_pnl FROM bot_status WHERE id = 1"
            ).fetchone()
            if row:
                await push_daily_pnl("crypto", {
                    "total_trades": row["total_trades"],
                    "total_pnl": row["total_pnl"],
                })
        except Exception:
            pass  # Table might not exist in stocks-only mode

    except Exception as e:
        logging.getLogger("run_bot").debug("Supabase sync tick error: %s", e)


async def _build_oracle(audit: AuditLogger | None = None) -> SentimentOracle:
    """Build Sentiment Oracle with configured LLM provider."""
    llm_creds = await credential_provider.get_llm_credentials()

    provider_name = settings.sentiment.llm_provider.lower()
    if provider_name == "claude":
        provider = ClaudeProvider(api_key=llm_creds.get("claude_key", ""))
    else:
        provider = GeminiProvider(api_key=llm_creds.get("gemini_key", ""))

    return SentimentOracle(
        llm_provider=provider,
        polling_interval=settings.sentiment.polling_seconds,
        panic_threshold=settings.sentiment.panic_threshold,
        audit_logger=audit,
    )


async def main(domain: str = "crypto") -> None:
    setup_logging()
    logger = logging.getLogger("run_bot")

    logger.info("=" * 60)
    logger.info("⚡ Trading Bot Starting")
    logger.info(f"   Domain:    {domain.upper()}")
    logger.info(f"   Symbol:    {settings.trading.symbol}")
    logger.info(f"   Timeframe: {settings.trading.timeframe}")
    logger.info(f"   Sandbox:   {settings.exchange.sandbox}")
    logger.info(f"   Paper:     {settings.trading.paper_trading}")
    logger.info(f"   Dashboard: http://localhost:{settings.dashboard.port}")
    logger.info(f"   ── Performance ──")
    logger.info(f"   Fast Loop:  {settings.performance.use_fast_loop}")
    logger.info(f"   Numba JIT:  {settings.performance.use_numba}")
    logger.info(f"   LangGraph:  {settings.performance.use_langgraph}")
    logger.info(f"   ── Risk Controls ──")
    logger.info(f"   Max Daily Loss: {settings.risk.max_daily_loss_pct:.1%}")
    logger.info(f"   Max Drawdown:   {settings.risk.max_drawdown_pct:.1%}")
    logger.info(f"   Loss Streak:    {settings.risk.max_consecutive_losses} → {settings.risk.loss_streak_cooldown_sec}s cooldown")
    logger.info(f"   Trailing Stop:  {settings.risk.trailing_stop_pct:.1%}")
    logger.info(f"   ── Alerts ──")
    logger.info(f"   Telegram:   {'✅ Enabled' if settings.telegram.enabled else '❌ Disabled'}")
    logger.info("=" * 60)

    # Validate settings
    settings.validate()

    # Initialize databases
    db = Database()
    set_database(db)
    await db.update_status("running")

    # Initialize Audit Logger (uses same SQLite as trades)
    audit = AuditLogger(db_path=settings.database.sqlite_path)
    set_audit_logger(audit)

    await audit.log_action(
        source="run_bot",
        action="BOT_STARTUP",
        detail={
            "symbol": settings.trading.symbol,
            "paper_mode": settings.trading.paper_trading,
            "sandbox": settings.exchange.sandbox,
        },
    )

    # Initialize Exchange (crypto only, but needed early for some shared code)
    exchange = None
    paper_wallet = None
    oracle = None

    # Create uvicorn server config (shared by all domains)
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=settings.dashboard.port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    # ── Domain‑specific startup ──────────────────────────────

    if domain == "stocks":
        await _run_stocks_domain(db, audit, server, logger)
    else:
        # Crypto-specific initialization
        exchange = ExchangeClient()

        if settings.trading.paper_trading:
            paper_wallet = PaperWallet(
                db_path=settings.database.sqlite_path,
                initial_quote=settings.trading.paper_initial_balance,
                quote_asset=settings.trading.symbol.split("/")[1],
            )

        oracle = await _build_oracle(audit=audit)
        set_oracle(oracle)
        await _run_crypto_domain(db, audit, server, logger, exchange, paper_wallet, oracle)


async def _run_stocks_domain(db, audit, server, logger):
    """Start the Stocks trading bot alongside dashboard."""
    from core.stocks_exchange_client import create_stocks_client, AlpacaStocksClient
    from core.stocks_strategy import StocksStrategy, StocksStrategyConfig
    from core.stocks_bot import StocksBot, StocksRiskConfig
    from services.stocks_runtime import set_stocks_bot

    stocks_client = create_stocks_client(settings.stocks)

    # Load config from DB (or defaults)
    cfg = db.get_stocks_config()

    strategy = StocksStrategy.from_db_config(cfg)

    watchlist = [s.strip() for s in cfg.get("watchlist", "AAPL,MSFT,TSLA").split(",") if s.strip()]

    risk_config = StocksRiskConfig(
        max_position_qty=float(cfg.get("max_position_qty", 10.0)),
        max_daily_orders=int(cfg.get("max_daily_trades", 50)),
    )

    def trade_logger(trade: dict):
        db._log_stock_trade_sync(trade)

    bot = StocksBot(
        client=stocks_client,
        strategy=strategy,
        risk_config=risk_config,
        interval_sec=60.0,
        trade_logger=trade_logger,
        watchlist=watchlist,
    )

    # Register bot globally so API endpoints can access it
    set_stocks_bot(bot)

    logger.info("📊 Stocks domain selected — starting StocksBot")
    logger.info(f"   Provider:  {settings.stocks.provider}")
    logger.info(f"   Watchlist: {bot.watchlist}")

    try:
        await asyncio.gather(
            server.serve(),
            bot.run_forever(),
            heartbeat_loop(db),
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        await db.update_status("error", str(e))
        await audit.log_error(source="run_bot", action="FATAL_ERROR", exception=e)
    finally:
        bot.stop()
        if hasattr(stocks_client, 'aclose'):
            await stocks_client.aclose()
        audit.close()
        db.close()
        logger.info("🛑 Stocks Bot stopped")


async def _run_crypto_domain(db, audit, server, logger, exchange, paper_wallet, oracle):
    """Start the Crypto trading bot (original behavior)."""
    # Initialize Strategy & Risk
    strategy = OrderBookStrategy(depth=10, imbalance_threshold=0.65)
    risk_manager = RiskManager(exchange)

    # Initialize Trade Executor (full DI)
    executor = TradeExecutor(
        exchange=exchange,
        strategy=strategy,
        risk_manager=risk_manager,
        db=db,
        ws_manager=ws_manager,
        audit_logger=audit,
        oracle=oracle,
        paper_wallet=paper_wallet,
    )
    set_trade_executor(executor)

    try:
        await asyncio.gather(
            server.serve(),
            executor.start(),
            balance_snapshot_loop(exchange, db, paper_wallet,
                                  settings.trading.paper_trading),
            heartbeat_loop(db),
        )
    except KeyboardInterrupt:
        logger.info("Shutdown requested...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        await db.update_status("error", str(e))
        await audit.log_error(source="run_bot", action="FATAL_ERROR", exception=e)
    finally:
        executor.stop()
        await exchange.close()
        if paper_wallet:
            paper_wallet.close()
        audit.close()
        db.close()
        logger.info("🛑 Trading Bot stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Q-Trader Bot")
    parser.add_argument(
        "--domain", choices=["crypto", "stocks"], default="crypto",
        help="Trading domain: crypto (default) or stocks",
    )
    args = parser.parse_args()

    # Install high-performance event loop (platform-aware)
    if sys.platform == "win32":
        try:
            import winloop
            winloop.install()
            logging.getLogger("run_bot").info("⚡ winloop installed (high-perf event loop)")
        except ImportError:
            pass
    else:
        try:
            import uvloop
            uvloop.install()
            logging.getLogger("run_bot").info("⚡ uvloop installed (high-perf event loop)")
        except ImportError:
            pass

    asyncio.run(main(domain=args.domain))
