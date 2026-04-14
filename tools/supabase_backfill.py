#!/usr/bin/env python
"""Supabase Backfill — Push historical daily PnL to Supabase.

Usage:
    python tools/supabase_backfill.py --domain=all
    python tools/supabase_backfill.py --domain=crypto --start-date=2025-01-01
    python tools/supabase_backfill.py --domain=stocks --start-date=2025-06-01 --end-date=2025-12-31
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def backfill_domain(domain: str, rows: list[dict]) -> int:
    """Push rows to Supabase daily_pnl. Returns count of rows pushed."""
    from services.supabase_sync import push_daily_pnl

    pushed = 0
    for row in rows:
        payload = {
            "date": row["date"],
            "total_pnl": round(row.get("total_pnl", 0.0), 4),
            "total_trades": row.get("total_trades", 0),
            "win_rate": round(row.get("win_rate", 0.0), 4),
        }
        await push_daily_pnl(domain, payload)
        pushed += 1
    return pushed


async def main(args: argparse.Namespace | None = None) -> int:
    """Main entry point. Returns exit code."""
    from config.settings import settings
    from services.db import Database

    if args is None:
        args = parse_args()

    # Gate: Supabase must be enabled
    if not settings.supabase.enabled:
        print("[!] Supabase sync disabled; set SUPABASE_ENABLED=true in .env")
        return 0

    db = Database()
    domains = [args.domain] if args.domain != "all" else ["crypto", "stocks"]
    total = 0

    for domain in domains:
        print(f"\n[*] Backfilling {domain}...")

        if domain == "crypto":
            rows = db.get_crypto_daily_pnl(
                start_date=args.start_date,
                end_date=args.end_date,
            )
        else:
            rows = db.get_stocks_daily_pnl(
                start_date=args.start_date,
                end_date=args.end_date,
            )

        if not rows:
            print(f"    No data found for {domain}")
            continue

        print(f"    Found {len(rows)} day(s) of data")
        pushed = await backfill_domain(domain, rows)
        total += pushed
        print(f"    [OK] Pushed {pushed} row(s) to Supabase daily_pnl")

    db.close()
    print(f"\n[DONE] Backfill complete -- {total} total row(s) pushed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="supabase_backfill",
        description="Push historical daily PnL from local DB to Supabase.",
        epilog=(
            "Examples:\n"
            "  python tools/supabase_backfill.py --domain=all\n"
            "  python tools/supabase_backfill.py --domain=crypto --start-date=2025-01-01\n"
            "  python tools/supabase_backfill.py --domain=stocks --start-date=2025-06-01 --end-date=2025-12-31\n"
            "\n"
            "Prerequisites:\n"
            "  Set SUPABASE_ENABLED=true, SUPABASE_URL, and SUPABASE_SERVICE_KEY in .env\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--domain",
        choices=["crypto", "stocks", "all"],
        default="all",
        help="Domain to backfill (default: all)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date filter YYYY-MM-DD (inclusive)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date filter YYYY-MM-DD (inclusive)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
