"""Tests for Paper Wallet — SQLite-persistent virtual execution."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from core.paper_wallet import PaperWallet
from core.strategy_base import Signal


@pytest.fixture
def db_path(tmp_path):
    """Temporary SQLite path for tests."""
    return tmp_path / "test_paper.db"


@pytest.fixture
def wallet(db_path):
    """Fresh PaperWallet instance — fees and slippage zeroed for deterministic tests."""
    w = PaperWallet(
        db_path=db_path,
        initial_quote=1000.0,
        quote_asset="USDT",
        maker_fee=0.0,
        slippage_bps=0.0,
    )
    yield w
    w.close()


class TestPaperWalletInit:
    """Initialization and SQLite persistence tests."""

    def test_init_creates_tables_and_seeds_balance(self, wallet):
        """Fresh wallet should have 1000 USDT."""
        bal = asyncio.run(wallet.fetch_balance("USDT"))
        assert bal["free"] == 1000.0
        assert bal["total"] == 1000.0

    def test_init_no_base_balance(self, wallet):
        """Fresh wallet should have 0 BTC."""
        bal = asyncio.run(wallet.fetch_balance("BTC"))
        assert bal["free"] == 0.0

    def test_persistence_across_instances(self, db_path):
        """Balance should persist after recreating PaperWallet."""
        # Instance 1: execute a trade
        w1 = PaperWallet(db_path=db_path, initial_quote=1000.0)
        result = asyncio.run(
            w1.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 0.01)
        )
        assert result["status"] == "filled"
        bal_after = asyncio.run(w1.fetch_balance("USDT"))
        w1.close()

        # Instance 2: same DB, should see updated balance
        w2 = PaperWallet(db_path=db_path, initial_quote=1000.0)
        bal_restored = asyncio.run(w2.fetch_balance("USDT"))
        assert bal_restored["free"] == bal_after["free"]

        # BTC should persist too
        btc_bal = asyncio.run(w2.fetch_balance("BTC"))
        assert btc_bal["free"] == 0.01
        w2.close()


class TestPaperWalletExecution:
    """Trade execution logic tests."""

    def test_buy_decreases_quote_increases_base(self, wallet):
        """BUY should deduct USDT and add BTC."""
        result = asyncio.run(
            wallet.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 0.01)
        )
        assert result["status"] == "filled"
        assert result["filled"] == 0.01
        assert result["cost"] == 500.0

        quote = asyncio.run(wallet.fetch_balance("USDT"))
        base = asyncio.run(wallet.fetch_balance("BTC"))
        # With maker_fee=0 and slippage_bps=0: 1000 - (50000 * 0.01) = 500.0
        assert quote["free"] == 500.0  # 1000 - 500
        assert base["free"] == 0.01

    def test_sell_decreases_base_increases_quote(self, wallet):
        """SELL should deduct BTC and add USDT."""
        # First buy some BTC
        asyncio.run(
            wallet.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 0.01)
        )
        # Then sell
        result = asyncio.run(
            wallet.execute_simulated_trade(Signal.SELL, "BTC/USDT", 51000.0, 0.01)
        )
        assert result["status"] == "filled"

        quote = asyncio.run(wallet.fetch_balance("USDT"))
        # With slippage=0, fee=0: 1000 - 500 + 510 = 1010
        assert quote["free"] == 1010.0

    def test_buy_insufficient_quote_rejected(self, wallet):
        """BUY should fail if not enough USDT."""
        result = asyncio.run(
            wallet.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 1.0)
        )
        # 1.0 BTC @ 50000 = 50000 USDT, but only have 1000
        assert result["status"] == "failed"
        assert result["reason"] == "insufficient_quote"

    def test_sell_insufficient_base_rejected(self, wallet):
        """SELL should fail if no BTC held."""
        result = asyncio.run(
            wallet.execute_simulated_trade(Signal.SELL, "BTC/USDT", 50000.0, 0.01)
        )
        assert result["status"] == "failed"
        assert result["reason"] == "insufficient_base"

    def test_trade_history_recorded(self, wallet):
        """Trades should appear in history."""
        asyncio.run(
            wallet.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 0.01)
        )
        history = asyncio.run(wallet.get_trade_history(limit=10))
        assert len(history) == 1
        assert history[0]["signal"] == "BUY"
        # With slippage_bps=0.0 the stored price equals the input price exactly
        assert history[0]["price"] == 50000.0

    def test_pnl_summary(self, wallet):
        """PnL summary should track trades."""
        asyncio.run(
            wallet.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 0.01)
        )
        asyncio.run(
            wallet.execute_simulated_trade(Signal.SELL, "BTC/USDT", 51000.0, 0.01)
        )
        pnl = asyncio.run(wallet.get_pnl_summary())
        assert pnl["total_trades"] == 2
        assert pnl["buys"] == 1
        assert pnl["sells"] == 1
        # With fee=0 and slippage=0: 1010 - 1000 = 10.0
        assert pnl["unrealized_pnl"] == 10.0  # 1010 - 1000

    def test_reset_wipes_state(self, wallet):
        """Reset should return to initial state."""
        asyncio.run(
            wallet.execute_simulated_trade(Signal.BUY, "BTC/USDT", 50000.0, 0.01)
        )
        asyncio.run(wallet.reset())

        quote = asyncio.run(wallet.fetch_balance("USDT"))
        assert quote["free"] == 1000.0

        btc = asyncio.run(wallet.fetch_balance("BTC"))
        assert btc["free"] == 0.0

        history = asyncio.run(wallet.get_trade_history())
        assert len(history) == 0
