"""Tests for Gemini Dual-Model Router (gemini_client + Oracle integration).

All tests synthetic — no HTTP calls.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── T1: should_use_pro routing logic ──────────────────────


class TestShouldUsePro:

    def test_high_obi_triggers_pro(self):
        from services.gemini_client import should_use_pro
        assert should_use_pro(obi=0.85, spread_ratio=1.5, obi_threshold=0.80, spread_multiplier=3.0) is True

    def test_high_spread_triggers_pro(self):
        from services.gemini_client import should_use_pro
        assert should_use_pro(obi=0.50, spread_ratio=3.5, obi_threshold=0.80, spread_multiplier=3.0) is True

    def test_normal_conditions_no_pro(self):
        from services.gemini_client import should_use_pro
        assert should_use_pro(obi=0.50, spread_ratio=1.5, obi_threshold=0.80, spread_multiplier=3.0) is False

    def test_zero_values_no_pro(self):
        from services.gemini_client import should_use_pro
        assert should_use_pro(obi=0.0, spread_ratio=0.0, obi_threshold=0.80, spread_multiplier=3.0) is False


# ── T2: call_pro_with_fallback stubs ─────────────────────


class TestCallProWithFallback:

    def _run(self, coro):
        return asyncio.run(coro)

    @patch("services.gemini_client._call_model")
    def test_pro_timeout_falls_back_to_flash(self, mock_call):
        from services.gemini_client import call_pro_with_fallback

        # Pro times out, Flash returns response
        call_count = 0

        async def side_effect(prompt, api_key, model, timeout):
            nonlocal call_count
            call_count += 1
            if "pro" in model:
                raise asyncio.TimeoutError()
            return '{"sentiment_score": 0.0}'

        mock_call.side_effect = side_effect

        text, model_used = self._run(
            call_pro_with_fallback("test", "key", "gemini-2.5-pro", "gemini-2.5-flash", 4.0)
        )

        assert model_used == "flash"
        assert call_count == 2

    @patch("services.gemini_client._call_model")
    def test_pro_error_falls_back_to_flash(self, mock_call):
        from services.gemini_client import call_pro_with_fallback, GeminiClientError

        async def side_effect(prompt, api_key, model, timeout):
            if "pro" in model:
                raise GeminiClientError("503 overloaded")
            return '{"panic": false}'

        mock_call.side_effect = side_effect

        text, model_used = self._run(
            call_pro_with_fallback("test", "key", "gemini-2.5-pro", "gemini-2.5-flash", 4.0)
        )

        assert model_used == "flash"
        assert "panic" in text

    @patch("services.gemini_client._call_model")
    def test_pro_success_returns_pro(self, mock_call):
        from services.gemini_client import call_pro_with_fallback

        async def side_effect(prompt, api_key, model, timeout):
            return '{"panic": false, "severity": "low"}'

        mock_call.side_effect = side_effect

        text, model_used = self._run(
            call_pro_with_fallback("test", "key", "gemini-2.5-pro", "gemini-2.5-flash", 4.0)
        )

        assert model_used == "pro"
        assert "severity" in text


# ── T3: notify_market_conditions on Oracle ────────────────


class TestNotifyMarketConditions:

    def _make_oracle(self):
        from core.sentiment_oracle import SentimentOracle
        return SentimentOracle(polling_interval=300, panic_threshold=-0.5)

    @patch("config.settings.settings")
    @patch("services.gemini_client.should_use_pro", return_value=False)
    def test_normal_obi_sets_depth_normal(self, mock_pro, mock_settings):
        mock_settings.gemini.api_key = "test-key"
        mock_settings.gemini.pro_obi_threshold = 0.80
        mock_settings.gemini.pro_spread_multiplier = 3.0
        mock_settings.gemini.pro_cooldown_seconds = 300

        oracle = self._make_oracle()
        oracle.notify_market_conditions(obi=0.3, spread_ratio=1.0)

        assert oracle._analysis_depth == "normal"

    @patch("config.settings.settings")
    @patch("services.gemini_client.should_use_pro", return_value=True)
    def test_stress_with_cooldown_expired_creates_task(self, mock_pro, mock_settings):
        mock_settings.gemini.api_key = "test-key"
        mock_settings.gemini.pro_obi_threshold = 0.80
        mock_settings.gemini.pro_spread_multiplier = 3.0
        mock_settings.gemini.pro_cooldown_seconds = 300

        oracle = self._make_oracle()
        oracle._pro_last_called = 0.0  # Expired (monotonic started at 0)

        with patch.object(oracle, '_run_pro_analysis', new_callable=AsyncMock) as mock_run:
            with patch("asyncio.create_task") as mock_task:
                oracle.notify_market_conditions(obi=0.9, spread_ratio=4.0)
                mock_task.assert_called_once()

    @patch("config.settings.settings")
    @patch("services.gemini_client.should_use_pro", return_value=True)
    def test_stress_with_cooldown_active_sets_elevated(self, mock_pro, mock_settings):
        mock_settings.gemini.api_key = "test-key"
        mock_settings.gemini.pro_obi_threshold = 0.80
        mock_settings.gemini.pro_spread_multiplier = 3.0
        mock_settings.gemini.pro_cooldown_seconds = 300

        oracle = self._make_oracle()
        oracle._pro_last_called = time.monotonic()  # Just called — cooldown active

        oracle.notify_market_conditions(obi=0.9, spread_ratio=4.0)

        assert oracle._analysis_depth == "elevated"


# ── T4: keyword-only mode (no API key) ───────────────────


class TestKeywordOnlyMode:

    def _run(self, coro):
        return asyncio.run(coro)

    def test_oracle_starts_without_api_key(self):
        """GEMINI_API_KEY="" → Oracle starts fine, is_market_safe() returns True."""
        from core.sentiment_oracle import SentimentOracle

        oracle = SentimentOracle(polling_interval=300, panic_threshold=-0.5)

        # Should not raise
        assert self._run(oracle.is_market_safe()) is True
        assert oracle._analysis_depth == "normal"
        assert oracle._model_used_last == "flash"

    def test_notify_noop_without_api_key(self):
        """Without API key, notify_market_conditions does nothing."""
        from core.sentiment_oracle import SentimentOracle

        oracle = SentimentOracle(polling_interval=300, panic_threshold=-0.5)

        with patch("config.settings.settings") as mock_settings:
            mock_settings.gemini.api_key = ""
            oracle.notify_market_conditions(obi=0.95, spread_ratio=5.0)

        assert oracle._analysis_depth == "normal"


# ── T5: get_status includes new fields ────────────────────


class TestGetStatusExtended:

    def test_status_has_gemini_fields(self):
        from core.sentiment_oracle import SentimentOracle

        oracle = SentimentOracle(polling_interval=300, panic_threshold=-0.5)
        status = oracle.get_status()

        assert "analysis_depth" in status
        assert "model_used_last" in status
        assert "pro_cooldown_remaining" in status
        assert status["analysis_depth"] == "normal"
        assert status["model_used_last"] == "flash"
        assert isinstance(status["pro_cooldown_remaining"], (int, float))
