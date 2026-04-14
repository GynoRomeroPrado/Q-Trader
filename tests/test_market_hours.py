"""Tests for NYSE market hours guard — core/market_hours.py."""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

from core.market_hours import is_market_open, get_market_status, _NYSE_TZ


def _et(year, month, day, hour, minute, second=0):
    """Create a timezone-aware datetime in America/New_York."""
    return datetime(year, month, day, hour, minute, second, tzinfo=_NYSE_TZ)


# ── Test: Market open ─────────────────────────────────────


class TestMarketOpen:

    def test_monday_1030_is_open(self):
        # 2026-04-06 is a Monday
        dt = _et(2026, 4, 6, 10, 30)
        assert dt.weekday() == 0  # Monday
        assert is_market_open(dt) is True

    def test_wednesday_noon_is_open(self):
        dt = _et(2026, 4, 8, 12, 0)
        assert is_market_open(dt) is True

    def test_friday_1530_is_open(self):
        dt = _et(2026, 4, 10, 15, 30)
        assert is_market_open(dt) is True


# ── Test: Market closed (night) ───────────────────────────


class TestMarketClosedNight:

    def test_monday_2300_is_closed(self):
        dt = _et(2026, 4, 6, 23, 0)
        assert is_market_open(dt) is False

    def test_monday_0500_is_closed(self):
        dt = _et(2026, 4, 6, 5, 0)
        assert is_market_open(dt) is False


# ── Test: Weekend ─────────────────────────────────────────


class TestWeekend:

    def test_saturday_1100_is_closed(self):
        # 2026-04-11 is a Saturday
        dt = _et(2026, 4, 11, 11, 0)
        assert dt.weekday() == 5  # Saturday
        assert is_market_open(dt) is False

    def test_sunday_1400_is_closed(self):
        dt = _et(2026, 4, 12, 14, 0)
        assert dt.weekday() == 6  # Sunday
        assert is_market_open(dt) is False


# ── Test: Holiday (Christmas) ─────────────────────────────


class TestHoliday:

    def test_christmas_2026_is_closed(self):
        # Dec 25, 2026 is a Friday
        dt = _et(2026, 12, 25, 11, 0)
        assert is_market_open(dt) is False

    def test_july_4th_is_closed(self):
        # July 4, 2026 is a Saturday → observed Friday July 3
        dt = _et(2026, 7, 3, 11, 0)
        assert is_market_open(dt) is False

    def test_new_years_day_is_closed(self):
        dt = _et(2026, 1, 1, 11, 0)
        assert is_market_open(dt) is False


# ── Test: Pre-market ──────────────────────────────────────


class TestPreMarket:

    def test_monday_0800_is_closed(self):
        dt = _et(2026, 4, 6, 8, 0)
        assert is_market_open(dt) is False

    def test_monday_0900_is_closed(self):
        dt = _et(2026, 4, 6, 9, 0)
        assert is_market_open(dt) is False


# ── Test: Edge cases (exact open/close) ───────────────────


class TestEdgeCases:

    def test_exact_open_0930_is_open(self):
        dt = _et(2026, 4, 6, 9, 30, 0)
        assert is_market_open(dt) is True

    def test_one_second_before_open_is_closed(self):
        dt = _et(2026, 4, 6, 9, 29, 59)
        assert is_market_open(dt) is False

    def test_exact_close_1600_is_closed(self):
        # 16:00 is NOT within [09:30, 16:00) — strictly less than
        dt = _et(2026, 4, 6, 16, 0, 0)
        assert is_market_open(dt) is False

    def test_one_second_before_close_is_open(self):
        dt = _et(2026, 4, 6, 15, 59, 59)
        assert is_market_open(dt) is True


# ── Test: get_market_status ───────────────────────────────


class TestGetMarketStatus:

    def test_open_returns_closes_at(self):
        dt = _et(2026, 4, 6, 10, 30)
        status = get_market_status(dt)
        assert status["is_open"] is True
        assert "Closes at 16:00" in status["next_event"]

    def test_closed_returns_opens_at(self):
        dt = _et(2026, 4, 6, 20, 0)
        status = get_market_status(dt)
        assert status["is_open"] is False
        assert "Opens at 09:30" in status["next_event"]

    def test_has_timezone_field(self):
        status = get_market_status(_et(2026, 4, 6, 10, 0))
        assert status["timezone"] == "America/New_York"


# ── Test: Loop skips when closed ──────────────────────────


class TestBotMarketGuard:

    def test_loop_skips_process_symbol_when_closed(self):
        """Verify run_forever does NOT call _process_symbol when market is closed."""
        from core.stocks_bot import StocksBot, StocksRiskConfig
        from core.stocks_strategy import StocksStrategy

        mock_client = MagicMock()
        strategy = StocksStrategy()
        bot = StocksBot(client=mock_client, strategy=strategy, interval_sec=0.01)

        # Patch is_market_open to return False
        with patch("core.market_hours.is_market_open", return_value=False):
            bot._running = True
            iteration_count = 0

            original_run_once = bot._run_once

            async def patched_run_once():
                nonlocal iteration_count
                iteration_count += 1
                bot._running = False  # Stop after 1 iteration
                return await original_run_once()

            bot._run_once = patched_run_once

            async def run_test():
                # Run for a very brief time
                async def stop_after_delay():
                    await asyncio.sleep(0.05)
                    bot._running = False

                await asyncio.gather(
                    bot.run_forever(),
                    stop_after_delay(),
                )

            asyncio.run(run_test())

            # _run_once should NOT have been called
            assert iteration_count == 0
