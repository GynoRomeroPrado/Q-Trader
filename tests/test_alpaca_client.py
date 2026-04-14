"""Tests for AlpacaStocksClient — all HTTP calls mocked via httpx.MockTransport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from core.stocks_exchange_client import (
    AlpacaStocksClient,
    StocksClientError,
    Quote,
    Position,
    OrderResult,
)


# ── Fixtures ──────────────────────────────────────────────


def _mock_settings(base_url: str = "https://paper-api.alpaca.markets"):
    """Build a mock settings object for AlpacaStocksClient."""
    from unittest.mock import MagicMock
    s = MagicMock()
    s.alpaca_base_url = base_url
    s.alpaca_api_key = "PKTEST123456"
    s.alpaca_api_secret = "secret_test_key_abc"
    return s


def _make_client(
    trading_handler=None,
    data_handler=None,
) -> AlpacaStocksClient:
    """Build client with mocked HTTP transports."""
    settings = _mock_settings()
    client = AlpacaStocksClient(settings)

    if trading_handler:
        client._trading = httpx.AsyncClient(
            transport=httpx.MockTransport(trading_handler),
            base_url=settings.alpaca_base_url,
        )
    if data_handler:
        client._data = httpx.AsyncClient(
            transport=httpx.MockTransport(data_handler),
            base_url="https://data.alpaca.markets",
        )
    return client


def _run(coro):
    return asyncio.run(coro)


# ── T1: get_account_balance ───────────────────────────────


class TestGetAccountBalance:

    def test_200_returns_account_dict(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "cash": "50000.00",
                "buying_power": "100000.00",
                "portfolio_value": "50000.00",
                "equity": "50000.00",
                "status": "ACTIVE",
            })

        client = _make_client(trading_handler=handler)
        result = _run(client.get_account_balance())

        assert result["cash"] == 50000.0
        assert result["equity"] == 50000.0
        assert result["buying_power"] == 100000.0
        assert result["portfolio_value"] == 50000.0

    def test_403_raises_client_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden — invalid API key")

        client = _make_client(trading_handler=handler)

        with pytest.raises(StocksClientError, match="403"):
            _run(client.get_account_balance())


# ── T2: fetch_quote ───────────────────────────────────────


class TestFetchQuote:

    def test_200_returns_quote(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "quote": {
                    "bp": 149.50,
                    "ap": 150.50,
                    "bs": 100,
                    "as": 200,
                    "t": "2026-04-09T16:00:00Z",
                }
            })

        client = _make_client(data_handler=handler)
        quote = _run(client.fetch_quote("AAPL"))

        assert isinstance(quote, Quote)
        assert quote.symbol == "AAPL"
        assert quote.bid == 149.50
        assert quote.ask == 150.50

    def test_404_raises_client_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Symbol not found")

        client = _make_client(data_handler=handler)

        with pytest.raises(StocksClientError, match="404"):
            _run(client.fetch_quote("INVALID"))

    def test_mid_price_calculation(self):
        """bid=149.50, ask=150.50 -> mid_price == 150.00"""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "quote": {"bp": 149.50, "ap": 150.50, "bs": 100, "as": 200, "t": ""}
            })

        client = _make_client(data_handler=handler)
        quote = _run(client.fetch_quote("AAPL"))
        mid = (quote.bid + quote.ask) / 2.0

        assert mid == 150.00


# ── T3: create_order ──────────────────────────────────────


class TestCreateOrder:

    def test_200_returns_order_result(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            return httpx.Response(200, json={
                "id": "order-abc-123",
                "symbol": body["symbol"],
                "side": body["side"],
                "qty": body["qty"],
                "type": body["type"],
                "status": "accepted",
                "filled_avg_price": None,
                "filled_at": None,
            })

        client = _make_client(trading_handler=handler)
        result = _run(client.create_order("AAPL", "buy", 5.0))

        assert isinstance(result, OrderResult)
        assert result.order_id == "order-abc-123"
        assert result.symbol == "AAPL"
        assert result.side == "buy"
        assert result.status == "accepted"

    def test_422_insufficient_funds_raises_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={
                "message": "insufficient buying power"
            })

        client = _make_client(trading_handler=handler)

        with pytest.raises(StocksClientError, match="422"):
            _run(client.create_order("AAPL", "buy", 999999.0))


# ── T4: fetch_positions ──────────────────────────────────


class TestFetchPositions:

    def test_200_returns_positions_list(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "180.00",
                    "current_price": "190.00",
                    "unrealized_pl": "100.00",
                    "market_value": "1900.00",
                    "side": "long",
                },
                {
                    "symbol": "TSLA",
                    "qty": "5",
                    "avg_entry_price": "300.00",
                    "current_price": "310.00",
                    "unrealized_pl": "50.00",
                    "market_value": "1550.00",
                    "side": "long",
                },
            ])

        client = _make_client(trading_handler=handler)
        positions = _run(client.fetch_positions())

        assert len(positions) == 2
        assert all(isinstance(p, Position) for p in positions)
        assert positions[0].symbol == "AAPL"
        assert positions[0].qty == 10.0
        assert positions[0].unrealized_pnl == 100.0
        assert positions[1].symbol == "TSLA"

    def test_empty_positions(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        client = _make_client(trading_handler=handler)
        positions = _run(client.fetch_positions())
        assert positions == []


# ── T5: cancel_all_orders ─────────────────────────────────


class TestCancelAllOrders:

    def test_200_returns_count(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[
                {"id": "order-1", "status": "pending_cancel"},
                {"id": "order-2", "status": "pending_cancel"},
            ])

        client = _make_client(trading_handler=handler)
        count = _run(client.cancel_all_orders())
        assert count == 2

    def test_204_no_orders(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        client = _make_client(trading_handler=handler)
        count = _run(client.cancel_all_orders())
        assert count == 0


# ── T6: Headers contain API key ───────────────────────────


class TestAuthHeaders:

    def test_trading_client_has_auth_headers(self):
        settings = _mock_settings()
        client = AlpacaStocksClient(settings)

        headers = client._trading.headers
        assert headers["APCA-API-KEY-ID"] == "PKTEST123456"
        assert headers["APCA-API-SECRET-KEY"] == "secret_test_key_abc"

    def test_data_client_has_auth_headers(self):
        settings = _mock_settings()
        client = AlpacaStocksClient(settings)

        headers = client._data.headers
        assert headers["APCA-API-KEY-ID"] == "PKTEST123456"
