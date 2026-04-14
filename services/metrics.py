"""Pure metric functions — no DB or framework dependencies.

All functions receive plain Python data and return floats.
Easily testable in isolation.
"""

from __future__ import annotations

from typing import Any


def compute_win_rate(trades: list[dict[str, Any]]) -> float:
    """Proportion of trades with pnl > 0.  Returns 0.0 if no trades."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.get("pnl", 0.0) > 0)
    return wins / len(trades)


def compute_max_drawdown(equity_points: list[float]) -> float:
    """Maximum peak-to-trough relative drawdown.

    Returns a positive float (e.g. 0.15 = 15 %).
    Returns 0.0 if fewer than 2 points or no drawdown.
    """
    if len(equity_points) < 2:
        return 0.0

    peak = equity_points[0]
    max_dd = 0.0

    for value in equity_points:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

    return max_dd


def build_equity_series(
    initial_balance: float, trades: list[dict[str, Any]]
) -> list[float]:
    """Build an equity curve from an initial balance and a list of trades.

    Trades must be in chronological order and contain a 'pnl' key.
    Returns [initial_balance, balance_after_t1, balance_after_t2, ...].
    """
    equity = [initial_balance]
    running = initial_balance
    for t in trades:
        running += t.get("pnl", 0.0)
        equity.append(running)
    return equity
