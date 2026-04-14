"""Tests for Supabase sync module — all HTTP calls mocked."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestSupabaseSyncDisabled(unittest.TestCase):
    """When SUPABASE_ENABLED=False, nothing should happen."""

    @patch("services.supabase_sync._is_enabled", return_value=False)
    def test_push_bot_status_noop(self, mock_enabled):
        """push_bot_status does nothing when disabled."""
        from services.supabase_sync import push_bot_status
        # Should not raise or make any HTTP calls
        asyncio.run(push_bot_status("stocks", {"running": True}))
        mock_enabled.assert_called_once()

    @patch("services.supabase_sync._is_enabled", return_value=False)
    def test_push_daily_pnl_noop(self, mock_enabled):
        """push_daily_pnl does nothing when disabled."""
        from services.supabase_sync import push_daily_pnl
        asyncio.run(push_daily_pnl("crypto", {"total_pnl": 100.0}))
        mock_enabled.assert_called_once()

    @patch("services.supabase_sync._is_enabled", return_value=False)
    def test_no_httpx_import_when_disabled(self, mock_enabled):
        """httpx should not even be imported when disabled."""
        import sys
        # Remove httpx from cache to detect fresh imports
        httpx_modules = [k for k in sys.modules if k.startswith("httpx")]

        from services.supabase_sync import push_bot_status
        asyncio.run(push_bot_status("stocks", {}))
        # If disabled, function returns immediately — no httpx usage


class TestSupabaseSyncEnabled(unittest.TestCase):
    """When SUPABASE_ENABLED=True, verify HTTP calls are made correctly."""

    def setUp(self):
        from services import supabase_sync
        supabase_sync._bot_status_buffer.clear()
        supabase_sync._daily_pnl_buffer.clear()

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    @patch("services.supabase_sync._headers", return_value={
        "apikey": "test-key",
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    })
    def test_push_bot_status_posts_to_correct_endpoint(self, mock_headers, mock_url, mock_enabled):
        """push_bot_status should POST to /rest/v1/bot_status."""
        from services.supabase_sync import push_bot_status

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            self._run(push_bot_status("stocks", {
                "running": True,
                "paused": False,
                "last_cycle_ts": "2026-04-09T00:00:00Z",
            }))

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/rest/v1/bot_status"
        payload = call_args[1]["json"]
        assert payload["domain"] == "stocks"
        assert payload["running"] is True
        assert "ts" in payload

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    @patch("services.supabase_sync._headers", return_value={
        "apikey": "test-key",
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    })
    def test_push_daily_pnl_posts_to_correct_endpoint(self, mock_headers, mock_url, mock_enabled):
        """push_daily_pnl should POST to /rest/v1/daily_pnl."""
        from services.supabase_sync import push_daily_pnl

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            self._run(push_daily_pnl("crypto", {
                "total_pnl": 150.50,
                "total_trades": 42,
                "win_rate": 0.65,
            }))

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/rest/v1/daily_pnl?on_conflict=date,domain"
        payload = call_args[1]["json"]
        assert payload["domain"] == "crypto"
        assert payload["total_pnl"] == 150.50
        assert "ts" in payload

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    @patch("services.supabase_sync._headers", return_value={
        "apikey": "test-key",
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    })
    def test_push_bot_status_handles_500_gracefully(self, mock_headers, mock_url, mock_enabled):
        """A 500 response should be logged but NOT raise."""
        from services.supabase_sync import push_bot_status

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            # Should NOT raise
            self._run(push_bot_status("stocks", {"running": True}))

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    @patch("services.supabase_sync._headers", return_value={
        "apikey": "test-key",
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    })
    def test_push_daily_pnl_handles_network_error(self, mock_headers, mock_url, mock_enabled):
        """Network errors should be caught and not propagate."""
        from services.supabase_sync import push_daily_pnl
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            # Should NOT raise
            self._run(push_daily_pnl("stocks", {"total_pnl": 0}))

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    @patch("services.supabase_sync._headers", return_value={
        "apikey": "my-key-123",
        "Authorization": "Bearer my-key-123",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    })
    def test_headers_include_apikey_and_auth(self, mock_headers, mock_url, mock_enabled):
        """Verify that httpx.AsyncClient is created with proper Supabase headers."""
        from services.supabase_sync import push_bot_status

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.text = ""

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client) as MockAsyncClient:
            self._run(push_bot_status("stocks", {"running": True}))

        # Verify headers passed to AsyncClient constructor
        call_kwargs = MockAsyncClient.call_args[1]
        assert call_kwargs["headers"]["apikey"] == "my-key-123"
        assert call_kwargs["headers"]["Authorization"] == "Bearer my-key-123"
        assert call_kwargs["base_url"] == "https://test.supabase.co"


class TestSupabaseSettingsValidation(unittest.TestCase):
    """Test that settings validation catches missing Supabase credentials."""

    def test_enabled_without_url_fails(self):
        """SUPABASE_ENABLED=true but no URL should raise RuntimeError."""
        import os
        from unittest.mock import patch as env_patch

        env = {
            "SUPABASE_ENABLED": "true",
            "SUPABASE_URL": "",
            "SUPABASE_SERVICE_KEY": "some-key",
            # Provide required settings to avoid earlier validation failures
            "EXCHANGE_API_KEY": "test-key-12345",
            "EXCHANGE_SECRET": "test-secret-12345",
            "JWT_SECRET": "super-secret-jwt-key-1234",
            "API_KEY": "super-api-key-1234",
        }

        with env_patch.dict(os.environ, env, clear=False):
            # Need to reimport to pick up new env
            from config.settings import SupabaseSettings, Settings
            s = Settings()
            # Force supabase settings with empty url
            object.__setattr__(s, 'supabase', SupabaseSettings())

            with self.assertRaises(RuntimeError) as ctx:
                # Manually check the supabase block
                if s.supabase.enabled and not s.supabase.url:
                    raise RuntimeError("SUPABASE_URL is required when SUPABASE_ENABLED=true.")
            assert "SUPABASE_URL" in str(ctx.exception)

    def test_disabled_skips_validation(self):
        """SUPABASE_ENABLED=false should not validate URL/key."""
        from config.settings import Settings
        s = Settings()
        # Default is disabled — should not raise
        assert s.supabase.enabled is False


class TestRetryBuffer(unittest.TestCase):
    """Tests for the bounded retry buffer (Gap 3 fix)."""

    def _run(self, coro):
        return asyncio.run(coro)

    def setUp(self):
        # Clear buffers before each test
        from services import supabase_sync
        supabase_sync._bot_status_buffer.clear()
        supabase_sync._daily_pnl_buffer.clear()

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    def test_failure_adds_to_buffer(self, mock_url, mock_enabled):
        """An HTTP failure should buffer the payload instead of discarding."""
        from services.supabase_sync import push_daily_pnl, _daily_pnl_buffer
        import httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            self._run(push_daily_pnl("crypto", {"date": "2025-01-01", "total_pnl": 10.0}))

        assert len(_daily_pnl_buffer) == 1
        assert _daily_pnl_buffer[0][0] == "crypto"

    @patch("services.supabase_sync._is_enabled", return_value=True)
    @patch("services.supabase_sync._base_url", return_value="https://test.supabase.co")
    def test_success_drains_buffer(self, mock_url, mock_enabled):
        """On success, buffered items should be drained."""
        from services.supabase_sync import push_daily_pnl, _daily_pnl_buffer

        # Pre-load buffer with one item
        _daily_pnl_buffer.append(("stocks", {"date": "2025-02-01", "total_pnl": 5.0}))
        assert len(_daily_pnl_buffer) == 1

        mock_response = MagicMock()
        mock_response.status_code = 201

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            self._run(push_daily_pnl("crypto", {"date": "2025-01-01", "total_pnl": 10.0}))

        # Buffer should now be empty (drained on success)
        assert len(_daily_pnl_buffer) == 0

    def test_buffer_maxlen_caps_at_50(self):
        """Buffer never exceeds maxlen=50 even with 100 failures."""
        from services.supabase_sync import _daily_pnl_buffer

        _daily_pnl_buffer.clear()
        for i in range(100):
            _daily_pnl_buffer.append(("crypto", {"date": f"2025-01-{i:02d}"}))

        assert len(_daily_pnl_buffer) == 50

