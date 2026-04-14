"""Stocks Exchange Client — Protocol, factory, and broker adapters.

Defines the interface all stock broker adapters must follow,
a factory to instantiate the correct client from settings,
and concrete implementations (Paper + Alpaca).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


# ── Exception ───────────────────────────────────────────────

class StocksClientError(Exception):
    """Raised when a broker API call fails (network, auth, bad response)."""


# ── Data Types ──────────────────────────────────────────────

@dataclass
class Quote:
    """Real-time or delayed quote for a single equity."""
    symbol: str
    bid: float
    ask: float
    last: float
    volume: int
    timestamp: str  # ISO-8601


@dataclass
class Position:
    """Open position in the portfolio."""
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    market_value: float = 0.0
    side: str = "long"  # "long" | "short"


@dataclass
class OrderResult:
    """Response after submitting an order."""
    order_id: str
    symbol: str
    side: str
    qty: float
    order_type: str  # "market" | "limit"
    status: str      # "filled" | "pending" | "rejected"
    filled_price: float | None = None
    filled_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ── Protocol (interface contract) ───────────────────────────

@runtime_checkable
class StocksExchangeClientProtocol(Protocol):
    """Every stock broker adapter must satisfy this protocol."""

    async def fetch_quote(self, symbol: str) -> Quote: ...
    async def fetch_positions(self) -> list[Position]: ...
    async def create_order(
        self, symbol: str, side: str, qty: float,
        order_type: str = "market", limit_price: float | None = None,
    ) -> OrderResult: ...
    async def cancel_order(self, order_id: str) -> bool: ...
    async def get_account_balance(self) -> dict[str, Any]: ...


# ── Paper Implementation ────────────────────────────────────

class PaperStocksClient:
    """In-memory paper trading client for development/testing."""

    def __init__(self) -> None:
        self._positions: list[Position] = []
        self._balance = 100_000.0  # $100k paper portfolio

    async def fetch_quote(self, symbol: str) -> Quote:
        _prices = {"AAPL": 189.72, "MSFT": 422.15, "TSLA": 175.60}
        price = _prices.get(symbol, 100.0)
        return Quote(
            symbol=symbol, bid=price - 0.01, ask=price + 0.01,
            last=price, volume=1_000_000, timestamp="2026-01-01T00:00:00Z",
        )

    async def fetch_positions(self) -> list[Position]:
        return self._positions

    async def create_order(
        self, symbol: str, side: str, qty: float,
        order_type: str = "market", limit_price: float | None = None,
    ) -> OrderResult:
        quote = await self.fetch_quote(symbol)
        return OrderResult(
            order_id=f"paper_{symbol}_{side}", symbol=symbol,
            side=side, qty=qty, order_type=order_type,
            status="filled", filled_price=quote.last,
            filled_at="2026-01-01T00:00:00Z",
        )

    async def cancel_order(self, order_id: str) -> bool:
        return True

    async def get_account_balance(self) -> dict[str, Any]:
        return {"cash": self._balance, "buying_power": self._balance}


# ── Alpaca Implementation ───────────────────────────────────

# Alpaca data API uses a different base URL for market data
_ALPACA_DATA_URL = "https://data.alpaca.markets"


class AlpacaStocksClient:
    """Alpaca Markets broker adapter — connects to Alpaca REST API v2.

    Trading API: {base_url}/v2/...  (paper or live)
    Market Data: https://data.alpaca.markets/v2/stocks/...

    Requires: ALPACA_API_KEY + ALPACA_API_SECRET in .env
    Docs: https://docs.alpaca.markets/
    """

    def __init__(self, settings: Any) -> None:
        self._base_url = settings.alpaca_base_url.rstrip("/")
        self._api_key = settings.alpaca_api_key
        self._api_secret = settings.alpaca_api_secret

        headers = {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Accept": "application/json",
        }

        # Trading client (orders, positions, account)
        self._trading = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=10.0,
        )
        # Market data client (quotes, bars)
        self._data = httpx.AsyncClient(
            base_url=_ALPACA_DATA_URL,
            headers=headers,
            timeout=10.0,
        )

    async def aclose(self) -> None:
        """Close both HTTP clients gracefully."""
        await self._trading.aclose()
        await self._data.aclose()

    async def __aenter__(self) -> AlpacaStocksClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ── fetch_quote ─────────────────────────────────────────

    async def fetch_quote(self, symbol: str) -> Quote:
        """GET /v2/stocks/{symbol}/quotes/latest from Alpaca data API."""
        try:
            resp = await self._data.get(f"/v2/stocks/{symbol}/quotes/latest")
            if resp.status_code != 200:
                raise StocksClientError(
                    f"Alpaca quote error {resp.status_code}: {resp.text[:200]}"
                )
            data = resp.json()
            q = data.get("quote", data)
            return Quote(
                symbol=symbol,
                bid=float(q.get("bp", 0)),
                ask=float(q.get("ap", 0)),
                last=float(q.get("ap", 0)),  # Alpaca quotes don't have 'last'; use ask
                volume=int(q.get("as", 0)) + int(q.get("bs", 0)),
                timestamp=q.get("t", ""),
            )
        except StocksClientError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise StocksClientError(f"Alpaca network error (quote): {e}") from e

    # ── fetch_positions ─────────────────────────────────────

    async def fetch_positions(self) -> list[Position]:
        """GET /v2/positions from Alpaca trading API."""
        try:
            resp = await self._trading.get("/v2/positions")
            if resp.status_code != 200:
                raise StocksClientError(
                    f"Alpaca positions error {resp.status_code}: {resp.text[:200]}"
                )
            positions = []
            for p in resp.json():
                positions.append(Position(
                    symbol=p["symbol"],
                    qty=float(p["qty"]),
                    avg_entry_price=float(p["avg_entry_price"]),
                    current_price=float(p["current_price"]),
                    unrealized_pnl=float(p["unrealized_pl"]),
                    market_value=float(p.get("market_value", 0)),
                    side=p.get("side", "long"),
                ))
            return positions
        except StocksClientError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise StocksClientError(f"Alpaca network error (positions): {e}") from e

    # ── create_order ────────────────────────────────────────

    async def create_order(
        self, symbol: str, side: str, qty: float,
        order_type: str = "market", limit_price: float | None = None,
    ) -> OrderResult:
        """POST /v2/orders to Alpaca trading API."""
        body: dict[str, Any] = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": "day",
        }
        if order_type == "limit" and limit_price is not None:
            body["limit_price"] = str(limit_price)

        try:
            resp = await self._trading.post("/v2/orders", json=body)
            if resp.status_code not in (200, 201):
                raise StocksClientError(
                    f"Alpaca order error {resp.status_code}: {resp.text[:200]}"
                )
            o = resp.json()
            return OrderResult(
                order_id=o["id"],
                symbol=o["symbol"],
                side=o["side"],
                qty=float(o.get("qty", qty)),
                order_type=o.get("type", order_type),
                status=o.get("status", "pending"),
                filled_price=float(o["filled_avg_price"]) if o.get("filled_avg_price") else None,
                filled_at=o.get("filled_at"),
                raw=o,
            )
        except StocksClientError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise StocksClientError(f"Alpaca network error (order): {e}") from e

    # ── cancel_order ────────────────────────────────────────

    async def cancel_order(self, order_id: str) -> bool:
        """DELETE /v2/orders/{order_id}."""
        try:
            resp = await self._trading.delete(f"/v2/orders/{order_id}")
            return resp.status_code in (200, 204)
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise StocksClientError(f"Alpaca network error (cancel): {e}") from e

    # ── get_account_balance ─────────────────────────────────

    async def get_account_balance(self) -> dict[str, Any]:
        """GET /v2/account."""
        try:
            resp = await self._trading.get("/v2/account")
            if resp.status_code != 200:
                raise StocksClientError(
                    f"Alpaca account error {resp.status_code}: {resp.text[:200]}"
                )
            a = resp.json()
            return {
                "cash": float(a.get("cash", 0)),
                "buying_power": float(a.get("buying_power", 0)),
                "portfolio_value": float(a.get("portfolio_value", 0)),
                "equity": float(a.get("equity", 0)),
            }
        except StocksClientError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise StocksClientError(f"Alpaca network error (account): {e}") from e

    # ── cancel_all_orders ──────────────────────────────────

    async def cancel_all_orders(self) -> int:
        """DELETE /v2/orders — cancel all open orders. Returns count cancelled."""
        try:
            resp = await self._trading.delete("/v2/orders")
            if resp.status_code not in (200, 204, 207):
                raise StocksClientError(
                    f"Alpaca cancel_all error {resp.status_code}: {resp.text[:200]}"
                )
            if resp.status_code == 204:
                return 0
            data = resp.json()
            return len(data) if isinstance(data, list) else 0
        except StocksClientError:
            raise
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            raise StocksClientError(f"Alpaca network error (cancel_all): {e}") from e


# ── Factory ─────────────────────────────────────────────────

def create_stocks_client(settings: Any) -> StocksExchangeClientProtocol:
    """Instantiate the correct stocks client based on provider config."""
    if settings.provider == "alpaca":
        return AlpacaStocksClient(settings)
    return PaperStocksClient()
