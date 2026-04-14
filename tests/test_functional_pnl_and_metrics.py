"""Functional tests — metrics module + /api/performance endpoint via TestClient."""

import sqlite3
from unittest.mock import patch, MagicMock

import pytest

# ──────────────────────────────────────────────────────────
# 1. Pure metric function tests
# ──────────────────────────────────────────────────────────

from services.metrics import compute_win_rate, compute_max_drawdown, build_equity_series


class TestComputeWinRate:

    def test_empty_list(self):
        assert compute_win_rate([]) == 0.0

    def test_all_winners(self):
        trades = [{"pnl": 5.0}, {"pnl": 3.0}, {"pnl": 0.01}]
        assert compute_win_rate(trades) == pytest.approx(1.0)

    def test_all_losers(self):
        trades = [{"pnl": -2.0}, {"pnl": -0.5}]
        assert compute_win_rate(trades) == pytest.approx(0.0)

    def test_mixed(self):
        # 2 wins, 1 loss → 2/3
        trades = [{"pnl": 10.0}, {"pnl": -5.0}, {"pnl": 3.0}]
        assert compute_win_rate(trades) == pytest.approx(2 / 3)

    def test_breakeven_is_not_a_win(self):
        trades = [{"pnl": 0.0}]
        assert compute_win_rate(trades) == pytest.approx(0.0)


class TestComputeMaxDrawdown:

    def test_no_drawdown(self):
        # Monotonically increasing
        assert compute_max_drawdown([100, 110, 120, 130]) == pytest.approx(0.0)

    def test_single_point(self):
        assert compute_max_drawdown([100]) == 0.0

    def test_empty(self):
        assert compute_max_drawdown([]) == 0.0

    def test_simple_drawdown(self):
        # 100 → 120 → 90 → 110
        # Peak=120, trough=90 → dd = (120-90)/120 = 0.25
        assert compute_max_drawdown([100, 120, 90, 110]) == pytest.approx(0.25)

    def test_multiple_drawdowns_returns_max(self):
        # Peak 200, trough 150 → dd=0.25; peak 180, trough 100 → dd=0.444
        # max is 0.444
        equity = [100, 200, 150, 180, 100]
        expected = (200 - 100) / 200  # second drawdown: 180→100 uses peak 200
        assert compute_max_drawdown(equity) == pytest.approx(expected)


class TestBuildEquitySeries:

    def test_basic(self):
        trades = [{"pnl": 10}, {"pnl": -5}, {"pnl": 3}]
        eq = build_equity_series(1000, trades)
        assert eq == [1000, 1010, 1005, 1008]

    def test_empty_trades(self):
        eq = build_equity_series(500, [])
        assert eq == [500]


# ──────────────────────────────────────────────────────────
# 2. DB-layer performance summary
# ──────────────────────────────────────────────────────────

