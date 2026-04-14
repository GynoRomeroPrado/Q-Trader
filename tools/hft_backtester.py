#!/usr/bin/env python3
"""HFT Backtester — Valida OBI históricamente usando DuckDB Stream cursors."""

import asyncio
import logging
import sys
from pathlib import Path

# Fix python path constraints
sys.path.insert(0, str(Path(__file__).parent.parent))

import duckdb
from config.settings import settings
from core.strategy_base import OrderBookStrategy, Signal

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def generate_mock_orderbook(bid_vwap: float, ask_vwap: float, imbalance: float) -> dict:
    """Reconstruye un L2 depth=1 falso que arroje matemáticamente el mismo Imbalance."""
    # Despeje de algebra: imbalance = (B_vol - A_vol) / (B_vol + A_vol)
    # Si total_vol = 100, B_vol = 50 * (imbalance + 1), A_vol = 100 - B_vol
    total_vol = 100.0
    bid_vol = 50.0 * (imbalance + 1.0)
    ask_vol = total_vol - bid_vol
    
    return {
        "bids": [[bid_vwap, bid_vol]],
        "asks": [[ask_vwap, ask_vol]]
    }


def run_backtest(db_path: str, chunk_size: int = 50000):
    """
    Lee snapshots históricos de microestructura usando un C de DuckDB.
    Esto permite procesar millones de rows (~GBs) usando < 100MB de RAM.
    """
    logger.info(f"🚀 Iniciando HFT Backtester (Chunk Size: {chunk_size} rows)")
    
    if not Path(db_path).exists():
        logger.error(f"❌ Base de datos OBI no encontrada en {db_path}")
        logger.info("Asegúrate de haber recolectado datos corriendo el bot en vivo primero.")
        return

    con = duckdb.connect(db_path, read_only=True)
    
    # Pre-configuramos la estrategia (Depth=1 porque restauramos una versión colapsada del L2)
    strategy = OrderBookStrategy(depth=1, imbalance_threshold=0.65)
    
    # Métricas de PnL y Rendimiento
    initial_capital = 1000.0
    capital = initial_capital
    base_asset = 0.0
    
    total_trades = 0
    winning_trades = 0
    
    maker_fee = 0.0000  # 0% Maker fee simulation (optimistic routing)
    fixed_size_usd = 50.0 # Operando 50$ fijos por trade HFT
    
    query = """
    SELECT timestamp, vwap_bid, vwap_ask, imbalance, spread_bps 
    FROM ob_stats 
    ORDER BY timestamp ASC
    """
    
    try:
        # Ejecución diferida (no carga todo a la RAM)
        cursor = con.execute(query)
        
        while True:
            chunk = cursor.fetch_df_chunk(chunk_size)
            if chunk.empty:
                break
                
            for row in chunk.itertuples():
                # Reconstruimos la firma de un Delta de CCXT
                ob_proxy = generate_mock_orderbook(row.vwap_bid, row.vwap_ask, row.imbalance)
                
                signal, _ = strategy.process_orderbook(ob_proxy)
                
                if signal == Signal.HOLD:
                    continue
                    
                price = row.vwap_bid if signal == Signal.BUY else row.vwap_ask
                
                if signal == Signal.BUY and capital >= fixed_size_usd:
                    amount = fixed_size_usd / price
                    capital -= fixed_size_usd
                    base_asset += amount
                    total_trades += 1
                elif signal == Signal.SELL and base_asset > 0:
                    revenue = base_asset * price
                    capital += revenue
                    
                    if revenue > fixed_size_usd:
                        winning_trades += 1
                        
                    base_asset = 0.0
                    total_trades += 1
                    
    except duckdb.CatalogException:
        logger.error("❌ Tabla 'ob_stats' no existe. El recolector OBI aún no ha corrido.")
        return

    # Liquidación Final
    if base_asset > 0:
        # Forzar venta al último bid conocido
        final_price = row.vwap_bid
        capital += base_asset * final_price
        base_asset = 0.0

    # Reporte
    pnl = capital - initial_capital
    win_rate = (winning_trades / (total_trades / 2)) * 100 if total_trades > 0 else 0.0

    logger.info("══════════════════════════════════════════")
    logger.info("🔬 REPORTE DE SIMULACIÓN HFT (Microestructura)")
    logger.info("══════════════════════════════════════════")
    logger.info(f"Total Operaciones  : {total_trades}")
    logger.info(f"Win Rate (Ida/Vta) : {win_rate:.2f}%")
    logger.info(f"Capital Inicial    : ${initial_capital:.2f}")
    logger.info(f"Capital Final      : ${capital:.2f}")
    logger.info(f"PnL Neto           : ${pnl:.2f} ({(pnl/initial_capital)*100:.2f}%)")
    logger.info("══════════════════════════════════════════")


if __name__ == "__main__":
    db_file = str(settings.database.duckdb_path)
    run_backtest(db_path=db_file)
