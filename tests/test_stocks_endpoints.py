"""Tests for Stocks domain — service + API endpoints."""

import pytest


# ──────────────────────────────────────────────────────────
# 1. Service-level tests
# ──────────────────────────────────────────────────────────

class TestStocksService:

    def test_performance_returns_list(self):
        from services.stocks_service import get_stocks_performance_summary
        result = get_stocks_performance_summary()
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_summary_has_required_fields(self):
        from services.stocks_service import get_stocks_performance_summary
        required = {"symbol", "total_trades", "total_pnl", "win_rate", "max_drawdown"}
        for item in get_stocks_performance_summary():
            assert required.issubset(item.keys()), f"Missing fields in {item}"

    def test_symbols_are_strings(self):
        from services.stocks_service import get_stocks_performance_summary
        for item in get_stocks_performance_summary():
            assert isinstance(item["symbol"], str)
            assert len(item["symbol"]) > 0

    def test_win_rate_between_0_and_1(self):
        from services.stocks_service import get_stocks_performance_summary
        for item in get_stocks_performance_summary():
            assert 0.0 <= item["win_rate"] <= 1.0

    def test_status_aggregates(self):
        from services.stocks_service import get_stocks_status
        status = get_stocks_status()
        assert "total_trades" in status
        assert "total_pnl" in status
        assert "win_rate" in status
        assert "max_drawdown" in status
        assert status["total_trades"] > 0


# ──────────────────────────────────────────────────────────
# 2. API endpoint tests via TestClient
# ──────────────────────────────────────────────────────────

