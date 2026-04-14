"""Tests for /api/ai/forecast endpoint and ai_service."""

import pytest


# ──────────────────────────────────────────────────────────
# 1. Service-level tests
# ──────────────────────────────────────────────────────────

class TestAiService:

    def test_returns_list(self):
        from services.ai_service import get_dummy_forecasts
        result = get_dummy_forecasts("crypto", ["BTC/USDT"], "1h")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_has_required_fields(self):
        from services.ai_service import get_dummy_forecasts
        required = {"symbol", "domain", "timeframe", "trend_score",
                     "volatility_score", "confidence", "forecast_horizon"}
        for item in get_dummy_forecasts("crypto", ["BTC/USDT", "ETH/USDT"], "1h"):
            assert required.issubset(item.keys()), f"Missing fields in {item}"

    def test_domain_preserved(self):
        from services.ai_service import get_dummy_forecasts
        for item in get_dummy_forecasts("stocks", ["AAPL"], "1d"):
            assert item["domain"] == "stocks"

    def test_scores_in_range(self):
        from services.ai_service import get_dummy_forecasts
        for item in get_dummy_forecasts("crypto", ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "1h"):
            assert -1.0 <= item["trend_score"] <= 1.0
            assert 0.0 <= item["volatility_score"] <= 1.0
            assert 0.0 <= item["confidence"] <= 1.0

    def test_deterministic(self):
        from services.ai_service import get_dummy_forecasts
        a = get_dummy_forecasts("crypto", ["BTC/USDT"], "1h")
        b = get_dummy_forecasts("crypto", ["BTC/USDT"], "1h")
        assert a == b

    def test_sorted_by_abs_trend(self):
        from services.ai_service import get_dummy_forecasts
        result = get_dummy_forecasts("crypto", ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "1h")
        trends = [abs(r["trend_score"]) for r in result]
        assert trends == sorted(trends, reverse=True)

    def test_horizon_varies_by_timeframe(self):
        from services.ai_service import get_dummy_forecasts
        h1 = get_dummy_forecasts("crypto", ["BTC/USDT"], "1h")[0]["forecast_horizon"]
        d1 = get_dummy_forecasts("crypto", ["BTC/USDT"], "1d")[0]["forecast_horizon"]
        assert h1 != d1

    def test_multiple_symbols(self):
        from services.ai_service import get_dummy_forecasts
        syms = ["AAPL", "MSFT", "TSLA", "NVDA"]
        result = get_dummy_forecasts("stocks", syms, "1d")
        assert len(result) == 4
        returned_syms = {r["symbol"] for r in result}
        assert returned_syms == set(syms)


# ──────────────────────────────────────────────────────────
# 2. API endpoint tests
# ──────────────────────────────────────────────────────────

class TestAiForecastEndpoint:

    def _client(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient
        return TestClient(app), settings.dashboard.api_key

    def test_crypto_forecast_200(self):
        client, key = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "crypto", "symbols": ["BTC/USDT"], "timeframe": "1h"
        }, headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_stocks_forecast_200(self):
        client, key = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "stocks", "symbols": ["AAPL", "MSFT"], "timeframe": "1d"
        }, headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_response_fields(self):
        client, key = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "crypto", "symbols": ["BTC/USDT"], "timeframe": "1h"
        }, headers={"X-API-Key": key})
        required = {"symbol", "domain", "timeframe", "trend_score",
                     "volatility_score", "confidence", "forecast_horizon"}
        for item in resp.json():
            assert required.issubset(item.keys())

    def test_invalid_domain_422(self):
        client, key = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "forex", "symbols": ["EUR/USD"], "timeframe": "1h"
        }, headers={"X-API-Key": key})
        assert resp.status_code == 422

    def test_requires_auth(self):
        client, _ = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "crypto", "symbols": ["BTC/USDT"], "timeframe": "1h"
        })
        assert resp.status_code == 401

    def test_default_timeframe(self):
        client, key = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "crypto", "symbols": ["ETH/USDT"]
        }, headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json()[0]["timeframe"] == "1h"

    def test_empty_symbols_returns_empty(self):
        client, key = self._client()
        resp = client.post("/api/ai/forecast", json={
            "domain": "crypto", "symbols": [], "timeframe": "1h"
        }, headers={"X-API-Key": key})
        assert resp.status_code == 200
        assert resp.json() == []
