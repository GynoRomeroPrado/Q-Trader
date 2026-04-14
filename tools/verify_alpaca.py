"""Alpaca Paper Trading — Connectivity Verification Script.

Usage: python tools/verify_alpaca.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()


async def main():
    from config.settings import settings
    from core.stocks_exchange_client import AlpacaStocksClient, StocksClientError

    if not settings.stocks.alpaca_api_key:
        print("❌ ALPACA_API_KEY not configured in .env")
        return

    print("=" * 50)
    print("  Alpaca Paper Trading — Verificación")
    print("=" * 50)
    print(f"  Base URL: {settings.stocks.alpaca_base_url}")
    print(f"  Key preview: {settings.stocks.alpaca_api_key[:8]}...")
    print()

    async with AlpacaStocksClient(settings.stocks) as client:

        # 1. Account
        try:
            account = await client.get_account_balance()
            print(
                f"✅ Cuenta:  cash=${account['cash']:,.2f} | "
                f"equity=${account['equity']:,.2f} | "
                f"buying_power=${account['buying_power']:,.2f}"
            )
        except StocksClientError as e:
            print(f"❌ Account error: {e}")
            return

        # 2. Positions
        try:
            positions = await client.fetch_positions()
            print(f"✅ Posiciones abiertas: {len(positions)}")
            for p in positions[:5]:
                print(
                    f"   {p.symbol}: {p.qty} shares @ ${p.current_price:.2f} "
                    f"(PnL: ${p.unrealized_pnl:+.2f})"
                )
        except StocksClientError as e:
            print(f"❌ Positions error: {e}")

        # 3. Quote
        symbols = ["AAPL", "MSFT", "TSLA"]
        for sym in symbols:
            try:
                quote = await client.fetch_quote(sym)
                mid = (quote.bid + quote.ask) / 2.0
                print(
                    f"✅ {sym} bid=${quote.bid:.2f} | "
                    f"ask=${quote.ask:.2f} | mid=${mid:.2f}"
                )
            except StocksClientError as e:
                print(f"⚠️  {sym} quote error: {e}")

        # 4. Cancel all (safe op — cancels nothing if no pending orders)
        try:
            cancelled = await client.cancel_all_orders()
            print(f"✅ Cancel all: {cancelled} órdenes canceladas")
        except StocksClientError as e:
            print(f"⚠️  Cancel all error: {e}")

    print()
    print("=" * 50)
    print("🟢 Conexión a Alpaca Paper Trading: OK")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