@pytest.fixture
def perf_db():
    """In-memory Database with trades for performance testing."""
    from services.db import Database

    with patch.object(Database, "__init__", lambda self: None):
        d = Database()

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, symbol TEXT, side TEXT,
            price REAL, amount REAL, order_id TEXT,
            pnl REAL DEFAULT 0.0
        )
    """)
    conn.execute("""
        CREATE TABLE bot_status (
            id INTEGER PRIMARY KEY DEFAULT 1,
            state TEXT DEFAULT 'stopped',
            started_at TEXT, last_heartbeat TEXT, last_error TEXT,
            total_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0.0
        )
    """)
    conn.execute("INSERT INTO bot_status (id) VALUES (1)")
    conn.commit()
    d._sqlite = conn
    return d


class TestDBPerformanceSummary:

    def _insert_trades(self, db, pnl_list):
        """Helper: insert trades and update bot_status atomically."""
        for pnl in pnl_list:
            db._log_trade_sync({
                "timestamp": "2026-01-01T00:00:00Z",
                "symbol": "BTC/USDT",
                "side": "sell" if pnl != 0 else "buy",
                "price": 100.0,
                "amount": 1.0,
                "pnl": pnl,
            })

    def test_performance_summary_mixed(self, perf_db):
        """3 trades: +10, -5, +3 → win_rate=2/3, total_pnl=8."""
        self._insert_trades(perf_db, [10.0, -5.0, 3.0])

        result = perf_db._get_performance_sync()
        assert result["total_trades"] == 3
        assert result["total_pnl"] == pytest.approx(8.0)
        assert result["win_rate"] == pytest.approx(round(2 / 3, 4))
        assert result["max_drawdown"] > 0  # there is a drawdown after the loss

    def test_performance_no_trades(self, perf_db):
        result = perf_db._get_performance_sync()
        assert result["total_trades"] == 0
        assert result["total_pnl"] == 0.0
        assert result["win_rate"] == 0.0
        assert result["max_drawdown"] == 0.0

    def test_max_drawdown_calculation(self, perf_db):
        """Verify max_drawdown with a known sequence.

        Initial: 1000, trades: +50, -100, +30
        Equity: [1000, 1050, 950, 980]
        Peak=1050, Trough=950 → dd = 100/1050 ≈ 0.0952
        """
        self._insert_trades(perf_db, [50.0, -100.0, 30.0])
        result = perf_db._get_performance_sync()
        expected_dd = round(100 / 1050, 4)
        assert result["max_drawdown"] == pytest.approx(expected_dd)


# ──────────────────────────────────────────────────────────
# 3. /api/performance endpoint via TestClient
# ──────────────────────────────────────────────────────────

class TestPerformanceEndpoint:

    @pytest.fixture(autouse=True)
    def setup_app(self, perf_db):
        """Wire the test DB into the FastAPI app."""
        from services import api_server
        self._original_db = api_server._db
        api_server._db = perf_db
        self.db = perf_db
        yield
        api_server._db = self._original_db

    def _insert(self, pnl_list):
        for pnl in pnl_list:
            self.db._log_trade_sync({
                "timestamp": "2026-01-01T00:00:00Z",
                "symbol": "BTC/USDT",
                "side": "sell",
                "price": 100.0,
                "amount": 1.0,
                "pnl": pnl,
            })

    def _client(self):
        from services.api_server import app
        from config.settings import settings
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # Use the real API key as auth header
        return client, settings.dashboard.api_key

    def test_performance_returns_correct_data(self):
        self._insert([10.0, -5.0, 3.0])
        client, key = self._client()
        resp = client.get("/api/performance", headers={"X-API-Key": key})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_trades"] == 3
        assert data["total_pnl"] == pytest.approx(8.0)
        assert data["win_rate"] == pytest.approx(round(2 / 3, 4))
        assert "max_drawdown" in data

    def test_performance_requires_auth(self):
        client, _ = self._client()
        resp = client.get("/api/performance")
        assert resp.status_code == 401

    def test_performance_empty(self):
        client, key = self._client()
        resp = client.get("/api/performance", headers={"X-API-Key": key})
        data = resp.json()
        assert data["total_trades"] == 0
        assert data["win_rate"] == 0.0


# ──────────────────────────────────────────────────────────
# 4. is_win / LossStreakGuard integration check
# ──────────────────────────────────────────────────────────

class TestLossStreakFromPnL:
    """Verify that losing trades are tracked by LossStreakGuard."""

    def test_loss_streak_counts_losses(self):
        from core.risk_manager import LossStreakGuard

        guard = LossStreakGuard(max_consecutive=3, cooldown_sec=900)

        # Simulate PnL results → derive is_win → feed guard
        pnl_sequence = [10.0, -5.0, -3.0, -1.0]  # 3 consecutive losses after 1 win

        for pnl in pnl_sequence:
            is_win = pnl > 0
            if is_win:
                guard.record_win()
            else:
                guard.record_loss()

        # After 3 consecutive losses, guard should block
        allowed, reason = guard.is_allowed()
        assert not allowed
        assert "cooldown" in reason.lower() or "streak" in reason.lower()

    def test_win_resets_streak(self):
        from core.risk_manager import LossStreakGuard

        guard = LossStreakGuard(max_consecutive=3, cooldown_sec=900)

        # 2 losses then a win → streak resets
        guard.record_loss()
        guard.record_loss()
        guard.record_win()

        allowed, _ = guard.is_allowed()
        assert allowed  # streak was reset by the win
