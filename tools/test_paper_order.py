"""End-to-end paper order test — manual execution.

Usage: python -X utf8 tools/test_paper_order.py

Places a micro buy order for 1 share of AAPL on Alpaca Paper Trading.
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
    from core.market_hours import is_market_open, get_market_status

    if not settings.stocks.alpaca_api_key:
        print("[SKIP] ALPACA_API_KEY not set in .env")
        return

    print("=" * 55)
    print("  Alpaca Paper Trading -- E2E Order Test")
    print("=" * 55)

    # Check market hours
    mkt = get_market_status()
    print(f"  Market: {'OPEN' if mkt['is_open'] else 'CLOSED'} | {mkt['current_time_et']}")
    print(f"  {mkt['next_event']}")
    print()

    if not mkt["is_open"]:
        print("WARNING: Market closed -- the order may be queued or rejected.")
        print("  Proceed anyway for testing? (y/n): ", end="", flush=True)
        ans = input().strip().lower()
        if ans != "y":
            print("Aborted.")
            return

    async with AlpacaStocksClient(settings.stocks) as client:

        # 1. Initial balance
        account = await client.get_account_balance()
        print(f"[1] Balance: cash=${account['cash']:,.2f} | equity=${account['equity']:,.2f}")

        # 2. Place buy order
        print("[2] Placing BUY 1x AAPL (market order)...")
        try:
            result = await client.create_order(
                symbol="AAPL",
                side="buy",
                qty=1.0,
                order_type="market",
            )
            print(f"    Order ID:  {result.order_id}")
            print(f"    Status:    {result.status}")
            print(f"    Symbol:    {result.symbol}")
            print(f"    Side:      {result.side}")
        except StocksClientError as e:
            print(f"    [FAIL] Order rejected: {e}")
            return

        # 3. Wait
        print("[3] Waiting 3 seconds for fill...")
        await asyncio.sleep(3)

        # 4. Check positions
        positions = await client.fetch_positions()
        aapl_pos = [p for p in positions if p.symbol == "AAPL"]
        if aapl_pos:
            p = aapl_pos[0]
            print(f"[4] AAPL position: {p.qty} shares @ ${p.current_price:.2f}")
            print(f"    PnL: ${p.unrealized_pnl:+.2f}")
        else:
            print("[4] No AAPL position found (order may be pending)")

        # 5. Cancel pending orders
        cancelled = await client.cancel_all_orders()
        print(f"[5] Cancelled {cancelled} pending orders")

        # 6. Final balance
        account2 = await client.get_account_balance()
        print(f"[6] Balance: cash=${account2['cash']:,.2f} | equity=${account2['equity']:,.2f}")

        delta = account2["equity"] - account["equity"]
        print(f"    Delta: ${delta:+,.2f}")

    print()
    print("=" * 55)
    print("  E2E Order Test: COMPLETE")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
