"""Tests for Stocks client factory + settings validation."""

from unittest.mock import patch
import pytest


# ──────────────────────────────────────────────────────────
# 1. Factory tests
# ──────────────────────────────────────────────────────────

class TestCreateStocksClient:

    def test_paper_provider_returns_paper_client(self):
        from config.settings import StocksSettings
        from core.stocks_exchange_client import create_stocks_client, PaperStocksClient

        cfg = StocksSettings(provider="paper")
        client = create_stocks_client(cfg)
        assert isinstance(client, PaperStocksClient)

    def test_alpaca_provider_returns_alpaca_client(self):
        from config.settings import StocksSettings
        from core.stocks_exchange_client import create_stocks_client, AlpacaStocksClient

        cfg = StocksSettings(
            provider="alpaca",
            alpaca_api_key="PKTEST12345678",
            alpaca_api_secret="secretsecret1234",
        )
        client = create_stocks_client(cfg)
        assert isinstance(client, AlpacaStocksClient)

    def test_unknown_provider_defaults_to_paper(self):
        from config.settings import StocksSettings
        from core.stocks_exchange_client import create_stocks_client, PaperStocksClient

        cfg = StocksSettings(provider="unknown_broker")
        client = create_stocks_client(cfg)
        assert isinstance(client, PaperStocksClient)

    def test_alpaca_client_stores_credentials(self):
        from config.settings import StocksSettings
        from core.stocks_exchange_client import create_stocks_client, AlpacaStocksClient

        cfg = StocksSettings(
            provider="alpaca",
            alpaca_api_key="MYKEY123",
            alpaca_api_secret="MYSECRET456",
            alpaca_base_url="https://paper-api.alpaca.markets",
        )
        client = create_stocks_client(cfg)
        assert isinstance(client, AlpacaStocksClient)
        assert client._api_key == "MYKEY123"
        assert client._api_secret == "MYSECRET456"
        assert "alpaca" in client._base_url

    def test_alpaca_client_has_http_clients(self):
        from config.settings import StocksSettings
        from core.stocks_exchange_client import create_stocks_client
        import httpx

        cfg = StocksSettings(
            provider="alpaca",
            alpaca_api_key="PKTEST12345678",
            alpaca_api_secret="secretsecret1234",
        )
        client = create_stocks_client(cfg)

        assert hasattr(client, "_trading")
        assert hasattr(client, "_data")
        assert isinstance(client._trading, httpx.AsyncClient)
        assert isinstance(client._data, httpx.AsyncClient)


# ──────────────────────────────────────────────────────────
# 2. Settings validation tests
# ──────────────────────────────────────────────────────────

class TestStocksSettingsValidation:

    def _make_settings(self, **stocks_overrides):
        """Create a Settings with custom StocksSettings + safe dashboard creds."""
        from config.settings import Settings, StocksSettings, DashboardSettings

        stocks_cfg = StocksSettings(**stocks_overrides)
        dash_cfg = DashboardSettings(
            jwt_secret="test_secret_long_enough_1234",
            api_key="test_api_key_ok",
        )
        return Settings(stocks=stocks_cfg, dashboard=dash_cfg)

    def test_paper_provider_no_creds_passes(self):
        s = self._make_settings(provider="paper", alpaca_api_key="", alpaca_api_secret="")
        # Should NOT raise
        s.validate()

    def test_alpaca_provider_valid_creds_passes(self):
        s = self._make_settings(
            provider="alpaca",
            alpaca_api_key="PKTEST12345678",
            alpaca_api_secret="secretsecret1234",
        )
        s.validate()

    def test_alpaca_missing_api_key_raises(self):
        s = self._make_settings(
            provider="alpaca",
            alpaca_api_key="",
            alpaca_api_secret="secretsecret1234",
        )
        with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
            s.validate()

    def test_alpaca_missing_api_secret_raises(self):
        s = self._make_settings(
            provider="alpaca",
            alpaca_api_key="PKTEST12345678",
            alpaca_api_secret="",
        )
        with pytest.raises(RuntimeError, match="ALPACA_API_SECRET"):
            s.validate()

    def test_alpaca_short_key_raises(self):
        s = self._make_settings(
            provider="alpaca",
            alpaca_api_key="short",
            alpaca_api_secret="secretsecret1234",
        )
        with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
            s.validate()

    def test_alpaca_short_secret_raises(self):
        s = self._make_settings(
            provider="alpaca",
            alpaca_api_key="PKTEST12345678",
            alpaca_api_secret="short",
        )
        with pytest.raises(RuntimeError, match="ALPACA_API_SECRET"):
            s.validate()


# ──────────────────────────────────────────────────────────
# 3. Protocol compliance
# ──────────────────────────────────────────────────────────

class TestProtocolCompliance:

    def test_paper_client_satisfies_protocol(self):
        from core.stocks_exchange_client import (
            PaperStocksClient,
            StocksExchangeClientProtocol,
        )
        client = PaperStocksClient()
        assert isinstance(client, StocksExchangeClientProtocol)

    def test_alpaca_client_satisfies_protocol(self):
        from config.settings import StocksSettings
        from core.stocks_exchange_client import (
            AlpacaStocksClient,
            StocksExchangeClientProtocol,
        )
        cfg = StocksSettings(
            provider="alpaca",
            alpaca_api_key="PKTEST12345678",
            alpaca_api_secret="secretsecret1234",
        )
        client = AlpacaStocksClient(cfg)
        assert isinstance(client, StocksExchangeClientProtocol)
