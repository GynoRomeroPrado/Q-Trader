"""NYSE Market Hours Guard.

Determines if NYSE is open based on current time in America/New_York.
Used by StocksBot to avoid trading outside market hours.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# NYSE regular session
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)
_NYSE_TZ = ZoneInfo("America/New_York")


def _nyse_holidays(year: int) -> set[date]:
    """Fixed + observed NYSE holidays for a given year.

    Covers: New Year, MLK Day, Presidents Day, Good Friday,
    Memorial Day, Juneteenth, July 4, Labor Day, Thanksgiving, Christmas.
    """
    holidays: set[date] = set()

    # New Year's Day
    ny = date(year, 1, 1)
    holidays.add(_observe(ny))

    # MLK Day — 3rd Monday of January
    holidays.add(_nth_weekday(year, 1, 0, 3))

    # Presidents Day — 3rd Monday of February
    holidays.add(_nth_weekday(year, 2, 0, 3))

    # Good Friday — 2 days before Easter
    holidays.add(_easter(year) - timedelta(days=2))

    # Memorial Day — last Monday of May
    holidays.add(_last_weekday(year, 5, 0))

    # Juneteenth
    holidays.add(_observe(date(year, 6, 19)))

    # Independence Day
    holidays.add(_observe(date(year, 7, 4)))

    # Labor Day — 1st Monday of September
    holidays.add(_nth_weekday(year, 9, 0, 1))

    # Thanksgiving — 4th Thursday of November
    holidays.add(_nth_weekday(year, 11, 3, 4))

    # Christmas
    holidays.add(_observe(date(year, 12, 25)))

    return holidays


def _observe(d: date) -> date:
    """If holiday falls on Saturday → Friday; Sunday → Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """Return nth occurrence of weekday (0=Mon) in month."""
    first = date(year, month, 1)
    day = first + timedelta(days=(weekday - first.weekday()) % 7)
    return day + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Return last occurrence of weekday in month."""
    if month == 12:
        last_day = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)
    day = last_day - timedelta(days=(last_day.weekday() - weekday) % 7)
    return day


def _easter(year: int) -> date:
    """Computus algorithm for Easter Sunday."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(year, month, day)


# ── Public API ─────────────────────────────────────────────

def is_market_open(now: datetime | None = None) -> bool:
    """Check if NYSE is currently in regular trading session.

    Args:
        now: Override current time for testing. If None, uses real clock.

    Returns:
        True if market is open (Mon-Fri, 09:30-16:00 ET, non-holiday).
    """
    if now is None:
        now = datetime.now(_NYSE_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_NYSE_TZ)
    else:
        now = now.astimezone(_NYSE_TZ)

    # Weekend check (5=Sat, 6=Sun)
    if now.weekday() >= 5:
        return False

    # Holiday check
    if now.date() in _nyse_holidays(now.year):
        return False

    # Time check
    current_time = now.time()
    return _MARKET_OPEN <= current_time < _MARKET_CLOSE


def get_market_status(now: datetime | None = None) -> dict:
    """Return market status + next event info for dashboard display."""
    if now is None:
        now = datetime.now(_NYSE_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_NYSE_TZ)
    else:
        now = now.astimezone(_NYSE_TZ)

    open_now = is_market_open(now)

    if open_now:
        next_event = "Closes at 16:00 ET"
    else:
        # Find next open
        check = now
        for _ in range(10):
            if check.time() < _MARKET_OPEN and check.weekday() < 5:
                if check.date() not in _nyse_holidays(check.year):
                    next_event = f"Opens at 09:30 ET ({check.strftime('%A')})"
                    break
            check = datetime.combine(
                check.date() + timedelta(days=1),
                _MARKET_OPEN,
                tzinfo=_NYSE_TZ,
            )
            if check.weekday() < 5 and check.date() not in _nyse_holidays(check.year):
                next_event = f"Opens at 09:30 ET ({check.strftime('%A')})"
                break
        else:
            next_event = "Opens next business day"

    return {
        "is_open": open_now,
        "timezone": "America/New_York",
        "next_event": next_event,
        "current_time_et": now.strftime("%H:%M:%S ET"),
    }
