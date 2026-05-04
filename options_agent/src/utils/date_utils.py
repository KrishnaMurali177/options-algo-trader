"""Date utilities — DTE calculation, market hours, earnings calendar."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def days_to_expiration(expiration: date) -> int:
    """Calendar days until option expiration."""
    return (expiration - date.today()).days


def is_market_open() -> bool:
    """Return True if the US stock market is currently open (simple check, ignores holidays)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:  # Saturday / Sunday
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def next_market_open() -> datetime:
    """Return the next market open datetime (ET)."""
    now = datetime.now(ET)
    candidate = now.replace(hour=9, minute=30, second=0, microsecond=0)
    if now.time() >= MARKET_CLOSE or now.weekday() >= 5:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def fridays_between(start: date, end: date) -> list[date]:
    """List all Fridays between start and end (common option expiration days)."""
    fridays = []
    current = start
    while current <= end:
        if current.weekday() == 4:  # Friday
            fridays.append(current)
        current += timedelta(days=1)
    return fridays


def target_expiration(min_dte: int = 30, max_dte: int = 365) -> date:
    """Return the nearest Friday expiration within [min_dte, max_dte]."""
    start = date.today() + timedelta(days=min_dte)
    end = date.today() + timedelta(days=max_dte)
    candidates = fridays_between(start, end)
    if candidates:
        return candidates[0]
    # Fallback: midpoint
    return date.today() + timedelta(days=(min_dte + max_dte) // 2)

