"""Security tests for JWT_SECRET validation and /api/demo endpoint."""

from unittest.mock import patch, MagicMock
import pytest


# ──────────────────────────────────────────────────────────
# 1. JWT_SECRET validation at startup
# ──────────────────────────────────────────────────────────

class TestJWTSecretValidation:
    """settings.validate() must reject weak JWT secrets."""

    def _make_settings(self, jwt_secret: str, api_key: str = "a_secure_api_key_1234"):
        """Build a Settings instance with custom jwt_secret, bypassing .env."""
        from config.settings import (
            Settings, ExchangeSettings, DashboardSettings, TradingSettings,
            SentimentSettings, DatabaseSettings, TelegramSettings,
            PerformanceSettings, RiskSettings, StocksSettings,
        )

        # Patch env reads for exchange keys (required by validate)
        with patch("config.settings._env", side_effect=lambda k, d="": {
            "EXCHANGE_API_KEY": "test_key",
            "EXCHANGE_SECRET": "test_secret",
        }.get(k, d)):
            exchange = ExchangeSettings()

        dashboard = DashboardSettings.__new__(DashboardSettings)
        object.__setattr__(dashboard, "port", 8888)
        object.__setattr__(dashboard, "jwt_secret", jwt_secret)
        object.__setattr__(dashboard, "api_key", api_key)
        object.__setattr__(dashboard, "allow_demo", False)

        s = Settings.__new__(Settings)
        object.__setattr__(s, "exchange", exchange)
        object.__setattr__(s, "dashboard", dashboard)
        object.__setattr__(s, "stocks", StocksSettings())  # paper mode, no creds needed
        # Supabase disabled by default (no validation needed)
        from config.settings import SupabaseSettings
        object.__setattr__(s, "supabase", SupabaseSettings())
        # Remaining fields not needed for validate()
        return s

    def test_default_change_me_raises(self):
        s = self._make_settings("CHANGE_ME")
        with pytest.raises(RuntimeError, match="JWT_SECRET is unsafe"):
            s.validate()

    def test_template_value_raises(self):
        s = self._make_settings("CHANGE_ME_TO_RANDOM_STRING")
        with pytest.raises(RuntimeError, match="JWT_SECRET is unsafe"):
            s.validate()

    def test_empty_secret_raises(self):
        s = self._make_settings("")
        with pytest.raises(RuntimeError, match="JWT_SECRET is unsafe"):
            s.validate()

    def test_short_secret_raises(self):
        s = self._make_settings("tooshort")  # 8 chars < 16 minimum
        with pytest.raises(RuntimeError, match="JWT_SECRET is unsafe"):
            s.validate()

    def test_strong_secret_passes(self):
        s = self._make_settings("a_very_strong_secret_key_1234")
        # Should NOT raise
        s.validate()

    def test_weak_api_key_raises(self):
        s = self._make_settings("a_very_strong_secret_key_1234", api_key="CHANGE_ME")
        with pytest.raises(RuntimeError, match="API_KEY is unsafe"):
            s.validate()


# ──────────────────────────────────────────────────────────
# 2. /api/demo endpoint blocked by default
# ──────────────────────────────────────────────────────────

class TestDemoEndpoint:
    """/api/demo must return 403 when ALLOW_DEMO_MODE is false (default)."""

    def test_demo_blocked_by_default(self):
        """Default config (allow_demo=False) → 403."""
        from services.api_server import app
        from config.settings import settings as real_settings
        from fastapi.testclient import TestClient

        # Temporarily override allow_demo (frozen dataclass → use object.__setattr__)
        original = real_settings.dashboard.allow_demo
        object.__setattr__(real_settings.dashboard, "allow_demo", False)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/demo")
            assert resp.status_code == 403
            assert "disabled" in resp.json()["error"].lower()
        finally:
            object.__setattr__(real_settings.dashboard, "allow_demo", original)

    def test_demo_allowed_when_enabled(self):
        """When allow_demo=True, endpoint issues a token."""
        from services.api_server import app
        from config.settings import settings as real_settings
        from fastapi.testclient import TestClient

        original = real_settings.dashboard.allow_demo
        object.__setattr__(real_settings.dashboard, "allow_demo", True)
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post("/api/demo")
            assert resp.status_code == 200
            assert "token" in resp.json()
        finally:
            object.__setattr__(real_settings.dashboard, "allow_demo", original)
