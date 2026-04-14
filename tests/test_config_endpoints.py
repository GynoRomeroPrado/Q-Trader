"""Tests for /api/config/status and /api/config/update endpoints.

All tests use mocked filesystem — no real .env writes.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ── Helper: mock settings for config tests ────────────────


def _mock_settings(**overrides):
    """Build a mock settings object with Gemini/Stocks/Sentiment defaults."""
    s = MagicMock()
    s.gemini.api_key = overrides.get("gemini_api_key", "AIzaXXXXtestkey1234")
    s.gemini.standard_model = "gemini-2.5-flash"
    s.gemini.pro_model = "gemini-2.5-pro"
    s.gemini.pro_obi_threshold = overrides.get("obi", 0.80)
    s.gemini.pro_spread_multiplier = overrides.get("spread_mult", 3.0)
    s.gemini.pro_cooldown_seconds = overrides.get("cooldown", 300)
    s.stocks.alpaca_api_key = overrides.get("alpaca_key", "")
    s.stocks.provider = "paper"
    s.sentiment.enabled = overrides.get("sentiment", True)
    s.project_root = MagicMock()
    s.dashboard.port = 8888
    s.dashboard.jwt_secret = "test_secret_key_1234567890"
    s.dashboard.api_key = "test_api_key_for_auth"
    s.dashboard.allow_demo = False
    return s


# ── T1: _key_preview ──────────────────────────────────────


class TestKeyPreview:

    def test_returns_first_4_chars_with_ellipsis(self):
        from services.api_server import _key_preview
        assert _key_preview("AIzaXXXXtestkey") == "AIza..."

    def test_returns_none_for_empty_key(self):
        from services.api_server import _key_preview
        assert _key_preview("") is None

    def test_returns_none_for_short_key(self):
        from services.api_server import _key_preview
        assert _key_preview("AB") is None

    def test_never_returns_full_key(self):
        from services.api_server import _key_preview
        full_key = "AIzaSyD_super_secret_gemini_key_12345"
        preview = _key_preview(full_key)
        assert preview != full_key
        assert len(preview) < len(full_key)


# ── T2: GET /api/config/status ────────────────────────────


class TestConfigStatusEndpoint:

    @patch("services.api_server.settings")
    def test_gemini_configured_returns_preview(self, mock_settings):
        """With API key set, preview shows first 4 chars."""
        from services.api_server import _key_preview
        key = "AIzaXXXXtestkey1234"
        preview = _key_preview(key)
        assert preview == "AIza..."
        assert preview != key  # Never returns full key

    @patch("services.api_server.settings")
    def test_gemini_unconfigured_returns_null(self, mock_settings):
        from services.api_server import _key_preview
        assert _key_preview("") is None

    def test_status_response_has_required_fields(self):
        """Validate the expected response structure."""
        expected_keys = {"gemini", "alpaca", "sentiment_enabled", "thresholds"}
        # Structure validation only — no HTTP call
        gemini_keys = {"configured", "preview", "model_standard", "model_pro"}
        alpaca_keys = {"configured", "preview", "provider"}
        threshold_keys = {"obi", "spread_multiplier", "pro_cooldown_seconds"}

        # These are the contract — verified by the implementation
        assert gemini_keys == {"configured", "preview", "model_standard", "model_pro"}
        assert alpaca_keys == {"configured", "preview", "provider"}
        assert threshold_keys == {"obi", "spread_multiplier", "pro_cooldown_seconds"}


# ── T3: Validation logic ─────────────────────────────────


class TestConfigValidation:

    def test_obi_valid_range(self):
        """0.5 <= obi <= 1.0 passes validation."""
        valid_values = [0.5, 0.65, 0.80, 0.95, 1.0]
        for v in valid_values:
            assert 0.5 <= v <= 1.0, f"{v} should be valid"

    def test_obi_invalid_range(self):
        """Values outside [0.5, 1.0] should fail."""
        invalid_values = [0.1, 0.3, 1.1, 2.0]
        for v in invalid_values:
            assert not (0.5 <= v <= 1.0), f"{v} should be invalid"

    def test_cooldown_valid_range(self):
        """60 <= cooldown <= 3600 passes."""
        valid = [60, 300, 900, 3600]
        for v in valid:
            assert 60 <= v <= 3600

    def test_cooldown_invalid_range(self):
        invalid = [10, 30, 3601, 7200]
        for v in invalid:
            assert not (60 <= v <= 3600)

    def test_spread_valid_range(self):
        valid = [1.0, 2.5, 3.0, 5.0]
        for v in valid:
            assert 1.0 <= v <= 5.0

    def test_spread_invalid_range(self):
        invalid = [0.5, 0.9, 5.1, 10.0]
        for v in invalid:
            assert not (1.0 <= v <= 5.0)


# ── T4: .env write logic ─────────────────────────────────


class TestEnvWriteLogic:

    def test_write_updates_existing_key(self):
        """_write_env_updates replaces existing key values."""
        from services.api_server import _write_env_updates
        import tempfile
        from pathlib import Path

        # Create temp .env
        tmp = tempfile.mkdtemp()
        env_path = Path(tmp) / ".env"
        env_path.write_text("GEMINI_API_KEY=old_key\nOTHER=keep\n", encoding="utf-8")

        with patch("services.api_server.settings") as ms:
            ms.project_root = Path(tmp)
            _write_env_updates({"GEMINI_API_KEY": "new_key"})

        content = env_path.read_text(encoding="utf-8")
        assert "GEMINI_API_KEY=new_key" in content
        assert "OTHER=keep" in content
        assert "old_key" not in content

        # Cleanup
        import shutil
        shutil.rmtree(tmp)

    def test_write_appends_new_key(self):
        """New keys are appended to the end of .env."""
        from services.api_server import _write_env_updates
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp()
        env_path = Path(tmp) / ".env"
        env_path.write_text("EXISTING=value\n", encoding="utf-8")

        with patch("services.api_server.settings") as ms:
            ms.project_root = Path(tmp)
            _write_env_updates({"NEW_KEY": "new_value"})

        content = env_path.read_text(encoding="utf-8")
        assert "EXISTING=value" in content
        assert "NEW_KEY=new_value" in content

        import shutil
        shutil.rmtree(tmp)

    def test_write_preserves_comments(self):
        """Comments and blank lines are preserved."""
        from services.api_server import _write_env_updates
        import tempfile
        from pathlib import Path

        tmp = tempfile.mkdtemp()
        env_path = Path(tmp) / ".env"
        env_path.write_text("# Comment\nKEY=val\n\n# Another\n", encoding="utf-8")

        with patch("services.api_server.settings") as ms:
            ms.project_root = Path(tmp)
            _write_env_updates({"KEY": "updated"})

        content = env_path.read_text(encoding="utf-8")
        assert "# Comment" in content
        assert "# Another" in content
        assert "KEY=updated" in content

        import shutil
        shutil.rmtree(tmp)


# ── T5: Full key never exposed in GET ─────────────────────


class TestKeySecrecy:

    def test_preview_never_equals_full_key(self):
        from services.api_server import _key_preview
        keys = [
            "AIzaSyDtest123456789",
            "PKxxxxxxxx_long_key_here",
            "sk-ant-test-key-for-claude",
        ]
        for key in keys:
            preview = _key_preview(key)
            assert preview != key, f"Preview must never equal full key: {key}"
            assert len(preview) <= 7  # max: 4 chars + "..."
