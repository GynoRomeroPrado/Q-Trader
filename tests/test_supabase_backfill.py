"""Tests for Supabase backfill CLI tool — all HTTP calls mocked."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure project root importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SAMPLE_CRYPTO = [
    {"date": "2025-03-01", "total_pnl": 25.50, "total_trades": 10, "win_rate": 0.6},
    {"date": "2025-03-02", "total_pnl": -5.00, "total_trades": 8, "win_rate": 0.375},
]

SAMPLE_STOCKS = [
    {"date": "2025-03-01", "total_pnl": 12.00, "total_trades": 5, "win_rate": 0.8},
]


class TestBackfillDisabled(unittest.TestCase):
    """When SUPABASE_ENABLED=False, backfill should exit cleanly without pushing."""

    @patch("services.supabase_sync.push_daily_pnl", new_callable=AsyncMock)
    def test_disabled_does_not_push(self, mock_push):
        from config.settings import settings, SupabaseSettings

        # Force disabled
        original = settings.supabase
        object.__setattr__(settings, "supabase", SupabaseSettings())

        try:
            from tools.supabase_backfill import main, parse_args
            args = parse_args(["--domain=all"])
            code = asyncio.run(main(args))
            assert code == 0
            mock_push.assert_not_called()
        finally:
            object.__setattr__(settings, "supabase", original)


class TestBackfillEnabled(unittest.TestCase):
    """When enabled, backfill should read from DB and push to Supabase."""

    def _enable_supabase(self):
        from config.settings import settings, SupabaseSettings
        fake = SupabaseSettings.__new__(SupabaseSettings)
        object.__setattr__(fake, "enabled", True)
        object.__setattr__(fake, "url", "https://test.supabase.co")
        object.__setattr__(fake, "service_key", "test-key")
        original = settings.supabase
        object.__setattr__(settings, "supabase", fake)
        return original

    def _restore_supabase(self, original):
        from config.settings import settings
        object.__setattr__(settings, "supabase", original)

    @patch("services.supabase_sync.push_daily_pnl", new_callable=AsyncMock)
    @patch("services.db.Database.get_crypto_daily_pnl", return_value=SAMPLE_CRYPTO)
    @patch("services.db.Database.get_stocks_daily_pnl", return_value=[])
    def test_crypto_domain_pushes_correct_rows(self, mock_stocks, mock_crypto, mock_push):
        original = self._enable_supabase()
        try:
            from tools.supabase_backfill import main, parse_args
            args = parse_args(["--domain=crypto"])
            code = asyncio.run(main(args))
            assert code == 0
            assert mock_push.call_count == 2
            # Verify first call
            call_args = mock_push.call_args_list[0]
            assert call_args[0][0] == "crypto"
            assert call_args[0][1]["date"] == "2025-03-01"
            assert call_args[0][1]["total_pnl"] == 25.50
        finally:
            self._restore_supabase(original)

    @patch("services.supabase_sync.push_daily_pnl", new_callable=AsyncMock)
    @patch("services.db.Database.get_stocks_daily_pnl", return_value=SAMPLE_STOCKS)
    @patch("services.db.Database.get_crypto_daily_pnl", return_value=[])
    def test_stocks_domain_pushes_correct_rows(self, mock_crypto, mock_stocks, mock_push):
        original = self._enable_supabase()
        try:
            from tools.supabase_backfill import main, parse_args
            args = parse_args(["--domain=stocks"])
            code = asyncio.run(main(args))
            assert code == 0
            assert mock_push.call_count == 1
            call_args = mock_push.call_args_list[0]
            assert call_args[0][0] == "stocks"
            assert call_args[0][1]["total_trades"] == 5
        finally:
            self._restore_supabase(original)

    @patch("services.supabase_sync.push_daily_pnl", new_callable=AsyncMock)
    @patch("services.db.Database.get_crypto_daily_pnl", return_value=SAMPLE_CRYPTO)
    @patch("services.db.Database.get_stocks_daily_pnl", return_value=SAMPLE_STOCKS)
    def test_all_domains_pushes_both(self, mock_stocks, mock_crypto, mock_push):
        original = self._enable_supabase()
        try:
            from tools.supabase_backfill import main, parse_args
            args = parse_args(["--domain=all"])
            code = asyncio.run(main(args))
            assert code == 0
            # 2 crypto + 1 stocks = 3
            assert mock_push.call_count == 3
            domains_called = [c[0][0] for c in mock_push.call_args_list]
            assert "crypto" in domains_called
            assert "stocks" in domains_called
        finally:
            self._restore_supabase(original)

    @patch("services.supabase_sync.push_daily_pnl", new_callable=AsyncMock)
    @patch("services.db.Database.get_crypto_daily_pnl", return_value=SAMPLE_CRYPTO)
    def test_date_filter_passed_to_db(self, mock_crypto_pnl, mock_push):
        original = self._enable_supabase()
        try:
            from tools.supabase_backfill import main, parse_args
            args = parse_args(["--domain=crypto", "--start-date=2025-03-01", "--end-date=2025-03-02"])
            code = asyncio.run(main(args))
            assert code == 0
            # Verify date filters were passed
            mock_crypto_pnl.assert_called_once_with(
                start_date="2025-03-01",
                end_date="2025-03-02",
            )
        finally:
            self._restore_supabase(original)

    @patch("services.supabase_sync.push_daily_pnl", new_callable=AsyncMock)
    @patch("services.db.Database.get_crypto_daily_pnl", return_value=[])
    @patch("services.db.Database.get_stocks_daily_pnl", return_value=[])
    def test_empty_data_no_pushes(self, mock_stocks, mock_crypto, mock_push):
        original = self._enable_supabase()
        try:
            from tools.supabase_backfill import main, parse_args
            args = parse_args(["--domain=all"])
            code = asyncio.run(main(args))
            assert code == 0
            mock_push.assert_not_called()
        finally:
            self._restore_supabase(original)


class TestBackfillCLI(unittest.TestCase):
    """Test CLI argument parsing."""

    def test_default_domain_is_all(self):
        from tools.supabase_backfill import parse_args
        args = parse_args([])
        assert args.domain == "all"
        assert args.start_date is None
        assert args.end_date is None

    def test_domain_crypto(self):
        from tools.supabase_backfill import parse_args
        args = parse_args(["--domain=crypto"])
        assert args.domain == "crypto"

    def test_date_args(self):
        from tools.supabase_backfill import parse_args
        args = parse_args(["--start-date=2025-01-01", "--end-date=2025-12-31"])
        assert args.start_date == "2025-01-01"
        assert args.end_date == "2025-12-31"


class TestBackfillUpsert(unittest.TestCase):
    """Verify upsert headers and URL params for idempotent backfill."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _enable_supabase(self):
        from config.settings import settings, SupabaseSettings
        fake = SupabaseSettings.__new__(SupabaseSettings)
        object.__setattr__(fake, "enabled", True)
        object.__setattr__(fake, "url", "https://test.supabase.co")
        object.__setattr__(fake, "service_key", "test-key")
        original = settings.supabase
        object.__setattr__(settings, "supabase", fake)
        return original

    def _restore_supabase(self, original):
        from config.settings import settings
        object.__setattr__(settings, "supabase", original)

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    def test_push_daily_pnl_url_includes_on_conflict(self, mock_url, mock_enabled):
        """POST URL must include ?on_conflict=date,domain."""
        from services.supabase_sync import push_daily_pnl

        mock_response = MagicMock()
        mock_response.status_code = 201

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            self._run(push_daily_pnl("crypto", {"date": "2025-01-01", "total_pnl": 10.0}))

        call_args = mock_client.post.call_args
        assert "on_conflict=date,domain" in call_args[0][0]

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    def test_push_daily_pnl_prefer_header_has_merge_duplicates(self, mock_url, mock_enabled):
        """Prefer header must contain resolution=merge-duplicates."""
        from services.supabase_sync import push_daily_pnl

        mock_response = MagicMock()
        mock_response.status_code = 201

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client) as MockAsyncClient:
            self._run(push_daily_pnl("crypto", {"date": "2025-01-01", "total_pnl": 10.0}))

        call_kwargs = MockAsyncClient.call_args[1]
        prefer = call_kwargs["headers"]["Prefer"]
        assert "resolution=merge-duplicates" in prefer