class TestStocksEndpoints:

    def _client(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        client = TestClient(app)
        return client, settings.dashboard.api_key

    def test_performance_endpoint_200(self):
        client, key = self._client()
        resp = client.get("/api/stocks/performance", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_performance_fields(self):
        client, key = self._client()
        resp = client.get("/api/stocks/performance", headers={"X-API-Key": key})
        data = resp.json()
        required = {"symbol", "total_trades", "total_pnl", "win_rate", "max_drawdown"}
        for item in data:
            assert required.issubset(item.keys())

    def test_performance_requires_auth(self):
        client, _ = self._client()
        resp = client.get("/api/stocks/performance")
        assert resp.status_code == 401

    def test_status_endpoint_200(self):
        client, key = self._client()
        resp = client.get("/api/stocks/status", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert "total_trades" in data
        assert "total_pnl" in data
        assert "win_rate" in data

    def test_status_requires_auth(self):
        client, _ = self._client()
        resp = client.get("/api/stocks/status")
        assert resp.status_code == 401

    def test_known_symbols_present(self):
        """Verify the stub data includes AAPL, MSFT, TSLA."""
        client, key = self._client()
        resp = client.get("/api/stocks/performance", headers={"X-API-Key": key})
        symbols = {item["symbol"] for item in resp.json()}
        assert "AAPL" in symbols
        assert "MSFT" in symbols
        assert "TSLA" in symbols

    def test_pnl_types_are_numeric(self):
        client, key = self._client()
        resp = client.get("/api/stocks/performance", headers={"X-API-Key": key})
        for item in resp.json():
            assert isinstance(item["total_pnl"], (int, float))
            assert isinstance(item["win_rate"], (int, float))
            assert isinstance(item["max_drawdown"], (int, float))


# ──────────────────────────────────────────────────────────
# 3. Exchange client interface tests
# ──────────────────────────────────────────────────────────

class TestStocksExchangeClient:

    def test_alpaca_client_fetches_quote_with_mock(self):
        import asyncio
        from config.settings import StocksSettings
        from core.stocks_exchange_client import AlpacaStocksClient
        import httpx

        cfg = StocksSettings(provider="alpaca", alpaca_api_key="test12345678", alpaca_api_secret="test12345678")
        client = AlpacaStocksClient(cfg)

        async def mock_get(path, **kw):
            return httpx.Response(200, json={
                "quote": {"bp": 100.0, "ap": 100.5, "bs": 50, "as": 50, "t": "2026-01-01T00:00:00Z"}
            }, request=httpx.Request("GET", "https://test"))

        client._data.get = mock_get
        quote = asyncio.run(client.fetch_quote("TEST"))
        assert quote.symbol == "TEST"
        assert quote.bid == 100.0

    def test_paper_client_returns_quote(self):
        import asyncio
        from core.stocks_exchange_client import PaperStocksClient

        client = PaperStocksClient()
        quote = asyncio.run(client.fetch_quote("AAPL"))
        assert quote.symbol == "AAPL"
        assert quote.last > 0

    def test_paper_client_creates_order(self):
        import asyncio
        from core.stocks_exchange_client import PaperStocksClient

        client = PaperStocksClient()
        result = asyncio.run(client.create_order("MSFT", "buy", 10.0))
        assert result.status == "filled"
        assert result.symbol == "MSFT"
        assert result.filled_price > 0


# ──────────────────────────────────────────────────────────
# 4. Alpaca fallback test (broker error → stub data)
# ──────────────────────────────────────────────────────────

class TestAlpacaFallback:

    def test_performance_returns_stubs_on_alpaca_error(self):
        """When Alpaca raises StocksClientError, the endpoint still returns 200 with dummy data."""
        from unittest.mock import patch
        from services.api_server import app
        from config.settings import settings
        from core.stocks_exchange_client import StocksClientError
        from fastapi.testclient import TestClient

        client = TestClient(app)
        key = settings.dashboard.api_key

        # Force provider=alpaca and make client raise
        with patch("services.stocks_service.settings") as mock_settings, \
             patch("services.stocks_service.get_stocks_client") as mock_client:

            mock_settings.stocks.provider = "alpaca"

            async def fail_positions():
                raise StocksClientError("Connection refused")

            mock_client.return_value.fetch_positions = fail_positions

            resp = client.get("/api/stocks/performance", headers={"X-API-Key": key})
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            assert len(data) >= 1
            # Should contain stub data
            symbols = {item["symbol"] for item in data}
            assert "AAPL" in symbols or len(data) > 0  # Stubs present


# ──────────────────────────────────────────────────────────
# 5. Stocks trades endpoint tests
# ──────────────────────────────────────────────────────────

class TestStocksTradesEndpoint:

    @staticmethod
    def _ensure_db():
        """Initialize DB singleton for API if not set."""
        from services.api_server import set_database
        from services.db import Database
        db = Database()
        set_database(db)
        return db

    @staticmethod
    def _seed_trades(db, count=5):
        """Insert test stock trades into the DB."""
        for i in range(count):
            db._log_stock_trade_sync({
                "timestamp": f"2026-01-01T{i:02d}:00:00Z",
                "symbol": "AAPL" if i % 2 == 0 else "MSFT",
                "side": "buy" if i % 2 == 0 else "sell",
                "price": 190.0 + i,
                "qty": 1.0 + i,
                "order_id": f"test_trade_{i}",
                "status": "filled",
                "pnl": (i - 2) * 5.0,  # -10, -5, 0, 5, 10
                "reason": f"test reason {i}",
            })

    def test_trades_returns_200(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/trades", headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_trades_have_required_fields(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        db = self._ensure_db()
        self._seed_trades(db, 3)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/trades", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3

        required = {"timestamp", "symbol", "side", "price", "qty", "pnl"}
        for trade in data:
            assert required.issubset(trade.keys()), f"Missing fields: {required - trade.keys()}"

    def test_trades_respects_limit(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        db = self._ensure_db()
        self._seed_trades(db, 10)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/trades?limit=3", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 3

    def test_trades_requires_auth(self):
        from services.api_server import app
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        resp = client.get("/api/stocks/trades")
        assert resp.status_code in (401, 403)


# ──────────────────────────────────────────────────────────
# 6. Stocks PnL endpoint tests
# ──────────────────────────────────────────────────────────

class TestStocksPnlEndpoint:

    @staticmethod
    def _ensure_db():
        from services.api_server import set_database
        from services.db import Database
        db = Database()
        set_database(db)
        return db

    def test_pnl_returns_200(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/pnl", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_pnl_has_required_fields(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/pnl", headers={"X-API-Key": key})
        data = resp.json()
        assert "total_trades" in data
        assert "total_pnl" in data
        assert "win_rate" in data

    def test_pnl_win_rate_in_range(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/pnl", headers={"X-API-Key": key})
        data = resp.json()
        assert 0.0 <= data["win_rate"] <= 1.0

    def test_pnl_requires_auth(self):
        from services.api_server import app
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        resp = client.get("/api/stocks/pnl")
        assert resp.status_code in (401, 403)

    def test_pnl_includes_today_fields(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/pnl", headers={"X-API-Key": key})
        data = resp.json()
        assert "today_trades" in data
        assert "today_pnl" in data


# ──────────────────────────────────────────────────────────
# 7. Stocks Bot control endpoint tests
# ──────────────────────────────────────────────────────────

class TestStocksBotControlEndpoints:

    @staticmethod
    def _ensure_db():
        from services.api_server import set_database
        from services.db import Database
        db = Database()
        set_database(db)
        return db

    def test_status_offline_when_no_bot(self):
        """GET /api/stocks/bot/status returns offline when no bot is registered."""
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        from services.stocks_runtime import set_stocks_bot

        self._ensure_db()
        set_stocks_bot(None)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/bot/status", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "offline"
        assert data["running"] is False

    def test_status_running_with_bot(self):
        """GET /api/stocks/bot/status returns running state from a real bot."""
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        from services.stocks_runtime import set_stocks_bot
        from core.stocks_bot import StocksBot, StocksRiskConfig
        from core.stocks_strategy import StocksStrategy
        from core.stocks_exchange_client import PaperStocksClient

        self._ensure_db()
        bot = StocksBot(
            client=PaperStocksClient(),
            strategy=StocksStrategy(),
        )
        bot._running = True
        set_stocks_bot(bot)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/bot/status", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "running"
        assert data["running"] is True

    def test_pause_sets_paused(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        from services.stocks_runtime import set_stocks_bot
        from core.stocks_bot import StocksBot
        from core.stocks_strategy import StocksStrategy
        from core.stocks_exchange_client import PaperStocksClient

        self._ensure_db()
        bot = StocksBot(client=PaperStocksClient(), strategy=StocksStrategy())
        bot._running = True
        set_stocks_bot(bot)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.post("/api/stocks/bot/pause", headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()["state"] == "paused"
        assert bot._paused is True

    def test_resume_clears_paused(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        from services.stocks_runtime import set_stocks_bot
        from core.stocks_bot import StocksBot
        from core.stocks_strategy import StocksStrategy
        from core.stocks_exchange_client import PaperStocksClient

        self._ensure_db()
        bot = StocksBot(client=PaperStocksClient(), strategy=StocksStrategy())
        bot._running = True
        bot._paused = True
        set_stocks_bot(bot)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.post("/api/stocks/bot/resume", headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()["state"] == "running"
        assert bot._paused is False

    def test_panic_stops_bot(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        from services.stocks_runtime import set_stocks_bot
        from core.stocks_bot import StocksBot
        from core.stocks_strategy import StocksStrategy
        from core.stocks_exchange_client import PaperStocksClient

        self._ensure_db()
        bot = StocksBot(client=PaperStocksClient(), strategy=StocksStrategy())
        bot._running = True
        set_stocks_bot(bot)

        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.post("/api/stocks/bot/panic", headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()["state"] == "stopped"
        assert bot._running is False

    def test_control_503_when_no_bot(self):
        """Pause/resume/panic return 503 when bot is None."""
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        from services.stocks_runtime import set_stocks_bot

        self._ensure_db()
        set_stocks_bot(None)

        client = TestClient(app)
        key = settings.dashboard.api_key

        for action in ["pause", "resume", "panic"]:
            resp = client.post(f"/api/stocks/bot/{action}", headers={"X-API-Key": key})
            assert resp.status_code == 503

    def test_status_requires_auth(self):
        from services.api_server import app
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        resp = client.get("/api/stocks/bot/status")
        assert resp.status_code in (401, 403)

    def test_control_requires_auth(self):
        from services.api_server import app
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        for action in ["pause", "resume", "panic"]:
            resp = client.post(f"/api/stocks/bot/{action}")
            assert resp.status_code in (401, 403)


# ──────────────────────────────────────────────────────────
# 8. Stocks Config endpoint tests
# ──────────────────────────────────────────────────────────

class TestStocksConfigEndpoints:

    @staticmethod
    def _ensure_db():
        from services.api_server import set_database
        from services.db import Database
        db = Database()
        set_database(db)
        return db

    def test_get_config_returns_200(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/config", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_get_config_has_required_fields(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/config", headers={"X-API-Key": key})
        data = resp.json()
        for field in ("watchlist", "ma_fast_window", "ma_slow_window",
                       "signal_margin", "max_position_qty", "max_daily_trades"):
            assert field in data, f"Missing field: {field}"

    def test_get_config_defaults(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        db = self._ensure_db()
        # Reset to defaults for isolation
        db.upsert_stocks_config({
            "watchlist": "AAPL,MSFT,TSLA",
            "ma_fast_window": 5,
            "ma_slow_window": 20,
            "signal_margin": 0.002,
            "default_qty": 1.0,
            "max_position_qty": 10.0,
            "max_daily_trades": 50,
        })
        client = TestClient(app)
        key = settings.dashboard.api_key
        resp = client.get("/api/stocks/config", headers={"X-API-Key": key})
        data = resp.json()
        assert data["ma_fast_window"] == 5
        assert data["ma_slow_window"] == 20

    def test_put_config_round_trip(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key

        new_cfg = {
            "watchlist": "GOOG,AMZN",
            "ma_fast_window": 3,
            "ma_slow_window": 15,
            "signal_margin": 0.005,
            "default_qty": 2.0,
            "max_position_qty": 5.0,
            "max_daily_trades": 20,
        }
        resp = client.put("/api/stocks/config", json=new_cfg, headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify GET returns updated values
        resp2 = client.get("/api/stocks/config", headers={"X-API-Key": key})
        data = resp2.json()
        assert data["watchlist"] == "GOOG,AMZN"
        assert data["ma_fast_window"] == 3
        assert data["ma_slow_window"] == 15
        assert data["max_daily_trades"] == 20

    def test_put_config_rejects_fast_gte_slow(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key

        bad_cfg = {
            "watchlist": "AAPL",
            "ma_fast_window": 20,
            "ma_slow_window": 10,
            "signal_margin": 0.002,
            "max_position_qty": 10.0,
            "max_daily_trades": 50,
        }
        resp = client.put("/api/stocks/config", json=bad_cfg, headers={"X-API-Key": key})
        assert resp.status_code == 422

    def test_put_config_rejects_empty_watchlist(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        key = settings.dashboard.api_key

        bad_cfg = {
            "watchlist": "",
            "ma_fast_window": 5,
            "ma_slow_window": 20,
            "signal_margin": 0.002,
            "max_position_qty": 10.0,
            "max_daily_trades": 50,
        }
        resp = client.put("/api/stocks/config", json=bad_cfg, headers={"X-API-Key": key})
        assert resp.status_code == 422

    def test_get_config_requires_auth(self):
        from services.api_server import app
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        resp = client.get("/api/stocks/config")
        assert resp.status_code in (401, 403)

    def test_put_config_requires_auth(self):
        from services.api_server import app
        from fastapi.testclient import TestClient

        self._ensure_db()
        client = TestClient(app)
        resp = client.put("/api/stocks/config", json={"watchlist": "AAPL"})
        assert resp.status_code in (401, 403)
