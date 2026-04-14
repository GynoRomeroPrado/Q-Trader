"""Tests for fee settings and paper_wallet fee source."""

import os
import unittest
from unittest.mock import patch


class TestFeeSettings(unittest.TestCase):
    """Verify fee_maker and fee_taker are loaded from env."""

    def test_fee_maker_default(self):
        from config.settings import RiskSettings
        rs = RiskSettings()
        # Default when TRADING_FEE_MAKER not in env already set by .env
        assert isinstance(rs.fee_maker, float)
        assert rs.fee_maker > 0

    def test_fee_taker_default(self):
        from config.settings import RiskSettings
        rs = RiskSettings()
        assert isinstance(rs.fee_taker, float)
        assert rs.fee_taker > 0

    def test_fee_taker_gte_maker(self):
        """Taker fee should be >= maker fee (exchange standard)."""
        from config.settings import RiskSettings
        rs = RiskSettings()
        assert rs.fee_taker >= rs.fee_maker

    @patch.dict(os.environ, {"TRADING_FEE_MAKER": "0.0001", "TRADING_FEE_TAKER": "0.0003"})
    def test_fee_from_env_override(self):
        from config.settings import RiskSettings
        rs = RiskSettings()
        assert rs.fee_maker == 0.0001
        assert rs.fee_taker == 0.0003


class TestPaperWalletUsesSettingsFee(unittest.TestCase):
    """Verify paper_wallet reads fee_taker from settings, not hardcoded."""

    def test_paper_wallet_fee_source(self):
        """PaperWallet default fee should match settings.risk.fee_taker."""
        import inspect
        from core.paper_wallet import PaperWallet
        source = inspect.getsource(PaperWallet.__init__)
        # Must reference fee_taker, not paper_fee_pct
        assert "fee_taker" in source, (
            "PaperWallet.__init__ should use settings.risk.fee_taker, "
            "not the legacy paper_fee_pct"
        )

    def test_market_type_in_settings(self):
        """ExchangeSettings should expose market_type."""
        from config.settings import settings
        assert hasattr(settings.exchange, "market_type")
        assert settings.exchange.market_type in ("spot", "future", "swap")
