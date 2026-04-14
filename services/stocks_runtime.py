"""Stocks runtime — global reference to the active StocksBot instance.

Used by API endpoints to query status and send control commands.
Set by run_bot._run_stocks_domain() at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.stocks_bot import StocksBot

_stocks_bot: StocksBot | None = None


def set_stocks_bot(bot: StocksBot) -> None:
    """Register the active StocksBot instance."""
    global _stocks_bot
    _stocks_bot = bot


def get_stocks_bot() -> StocksBot | None:
    """Get the active StocksBot instance (None if not started)."""
    return _stocks_bot
