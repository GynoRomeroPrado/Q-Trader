"""Balance manager — reads balances and handles withdrawals."""

from __future__ import annotations

import logging

from core.exchange_client import ExchangeClient

logger = logging.getLogger(__name__)


class BalanceManager:
    """Manages balance queries and withdrawal operations."""

    def __init__(self, exchange: ExchangeClient) -> None:
        self._exchange = exchange

    async def get_available(self, asset: str = "USDT") -> float:
        """Return free balance for the given asset."""
        bal = await self._exchange.fetch_balance(asset)
        logger.info(f"💰 {asset} balance: free={bal['free']}, total={bal['total']}")
        return bal["free"]

    async def check_minimum(self, asset: str = "USDT",
                            min_amount: float = 10.0) -> bool:
        """Return True if free balance >= min_amount."""
        free = await self.get_available(asset)
        if free < min_amount:
            logger.warning(
                f"⚠️  {asset} balance ({free}) below minimum ({min_amount})"
            )
            return False
        return True

    async def withdraw(self, asset: str, amount: float,
                       address: str, network: str = "TRC20") -> dict:
        """Withdraw funds to an external wallet.

        Peru-optimized: uses TRC20 (Tron) for USDT — ~1 USDT fee.
        After withdrawal, sell on Binance P2P for PEN.
        """
        logger.info(
            f"📤 Withdrawing {amount} {asset} to {address[:8]}... "
            f"via {network}"
        )
        # Use ExchangeClient's internal exchange via public REST retry wrapper
        order = await self._exchange._rest_call_with_retry(
            lambda: self._exchange._exchange.withdraw(
                asset, amount, address, params={"network": network}
            ),
            f"withdraw({asset})",
        )
        logger.info(f"✅ Withdrawal ID: {order.get('id', 'N/A')}")
        return order
